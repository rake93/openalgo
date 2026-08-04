"""Live, validated per-date trading-session provider for the Option Target Calculator.

`services.option_target.daycount` is pure (no IO) and resolves session hours
from a static per-exchange table. That table cannot express a special session
— for example the evening-only MCX session held on some equity holidays —
because those vary day to day. `services.market_calendar_service.get_timings`
is the platform's source of truth for that, but it is a service call (IO), so
it cannot live inside the pure day-count module. This module is the bridge: it
builds a `session_provider` callable (`date -> ((open_h, open_m), (close_h,
close_m)) | None`) that `daycount.year_fraction` accepts and treats as
authoritative.

Validation before trust. The seeded special-session data is currently corrupt:
every seeded special-session row spans two calendar dates (for example
2025-02-26 decodes to 11:20 -> 02:15 the *next* day, 895 minutes, though the
seed comment claims a 17:00-23:55, 415-minute evening session). That is a
pre-existing platform bug in the seed data, not something this module fixes —
it just refuses to propagate it. A window is trusted only when both endpoints
fall on the requested calendar date and the duration is a plausible positive
fraction of a day; anything else falls back to the static table with a single
warning per date, via the cache.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from cachetools import TTLCache

from services.market_calendar_service import get_timings
from services.option_target.daycount import session_for
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

_MIN_DURATION_MINUTES = 1
_MAX_DURATION_MINUTES = 1440

# Keyed by (exchange upper, iso date). Caches both a resolved session and a
# "market shut" result so repeated lookups for the same date do not re-hit the
# service or re-log the same fallback warning. Bounded and time-limited so a
# Gunicorn worker that never restarts cannot accumulate one entry per date
# forever.
_SESSION_CACHE: TTLCache = TTLCache(maxsize=512, ttl=3600)

# Distinguishes "cached, and the market was shut" from "not cached at all" —
# TTLCache.get returns None for both a miss and a stored None.
_CLOSED = object()


def _row_matches(row: dict[str, Any], exchange: str) -> bool:
    return str(row.get("exchange") or "").upper() == exchange


def _to_ist(epoch_ms: Any) -> datetime | None:
    """Convert an epoch-milliseconds value to an IST datetime, or None if invalid."""
    if epoch_ms is None or isinstance(epoch_ms, bool):
        return None
    try:
        seconds = float(epoch_ms) / 1000.0
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(seconds, IST)
    except (OverflowError, OSError, ValueError):
        return None


def _validate_window(
    row: dict[str, Any], requested: date
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Return the session bounds if `row`'s window is sane for `requested`, else None.

    Sane means: both timestamps present and numeric, both land on `requested`
    in IST, the window is forward (end > start), and its duration is between 1
    and 1440 minutes — i.e. it fits within a single calendar day.
    """
    start_dt = _to_ist(row.get("start_time"))
    end_dt = _to_ist(row.get("end_time"))
    if start_dt is None or end_dt is None:
        return None
    if start_dt.date() != requested or end_dt.date() != requested:
        return None
    if end_dt <= start_dt:
        return None
    duration_minutes = (end_dt - start_dt).total_seconds() / 60.0
    if not (_MIN_DURATION_MINUTES <= duration_minutes <= _MAX_DURATION_MINUTES):
        return None
    return ((start_dt.hour, start_dt.minute), (end_dt.hour, end_dt.minute))


def _resolve(exchange: str, day: date) -> tuple[tuple[int, int], tuple[int, int]] | Any:
    """Resolve the session for (exchange, day), validating before trusting live data.

    Returns the static-table fallback bounds, the validated live bounds, or
    the `_CLOSED` sentinel when the exchange has no session that day.
    """
    iso_date = day.isoformat()
    try:
        success, response, _status = get_timings(iso_date)
    except Exception:
        logger.warning(
            "Market timings lookup raised for %s on %s; falling back to the static session table",
            exchange,
            iso_date,
        )
        return session_for(exchange)

    if not success:
        logger.warning(
            "Market timings lookup failed for %s on %s; falling back to the static session table",
            exchange,
            iso_date,
        )
        return session_for(exchange)

    rows = (response or {}).get("data") or []
    row = next((r for r in rows if _row_matches(r, exchange)), None)
    if row is None:
        # Exchange absent from the day's timings: the market was shut, not an
        # error — do not fall back to the static table, which would wrongly
        # claim a normal session on a full holiday.
        return _CLOSED

    bounds = _validate_window(row, day)
    if bounds is None:
        logger.warning(
            "Suspect session window for %s on %s (start_time=%r, end_time=%r); "
            "falling back to the static session table",
            exchange,
            iso_date,
            row.get("start_time"),
            row.get("end_time"),
        )
        return session_for(exchange)

    return bounds


def build_session_provider(
    exchange: str,
) -> Callable[[date], tuple[tuple[int, int], tuple[int, int]] | None]:
    """Build a `daycount.year_fraction`-compatible session provider for `exchange`.

    The returned callable looks up that day's session via
    `services.market_calendar_service.get_timings`, validates the window, and
    falls back to the static per-exchange table (`daycount.session_for`) on
    anything unavailable or suspect — including the known-corrupt seeded
    special-session data, which spans two calendar dates and is rejected by
    the same-date check. Results are cached per (exchange, date) so repeated
    calls — as happen once per calendar day inside a single `year_fraction`
    call — hit the service at most once.
    """
    exchange_upper = (exchange or "").upper()

    def provider(day: date) -> tuple[tuple[int, int], tuple[int, int]] | None:
        cache_key = (exchange_upper, day.isoformat())
        cached = _SESSION_CACHE.get(cache_key)
        if cached is not None:
            return None if cached is _CLOSED else cached

        resolved = _resolve(exchange_upper, day)
        _SESSION_CACHE[cache_key] = resolved
        return None if resolved is _CLOSED else resolved

    return provider
