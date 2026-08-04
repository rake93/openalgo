"""Year-fraction conventions for the Option Target Calculator.

`calendar` matches `option_greeks_service.calculate_time_to_expiry` (365-day),
so projections reconcile with the Option Greeks and Option Chain pages. It is
the default for exactly that reason.

`trading` prices only market time (252 sessions per year). Over a multi-day
hold spanning a weekend, calendar time materially overstates decay because the
market does not bleed premium while it is shut.

Session hours are per-exchange. An MCX contract trades 09:00-23:55, nearly
two and a half times the NSE equity session, so assuming NSE hours everywhere
undercounts MCX market time by 58 percent and CDS by 22 percent. The table
below was verified against the platform's own market-timings API rather than
recalled from memory. It covers the NORMAL session for each exchange and is
the OFFLINE FALLBACK: it cannot express a special session (for example the
evening-only MCX session held on some equity holidays), because that varies
day to day and this table is static.

Whichever convention is chosen must be applied to BOTH the time-to-expiry used
for IV calibration and the time used for repricing. Mixing conventions corrupts
every projection.

Dependency note: `trading` mode consults `utils.trading_calendar.is_trading_day`,
which transitively reaches `services.market_calendar_service` for the exchange
holiday list. That call degrades to weekends-only (never raises) when the
holiday feed is unavailable, so this module stays usable in tests and offline.
This is the one place the option_target package is not strictly dependency-free.

Callers that need special-session accuracy pass `session_provider`: a callable
`date -> ((open_h, open_m), (close_h, close_m)) | None` that this module treats
as AUTHORITATIVE for `trading` mode, taking priority over both the static table
and `is_trading_day`. `None` means the market was shut that day. This module
never builds one itself (that would be IO, and this module is pure) — see
`services.option_target_sessions.build_session_provider` for a provider backed
by the platform's live market-timings service, with validation against the
seeded data's known corruption.
"""

from collections.abc import Callable
from datetime import date as date_type
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from utils.logging import get_logger
from utils.trading_calendar import is_trading_day

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

TRADING_DAYS_PER_YEAR = 252

# (open, close) as (hour, minute), IST. Verified against POST /api/v1/market/timings.
SESSIONS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "NSE": ((9, 15), (15, 30)),
    "BSE": ((9, 15), (15, 30)),
    "NSE_INDEX": ((9, 15), (15, 30)),
    "BSE_INDEX": ((9, 15), (15, 30)),
    "NFO": ((9, 15), (15, 30)),
    "BFO": ((9, 15), (15, 30)),
    "CDS": ((9, 0), (17, 0)),
    "BCD": ((9, 0), (17, 0)),
    "MCX": ((9, 0), (23, 55)),
    "NCO": ((9, 0), (23, 55)),
}

DEFAULT_EXCHANGE = "NSE"


def session_for(exchange: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """Session open and close for an exchange, falling back to NSE hours.

    The fallback is logged rather than silent. A silent fallback is precisely
    how the original NSE-only assumption stayed invisible while quietly
    mispricing MCX time.
    """
    key = (exchange or "").upper()
    session = SESSIONS.get(key)
    if session is None:
        logger.warning(
            "No session hours for exchange %r; assuming %s hours (09:15-15:30)",
            exchange,
            DEFAULT_EXCHANGE,
        )
        return SESSIONS[DEFAULT_EXCHANGE]
    return session


def session_minutes_for(exchange: str) -> int:
    """Length of one full trading session in minutes."""
    (open_h, open_m), (close_h, close_m) = session_for(exchange)
    return (close_h * 60 + close_m) - (open_h * 60 + open_m)


def _session_minutes_on(
    day: datetime,
    start: datetime,
    end: datetime,
    session_bounds: tuple[tuple[int, int], tuple[int, int]],
) -> float:
    """Market minutes elapsed on `day` within [start, end], for the given session bounds.

    `session_bounds` is the ((open_h, open_m), (close_h, close_m)) already
    resolved for this specific day by the caller — this function does no
    lookup of its own, so it works identically whether the bounds came from
    the static table or a `session_provider`.
    """
    (open_h, open_m), (close_h, close_m) = session_bounds
    open_at = day.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    close_at = day.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    lo = max(start, open_at)
    hi = min(end, close_at)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds() / 60.0


def year_fraction(
    start: datetime,
    end: datetime,
    day_count: str = "calendar",
    exchange: str = DEFAULT_EXCHANGE,
    session_provider: Callable[[date_type], tuple[tuple[int, int], tuple[int, int]] | None]
    | None = None,
) -> float:
    """Time from `start` to `end` in years under the given convention.

    `exchange` selects the session hours and is ignored for `calendar`.
    Returns 0.0 when `end` is at or before `start`.

    `session_provider`, when given, is authoritative for `trading` mode: for
    each calendar day in the range it is called with that day's `date` and
    must return either the session bounds to use for that day (overriding
    both the static table and `is_trading_day`) or `None` to mean the market
    was shut. A day's minutes are always normalised against that day's OWN
    session length, not a fixed constant — a special session's length can
    differ from the exchange's normal session, and dividing by the wrong
    length silently mis-states the fraction of the day that traded.
    """
    if day_count not in ("calendar", "trading"):
        raise ValueError(f"Unknown day_count: {day_count!r}. Use 'calendar' or 'trading'.")

    if end <= start:
        return 0.0

    if day_count == "calendar":
        return (end - start).total_seconds() / (365 * 86400)

    day_fractions = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    last = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        if session_provider is not None:
            bounds = session_provider(day.date())
        elif is_trading_day(day.date()):
            bounds = session_for(exchange)
        else:
            bounds = None

        if bounds is None:
            day += timedelta(days=1)
            continue

        (open_h, open_m), (close_h, close_m) = bounds
        session_length = (close_h * 60 + close_m) - (open_h * 60 + open_m)
        if session_length <= 0:
            day += timedelta(days=1)
            continue

        minutes_on_day = _session_minutes_on(day, start, end, bounds)
        day_fractions += minutes_on_day / session_length
        day += timedelta(days=1)

    return day_fractions / TRADING_DAYS_PER_YEAR
