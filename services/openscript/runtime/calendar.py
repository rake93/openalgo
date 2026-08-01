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

import re
from dataclasses import dataclass

# Module-private, mirroring the TypeScript side: re-exporting the bare offset would
# only invite a second source of truth beside IST_CALENDAR.
_IST_OFFSET_SECONDS = 19800

# Public, also mirroring TypeScript: the engine's ONE spelling of the day length.
# A caller that needs it (executor.py's second-of-day derivation) imports it rather
# than re-declaring a bare 86400 -- the same second-source-of-truth pattern G7
# removed for the offset.
DAY_SECONDS = 86400


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
    # Seconds past LOCAL midnight at which the trading day opens, anchoring
    # intraday HTF buckets (session-model design 3.1). OPTIONAL, and None means
    # "the local day IS the trading day" -- the correct reading for a 24/7 venue
    # like CRYPTO, and what keeps this inert for every calendar that omits it.
    session_open_seconds: int | None = None


def session_calendar(utc_offset_seconds: int, session_open_seconds: int) -> SessionCalendar:
    """A fixed-offset calendar that also knows when its trading day opens.

    `semantic_key` MUST differ from the session-less form, and that is not
    cosmetic: it is the HTF resample cache identity. Two calendars sharing an
    offset and differing in session would otherwise collide, and a bucket
    structure built under one session would be served under the other -- a wrong
    answer with no error and no diff.
    """
    return SessionCalendar(
        utc_offset_seconds,
        f"fixed:{utc_offset_seconds}@{session_open_seconds}",
        session_open_seconds,
    )


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


IST_CALENDAR = fixed_offset_calendar(_IST_OFFSET_SECONDS)
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

    NON-FINITE INPUTS -- `t_sec` must be finite; callers are responsible for that
    (`_resolve_context` casts to int64 first, so no engine path can reach here with
    one). The two languages agree on NaN and DIVERGE on infinity:

        NaN  -> nan here, NaN in TypeScript (identical propagation).
        +inf -> nan here, but `Math.floor(Infinity / 86400)` is `Infinity` in
                TypeScript. Same for -inf.

    Neither raises, so the never-raises contract holds on both sides. The divergence
    is pinned by test rather than engineered around, because it is unreachable today.
    It would matter to a future Python `sessionStarts`: a NaN day key makes
    `day != prev_day` always true (every bar a session start), while TypeScript's
    `Infinity !== Infinity` is always false (never one) -- opposite behaviour,
    silently. See the engine's Python parity backlog.

    Args:
        t_sec: UTC epoch seconds, scalar or numpy array. Must be finite.
        calendar: The calendar whose offset defines the day boundary.

    Returns:
        The local day ordinal, same shape as `t_sec`.
    """
    return (t_sec + calendar.utc_offset_seconds) // DAY_SECONDS


# Exchange -> CALENDAR (not a raw offset). Codes track utils/constants VALID_EXCHANGES.
# Holding calendars means a future non-IST, non-UTC exchange cannot be silently
# collapsed onto one of the two shipped calendars: adding
# "JAPAN225": fixed_offset_calendar(32400) is correct by construction.
# GLOBAL_INDEX is deliberately ABSENT: it mixes zones (US30 is US Eastern WITH DST,
# JAPAN225 +09:00, HANGSENG +08:00, GIFTNIFTY IST) and is handled separately.
# Trading-day opens, seconds past LOCAL midnight (session-model design 3.2).
# A calendar per SESSION, not per offset: NSE and MCX share IST and do not share
# hours, so the single frozen IST_CALENDAR they used to share cannot carry one.
NSE_SESSION_CALENDAR = session_calendar(19_800, 9 * 3600 + 15 * 60)
MCX_SESSION_CALENDAR = session_calendar(19_800, 9 * 3600)

_CALENDAR_BY_EXCHANGE = {
    "NSE": NSE_SESSION_CALENDAR,
    "BSE": NSE_SESSION_CALENDAR,
    "NFO": NSE_SESSION_CALENDAR,
    "BFO": NSE_SESSION_CALENDAR,
    "NSE_INDEX": NSE_SESSION_CALENDAR,
    "BSE_INDEX": NSE_SESSION_CALENDAR,
    "MCX": MCX_SESSION_CALENDAR,
    "MCX_INDEX": MCX_SESSION_CALENDAR,
    # SESSION-LESS ON PURPOSE, not overlooked. CDS/BCD/NCDEX/NCO hours are not
    # owner-confirmed, and a WRONG session produces exactly the silently-shifted
    # buckets this change exists to remove.
    "CDS": IST_CALENDAR,
    "BCD": IST_CALENDAR,
    "NCDEX": IST_CALENDAR,
    "NCO": IST_CALENDAR,
    # 24/7 -- the local day IS the trading day.
    "CRYPTO": UTC_CALENDAR,
}

# Needs per-symbol resolution AND a tz database; deferred, not unknown.
_DEFERRED_PER_SYMBOL = frozenset({"GLOBAL_INDEX"})

# Leading/trailing whitespace OR byte-order mark. `\s` is Python's own whitespace
# class; U+FEFF is the character ES `trim()` removes and Python's `strip()` does not.
# Written as an escape (re resolves \uXXXX in patterns) so no invisible character
# lands in this source file. See `normalize_exchange` for why the two must agree.
_TRIM_RE = re.compile(r"^[\s\ufeff]+|[\s\ufeff]+$")

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
        warning_code: `None` iff `provenance == 'mapped'`. No Python path serializes
            this record today, and nothing here implements key omission --
            `dataclasses.asdict()` + `json.dumps` would emit `"warning_code": null`.
            The TypeScript record OMITS the key entirely when absent, matching the
            protocol's convention for optional fields, so any future Python
            serializer must drop the key rather than send null.
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

    Python's `str.strip()` and ES `String.trim()` are NOT the same character set, so
    the trim is spelled out rather than left to `strip()`. The one realistic
    disagreement is `U+FEFF` (BOM): ES counts it as whitespace and Python does not, so
    a BOM surviving a UTF-8 CSV master-contract import gives `"\\ufeffNSE"` -- mapped
    by TypeScript, `fallback-unknown` here. Stripping it closes that case.

    RESIDUAL DIVERGENCE, accepted: Python still trims `U+0085` and `U+001C`-`U+001F`,
    which ES `trim()` does not. Engineering full ES-`trim()` equivalence for control
    characters that cannot appear in an exchange code is not worth it; the residual is
    recorded in the engine's Python parity backlog.

    Args:
        exchange: The raw exchange code, from any source and of any type.

    Returns:
        The normalized lookup key, or `''` when the input is absent or not a string.
    """
    if not isinstance(exchange, str):
        return ""
    return _TRIM_RE.sub("", exchange).upper()


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
