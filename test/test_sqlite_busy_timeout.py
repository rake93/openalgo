"""Every SQLite connection must wait for a busy database, not fail instantly.

WAL — already applied process-wide — lets readers run during a write, which is
what fixed the original "database is locked" reports. It does nothing for two
*writers*: SQLite serialises those, and pysqlite only waits 5 seconds before
giving up.

That is not hypothetical. The master-contract download commits ~218k rows in one
transaction, holding the write lock for tens of seconds. An APScheduler jobstore
living in the same database (`indicator_apscheduler_jobs`, `flow_apscheduler_jobs`
and the historify one all do) tries to stamp `next_run_time` during that window,
gets `OperationalError: database is locked`, and the exception propagates out of
`BackgroundScheduler._process_jobs` and **kills the scheduler thread** — silently
stopping every scheduled job for the life of the process.

These jobstores build their own engines from a URL, bypassing
`database.engine_factory.create_db_engine`, so the fix has to live in the
process-wide connect listener to reach them.
"""

import threading
import time

import pytest
from sqlalchemy import text

import database
from database.engine_factory import create_db_engine

BUSY_TIMEOUT_MS = database.DEFAULT_BUSY_TIMEOUT_MS
# What pysqlite gives you without the listener, and what proved too short.
PYSQLITE_DEFAULT_MS = 5_000


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'busy.db'}"


def test_busy_timeout_is_applied_to_every_sqlite_connection(db_url):
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            # PRAGMA busy_timeout returns the value in milliseconds.
            timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
        assert timeout == BUSY_TIMEOUT_MS
        # The point of the change: comfortably longer than pysqlite's default,
        # which a multi-second bulk insert blows straight through.
        assert timeout > PYSQLITE_DEFAULT_MS
    finally:
        engine.dispose()


def test_busy_timeout_is_configurable_and_ignores_nonsense(monkeypatch):
    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "12345")
    assert database._busy_timeout_ms() == 12345
    for bad in ("", "abc", "0", "-1"):
        monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", bad)
        assert database._busy_timeout_ms() == BUSY_TIMEOUT_MS
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT_MS")
    assert database._busy_timeout_ms() == BUSY_TIMEOUT_MS


def test_busy_timeout_reaches_engines_that_bypass_the_factory(db_url):
    # An APScheduler SQLAlchemyJobStore does exactly this: create_engine(url).
    from sqlalchemy import create_engine

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == BUSY_TIMEOUT_MS
    finally:
        engine.dispose()


def test_a_second_writer_outwaits_a_write_longer_than_the_old_limit(db_url):
    """The actual failure: one long write, a second writer arriving mid-way.

    The lock is held past pysqlite's 5-second default, so this fails on the old
    behaviour and passes on the new one. Necessarily slow — the bug only exists
    beyond that threshold, so a faster test would prove nothing.
    """
    setup = create_db_engine(db_url)
    with setup.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE jobs (id TEXT PRIMARY KEY, next_run REAL)")
        conn.exec_driver_sql("INSERT INTO jobs VALUES ('sweep', 0)")
    setup.dispose()

    holder = create_db_engine(db_url)
    writer = create_db_engine(db_url)
    hold_seconds = PYSQLITE_DEFAULT_MS / 1000 + 2
    started = threading.Event()
    failure: list[Exception] = []

    def long_write() -> None:
        """Stand-in for the master-contract bulk insert holding the lock."""
        with holder.begin() as conn:
            conn.exec_driver_sql("INSERT INTO jobs VALUES ('bulk', 1)")
            started.set()
            time.sleep(hold_seconds)

    t = threading.Thread(target=long_write)
    t.start()
    try:
        assert started.wait(5), "the holding writer never started"
        # Exactly what APScheduler does to stamp a job's next run time.
        try:
            with writer.begin() as conn:
                conn.execute(text("UPDATE jobs SET next_run = 2 WHERE id = 'sweep'"))
        except Exception as exc:  # pragma: no cover - only on regression
            failure.append(exc)
    finally:
        t.join()
        holder.dispose()
        writer.dispose()

    assert not failure, f"second writer failed instead of waiting: {failure[0]!r}"
