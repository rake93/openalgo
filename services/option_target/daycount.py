"""Year-fraction conventions for the Option Target Calculator.

`calendar` matches `option_greeks_service.calculate_time_to_expiry` (365-day),
so projections reconcile with the Option Greeks and Option Chain pages. It is
the default for exactly that reason.

`trading` prices only market time (252 sessions, 09:15-15:30 IST). Over a
multi-day hold spanning a weekend, calendar time materially overstates decay
because the market does not bleed premium while it is shut.

Whichever is chosen must be applied to BOTH the time-to-expiry used for IV
calibration and the time used for repricing. Mixing conventions corrupts every
projection.

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
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)
SESSION_MINUTES = (SESSION_CLOSE[0] * 60 + SESSION_CLOSE[1]) - (
    SESSION_OPEN[0] * 60 + SESSION_OPEN[1]
)  # 375


def _session_bounds(day: datetime) -> tuple[datetime, datetime]:
    return (
        day.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1], second=0, microsecond=0),
        day.replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1], second=0, microsecond=0),
    )


def _session_minutes_on(day: datetime, start: datetime, end: datetime) -> float:
    """Market minutes elapsed on `day` within the window [start, end]."""
    if not is_trading_day(day.date()):
        return 0.0
    open_at, close_at = _session_bounds(day)
    lo = max(start, open_at)
    hi = min(end, close_at)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds() / 60.0


def year_fraction(start: datetime, end: datetime, day_count: str = "calendar") -> float:
    """Time from `start` to `end` in years under the given convention.

    Returns 0.0 when `end` is at or before `start`.
    """
    if day_count not in ("calendar", "trading"):
        raise ValueError(f"Unknown day_count: {day_count!r}. Use 'calendar' or 'trading'.")

    if end <= start:
        return 0.0

    if day_count == "calendar":
        return (end - start).total_seconds() / (365 * 86400)

    minutes = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    last = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        minutes += _session_minutes_on(day, start, end)
        day += timedelta(days=1)
    return (minutes / SESSION_MINUTES) / TRADING_DAYS_PER_YEAR
