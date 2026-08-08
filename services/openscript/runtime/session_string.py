"""Session-string grammar — Python port of the engine's parser
(openalgo-openscript/src/runtime/session-string.ts, session-surface design §3).

"HHmm-HHmm" or "HHmm-HHmm:days" — days 1=Sunday..7=Saturday (Pine's convention,
and already exactly `dayofweek`'s), strictly ascending and unique; omitted days =
all seven. The window is half-open [open, close) against BAR-OPEN time on the
exchange-local clock. open >= close — the midnight wrap — is a NAMED reject
(design decision 3).

Pure and dependency-free on purpose: both the compiler (literal sessions,
OS2031) and the executor (input-bound sessions, OS4005) call this, exactly as
the TS original is shared by its own two callers. ERROR MESSAGE TEXTS ARE A
FROZEN CROSS-LANGUAGE CONTRACT — the TS test matrix asserts them verbatim and
this mirror must reproduce them character for character.

Digit discipline (the TS header calls this out for this file specifically):
`[0-9]` and `re.fullmatch`, NEVER `\\d` — Python's `\\d` matches any Unicode
decimal digit and `int()` parses them, so a verbatim transliteration would
ACCEPT e.g. Arabic-Indic "٠٩١٥-..." that the TS
ASCII-only grammar rejects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The seven day-admission facets of a session-typed input, in day order:
#: `SESSION_DAY_FIELDS[N-1]` is day N (1=Sunday..7=Saturday, `dayofweek`'s own
#: convention), which is also `ParsedSession.days[N-1]` — so a positional
#: `.index()` into this tuple IS the `days` index, no arithmetic. Mirror of
#: `SESSION_DAY_FIELDS` in the engine's `src/types/ir.ts`; the executor's field
#: resolution and admission's field allowlist both consume THIS tuple, so the
#: day-field vocabulary cannot drift between the three consumers.
SESSION_DAY_FIELDS: tuple[str, ...] = ("d1", "d2", "d3", "d4", "d5", "d6", "d7")


@dataclass(frozen=True)
class ParsedSession:
    #: Minutes past exchange-local midnight, inclusive.
    open_minutes: int
    #: Minutes past exchange-local midnight, EXCLUSIVE.
    close_minutes: int
    #: Index 0..6 = Sunday..Saturday (dayofweek is 1..7 — subtract 1 to index).
    days: tuple[bool, ...]


@dataclass(frozen=True)
class SessionParseError:
    error: str


# `[0-9]`, not `\d` — see the module docstring. `re.fullmatch` anchors both
# ends without `^`/`$` (Python's `$` also matches before a trailing newline,
# which JS `$` without the `m` flag does not — fullmatch sidesteps the hole).
_SHAPE = re.compile(r"([0-9]{4})-([0-9]{4})(?::([0-9]*))?")

# Leading/trailing whitespace OR byte-order mark, the same idiom
# `calendar.py`'s `normalize_exchange` uses to mirror ES `trim()`: U+FEFF is
# the character JS trims and Python's `strip()` does not (written as an
# escape so no invisible character lands in this source file). The reverse
# direction (Python `\s` also covering U+0085 and U+001C-U+001F, which JS
# leaves) is the same accepted control-character divergence the calendar
# entry in the parity backlog records.
_TRIM_RE = re.compile("^[\\s\\ufeff]+|[\\s\\ufeff]+$")


def parse_session_string(raw: str) -> ParsedSession | SessionParseError:
    """Parse a session string; every reject carries the frozen TS message."""
    m = _SHAPE.fullmatch(_TRIM_RE.sub("", raw))
    if m is None:
        return SessionParseError(f'session must be "HHmm-HHmm" or "HHmm-HHmm:days", got "{raw}"')
    open_ = _clock(m.group(1))
    close = _clock(m.group(2))
    if open_ is None or close is None:
        return SessionParseError(
            f'session times must be HHmm with HH 00-23 and mm 00-59, got "{raw}"'
        )
    if open_ == close:
        return SessionParseError(f'session open must be before close, got "{raw}"')
    if open_ > close:
        return SessionParseError(
            f'session "{raw}" crosses local midnight — not supported in v1.1 '
            "(openscript-session-surface-design.md §3)"
        )
    days = [False] * 7
    mask = m.group(3)
    if mask is None:
        days = [True] * 7
    else:
        if len(mask) == 0:
            return SessionParseError(f'session days must be non-empty, got "{raw}"')
        prev = 0
        for ch in mask:
            d = ord(ch) - 48
            if d < 1 or d > 7:
                return SessionParseError(f'session days are 1=Sunday..7=Saturday, got "{raw}"')
            if d <= prev:
                return SessionParseError(
                    "session days must be ascending, unique — canonical form "
                    f'e.g. "23456", got "{raw}"'
                )
            prev = d
            days[d - 1] = True
    return ParsedSession(open_minutes=open_, close_minutes=close, days=tuple(days))


def _clock(hhmm: str) -> int | None:
    """"HHmm" -> minutes past midnight, or None outside 00-23:00-59. The
    digits are guaranteed ASCII by `_SHAPE`'s `[0-9]`, so `int()` is safe."""
    hh = int(hhmm[:2])
    mm = int(hhmm[2:4])
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm
