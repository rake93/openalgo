"""Timeframe parsing + base-interval inference for `request.security` (same-symbol HTF).

Mirror of the engine's `src/runtime/timeframe.ts`. Pinned byte-for-byte by the
shared fixture `fixtures/htf/bucket-key.json` (register C4).

Pine timeframe strings: a bare integer is MINUTES (`"5"`, `"60"`); `"D"`/`"W"`/`"M"`
are daily/weekly/monthly. The base bar interval is inferred from the dataset `time`
column (epoch SECONDS) rather than threaded through a protocol, so both runtimes
derive it identically from the same array.
"""

from dataclasses import dataclass
from typing import Literal

TimeframeUnit = Literal["min", "D", "W", "M"]


@dataclass(frozen=True)
class Timeframe:
    unit: TimeframeUnit
    multiple: int


def parse_timeframe(raw) -> Timeframe | None:
    """Parse a Pine timeframe string; a bare integer is minutes. None if unparseable.

    NOTE THE ASYMMETRY, which is deliberate and mirrors TS exactly: only `D`/`1D`,
    `W`/`1W`, `M`/`1M` are accepted for the calendar units, so `"2D"`, `"3W"` and
    `"6M"` return None. `bucket_key` and `timeframe_rank_seconds` nonetheless
    implement full `multiple` arithmetic for those units, which is therefore
    unreachable from source and reachable only via hand-authored IR (the register's
    N1 class). Do not "fix" one side without the other -- the shared fixture pins
    both behaviours.
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s == "":
        return None
    if s in ("D", "1D"):
        return Timeframe("D", 1)
    if s in ("W", "1W"):
        return Timeframe("W", 1)
    if s in ("M", "1M"):
        return Timeframe("M", 1)
    # TS uses /^\d+$/ — ASCII digits only. Python's str.isdigit() accepts
    # superscripts and other Unicode digit forms, which would diverge, so the
    # character class is spelled out.
    if s and all(c in "0123456789" for c in s):
        m = int(s, 10)
        return Timeframe("min", m) if m > 0 else None
    return None


def timeframe_rank_seconds(tf: Timeframe) -> int:
    """Nominal seconds, used ONLY for the `target >= base` ordering check.

    D/W/M use conservative LOWER bounds (a month is at least 28 days) so a
    legitimate HTF request is never wrongly rejected as below the base interval.
    """
    if tf.unit == "min":
        return tf.multiple * 60
    if tf.unit == "D":
        return tf.multiple * 86_400
    if tf.unit == "W":
        return tf.multiple * 7 * 86_400
    if tf.unit == "M":
        return tf.multiple * 28 * 86_400
    raise ValueError(f"unknown timeframe unit: {tf.unit!r}")


def infer_base_interval_seconds(time) -> float | None:
    """Median of the POSITIVE consecutive deltas of an epoch-seconds time array.

    Robust to gaps and holidays -- the median ignores the few large overnight and
    weekend deltas. Returns None when fewer than two bars provide a positive delta.

    The even-length case averages the two middle deltas, matching TS. That is a
    real behavioural detail: `statistics.median` happens to agree, but the
    arithmetic is spelled out here so the parity is visible rather than inherited
    from a library whose tie rule could change.
    """
    deltas = []
    for i in range(1, len(time)):
        d = time[i] - time[i - 1]
        if d > 0:
            deltas.append(d)
    if not deltas:
        return None
    deltas.sort()
    mid = len(deltas) // 2
    if len(deltas) % 2 == 1:
        return deltas[mid]
    return (deltas[mid - 1] + deltas[mid]) / 2
