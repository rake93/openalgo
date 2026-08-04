"""A busy database must never be able to kill a scheduler thread.

APScheduler's ``BaseScheduler._process_jobs`` stamps each fired job's next run
time with ``jobstore.update_job(job)`` and does **not** guard that write. When
the jobstore lives in a SQLite file that another writer is holding — the
master-contract download replaces ~200k ``symtoken`` rows in one transaction and
held the lock for 124 seconds on the machine where this was diagnosed — the
write raises ``OperationalError: database is locked``, the exception unwinds
through ``BlockingScheduler._main_loop`` into ``Thread._bootstrap_inner``, and
the thread dies. Every scheduled job then stops for the life of the process,
silently: nothing retries, and the only visible symptom is a ``next_run_time``
in the jobstore that stops advancing.

Two independent defences are asserted here, because either alone leaves a gap:

1. The indicator alert sweep — the one job that writes on a 60-second cadence —
   keeps its schedule in memory. It is re-registered from code on every boot, so
   persisting it bought nothing and cost one shared-database write per minute.
2. Any scheduler that does need a persistent jobstore (Flow's user-created
   workflow schedules) survives a failing pass and retries instead of dying.
"""

import sqlite3

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.exc import OperationalError

from utils.scheduler import SCHEDULER_RETRY_SECONDS, ResilientBackgroundScheduler


def _locked_database_error():
    """The exact error APScheduler let through, as SQLAlchemy raises it."""
    return OperationalError(
        "UPDATE indicator_apscheduler_jobs SET next_run_time=?, job_state=?",
        {},
        sqlite3.OperationalError("database is locked"),
    )


def test_upstream_scheduler_lets_a_jobstore_write_failure_escape(monkeypatch):
    """Documents the behaviour being defended against — this is what killed the thread."""

    def boom(self):
        raise _locked_database_error()

    monkeypatch.setattr(BackgroundScheduler, "_process_jobs", boom)
    with pytest.raises(OperationalError):
        BackgroundScheduler()._process_jobs()


def test_a_locked_database_does_not_kill_the_scheduler(monkeypatch):
    def boom(self):
        raise _locked_database_error()

    monkeypatch.setattr(BackgroundScheduler, "_process_jobs", boom)
    # _main_loop assigns this return value straight to its wait; returning
    # instead of raising is precisely what keeps the thread alive.
    assert ResilientBackgroundScheduler()._process_jobs() == SCHEDULER_RETRY_SECONDS


def test_the_retry_delay_is_bounded_so_a_failing_pass_cannot_spin():
    """A failed pass leaves next_run_time stale, so the job is due again immediately.

    Returning 0 would busy-loop the scheduler thread against a locked database
    for the whole download; the delay has to be a real pause but short enough
    that jobs resume promptly once the lock clears.
    """
    assert 1 <= SCHEDULER_RETRY_SECONDS <= 30


def test_a_healthy_pass_is_returned_untouched(monkeypatch):
    monkeypatch.setattr(BackgroundScheduler, "_process_jobs", lambda self: 42.5)
    assert ResilientBackgroundScheduler()._process_jobs() == 42.5


def test_indicator_alert_sweep_is_not_persisted_to_the_shared_database():
    """The sweep is re-registered on every boot, so it has nothing to persist.

    Keeping it in a SQLAlchemy jobstore inside openalgo.db meant one write per
    sweep interval into the same file the master-contract download locks.
    """
    from services.openscript import alert_service

    alert_service.IndicatorAlertScheduler._instance = None
    scheduler = alert_service.IndicatorAlertScheduler()
    try:
        scheduler.init(socketio=None)
        assert isinstance(scheduler._scheduler._jobstores["default"], MemoryJobStore)
        assert scheduler._scheduler.get_job("indicator_alert_sweep") is not None
    finally:
        scheduler.shutdown()
        alert_service.IndicatorAlertScheduler._instance = None
