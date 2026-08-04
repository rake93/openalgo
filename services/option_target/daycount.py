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
recalled from memory.

Whichever convention is chosen must be applied to BOTH the time-to-expiry used
for IV calibration and the time used for repricing. Mixing conventions corrupts
every projection.

Dependency note: `trading` mode consults `utils.trading_calendar.is_trading_day`,
which transitively reaches `services.market_calendar_service` for the exchange
holiday list. That call degrades to weekends-only (never raises) when the
holiday feed is unavailable, so this module stays usable in tests and offline.
This is the one place the option_target package is not strictly dependency-free.
"""

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


def _session_bounds(day: datetime, exchange: str) -> tuple[datetime, datetime]:
    (open_h, open_m), (close_h, close_m) = session_for(exchange)
    return (
        day.replace(hour=open_h, minute=open_m, second=0, microsecond=0),
        day.replace(hour=close_h, minute=close_m, second=0, microsecond=0),
    )


def _session_minutes_on(day: datetime, start: datetime, end: datetime, exchange: str) -> float:
    """Market minutes elapsed on `day` within the window [start, end]."""
    if not is_trading_day(day.date()):
        return 0.0
    open_at, close_at = _session_bounds(day, exchange)
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
) -> float:
    """Time from `start` to `end` in years under the given convention.

    `exchange` selects the session hours and is ignored for `calendar`.
    Returns 0.0 when `end` is at or before `start`.
    """
    if day_count not in ("calendar", "trading"):
        raise ValueError(f"Unknown day_count: {day_count!r}. Use 'calendar' or 'trading'.")

    if end <= start:
        return 0.0

    if day_count == "calendar":
        return (end - start).total_seconds() / (365 * 86400)

    session_minutes = session_minutes_for(exchange)
    minutes = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    last = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        minutes += _session_minutes_on(day, start, end, exchange)
        day += timedelta(days=1)
    return (minutes / session_minutes) / TRADING_DAYS_PER_YEAR
