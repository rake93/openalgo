"""Session calendars for OpenScript execution (G7 design sections 4 and 5).

Python port of `openalgo-openscript/src/runtime/session-calendar.ts` and
`calendar-resolution.ts`. The exchange table is pinned identical to the TypeScript
one by the shared fixture `fixtures/calendar/exchange-resolution.json`, replayed on
this side from the committed copy at `test/fixtures/calendar-exchange-resolution.json`.

Fixed UTC offsets only: no DST, no IANA zones, no timezone library.

Deliberately NOT mirrored: the TypeScript `bucketKey` (HTF resampling) and the
session builtins that bucket on it. The Python runtime has no resampler, no vwap
and no pivot levels, so context fields are its only calendar consumer. A shared
fixture cannot guard a function that exists on one side only -- see the parity
backlog note before adding a Python resampler.
"""

from __future__ import annotations

from dataclasses import dataclass

IST_OFFSET_SECONDS = 19800
_DAY_SECONDS = 86400


@dataclass(frozen=True)
class SessionCalendar:
    """A fixed-offset calendar. `semantic_key` is the cache identity.

    Frozen: the TypeScript side freezes its calendars too, because a calendar whose
    offset is mutated away from its `semantic_key` would serve a cache entry keyed
    for one calendar to bucketing done under another.

    Attributes:
        utc_offset_seconds: Offset added to UTC seconds before day bucketing.
        semantic_key: Stable cache identity, e.g. `fixed:19800` / `fixed:0`.
    """

    utc_offset_seconds: int
    semantic_key: str


def fixed_offset_calendar(utc_offset_seconds: int) -> SessionCalendar:
    """Build a fixed-offset calendar. The only constructor in v1.

    Args:
        utc_offset_seconds: Seconds to add to UTC before day bucketing.

    Returns:
        A frozen `SessionCalendar` whose `semantic_key` encodes the scheme and the
        offset, so a future IANA implementation can emit `iana:<zone>:<tzdb>`
        without redesigning cache identity.
    """
    return SessionCalendar(utc_offset_seconds, f"fixed:{utc_offset_seconds}")


IST_CALENDAR = fixed_offset_calendar(IST_OFFSET_SECONDS)
UTC_CALENDAR = fixed_offset_calendar(0)


def local_day_key(t_sec, calendar: SessionCalendar):
    """Local calendar-day ordinal for a UTC epoch SECOND (floor division).

    This is the ONLY day-boundary definition. Never detect a new day by comparing
    `dayofmonth`: a sparse dataset can skip a month and land on the same day number
    (daily bars, 1 Mar to 1 Apr), silently missing the boundary.

    Accepts a scalar OR a numpy array and returns the same shape, which is what lets
    the vectorized `_resolve_context` use this helper instead of inlining a second
    floor division. Return dtype follows the input (int64 in, int64 out).

    NOTE: no `int()` cast. Python's `//` already floors for floats, while `int()`
    truncates toward zero (disagreeing with TypeScript `Math.floor` on fractional
    negatives) AND raises on NaN/inf in a module documented never to raise.

    Args:
        t_sec: UTC epoch seconds, scalar or numpy array.
        calendar: The calendar whose offset defines the day boundary.

    Returns:
        The local day ordinal, same shape as `t_sec`.
    """
    return (t_sec + calendar.utc_offset_seconds) // _DAY_SECONDS


# Exchange -> CALENDAR (not a raw offset). Codes track utils/constants VALID_EXCHANGES.
# Holding calendars means a future non-IST, non-UTC exchange cannot be silently
# collapsed onto one of the two shipped calendars: adding
# "JAPAN225": fixed_offset_calendar(32400) is correct by construction.
# GLOBAL_INDEX is deliberately ABSENT: it mixes zones (US30 is US Eastern WITH DST,
# JAPAN225 +09:00, HANGSENG +08:00, GIFTNIFTY IST) and is handled separately.
_CALENDAR_BY_EXCHANGE = {
    "NSE": IST_CALENDAR,
    "BSE": IST_CALENDAR,
    "NFO": IST_CALENDAR,
    "BFO": IST_CALENDAR,
    "CDS": IST_CALENDAR,
    "BCD": IST_CALENDAR,
    "MCX": IST_CALENDAR,
    "NCDEX": IST_CALENDAR,
    "NCO": IST_CALENDAR,
    "NSE_INDEX": IST_CALENDAR,
    "BSE_INDEX": IST_CALENDAR,
    "MCX_INDEX": IST_CALENDAR,
    "CRYPTO": UTC_CALENDAR,
}

# Needs per-symbol resolution AND a tz database; deferred, not unknown.
_DEFERRED_PER_SYMBOL = frozenset({"GLOBAL_INDEX"})

_WARNING_BY_PROVENANCE = {
    "mapped": None,
    "fallback-unknown": "CALENDAR_FALLBACK_UNKNOWN_EXCHANGE",
    "fallback-missing": "CALENDAR_FALLBACK_MISSING_EXCHANGE",
    "deferred-per-symbol": "CALENDAR_DEFERRED_PER_SYMBOL",
}


@dataclass(frozen=True)
class CalendarResolution:
    """The outcome of resolving an instrument to a calendar.

    Attributes:
        calendar: The resolved calendar.
        semantic_key: Echoes `calendar.semantic_key` for consumers that carry the
            record alone.
        provenance: One of `mapped`, `fallback-unknown`, `fallback-missing`,
            `deferred-per-symbol`.
        normalized_exchange: Trimmed + upper-cased lookup key; `''` when absent.
        warning_code: `None` iff `provenance == 'mapped'`. When this record is
            serialized to JSON the key is OMITTED rather than sent as null, matching
            the TypeScript side's optional-property behaviour.
    """

    calendar: SessionCalendar
    semantic_key: str
    provenance: str
    normalized_exchange: str
    warning_code: str | None = None


def normalize_exchange(exchange) -> str:
    """Trim and upper-case an exchange code. A non-str (including None) yields ''.

    The isinstance check is not paranoia: the TypeScript twin takes this value across
    a worker boundary from a JavaScript host, and `(exchange or "").strip()` would
    raise on a non-str in a function documented never to raise.

    Args:
        exchange: The raw exchange code, from any source and of any type.

    Returns:
        The normalized lookup key, or `''` when the input is absent or not a string.
    """
    return exchange.strip().upper() if isinstance(exchange, str) else ""


def _resolved(calendar: SessionCalendar, provenance: str, normalized: str) -> CalendarResolution:
    """Assemble a resolution, deriving the warning code from the provenance."""
    return CalendarResolution(
        calendar=calendar,
        semantic_key=calendar.semantic_key,
        provenance=provenance,
        normalized_exchange=normalized,
        warning_code=_WARNING_BY_PROVENANCE[provenance],
    )


def calendar_for_instrument(exchange, symbol=None) -> CalendarResolution:
    """Resolve an instrument to its session calendar. NEVER raises.

    A broker introducing a new exchange code must not break a chart, but the fallback
    is classified and carries a warning code, so an IST result that is a real mapping
    and an IST result that is a guess are distinguishable.

    Args:
        exchange: The instrument's exchange code. Any type; a non-str is treated as
            absent.
        symbol: Accepted and unused in v1. It exists so GLOBAL_INDEX per-symbol
            resolution lands inside this function rather than at every call site.

    Returns:
        A `CalendarResolution` carrying the calendar, its semantic key, the
        provenance and (unless mapped) a warning code.
    """
    normalized = normalize_exchange(exchange)
    if normalized == "":
        return _resolved(IST_CALENDAR, "fallback-missing", normalized)
    if normalized in _DEFERRED_PER_SYMBOL:
        return _resolved(IST_CALENDAR, "deferred-per-symbol", normalized)
    calendar = _CALENDAR_BY_EXCHANGE.get(normalized)
    if calendar is None:
        return _resolved(IST_CALENDAR, "fallback-unknown", normalized)
    return _resolved(calendar, "mapped", normalized)
