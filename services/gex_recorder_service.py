# services/gex_recorder_service.py
"""
Records a GEX Levels snapshot per watchlisted series per minute into `gex.db`.

Why this exists at all: the study polls on a client-side timer, keeps one
snapshot in memory and discards the previous one, so nothing on the server knows
a snapshot ever happened. Gamma Bands and the GEX Heatmap are that history
plotted, and neither can be drawn from a single snapshot at any price.

**It reduces broker load rather than adding to it.** Because the study reads the
newest recorded row (see the fast path in `blueprints/gex.py`) instead of
calling the broker, N open tabs cost one poll rather than N. This deployment
shares one broker session across up to five devices, so three tabs on the study
used to be three identical chain fetches a minute.

One pipeline, not two. Every tick runs `gex_levels_service.fetch_snapshot_inputs`
and then `build_snapshot` - the exact functions the live endpoint runs - once per
weighting off a single chain fetch and a single IV solve. A recorder that
reimplemented the maths is the failure this design most wants to prevent: the
`/gex` Tools page drifted from the study exactly that way and shipped three
defects.

**Memory jobstore, deliberately.** The schedule is derived from the watchlist on
every boot rather than authored by a user, so persisting it buys nothing while
costing a write into a shared database per fired job. It also sidesteps the
APScheduler jobstore import-path trap: a persisted job stores its module path,
so renaming this module would error on startup until the row was cleaned out.

**Three rate-limit mitigations, all live rather than hypothetical** - a single
manual chain call during design hit `Rate limit hit (805)`:
  1. `stagger_seconds` spreads series across the cadence window so they do not
     all fire on the minute.
  2. `max_instances: 1` means a slow tick can never overlap itself.
  3. `coalesce: True` collapses a backlog to one run.

FD hygiene (CLAUDE.md): one module-level singleton scheduler, started once;
every job body ends in `finally: remove_all_scoped_sessions()`, because a
scheduler thread has no app context and never reaches
`app.teardown_appcontext`.
"""

import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database import gex_history_db
from database.auth_db import get_first_available_api_key
from services.expiry_service import get_expiry_dates
from services.gex_levels_service import (
    ChainFetchFailed,
    PricingLibraryMissing,
    UnusableChain,
    build_snapshot,
    fetch_snapshot_inputs,
    load_black76,
)
from services.option_target_sessions import session_is_open
from utils.db_sessions import remove_all_scoped_sessions
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# One snapshot per series per minute. Open interest does not change tick by
# tick, so a faster poll largely re-fetches identical data - and this cadence
# becomes the study's effective refresh rate through the recorded fast path.
CADENCE_SECONDS = int(os.getenv("GEX_RECORDER_CADENCE_SECONDS", "60"))

# ~100 MB per rolling month for two series. Two-tier storage (full grid recent,
# levels-only older) buys nothing at that size and the schema does not prevent
# adding it later as a roll-up over rows that already exist.
RETENTION_DAYS = int(os.getenv("GEX_RECORDER_RETENTION_DAYS", "30"))

# 03:30 IST: after the ~3:00 AM Indian broker token rollover, and well outside
# any exchange session.
_PRUNE_HOUR = 3
_PRUNE_MINUTE = 30

_JOB_PREFIX = "gex_record_"
_PRUNE_JOB_ID = "gex_prune"


def stagger_seconds(series_id: int) -> int:
    """A per-series offset into the cadence window.

    7 is coprime with 60, so consecutive ids land 7 seconds apart and wrap
    without colliding. Deterministic rather than random so a restart does not
    reshuffle every series onto a new second.

    Args:
        series_id: The series' database id.

    Returns:
        Seconds to delay this series' first fire, inside [0, CADENCE_SECONDS).
    """
    return (series_id * 7) % CADENCE_SECONDS


def _compact_expiry(dashed: str) -> str:
    """Convert the expiry API's DD-MMM-YY into the DDMMMYY the chain wants."""
    return dashed.replace("-", "").upper()


def _resolve_expiry(series: dict, api_key: str) -> str | None:
    """The contract to record this tick.

    A pinned rule is used verbatim and never looked up - a pinned contract must
    keep recording the contract that was pinned, even once it is no longer the
    nearest one.

    On `nearest`, `get_expiry_dates` is already filtered to live expiries and
    sorted ascending, so the first entry is the front contract. It rolls weekly,
    which is why the resolved value is stored on every snapshot rather than on
    the series row.

    Args:
        series: The series dict from `gex_history_db.get_series`.
        api_key: OpenAlgo API key.

    Returns:
        DDMMMYY, or None if the lookup failed or returned nothing. None means
        skip the tick: better a gap than a snapshot against a guessed contract.
    """
    rule = series["expiry_rule"]
    if rule != "nearest":
        return rule

    ok, response, _status = get_expiry_dates(
        series["underlying"], series["exchange"], "options", api_key
    )
    if not ok:
        logger.warning(
            f"GEX recorder: expiry lookup failed for {series['underlying']} "
            f"{series['exchange']}; skipping this tick"
        )
        return None

    dates = (response or {}).get("data") or []
    if not dates:
        logger.warning(
            f"GEX recorder: no live expiry for {series['underlying']} "
            f"{series['exchange']}; skipping this tick"
        )
        return None
    return _compact_expiry(dates[0])


def _snapshot_row(inputs, by_oi: dict, by_vol: dict, ts: int) -> dict:
    """Fold the two per-weighting payloads into one snapshot row."""
    return {
        "ts": ts,
        "expiry_date": inputs.expiry_date,
        "spot_price": by_oi["spot_price"],
        "forward_price": by_oi["forward_price"],
        "atm_strike": by_oi["atm_strike"],
        "dte_days": by_oi["dte_days"],
        "interest_rate": by_oi["interest_rate"],
        "lot_size": by_oi["lot_size"],
        # Weighting-independent: a count of strikes in the chain window.
        "strikes_used": by_oi["quality"]["strikes_used"],
        "call_wall_oi": by_oi["call_wall"],
        "call_wall_vol": by_vol["call_wall"],
        "put_wall_oi": by_oi["put_wall"],
        "put_wall_vol": by_vol["put_wall"],
        "zero_gamma_oi": by_oi["zero_gamma"],
        "zero_gamma_vol": by_vol["zero_gamma"],
        "net_gex_oi": by_oi["net_gex"],
        "net_gex_vol": by_vol["net_gex"],
        "regime_oi": by_oi["regime"],
        "regime_vol": by_vol["regime"],
        "sentiment_oi": by_oi["sentiment"],
        "sentiment_vol": by_vol["sentiment"],
        "quality_verdict_oi": by_oi["quality"]["verdict"],
        "quality_verdict_vol": by_vol["quality"]["verdict"],
        # Whole payload, not verdict + notes: `may_draw` is a @property and an
        # absent key reads as falsy in TypeScript, which would render every good
        # recorded snapshot as "do not draw".
        "quality_oi": by_oi["quality"],
        "quality_vol": by_vol["quality"],
    }


def _strike_rows(inputs, by_oi: dict, by_vol: dict) -> list[dict]:
    """Fold the two per-weighting profiles, plus the raw inputs, into strike rows.

    Both `strikes` lists come from the same `legs` object built once inside
    `build_snapshot`, so they are aligned by construction - `strict=True` and the
    strike assertion below turn a broken seam into a loud failure rather than a
    silently mismatched profile where one strike's gamma meets another's delta.
    """
    raw = {row.strike: row for row in inputs.rows}

    out: list[dict] = []
    for gex, dex in zip(by_oi["strikes"], by_vol["strikes"], strict=True):
        if gex["strike"] != dex["strike"]:
            raise ValueError(
                f"weighting profiles disagree on strike order: {gex['strike']} vs {dex['strike']}"
            )
        source = raw.get(gex["strike"])
        out.append(
            {
                "strike": gex["strike"],
                "call_gex_oi": gex["call_gex"],
                "put_gex_oi": gex["put_gex"],
                "net_gex_oi": gex["net_gex"],
                "call_dex_oi": gex["call_dex"],
                "put_dex_oi": gex["put_dex"],
                "net_dex_oi": gex["net_dex"],
                "call_gex_vol": dex["call_gex"],
                "put_gex_vol": dex["put_gex"],
                "net_gex_vol": dex["net_gex"],
                "call_dex_vol": dex["call_dex"],
                "put_dex_vol": dex["put_dex"],
                "net_dex_vol": dex["net_dex"],
                "call_oi": source.call_oi if source else 0.0,
                "put_oi": source.put_oi if source else 0.0,
                "call_volume": source.call_volume if source else 0.0,
                "put_volume": source.put_volume if source else 0.0,
            }
        )
    return out


def record_series_once(series_id: int, now: int | None = None) -> None:
    """Record one tick for one series. Never raises.

    A tick that cannot complete writes NOTHING and leaves a gap. That is the
    correct outcome: the Heatmap leaves the minute blank and the Bands break the
    line, whereas a partial row would draw flat gamma where there was no
    reading - the same error `quality.py` and `direction.ts` already forbid.

    Args:
        series_id: The series to record.
        now: Epoch seconds to stamp the snapshot with. Defaults to the clock;
            an explicit value exists for tests.
    """
    try:
        series = gex_history_db.get_series(series_id)
        if series is None:
            # sync_jobs removes a deleted series' job, but a tick already in
            # flight can arrive after the delete.
            logger.debug(f"GEX recorder: series {series_id} no longer exists")
            return
        if not series["enabled"]:
            return

        if not session_is_open(series["exchange"], datetime.now(IST), default=False):
            # default=False: failing open here would poll the broker every
            # minute around the clock. A merely suspect calendar window never
            # reaches this default - the provider has already fallen back to
            # the static per-exchange table.
            return

        api_key = get_first_available_api_key()
        if not api_key:
            logger.warning(
                "GEX recorder: no API key available; generate one at /apikey. Skipping this tick."
            )
            return

        expiry_date = _resolve_expiry(series, api_key)
        if not expiry_date:
            return

        black76 = load_black76()
        inputs = fetch_snapshot_inputs(
            series["underlying"], series["exchange"], expiry_date, api_key
        )
        # One chain fetch, one IV solve, both weightings. resolve_ivs takes no
        # weight_by, so the expensive half is paid once.
        by_oi = build_snapshot(black76, inputs, "oi")
        by_vol = build_snapshot(black76, inputs, "volume")

        # Floored to the cadence so the Heatmap's x-axis is regular and the
        # (series_id, ts) unique constraint actually catches a double fire.
        stamp = (now if now is not None else int(time.time())) // CADENCE_SECONDS
        stamp *= CADENCE_SECONDS

        gex_history_db.write_snapshot(
            series_id,
            _snapshot_row(inputs, by_oi, by_vol, stamp),
            _strike_rows(inputs, by_oi, by_vol),
        )

    except ChainFetchFailed as exc:
        logger.warning(f"GEX recorder: chain fetch failed for series {series_id}: {exc}")
    except UnusableChain as exc:
        logger.warning(f"GEX recorder: unusable chain for series {series_id}: {exc}")
    except PricingLibraryMissing as exc:
        logger.error(f"GEX recorder: {exc}")
    except Exception:
        # Deliberately broad. A bad tick must cost one gap, never the schedule.
        logger.exception(f"GEX recorder: tick failed for series {series_id}")
    finally:
        # This runs on a scheduler thread with no app context, so
        # teardown_appcontext never fires. The tick touched gex.db, the symbol
        # database (expiry lookup) and auth_db; every one of those sessions
        # holds a connection until it is released.
        remove_all_scoped_sessions()


def prune_history_once() -> None:
    """Delete snapshots past the retention window. Never raises.

    Logs rows deleted AND rows remaining, because a prune that silently stops
    working looks exactly like a prune that had nothing to do.
    """
    try:
        result = gex_history_db.prune_snapshots(RETENTION_DAYS)
        logger.info(
            f"GEX history prune: deleted {result['snapshots_deleted']} snapshot(s) and "
            f"{result['strikes_deleted']} strike row(s) older than {RETENTION_DAYS} days; "
            f"{result['snapshots_remaining']} snapshot(s) remain"
        )
    except Exception:
        logger.exception("GEX history prune failed")
    finally:
        remove_all_scoped_sessions()


class GexRecorderScheduler:
    """Module-level singleton owning the recorder's one scheduler thread.

    Mirrors `services/openscript/alert_service.IndicatorAlertScheduler`: double
    checked `__new__`, idempotent `init`, one thread for the life of the
    process. Never construct a second one - the recorder's whole rate-limit
    story assumes a single set of staggered jobs.
    """

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._scheduler = None
                    inst._initialized = False
                    cls._instance = inst
        return cls._instance

    def init(self) -> None:
        """Start the scheduler and register the prune plus one job per series.

        The thread starts even with an empty watchlist so a later `add_series`
        has something to register against. "Idle" means no recording jobs and
        therefore no broker calls, which an empty watchlist guarantees.
        """
        with self._init_lock:
            if self._initialized:
                return

            from utils.scheduler import ResilientBackgroundScheduler

            # Default (in-memory) jobstore on purpose - see the module docstring.
            self._scheduler = ResilientBackgroundScheduler(
                job_defaults={
                    "coalesce": True,
                    "max_instances": 1,
                    # Shorter than the cadence: a tick that missed its slot by
                    # more than a minute has been superseded by the next one.
                    "misfire_grace_time": 30,
                },
            )
            self._scheduler.start()
            self._scheduler.add_job(
                prune_history_once,
                trigger=CronTrigger(
                    hour=_PRUNE_HOUR, minute=_PRUNE_MINUTE, timezone="Asia/Kolkata"
                ),
                id=_PRUNE_JOB_ID,
                replace_existing=True,
                name="GEX history prune",
            )
            self._initialized = True

        self.sync_jobs()

    @property
    def scheduler(self):
        return self._scheduler

    def sync_jobs(self) -> None:
        """Reconcile the scheduled jobs with the enabled watchlist.

        Called at startup and after every watchlist mutation. Idempotent, and it
        only ever touches jobs it owns - the prune job lives on the same
        scheduler and must survive a watchlist change.
        """
        if self._scheduler is None:
            return

        try:
            wanted = {s["id"]: s for s in gex_history_db.list_series(enabled_only=True)}
            existing = {
                job.id for job in self._scheduler.get_jobs() if job.id.startswith(_JOB_PREFIX)
            }

            for job_id in existing:
                try:
                    series_id = int(job_id[len(_JOB_PREFIX) :])
                except ValueError:
                    continue
                if series_id not in wanted:
                    self._scheduler.remove_job(job_id)
                    logger.info(f"GEX recorder: stopped recording series {series_id}")

            for series_id, series in wanted.items():
                job_id = f"{_JOB_PREFIX}{series_id}"
                if job_id in existing:
                    continue
                self._scheduler.add_job(
                    record_series_once,
                    trigger=IntervalTrigger(
                        seconds=CADENCE_SECONDS,
                        start_date=datetime.now(IST)
                        + timedelta(seconds=stagger_seconds(series_id)),
                    ),
                    id=job_id,
                    args=[series_id],
                    replace_existing=True,
                    name=(
                        f"GEX record: {series['underlying']} {series['exchange']} "
                        f"{series['expiry_rule']}"
                    ),
                )
                logger.info(
                    f"GEX recorder: recording {series['underlying']} {series['exchange']} "
                    f"{series['expiry_rule']} every {CADENCE_SECONDS}s"
                )
        except Exception:
            logger.exception("GEX recorder: failed to sync jobs with the watchlist")
        finally:
            remove_all_scoped_sessions()

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        self._initialized = False


gex_recorder_scheduler = GexRecorderScheduler()


def get_gex_recorder() -> GexRecorderScheduler:
    """The process-wide recorder singleton."""
    return gex_recorder_scheduler


def init_gex_recorder() -> GexRecorderScheduler:
    """Start the recorder. Idempotent."""
    gex_recorder_scheduler.init()
    return gex_recorder_scheduler
