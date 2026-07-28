"""Database package initialization.

Registers a process-wide SQLAlchemy connect listener so EVERY SQLite
connection — regardless of which module created the engine — runs with:

    PRAGMA journal_mode=WAL      (persistent, stored in the db file)
    PRAGMA synchronous=NORMAL    (per-connection)
    PRAGMA busy_timeout=<ms>     (per-connection)

Why WAL: in the default rollback-journal mode every commit is a full fsync and
writers block readers, which is the root cause of the "database is locked"
errors documented in CLAUDE.md. WAL allows concurrent readers during writes
and, with synchronous=NORMAL, commits no longer fsync on every transaction
(WAL is still fsynced on checkpoint, so worst-case loss on power failure is
the last few transactions — never corruption).

Why busy_timeout: WAL does nothing for two *writers*. SQLite serialises those,
and the second one is told the database is busy. pysqlite defaults to a 5-second
wait, which is far too short here — the master-contract download commits ~218k
rows in a single transaction and holds the write lock for tens of seconds. Any
other writer arriving in that window used to fail outright, and when the writer
was an APScheduler jobstore (three of them live in ``openalgo.db``) the
exception propagated out of ``BackgroundScheduler._process_jobs`` and killed the
scheduler thread, silently stopping every scheduled job for the life of the
process. Waiting is always better than failing: normal writes take milliseconds,
so the timeout only ever engages behind a genuinely long transaction.

Registered here (the package __init__) because every database module is
imported as ``database.<module>``, so this listener is guaranteed to be in
place before any engine in the project creates its first connection. The
listener is a no-op for non-SQLite backends (PostgreSQL pools, DuckDB).

Note: WAL requires a local filesystem (it uses shared memory); do not place
the db/ directory on NFS/SMB mounts.
"""

import os
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine

# How long a blocked writer waits before giving up, in milliseconds. Sized to
# outlast the master-contract bulk insert, which is by far the longest write in
# the process; everything else finishes in milliseconds. Override with
# SQLITE_BUSY_TIMEOUT_MS on unusually slow storage.
DEFAULT_BUSY_TIMEOUT_MS = 60_000


def _busy_timeout_ms():
    """Configured busy timeout, falling back to the default on bad input."""
    raw = os.getenv("SQLITE_BUSY_TIMEOUT_MS")
    if raw is None:
        return DEFAULT_BUSY_TIMEOUT_MS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BUSY_TIMEOUT_MS
    return value if value > 0 else DEFAULT_BUSY_TIMEOUT_MS


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Apply WAL + synchronous=NORMAL + busy_timeout to every SQLite connection."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        # A pragma failure must never break the connection: if another
        # process holds a legacy-mode lock during first-time conversion the
        # connection simply continues in the journal mode already on disk.
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(f"PRAGMA busy_timeout={_busy_timeout_ms()}")
        except sqlite3.OperationalError:
            pass
    finally:
        cursor.close()
