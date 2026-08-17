"""Higher-timeframe resampling for `request.security` (same-symbol HTF).

Mirror of the engine's `src/runtime/htf-resample.ts`. `bucket_key` and the
timeframe helpers are pinned byte-for-byte by the shared fixture
`fixtures/htf/bucket-key.json` (register C4) -- which exists because the parity
backlog recorded a standing instruction: this function had NO shared fixture while
Python lacked a resampler, so a port could reinvent the day stride and diverge
silently. It is now covered on both sides.

Base bars (epoch-seconds `time`) are grouped into HTF buckets by the session
calendar's local civil-date key. Each bucket yields one HTF bar (open=first,
high=max, low=min, close=last, volume=sum, time=first-bar time).

Alignment back to base bars:
    offset 0   -> the forming bucket's running aggregate over base bars <= i (provisional)
    offset >= 1 -> the final aggregate of bucket (k(i) - offset) (confirmed), na before it exists

See the engine's docs/openscript-phase3-request-security-design.md sections 3-4.
"""

import numpy as np

from .calendar import DAY_SECONDS, IST_CALENDAR, SessionCalendar, local_day_key
from .ta_dispatch import invoke_kernel
from .timeframe import Timeframe

#: Day stride for the `min` bucket key: the key is `dayNumber * STRIDE + slotOfDay`,
#: so the stride MUST exceed the largest possible `slotOfDay` or two different days
#: collide onto one key and a whole day of bars merges into its neighbour.
#:
#: Bound: `slotOfDay = floor(secOfDay / (multiple * 60))` with `secOfDay < 86_400`
#: and `multiple >= 1`, so `slotOfDay <= 1439` -- headroom of 98_561. The
#: `multiple >= 1` precondition is enforced by `parse_timeframe`, which rejects
#: `m <= 0`.
#:
#: A future sub-minute unit (seconds, ticks) MUST re-derive this: at 1-second
#: buckets `slotOfDay` reaches 86_399, which still fits, but anything finer does not.
MIN_KEY_DAY_STRIDE = 100_000


def _year_month_ordinal(z: int) -> int:
    """Howard Hinnant civil_from_days -> `year*12 + (month-1)`; `z` = days since 1970-01-01.

    Used only for monthly bucketing. Mirrors the TS derivation literally, including
    the `zz - 146_096` compensation, which exists so the division behaves like the
    original C++ truncating integer division for negative inputs (pre-1970 dates).
    Python's `//` already floors, matching TS `Math.floor`, so the expressions
    transcribe one-to-one -- but do NOT "simplify" the compensation away: the
    fixture carries a 1969 instant precisely to keep it honest.
    """
    zz = z + 719_468
    era = (zz if zz >= 0 else zz - 146_096) // 146_097
    doe = zz - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    m = mp + 3 if mp < 10 else mp - 9
    year = y + 1 if m <= 2 else y
    return year * 12 + (m - 1)


def bucket_key(t_sec, tf: Timeframe, calendar: SessionCalendar) -> int:
    """A monotonic non-decreasing key over chronological base bars; a change marks a new HTF bar.

    `t_sec` is epoch seconds; the calendar's offset shifts to its local day.

    The calendar is REQUIRED -- no default. Its offset folds into EVERY unit, not
    only D/W/M, because `secOfDay` is measured from the LOCAL day start (19800/3600
    = 5.5, so IST moves the hourly grid by 30 minutes; 19800 = 22 * 900, so it
    leaves a 15-minute grid where it was). A default would let a bucket structure be
    built under the wrong calendar without the compiler ever objecting.
    """
    # The ONE day-boundary definition (see calendar.py). The local shift is NOT
    # repeated here: only the `min` branch needs it, and a second textual
    # `t_sec + utc_offset_seconds` is exactly what that claim rules out.
    day_number = int(local_day_key(t_sec, calendar))
    if tf.unit == "D":
        return day_number // tf.multiple
    if tf.unit == "W":
        # Monday-anchored: epoch day 0 (1970-01-01) is a Thursday, so +3 aligns
        # Mondays to 0.
        return (day_number + 3) // (7 * tf.multiple)
    if tf.unit == "M":
        return _year_month_ordinal(day_number) // tf.multiple
    if tf.unit == "min":
        # Anchored to the TRADING day, not the local one (session-model design
        # 3.3). NSE opens 09:15 = 555 minutes past midnight, so a midnight grid
        # only lines up for timeframes dividing 555 -- 1m/3m/5m/15m do, 30m/60m
        # are 15 minutes out. Absent session = 0 = the previous behaviour exactly.
        #
        # `sec_of_day - open` goes NEGATIVE for a pre-open bar, and Python's
        # floor division toward -inf is the behaviour we want: such a bar lands
        # in the bucket BEFORE the session's first, never folded into it.
        open_sec = calendar.session_open_seconds or 0
        sec_of_day = t_sec + calendar.utc_offset_seconds - day_number * DAY_SECONDS
        return day_number * MIN_KEY_DAY_STRIDE + int((sec_of_day - open_sec) // (tf.multiple * 60))
    raise ValueError(f"unknown timeframe unit: {tf.unit!r}")


def build_buckets(time, tf: Timeframe, calendar: SessionCalendar = IST_CALENDAR):
    """Assign each base bar a bucket ordinal; a new bucket starts when the key changes.

    Returns `(bucket_index, count)`. The calendar defaults to IST only for direct
    callers; every production path resolves it from the instrument and passes it
    explicitly.
    """
    n = len(time)
    bucket_index = np.zeros(n, dtype=np.int32)
    if n == 0:
        return bucket_index, 0
    k = 0
    prev_key = bucket_key(time[0], tf, calendar)
    for i in range(1, n):
        key = bucket_key(time[i], tf, calendar)
        if key != prev_key:
            k += 1
            prev_key = key
        bucket_index[i] = k
    return bucket_index, k + 1


def aggregate_buckets(sources: dict, bucket_index, count: int) -> dict:
    """Closed (final) per-bucket aggregates, one entry per bucket."""
    k_total = count
    agg = {
        "open": np.zeros(k_total, dtype=float),
        "high": np.full(k_total, -np.inf, dtype=float),
        "low": np.full(k_total, np.inf, dtype=float),
        "close": np.zeros(k_total, dtype=float),
        "volume": np.zeros(k_total, dtype=float),
        "time": np.zeros(k_total, dtype=float),
    }
    seen = np.zeros(k_total, dtype=np.uint8)
    for i in range(len(bucket_index)):
        k = int(bucket_index[i])
        if seen[k] == 0:
            agg["open"][k] = sources["open"][i]
            agg["time"][k] = sources["time"][i]
            seen[k] = 1
        agg["high"][k] = max(agg["high"][k], sources["high"][i])
        agg["low"][k] = min(agg["low"][k], sources["low"][i])
        agg["close"][k] = sources["close"][i]
        agg["volume"][k] = agg["volume"][k] + sources["volume"][i]
    return agg


def align_closed_into(closed_agg, bucket_index, offset: int, out, frm: int, to: int) -> None:
    """offset >= 1, range form: write bars [frm, to] only.

    Each cell is an independent lookup, so any range is serviceable.
    """
    for i in range(frm, to + 1):
        b = int(bucket_index[i]) - offset
        out[i] = closed_agg[b] if b >= 0 else np.nan


def align_htf_inner_range(
    fn: str,
    source_offset: int,
    length: int,
    source: str,
    result_offset: int,
    bucket_index,
    count: int,
    agg: dict,
    out,
    frm: int,
    to: int,
) -> None:
    """An inner `ta.highest`/`ta.lowest` evaluated in HTF space (design §4):

        1. shifted[j] = j >= n ? aggClosed[S][j - n] : nan     (HTF space, K entries)
        2. kernel_arr = invoke_kernel(fn, [shifted, length])   (the SAME dispatch base space uses)
        3. out[i]     = align_closed_into(kernel_arr, bucket_index, m)

    STEP 1 IS THE FINALITY GUARANTEE MADE STRUCTURAL. The forming bucket's partial
    aggregate sits at aggClosed[K-1]; with n >= 1 it would land at shifted index
    K-1+n, past the end. It cannot enter a consumed value -- not by policy, by
    construction.

    ⚠ THE `src >= 0` GUARD BELOW IS LOAD-BEARING **HERE AND NOWHERE ELSE**.
    Mutation-tested on the TS side 2026-08-18: removing it changes nothing in
    JavaScript, because Float64Array[-1] is `undefined` and assigning that writes
    NaN -- an equivalent mutant no TS test can kill. In NumPy `closed[-1]` WRAPS
    to the LAST element, which is the FORMING bucket's partial aggregate, so
    dropping the guard here would read the forming bucket into the kernel window
    and silently violate the one property this design exists to guarantee. Do not
    "simplify" it away.

    Mirror: htf-resample.ts alignHtfInnerRange.
    """
    closed = agg[source]
    shifted = np.full(count, np.nan, dtype=float)
    for j in range(count):
        src = j - source_offset
        if src >= 0:
            shifted[j] = closed[src]
    kernel_arr = invoke_kernel(fn, [shifted, int(length)])
    align_closed_into(kernel_arr, bucket_index, result_offset, out, frm, to)


def align_closed(closed_agg, bucket_index, offset: int):
    """offset >= 1: value at base bar i = the closed aggregate of bucket (k(i) - offset);
    na (NaN) before that bucket exists."""
    n = len(bucket_index)
    out = np.zeros(n, dtype=float)
    if n:
        align_closed_into(closed_agg, bucket_index, offset, out, 0, n - 1)
    return out


#: Running-aggregate rule per base source for an offset-0 (forming-bucket) read.
HTF_FORMING_KIND = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "time": "first",
}

#: Base components each combined source is derived from, in the executor's order.
_COMBINED_PARTS = {
    "hl2": ("high", "low"),
    "hlc3": ("high", "low", "close"),
    "ohlc4": ("open", "high", "low", "close"),
}


def align_forming_into(source, bucket_index, kind: str, out, frm: int, to: int) -> None:
    """offset 0, range form: write bars [frm, to] only.

    PRECONDITION: `frm` must be a BUCKET START (or 0) -- the running accumulator
    resets at the first bar written, so starting mid-bucket would drop the part of
    the bucket before `frm`. A violation raises rather than silently producing a
    wrong (too-small) running aggregate.
    """
    if frm > 0 and frm <= to and bucket_index[frm] == bucket_index[frm - 1]:
        raise ValueError(
            f"align_forming_into: frm={frm} is not a bucket start "
            "(running aggregate would restart mid-bucket)"
        )
    cur_bucket = -1
    acc = 0.0
    for i in range(frm, to + 1):
        b = int(bucket_index[i])
        v = source[i]
        if b != cur_bucket:
            cur_bucket = b
            acc = v
        elif kind == "first":
            pass
        elif kind == "max":
            acc = max(acc, v)
        elif kind == "min":
            acc = min(acc, v)
        elif kind == "last":
            acc = v
        elif kind == "sum":
            acc = acc + v
        else:
            raise ValueError(f"unknown forming kind: {kind!r}")
        out[i] = acc


def align_forming(source, bucket_index, kind: str):
    """offset 0: the running aggregate of the current bucket over base bars <= i,
    resetting at each bucket start."""
    n = len(bucket_index)
    out = np.zeros(n, dtype=float)
    if n:
        align_forming_into(source, bucket_index, kind, out, 0, n - 1)
    return out


def align_base_into(source: str, offset: int, base: dict, bucket_index, agg: dict, out, frm: int, to: int) -> None:
    """Align ONE base HTF source over [frm, to] into `out`."""
    if offset >= 1:
        align_closed_into(agg[source], bucket_index, offset, out, frm, to)
        return
    align_forming_into(base[source], bucket_index, HTF_FORMING_KIND[source], out, frm, to)


def align_htf_range(source: str, offset: int, base: dict, bucket_index, agg: dict, out, frm: int, to: int) -> None:
    """Align any HTF source kind -- base or combined (hl2/hlc3/ohlc4) -- over [frm, to].

    THE single alignment implementation, matching the TS structure so a bounded
    recompute is bit-identical to a full one by construction.
    """
    parts = _COMBINED_PARTS.get(source)
    if parts is None:
        align_base_into(source, offset, base, bucket_index, agg, out, frm, to)
        return
    n = len(out)
    aligned = {}
    for p in parts:
        buf = np.zeros(n, dtype=float)
        align_base_into(p, offset, base, bucket_index, agg, buf, frm, to)
        aligned[p] = buf
    for i in range(frm, to + 1):
        if source == "hl2":
            out[i] = (aligned["high"][i] + aligned["low"][i]) / 2
        elif source == "hlc3":
            out[i] = (aligned["high"][i] + aligned["low"][i] + aligned["close"][i]) / 3
        else:
            out[i] = (
                aligned["open"][i] + aligned["high"][i] + aligned["low"][i] + aligned["close"][i]
            ) / 4
