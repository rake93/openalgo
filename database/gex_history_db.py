# database/gex_history_db.py
"""
`gex.db` - recorded GEX Levels history and the recorder's watchlist.

The seventh isolated database. It exists because the GEX Levels study draws
from a single live snapshot and discards the previous one on every refresh, so
nothing on the server knows a snapshot ever happened: "when did the Call Wall
move, and did price respect it before it moved?" is unanswerable. Gamma Bands
and the GEX Heatmap are that history plotted; neither can be drawn from one
snapshot at any price.

Its own file rather than a table in `openalgo.db` for two reasons. It is the
only high-frequency writer in the platform outside the log databases - one row
per watchlisted series per minute, plus ~47 strike children - and it is a
feature store that can be dropped and rebuilt without touching anything else.
The watchlist lives here too rather than in `settings_db`, which is a
typed-column table (analyze mode, SMTP, security), not a general key-value
store: the recorder owning its configuration next to the data it produces keeps
the whole feature in one droppable store.

Engine policy, per CLAUDE.md: `engine_factory.create_db_engine` -> NullPool, a
fresh connection per operation closed immediately. StaticPool must NOT be used.
The scoped session is registered in `utils/db_sessions.py`, which is what
releases it both on `teardown_appcontext` and on the recorder's scheduler
thread - that thread has no app context and would otherwise leak a connection
per tick for the life of the Gunicorn worker.

Two schema decisions that look odd and are deliberate:

**Per-weighting results are suffixed columns** (`call_wall_oi` /
`call_wall_vol`), not rows discriminated by a `weighting` column. `WeightBy` is
a closed set of exactly two values, so this halves the strike table - 47 rows
per snapshot, not 94 - and lets the Bands query be a plain select with no pivot.
The cost is explicit: a third weighting would be a migration.

**The raw OI and volume are stored alongside the computed exposures.** This is
the one place the design argues against normalisation, and the argument is
recent history: a lot-size units bug in this exact pipeline survived 99 green
tests and was caught only by a live broker call. With the raw inputs, a maths
error means history can be REPAIRED; without them it must be DISCARDED. That is
worth roughly 25% more disk.
"""

import os
import time

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.sql import func

from database.engine_factory import create_db_engine
from utils.logging import get_logger

logger = get_logger(__name__)

GEX_DATABASE_URL = os.getenv("GEX_DATABASE_URL", "sqlite:///db/gex.db")

# Canonical engine factory enforces the project-wide pooling policy
# (SQLite -> NullPool with check_same_thread=False) for FD hygiene.
engine = create_db_engine(GEX_DATABASE_URL)

db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

# SQLite refuses a statement with more than 999 bound parameters by default, and
# 30 days of two series is ~22,500 snapshot ids. The prune batches its IN clause
# well under that ceiling.
_ID_CHUNK = 500

SECONDS_PER_DAY = 86400


class GexSeries(Base):
    """One watchlisted (underlying, exchange, expiry rule) the recorder polls.

    `expiry_rule` is either `nearest` - resolved per tick through
    `expiry_service.get_expiry_dates`, so the contract rolls weekly - or a
    pinned DDMMMYY. The resolved contract is stored on every snapshot, never
    here, because on `nearest` it changes underneath this row.

    The table ships EMPTY and the recorder stays idle until a series is added.
    An upgrade must not silently start making broker calls on a schedule nobody
    asked for.
    """

    __tablename__ = "gex_series"
    __table_args__ = (
        UniqueConstraint("underlying", "exchange", "expiry_rule", name="uq_gex_series"),
    )

    id = Column(Integer, primary_key=True)
    underlying = Column(String(20), nullable=False)
    exchange = Column(String(20), nullable=False)
    expiry_rule = Column(String(10), nullable=False, default="nearest")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GexSnapshot(Base):
    """One series, one minute. Both weightings side by side."""

    __tablename__ = "gex_snapshot"
    # UniqueConstraint alone. SQLite backs it with a unique index on
    # (series_id, ts), which IS the range scan Bands and the Heatmap live on. A
    # second Index over the same columns, or index=True on series_id, would be
    # dead weight written on every insert.
    __table_args__ = (UniqueConstraint("series_id", "ts", name="uq_gex_snapshot_series_ts"),)

    id = Column(Integer, primary_key=True)
    series_id = Column(Integer, nullable=False)
    # Epoch SECONDS, floored to the recorder's cadence. Directly usable by the
    # chart library and immune to timezone drift; India has no DST.
    ts = Column(Integer, nullable=False)
    # The RESOLVED expiry, never the rule. On `nearest` the contract rolls
    # weekly, so 30 days of history is four or five different books: walls jump
    # at each roll because the book changed, not because the market moved. A
    # reader either filters to one contract or marks the boundary, and can do
    # neither without this column.
    expiry_date = Column(String(10), nullable=False)

    spot_price = Column(Float, nullable=False)
    forward_price = Column(Float, nullable=False)
    atm_strike = Column(Float, nullable=True)
    dte_days = Column(Float, nullable=False)
    interest_rate = Column(Float, nullable=False)
    lot_size = Column(Integer, nullable=False, default=1)
    # Weighting-independent: it is a count of strikes in the chain window.
    strikes_used = Column(Integer, nullable=False, default=0)

    call_wall_oi = Column(Float, nullable=True)
    call_wall_vol = Column(Float, nullable=True)
    put_wall_oi = Column(Float, nullable=True)
    put_wall_vol = Column(Float, nullable=True)
    # Nullable and meant to be. "No local cross" is a real reading - a chain can
    # be long or short gamma across its whole plausible range - and 0.0 would
    # draw a Zero-Gamma band at the bottom of the chart.
    zero_gamma_oi = Column(Float, nullable=True)
    zero_gamma_vol = Column(Float, nullable=True)
    net_gex_oi = Column(Float, nullable=False, default=0.0)
    net_gex_vol = Column(Float, nullable=False, default=0.0)
    regime_oi = Column(String(12), nullable=True)
    regime_vol = Column(String(12), nullable=True)
    sentiment_oi = Column(JSON, nullable=True)
    sentiment_vol = Column(JSON, nullable=True)

    # Quality is suffixed like everything else because `assess_quality` takes
    # the PRICED exposures: a chain can be good on open interest and degraded on
    # volume, and a heatmap drawn on volume must dim the columns that were
    # degraded on volume.
    #
    # Two columns per weighting. The string is what a reader filters on to dim
    # or hatch a degraded column, without parsing JSON per column. The JSON is
    # the WHOLE quality payload, including `may_draw` - that is a @property, not
    # a dataclass field, and an absent key reads as undefined -> falsy in
    # TypeScript, which would render every good recorded snapshot as "do not
    # draw". See `_quality_payload` in services/gex_levels_service.py for the
    # same trap on the live path.
    quality_verdict_oi = Column(String(12), nullable=True)
    quality_verdict_vol = Column(String(12), nullable=True)
    quality_oi = Column(JSON, nullable=True)
    quality_vol = Column(JSON, nullable=True)


class GexSnapshotStrike(Base):
    """The per-strike profile: both metrics, both weightings, plus raw inputs.

    Composite primary key `(snapshot_id, strike)` - one row per strike per
    snapshot, and the key doubles as the lookup index.
    """

    __tablename__ = "gex_snapshot_strike"

    snapshot_id = Column(Integer, primary_key=True)
    strike = Column(Float, primary_key=True)

    call_gex_oi = Column(Float, nullable=False, default=0.0)
    put_gex_oi = Column(Float, nullable=False, default=0.0)
    net_gex_oi = Column(Float, nullable=False, default=0.0)
    call_gex_vol = Column(Float, nullable=False, default=0.0)
    put_gex_vol = Column(Float, nullable=False, default=0.0)
    net_gex_vol = Column(Float, nullable=False, default=0.0)
    call_dex_oi = Column(Float, nullable=False, default=0.0)
    put_dex_oi = Column(Float, nullable=False, default=0.0)
    net_dex_oi = Column(Float, nullable=False, default=0.0)
    call_dex_vol = Column(Float, nullable=False, default=0.0)
    put_dex_vol = Column(Float, nullable=False, default=0.0)
    net_dex_vol = Column(Float, nullable=False, default=0.0)

    # The raw inputs, kept deliberately against normalisation - see the module
    # docstring. These are what make a maths error repairable instead of fatal.
    call_oi = Column(Float, nullable=False, default=0.0)
    put_oi = Column(Float, nullable=False, default=0.0)
    call_volume = Column(Float, nullable=False, default=0.0)
    put_volume = Column(Float, nullable=False, default=0.0)


_SNAPSHOT_COLUMNS = tuple(c.name for c in GexSnapshot.__table__.columns)
_STRIKE_COLUMNS = tuple(c.name for c in GexSnapshotStrike.__table__.columns)


def init_gex_history_db():
    """Create the gex.db tables if they do not exist."""
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "GEX History DB", logger)


# ------------------------------------------------------------------- watchlist


def _series_dict(row: GexSeries) -> dict:
    return {
        "id": row.id,
        "underlying": row.underlying,
        "exchange": row.exchange,
        "expiry_rule": row.expiry_rule,
        "enabled": bool(row.enabled),
    }


def list_series(enabled_only: bool = False) -> list[dict]:
    """Every watchlisted series, oldest first.

    Args:
        enabled_only: Restrict to series the recorder should currently poll.

    Returns:
        A list of plain dicts - detached from the session, so a caller may hold
        them across a `db_session.remove()`. Empty on any error: the recorder
        registering zero jobs is a safe failure, registering jobs from a
        half-read watchlist is not.
    """
    try:
        query = db_session.query(GexSeries)
        if enabled_only:
            query = query.filter(GexSeries.enabled.is_(True))
        return [_series_dict(row) for row in query.order_by(GexSeries.id).all()]
    except Exception:
        logger.exception("Error listing GEX series")
        return []


def get_series(series_id: int) -> dict | None:
    """One series by id, or None if it does not exist."""
    try:
        row = db_session.query(GexSeries).filter(GexSeries.id == series_id).first()
        return _series_dict(row) if row else None
    except Exception:
        logger.exception(f"Error fetching GEX series {series_id}")
        return None


def add_series(underlying: str, exchange: str, expiry_rule: str = "nearest") -> tuple:
    """Add a series to the watchlist.

    Stored upper-cased: the chain service, the expiry lookup and the fast-path
    snapshot lookup all work in upper case, so a lower-case row would record
    fine and then never be found again.

    Args:
        underlying: Underlying symbol (e.g. NIFTY).
        exchange: Options exchange (e.g. NFO).
        expiry_rule: `nearest`, or a pinned DDMMMYY.

    Returns:
        Tuple of (success, message, series dict or None).
    """
    underlying = (underlying or "").strip().upper()
    exchange = (exchange or "").strip().upper()
    expiry_rule = (expiry_rule or "nearest").strip().upper()
    if expiry_rule == "NEAREST":
        expiry_rule = "nearest"

    if not underlying or not exchange:
        return False, "underlying and exchange are required", None

    try:
        row = GexSeries(
            underlying=underlying,
            exchange=exchange,
            expiry_rule=expiry_rule,
            enabled=True,
        )
        db_session.add(row)
        db_session.commit()
        logger.info(f"GEX recorder: watching {underlying} {exchange} {expiry_rule}")
        return True, "Series added", _series_dict(row)
    except IntegrityError:
        db_session.rollback()
        return False, f"{underlying} {exchange} {expiry_rule} is already being recorded", None
    except Exception as exc:
        logger.exception("Error adding GEX series")
        db_session.rollback()
        return False, str(exc), None


def set_series_enabled(series_id: int, enabled: bool) -> tuple:
    """Start or stop recording a series without touching its history.

    Returns:
        Tuple of (success, message).
    """
    try:
        row = db_session.query(GexSeries).filter(GexSeries.id == series_id).first()
        if row is None:
            return False, "Series not found"
        row.enabled = bool(enabled)
        db_session.commit()
        return True, "Series enabled" if enabled else "Series disabled"
    except Exception as exc:
        logger.exception(f"Error updating GEX series {series_id}")
        db_session.rollback()
        return False, str(exc)


def remove_series(series_id: int) -> tuple:
    """Delete a series AND every snapshot recorded for it.

    Destructive, and there is no source to rebuild from - the option chain API
    returns only current OI and volume. Callers must say so. `set_series_enabled(
    series_id, False)` is the "stop recording, keep the history" path.

    The children are deleted explicitly rather than by cascade for the same
    reason the prune does: SQLite does not enforce foreign keys unless
    `PRAGMA foreign_keys=ON` is set per connection, and NullPool hands out a
    fresh connection per operation, so that pragma cannot be assumed armed.

    Returns:
        Tuple of (success, message).
    """
    try:
        row = db_session.query(GexSeries).filter(GexSeries.id == series_id).first()
        if row is None:
            return False, "Series not found"

        snapshot_ids = [
            sid
            for (sid,) in db_session.query(GexSnapshot.id)
            .filter(GexSnapshot.series_id == series_id)
            .all()
        ]
        strikes_deleted = _delete_strikes_for(snapshot_ids)
        db_session.query(GexSnapshot).filter(GexSnapshot.series_id == series_id).delete(
            synchronize_session=False
        )
        db_session.delete(row)
        db_session.commit()
        logger.info(
            f"GEX recorder: removed series {series_id} with "
            f"{len(snapshot_ids)} snapshot(s) and {strikes_deleted} strike row(s)"
        )
        return True, (
            f"Series removed along with {len(snapshot_ids)} recorded snapshot(s). "
            "Recorded history cannot be rebuilt."
        )
    except Exception as exc:
        logger.exception(f"Error removing GEX series {series_id}")
        db_session.rollback()
        return False, str(exc)


# -------------------------------------------------------------------- snapshots


def _row_dict(row, columns) -> dict:
    return {name: getattr(row, name) for name in columns}


def write_snapshot(series_id: int, snapshot: dict, strikes: list[dict]) -> int | None:
    """Persist one tick: the snapshot row and all of its strike children.

    One transaction, so a crash between the two can never leave a snapshot with
    no profile.

    Args:
        series_id: The series this tick belongs to.
        snapshot: Column values for `GexSnapshot`, minus `id` and `series_id`.
            Unknown keys are ignored rather than raising, so a caller carrying
            an extra field does not break the write.
        strikes: Column values for `GexSnapshotStrike`, minus `snapshot_id`.

    Returns:
        The new snapshot id, or None if a snapshot already exists for this
        `(series_id, ts)` or the write failed.
    """
    try:
        values = {k: v for k, v in snapshot.items() if k in _SNAPSHOT_COLUMNS}
        values["series_id"] = series_id
        row = GexSnapshot(**values)
        db_session.add(row)
        # Flush, not commit: the id is needed for the children and both must
        # land in the same transaction.
        db_session.flush()

        if strikes:
            db_session.bulk_insert_mappings(
                GexSnapshotStrike,
                [
                    {
                        **{k: v for k, v in strike.items() if k in _STRIKE_COLUMNS},
                        "snapshot_id": row.id,
                    }
                    for strike in strikes
                ],
            )
        db_session.commit()
        return row.id
    except IntegrityError:
        # The (series_id, ts) unique constraint. Expected, not an error: with
        # coalesce enabled a backlog collapses to one run, and a retry can land
        # inside the same cadence bucket. Logging this at warning would fill
        # errors.jsonl on a perfectly healthy recorder.
        db_session.rollback()
        logger.debug(
            f"GEX snapshot for series {series_id} at ts={snapshot.get('ts')} already exists"
        )
        return None
    except Exception:
        logger.exception(f"Error writing GEX snapshot for series {series_id}")
        db_session.rollback()
        return None


def get_latest_snapshot(underlying: str, exchange: str, expiry_date: str) -> dict | None:
    """The newest recorded snapshot for one CONTRACT, with its strike profile.

    Scoped to the resolved `expiry_date`, not just the series: a `nearest`
    series holds several contracts over 30 days, and serving its newest row
    regardless of expiry would hand the study last week's book the moment the
    roll happened.

    Args:
        underlying: Underlying symbol.
        exchange: Options exchange.
        expiry_date: The RESOLVED expiry in DDMMMYY.

    Returns:
        The snapshot's columns, the owning series' `underlying` and `exchange`,
        and a `strikes` list ordered by strike ascending. None if nothing has
        been recorded for that contract.

        The two series fields are folded in because a snapshot row alone cannot
        say what instrument it belongs to, and the fast path has to echo them
        back in the study's payload.
    """
    try:
        found = (
            db_session.query(GexSnapshot, GexSeries.underlying, GexSeries.exchange)
            .join(GexSeries, GexSeries.id == GexSnapshot.series_id)
            .filter(
                GexSeries.underlying == (underlying or "").strip().upper(),
                GexSeries.exchange == (exchange or "").strip().upper(),
                GexSnapshot.expiry_date == (expiry_date or "").strip().upper(),
            )
            .order_by(GexSnapshot.ts.desc())
            .first()
        )
        if found is None:
            return None

        row, series_underlying, series_exchange = found
        payload = _row_dict(row, _SNAPSHOT_COLUMNS)
        payload["underlying"] = series_underlying
        payload["exchange"] = series_exchange
        payload["strikes"] = [
            _row_dict(strike, _STRIKE_COLUMNS)
            for strike in db_session.query(GexSnapshotStrike)
            .filter(GexSnapshotStrike.snapshot_id == row.id)
            .order_by(GexSnapshotStrike.strike)
            .all()
        ]
        return payload
    except Exception:
        logger.exception(f"Error reading the latest GEX snapshot for {underlying} {exchange}")
        return None


def get_series_by_contract(underlying: str, exchange: str, expiry_date: str) -> dict | None:
    """The series that has recorded snapshots for one RESOLVED contract.

    Resolved by what was actually recorded rather than by the series' rule,
    because the rule does not identify a contract: a `nearest` series holds
    several over its retention window, and a pinned one names exactly one. A
    caller holding an expiry - from a live snapshot, say - can therefore find
    its history without knowing which rule produced it.

    Args:
        underlying: Underlying symbol.
        exchange: Options exchange.
        expiry_date: The RESOLVED expiry in DDMMMYY.

    Returns:
        The series dict, or None if nothing has been recorded for that contract.
    """
    try:
        row = (
            db_session.query(GexSeries)
            .join(GexSnapshot, GexSnapshot.series_id == GexSeries.id)
            .filter(
                GexSeries.underlying == (underlying or "").strip().upper(),
                GexSeries.exchange == (exchange or "").strip().upper(),
                GexSnapshot.expiry_date == (expiry_date or "").strip().upper(),
            )
            .first()
        )
        return _series_dict(row) if row else None
    except Exception:
        logger.exception(f"Error resolving the GEX series for {underlying} {exchange}")
        return None


def get_snapshots_in_range(
    series_id: int,
    from_ts: int,
    to_ts: int,
    expiry_date: str | None = None,
) -> list[dict]:
    """Snapshot rows for one series between two timestamps, INCLUSIVE both ends.

    Snapshot rows only, no strike children: this is the query Gamma Bands runs,
    and a month of strike rows is exactly what the separate grid endpoint exists
    to downsample.

    A minute with no row stays absent. The caller must render that as a gap -
    interpolating would draw flat gamma where there was no reading.

    Args:
        series_id: The series to read.
        from_ts: Inclusive lower bound, epoch seconds.
        to_ts: Inclusive upper bound, epoch seconds.
        expiry_date: Optional RESOLVED expiry to scope to. A `nearest` series
            holds several contracts over its retention window, and splicing them
            into one line would draw a wall jump at every roll that is the book
            changing rather than the market moving. Callers plotting through time
            should always pass it.

    Returns:
        Snapshot dicts ordered by `ts` ascending.
    """
    try:
        query = db_session.query(GexSnapshot).filter(
            GexSnapshot.series_id == series_id,
            GexSnapshot.ts >= from_ts,
            GexSnapshot.ts <= to_ts,
        )
        if expiry_date:
            query = query.filter(GexSnapshot.expiry_date == expiry_date.strip().upper())
        return [_row_dict(row, _SNAPSHOT_COLUMNS) for row in query.order_by(GexSnapshot.ts).all()]
    except Exception:
        logger.exception(f"Error reading GEX snapshots for series {series_id}")
        return []


def get_snapshot_index_in_range(
    series_id: int,
    from_ts: int,
    to_ts: int,
    expiry_date: str | None = None,
) -> list[dict]:
    """A light index of the snapshots in a window: id, ts and quality only.

    The Heatmap's first pass. Its column budget is decided from how many
    snapshots a window holds, and reading whole snapshot rows - let alone their
    47 strike children each - only to discard fourteen of every fifteen is the
    cost the budget exists to avoid. So this reads four columns and nothing
    else, and `get_strikes_for_snapshots` is called afterwards for the survivors.

    Args:
        series_id: The series to read.
        from_ts: Inclusive lower bound, epoch seconds.
        to_ts: Inclusive upper bound, epoch seconds.
        expiry_date: Optional RESOLVED expiry to scope to. Callers plotting
            through time should always pass it - see `get_snapshots_in_range`.

    Returns:
        Dicts with `id`, `ts`, `quality_verdict_oi` and `quality_verdict_vol`,
        ordered by `ts` ascending.
    """
    try:
        query = db_session.query(
            GexSnapshot.id,
            GexSnapshot.ts,
            GexSnapshot.quality_verdict_oi,
            GexSnapshot.quality_verdict_vol,
        ).filter(
            GexSnapshot.series_id == series_id,
            GexSnapshot.ts >= from_ts,
            GexSnapshot.ts <= to_ts,
        )
        if expiry_date:
            query = query.filter(GexSnapshot.expiry_date == expiry_date.strip().upper())
        return [
            {
                "id": row.id,
                "ts": row.ts,
                "quality_verdict_oi": row.quality_verdict_oi,
                "quality_verdict_vol": row.quality_verdict_vol,
            }
            for row in query.order_by(GexSnapshot.ts).all()
        ]
    except Exception:
        logger.exception(f"Error reading the GEX snapshot index for series {series_id}")
        return []


def get_strikes_for_snapshots(snapshot_ids: list[int]) -> dict[int, list[dict]]:
    """Strike rows for several snapshots at once, grouped by snapshot id.

    Batched at `_ID_CHUNK` per query for the same reason `_delete_strikes_for`
    batches: SQLite caps the number of bound variables in one statement, and a
    thousand-column grid would blow straight past it. One query per batch, not
    one per snapshot - a thousand round trips would cost far more than the read.

    Args:
        snapshot_ids: Snapshot ids to read children for.

    Returns:
        Strike-row dicts keyed by snapshot id, each list ordered by strike
        ascending. A snapshot with no children is simply absent, which the grid
        builder renders as a blank column rather than a row of zeros.
    """
    if not snapshot_ids:
        return {}

    grouped: dict[int, list[dict]] = {}
    try:
        for start in range(0, len(snapshot_ids), _ID_CHUNK):
            batch = snapshot_ids[start : start + _ID_CHUNK]
            rows = (
                db_session.query(GexSnapshotStrike)
                .filter(GexSnapshotStrike.snapshot_id.in_(batch))
                .order_by(GexSnapshotStrike.snapshot_id, GexSnapshotStrike.strike)
                .all()
            )
            for row in rows:
                grouped.setdefault(row.snapshot_id, []).append(_row_dict(row, _STRIKE_COLUMNS))
        return grouped
    except Exception:
        logger.exception("Error reading GEX strike rows for a snapshot batch")
        return {}


def _delete_strikes_for(snapshot_ids: list[int]) -> int:
    """Delete strike rows for the given snapshots, in batches. Does not commit."""
    deleted = 0
    for start in range(0, len(snapshot_ids), _ID_CHUNK):
        chunk = snapshot_ids[start : start + _ID_CHUNK]
        deleted += (
            db_session.query(GexSnapshotStrike)
            .filter(GexSnapshotStrike.snapshot_id.in_(chunk))
            .delete(synchronize_session=False)
        )
    return deleted


def prune_snapshots(retention_days: int) -> dict:
    """Delete snapshots older than the retention window, children first.

    Strike rows go explicitly rather than by cascade: SQLite does not enforce
    foreign keys unless `PRAGMA foreign_keys=ON` is set PER CONNECTION, and with
    NullPool handing out a fresh connection per operation that pragma cannot be
    assumed armed. Orphaned strike rows would be silent disk growth - which is
    also why this returns counts rather than nothing, so the caller can log what
    actually happened.

    Args:
        retention_days: Keep snapshots newer than this many days.

    Returns:
        `{"snapshots_deleted", "strikes_deleted", "snapshots_remaining"}`. All
        zero on failure, with the traceback logged.
    """
    try:
        cutoff = int(time.time()) - retention_days * SECONDS_PER_DAY
        snapshot_ids = [
            sid for (sid,) in db_session.query(GexSnapshot.id).filter(GexSnapshot.ts < cutoff).all()
        ]

        strikes_deleted = _delete_strikes_for(snapshot_ids)
        snapshots_deleted = 0
        for start in range(0, len(snapshot_ids), _ID_CHUNK):
            chunk = snapshot_ids[start : start + _ID_CHUNK]
            snapshots_deleted += (
                db_session.query(GexSnapshot)
                .filter(GexSnapshot.id.in_(chunk))
                .delete(synchronize_session=False)
            )
        db_session.commit()

        return {
            "snapshots_deleted": snapshots_deleted,
            "strikes_deleted": strikes_deleted,
            "snapshots_remaining": db_session.query(GexSnapshot).count(),
        }
    except Exception:
        logger.exception("Error pruning GEX snapshots")
        db_session.rollback()
        return {"snapshots_deleted": 0, "strikes_deleted": 0, "snapshots_remaining": 0}
