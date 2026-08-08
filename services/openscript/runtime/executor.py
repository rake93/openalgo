"""OpenScript IR executor (server, numpy) — Python port of the TS executor
(openalgo-openscript/src/runtime/executor.ts).

Evaluates a compiled IRProgram over a dataset of numpy arrays in a single
vectorized forward sweep (nodes are topologically ordered, id == index). Values
are either a scalar (consts, inputs, scalar arithmetic — indicator periods) or a
numpy series. Booleans are 1.0/0.0; `na` is NaN. `ta.*` dispatches to
`openalgo.ta`, so server values match the browser/wasm exactly.
"""

from __future__ import annotations

import math
import re

import numpy as np

from services.openscript.limits import SCRIPT_LIMITS

from .admit import IRAdmissionError, admit_ir, resolve_plan_cost
from .calendar import DAY_SECONDS, IST_CALENDAR, SessionCalendar, local_day_key
from .htf_resample import aggregate_buckets, align_htf_range, build_buckets
from .session_string import SESSION_DAY_FIELDS, SessionParseError, parse_session_string
from .timeframe import (
    Timeframe,
    infer_base_interval_seconds,
    parse_timeframe,
    timeframe_rank_seconds,
)


class IRHtfBelowBase(Exception):
    """`IR_HTF_BELOW_BASE` — an HTF request finer than the base bar interval.

    A runtime admission error, not a source diagnostic: admission sees only the IR
    and has no dataset, so the base interval cannot be known until execution.
    """

from .operator_cost import cost_family_of
from .plancost import (
    DRAW_BASE_OPS,
    DRAW_OBJECT_WEIGHT,
    DRAW_SCAN_WEIGHT,
    clamp_numeric_input,
    declared_max,
)
from .plancost_config import plancost_mode
from .ta_dispatch import facade_of, invoke_kernel

_MATH_UNARY = {
    "abs": np.abs, "sign": np.sign, "sqrt": np.sqrt, "exp": np.exp, "log": np.log,
    "log10": np.log10, "round": np.round, "floor": np.floor, "ceil": np.ceil,
}
_MATH_BINARY = {"pow": np.power, "max": np.maximum, "min": np.minimum}
_ARITH = {"+", "-", "*", "/", "%"}
_CMP = {"<", "<=", ">", ">=", "==", "!="}


def _is_series(v) -> bool:
    return isinstance(v, np.ndarray)


def _resolve_source(dataset: dict, source_id: str) -> np.ndarray:
    if source_id in ("open", "high", "low", "close", "volume"):
        return dataset[source_id]
    h, lo, c, o = dataset["high"], dataset["low"], dataset["close"], dataset["open"]
    if source_id == "hl2":
        return (h + lo) / 2
    if source_id == "hlc3":
        return (h + lo + c) / 3
    if source_id == "ohlc4":
        return (o + h + lo + c) / 4
    if source_id == "hlcc4":
        return (h + lo + 2 * c) / 4
    return c


# Pine time/context series (P-time) — Python port of
# openalgo-openscript/src/registry/resolve-context.ts. Bit-identical integers.
_CONTEXT_IDS = frozenset(
    {
        "timeframe_in_seconds",
        "time", "bar_index", "last_bar_index", "dayofweek", "dayofmonth",
        "hour", "minute", "month", "year",
    }
)


def _infer_base_interval_seconds(time) -> float | None:
    """Median of the positive consecutive deltas of an (epoch-seconds) time array.

    Literal transcription of the TypeScript `inferBaseIntervalSeconds`
    (`src/runtime/timeframe.ts`), NOT `np.median`: the two must agree bit-for-bit
    and writing the same algorithm is how that stays true. Robust to
    gaps/holidays -- the median ignores the few large overnight/weekend deltas.
    Returns None when fewer than two bars provide a positive delta.
    """
    t = np.asarray(time, dtype=float)
    if t.size < 2:
        return None
    deltas = np.diff(t)
    deltas = deltas[deltas > 0]
    if deltas.size == 0:
        return None
    deltas = np.sort(deltas)
    mid = deltas.size >> 1
    if deltas.size % 2 == 1:
        return float(deltas[mid])
    return float((deltas[mid - 1] + deltas[mid]) / 2)


def _resolve_context(dataset: dict, cid: str, calendar: SessionCalendar) -> np.ndarray:
    """Resolve a context/time series to a full float series.

    `bar_index`/`last_bar_index` derive from the length; `time` and the civil
    calendar fields derive from the dataset `time` column (epoch SECONDS, UTC).
    Calendar math is fixed-offset (no DST) via Howard Hinnant's civil_from_days
    with floor division throughout — matching the TS runtime integer-for-integer.
    The offset comes from the supplied calendar, not a module constant. All series
    are na-free from bar 0.

    Args:
        dataset: Column dict; `time` is epoch seconds (UTC), `close` sets the length.
        cid: The context id to resolve (one of `_CONTEXT_IDS`).
        calendar: The session calendar whose offset defines the day boundary.
            REQUIRED, mirroring the TS `resolveContext`: `execute_ir` holds the one
            IST default and always passes explicitly, so two adjacent links of the
            same call chain cannot encode opposite policies.

    Returns:
        A float series of length `len(dataset["close"])`.
    """
    n = len(dataset["close"])
    if cid == "timeframe_in_seconds":
        # The chart's own bar interval (G2), derived from the `time` column.
        # Constant across the dataset, filled per bar rather than returned as a
        # scalar so it composes with every series operator without a special
        # case. `na` when there is no delta to measure (a single bar).
        secs = _infer_base_interval_seconds(dataset["time"])
        return np.full(n, float("nan") if secs is None else secs)
    if cid == "bar_index":
        return np.arange(n, dtype=float)
    if cid == "last_bar_index":
        return np.full(n, float(n - 1))
    t_sec = np.asarray(dataset["time"], dtype=np.int64)
    if cid == "time":
        return (t_sec * 1000).astype(float)  # seconds → Pine milliseconds
    local = t_sec + calendar.utc_offset_seconds
    days = local_day_key(t_sec, calendar)  # the ONE day-boundary definition
    sod = local - days * DAY_SECONDS  # second-of-day, 0..86399
    if cid == "hour":
        return (sod // 3600).astype(float)
    if cid == "minute":
        return ((sod % 3600) // 60).astype(float)
    if cid == "dayofweek":
        # 1970-01-01 = Thursday. Pine dayofweek: 1=Sunday … 7=Saturday.
        w = days % 7  # 0=Thu,1=Fri,2=Sat,3=Sun,4=Mon,5=Tue,6=Wed
        return (((w + 4) % 7) + 1).astype(float)
    # civil_from_days (Hinnant), floor division throughout.
    z = days + 719468
    era = z // 146097
    doe = z - era * 146097  # [0, 146096]
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365  # [0, 399]
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)  # [0, 365]
    mp = (5 * doy + 2) // 153  # [0, 11]
    dom = doy - (153 * mp + 2) // 5 + 1  # [1, 31]
    month = np.where(mp < 10, mp + 3, mp - 9)  # [1, 12]
    year = y + np.where(month <= 2, 1, 0)
    if cid == "dayofmonth":
        return dom.astype(float)
    if cid == "month":
        return month.astype(float)
    return year.astype(float)


def _const_value(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return math.nan  # None (na) or string


class SessionInputError(Exception):
    """A session-typed input failed to BIND — the string the run received (a
    caller-supplied value, or the declared default when unbound) does not parse
    as a session string. Mirrors the TS `SessionInputError` (executor.ts): an
    Exception subclass carrying the runtime code `OS4005`, raised instead of
    silently substituting a default — the wrong session served to every consumer
    with no error is the exact failure this surface exists to prevent."""

    code = "OS4005"

    def __init__(self, input_id: str, reason: str) -> None:
        super().__init__(f"input '{input_id}': {reason}")
        self.input_id = input_id


def _session_field_value(node, inputs, decls, session_cache):
    """Resolve one facet of a session-typed input (design §5.2): parse the bound
    string — `inputs[id]`, falling back to the declared default — once per run
    (`session_cache`, keyed by the RAW bound string), and serve the requested
    number. Every failure is LOUD (`SessionInputError`, OS4005): a decl that is
    missing or not session-typed (hand-forged IR — admission rejects it, this is
    the executor's own belt), a string that does not parse, or a field name
    outside the nine.

    INDEX: `SESSION_DAY_FIELDS` is ordered d1..d7 and `ParsedSession.days` is
    0-indexed 0=Sunday..6=Saturday — the SAME order — so the positional
    `.index()` IS the `days` index (`d1` ↔ `days[0]` … `d7` ↔ `days[6]`); the
    1-based-vs-0-based shift is structural, not arithmetic.
    """
    input_id = node["inputId"]
    decl = decls.get(input_id)
    if decl is None or decl.get("type") != "session":
        raise SessionInputError(
            input_id, f"field '{node['field']}' requires a session-typed input declaration"
        )
    raw = inputs.get(input_id)
    if raw is None:
        raw = decl.get("defaultValue")
    raw = str(raw)
    parsed = session_cache.get(raw)
    if parsed is None:
        p = parse_session_string(raw)
        if isinstance(p, SessionParseError):
            raise SessionInputError(input_id, p.error)
        parsed = p
        session_cache[raw] = parsed
    field = node["field"]
    if field == "open":
        return float(parsed.open_minutes)
    if field == "close":
        return float(parsed.close_minutes)
    try:
        day = SESSION_DAY_FIELDS.index(field)
    except ValueError:
        # Hand-forged IR carrying a name outside the nine — admission rejects
        # it; this is the executor's own loud backstop.
        raise SessionInputError(input_id, f"unknown session field '{field}'") from None
    return 1.0 if parsed.days[day] else 0.0


def _input_value(node, inputs, decls, dataset):
    input_id = node["inputId"]
    decl = decls.get(input_id)
    # Mirror TS `raw = inputs[id] ?? decl?.defaultValue`: an explicit null (like
    # an absent key) falls back to the declared default, for EVERY input type.
    raw = inputs.get(input_id)
    if raw is None:
        raw = decl.get("defaultValue") if decl else None
    dtype = decl.get("type") if decl else None
    if dtype in ("integer", "float"):
        # F2: clamp the EXECUTED value to the declared max so a caller period
        # above maxval does no more real kernel work than was charged (the budget
        # charges the same clamped value). Clamp ONLY when an explicit max is
        # declared — an unbounded numeric input (multiplier/threshold, not a
        # lookback) must NOT be clamped to maximumLookback.
        hi = declared_max(decl) if decl else None
        return clamp_numeric_input(decl, raw, hi) if hi is not None else float(raw)
    if dtype == "bool":
        return 1.0 if raw else 0.0
    if dtype == "source":
        return _resolve_source(dataset, raw if isinstance(raw, str) else "close")
    return float(raw) if isinstance(raw, (int, float)) else math.nan


def _scalar_binop(op, a, b):
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return a / b if b != 0 else (math.inf if a > 0 else -math.inf if a < 0 else math.nan)
    if op == "%":
        return math.fmod(a, b) if b != 0 else math.nan
    if op in _CMP:
        if math.isnan(a) or math.isnan(b):
            return math.nan
        return 1.0 if _cmp_scalar(op, a, b) else 0.0
    if op == "and":
        return 1.0 if _truthy_scalar(a) and _truthy_scalar(b) else 0.0
    if op == "or":
        return 1.0 if _truthy_scalar(a) or _truthy_scalar(b) else 0.0
    return math.nan


def _cmp_scalar(op, a, b):
    return {
        "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b, "!=": a != b,
    }[op]


def _truthy_scalar(x) -> bool:
    return not math.isnan(x) and x != 0


def _truthy(x):
    arr = np.asarray(x, dtype=float)
    return (~np.isnan(arr)) & (arr != 0)


def _binop(op, a, b):
    if not _is_series(a) and not _is_series(b):
        return _scalar_binop(op, float(a), float(b))
    if op in _ARITH:
        with np.errstate(divide="ignore", invalid="ignore"):
            return {"+": a + b, "-": a - b, "*": a * b, "/": a / b, "%": np.fmod(a, b)}[op]
    if op in _CMP:
        fn = {
            "<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal,
            "==": np.equal, "!=": np.not_equal,
        }[op]
        res = fn(a, b).astype(float)
        return np.where(np.isnan(a) | np.isnan(b), np.nan, res)
    if op == "and":
        return (_truthy(a) & _truthy(b)).astype(float)
    return (_truthy(a) | _truthy(b)).astype(float)  # or


def _unop(op, v):
    if op == "isna":
        if not _is_series(v):
            return 1.0 if math.isnan(v) else 0.0
        return np.isnan(v).astype(float)
    if not _is_series(v):
        return -v if op == "-" else (0.0 if _truthy_scalar(v) else 1.0)
    if op == "-":
        return -v
    return (~_truthy(v)).astype(float)


def _select(c, t, e, n):
    cond = _truthy(np.broadcast_to(np.asarray(c, dtype=float), (n,)))
    tt = np.broadcast_to(np.asarray(t, dtype=float), (n,))
    ee = np.broadcast_to(np.asarray(e, dtype=float), (n,))
    return np.where(cond, tt, ee)


def _hist(v, offset, n):
    out = np.full(n, np.nan)
    if _is_series(v):
        if offset < n:
            out[offset:] = v[: n - offset]
    else:
        out[offset:] = v
    return out


def _nz(v, replacement, n):
    if _is_series(v):
        return np.where(np.isnan(v), replacement, v)
    return replacement if math.isnan(v) else v


def _ta_arg(a):
    if _is_series(a):
        return np.asarray(a, dtype=float)
    fa = float(a)
    return int(fa) if fa.is_integer() else fa


def _is_window_kernel(namespace, fn) -> bool:
    """Window-family classifier — the SAME one the cost model uses
    (cost_family_of). An unpriced kernel is treated as non-window so nothing is
    clamped and dispatch fails exactly as before."""
    try:
        return cost_family_of(namespace, fn) == "window"
    except Exception:
        return False


def _clamp_window_arg(a):
    """Bound a window-family kernel's scalar length/window arg to maximumLookback
    (the length the cost model charges for an unbounded length), so real work <=
    charge. min(a, maximumLookback) with NaN passthrough; series (ndarray) args
    and legit small scalars (periods/multipliers <= maximumLookback) are
    unchanged. Mirror of the TS `clampWindowArg`."""
    if isinstance(a, np.ndarray):
        return a
    if isinstance(a, (int, float)) and not isinstance(a, bool) and a > SCRIPT_LIMITS["maximumLookback"]:
        return SCRIPT_LIMITS["maximumLookback"]
    return a


# Kernels every one of whose arguments is a SERIES, keyed `namespace.function`.
#
# A const IR node evaluates to a scalar, and these kernels index their arguments
# positionally, so a scalar argued into one raises (`IndexError: too many indices
# for array` / `TypeError: len() of unsized object`) instead of computing.
# `ta.crossover(close, 450)` is Pine-legal and the idiomatic threshold-cross test,
# so rejecting it would be wrong; the fix is to broadcast. Mirrors the TypeScript
# `SERIES_ONLY_KERNELS` in `src/runtime/executor.ts` -- the two lists must agree.
#
# Deliberately an allowlist rather than "coerce every scalar": most `ta.*`
# arguments are genuine scalars (`ta.ema(close, 9)`'s period) and broadcasting one
# of those would corrupt a currently-correct call. Adding a kernel here is a claim
# that it has NO scalar parameters.
_SERIES_ONLY_KERNELS = frozenset({"ta.crossover", "ta.crossunder", "ta.cross"})


def _broadcast_series_arg(namespace, fn, a, n):
    """Broadcast a scalar argued into a series-only kernel slot to a full series."""
    if _is_series(a) or f"{namespace}.{fn}" not in _SERIES_ONLY_KERNELS:
        return a
    return np.full(n, float(a), dtype=float)


def _call(node, values, ta_cache, n):
    args = [
        _broadcast_series_arg(node["namespace"], node["function"], values[i], n)
        for i in node["args"]
    ]
    # math.sum is windowed — route it to the rolling_sum kernel; every other
    # math.* stays on the elementwise path.
    if node["namespace"] == "math" and node["function"] != "sum":
        return _math_call(node["function"], args)
    facade = facade_of(node["function"])
    key = f"{facade}#{','.join(str(i) for i in node['args'])}"
    result = ta_cache.get(key)
    if result is None:
        # Finding 1 (review): bound a WINDOW-family kernel's SCALAR numeric args
        # to maximumLookback BEFORE dispatch. The executor otherwise passes the
        # RAW caller value, so a no-max (or computed-expression) window length
        # could drive O(value) real work far above the charged inputBound
        # (<= maximumLookback) — e.g. kernels.gaussian's window = start_at_bar + 2.
        # Series args are untouched; legit periods/multipliers pass through. This
        # is the belt over the F2 input-value clamp — it also covers no-max inputs
        # AND computed-expression lengths the input clamp can't reach.
        kernel_args = [_ta_arg(a) for a in args]
        if _is_window_kernel(node["namespace"], node["function"]):
            kernel_args = [_clamp_window_arg(a) for a in kernel_args]
        result = invoke_kernel(node["function"], kernel_args)
        ta_cache[key] = result
    if isinstance(result, tuple):
        return np.asarray(result[node.get("output", 0)], dtype=float)
    return np.asarray(result, dtype=float)


def _math_call(fn, args):
    if fn in _MATH_UNARY:
        a = args[0]
        return _MATH_UNARY[fn](a) if _is_series(a) else float(_MATH_UNARY[fn](a))
    f = _MATH_BINARY[fn]
    a, b = args[0], args[1]
    return f(a, b) if _is_series(a) or _is_series(b) else float(f(a, b))


def _resolve_htf_timeframe(node, inputs, decls):
    """The EFFECTIVE timeframe of an htf node at run time.

    A node carrying `timeframeInputId` prefers the live input value, then that
    input's declared default, then the node's own baked timeframe. That order is
    what lets a settings change re-resample without a recompile.
    """
    input_id = node.get("timeframeInputId")
    baked = Timeframe(node["timeframe"]["unit"], node["timeframe"]["multiple"])
    if input_id is None:
        return baked
    raw = inputs.get(input_id)
    if raw is None:
        decl = decls.get(input_id)
        raw = decl.get("defaultValue") if decl else None
    parsed = parse_timeframe(raw) if isinstance(raw, str) else None
    return parsed or baked


def _assert_htf_above_base(tf, dataset) -> None:
    """Reject a timeframe FINER than the base data (Phase-3 design §2).

    The base interval is a runtime property -- admission sees only the IR, with no
    dataset -- which is why this guard lives here and not there.
    """
    time = dataset.get("time")
    base_interval = infer_base_interval_seconds(time) if time is not None else None
    if base_interval is not None and timeframe_rank_seconds(tf) < base_interval:
        raise IRHtfBelowBase(
            f"IR_HTF_BELOW_BASE: request.security timeframe {tf.unit}:{tf.multiple} "
            "is below the base bar interval"
        )


def _eval_htf(node, dataset, htf_cache, inputs, decls, calendar):
    """`request.security` (same-symbol HTF).

    Resamples the base dataset into the node's timeframe -- cached per (timeframe,
    calendar) so N nodes on one timeframe resample ONCE -- then aligns through the
    shared `align_htf_range`, the same function a bounded recompute would call, so
    the two cannot drift.
    """
    if htf_cache is None:
        htf_cache = {}
    tf = _resolve_htf_timeframe(node, inputs, decls)
    key = f"{tf.unit}:{tf.multiple}|calendar={calendar.semantic_key}"
    entry = htf_cache.get(key)
    if entry is None:
        _assert_htf_above_base(tf, dataset)
        bucket_index, count = build_buckets(dataset["time"], tf, calendar)
        agg = aggregate_buckets(dataset, bucket_index, count)
        entry = (bucket_index, agg)
        htf_cache[key] = entry
    bucket_index, agg = entry
    out = np.zeros(len(bucket_index), dtype=float)
    if len(bucket_index):
        align_htf_range(
            node["source"], node["offset"], dataset, bucket_index, agg, out, 0, len(out) - 1
        )
    return out


def _hist_offset(node: dict, inputs: dict, decls: dict) -> int:
    """The shift for a `hist` node: the literal, or an integer input read at run time.

    Clamped to the input's declared `maxval` because the COMPILER priced warmup
    against that bound. A runtime value beyond it would read further back than
    was charged, which is the one way this feature could break the
    `charged <= estimate` invariant. Non-finite or negative falls back to the
    stored default rather than reading forward -- `x[-1]` is not history.
    """
    input_id = node.get("offsetInputId")
    if input_id is None:
        return int(node["offset"])
    decl = decls.get(input_id)
    raw = inputs.get(input_id)
    if raw is None:
        raw = decl.get("defaultValue") if decl else node["offset"]
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return int(node["offset"])
    if not math.isfinite(v):
        return int(node["offset"])
    declared = None
    if decl is not None and decl.get("type") == "integer":
        raw_max = decl.get("max")
        if isinstance(raw_max, (int, float)) and math.isfinite(raw_max):
            declared = int(raw_max)
    max_back = min(declared if declared is not None else SCRIPT_LIMITS["maximumLookback"],
                   SCRIPT_LIMITS["maximumLookback"])
    return max(0, min(max_back, int(v)))


def _eval_node(
    node, values, dataset, inputs, decls, n, ta_cache,
    calendar: SessionCalendar, htf_cache=None, session_cache=None,
):
    # `calendar` is REQUIRED, mirroring the TS `evalNode`: a default here is how a
    # wrong calendar would silently reach production.
    op = node["op"]
    if op == "source":
        src = node["source"]
        if src in _CONTEXT_IDS:
            return _resolve_context(dataset, src, calendar)
        return _resolve_source(dataset, src)
    if op == "const":
        return _const_value(node["value"])
    if op == "input":
        if node.get("field") is not None:
            # `field` (design §5.2) is only ever emitted on a session-typed
            # input. A None cache (a direct caller) still computes correctly,
            # just uncached — mirroring `_eval_htf`'s leniency above.
            return _session_field_value(
                node, inputs, decls, session_cache if session_cache is not None else {}
            )
        return _input_value(node, inputs, decls, dataset)
    if op == "binop":
        return _binop(node["operator"], values[node["args"][0]], values[node["args"][1]])
    if op == "unop":
        return _unop(node["operator"], values[node["arg"]])
    if op == "select":
        return _select(values[node["cond"]], values[node["then"]], values[node["else"]], n)
    if op == "hist":
        return _hist(values[node["arg"]], _hist_offset(node, inputs, decls), n)
    if op == "nz":
        return _nz(values[node["arg"]], node.get("replacement", 0), n)
    if op == "scan":
        return _eval_scan(node, values, n)
    if op == "htf":
        return _eval_htf(node, dataset, htf_cache, inputs, decls, calendar)
    return _call(node, values, ta_cache, n)


_SCAN_MATH_UNARY = {
    "abs": abs, "sign": lambda x: math.nan if math.isnan(x) else float((x > 0) - (x < 0)),
    "sqrt": lambda x: math.sqrt(x) if x >= 0 else math.nan,
    "exp": math.exp, "log": lambda x: math.log(x) if x > 0 else (-math.inf if x == 0 else math.nan),
    "log10": lambda x: math.log10(x) if x > 0 else (-math.inf if x == 0 else math.nan),
    "round": lambda x: math.nan if math.isnan(x) else float(int(x + 0.5) if x >= 0 else -int(-x + 0.5)),
    "floor": lambda x: math.nan if math.isnan(x) else math.floor(x),
    "ceil": lambda x: math.nan if math.isnan(x) else math.ceil(x),
}
_SCAN_MATH_BINARY = {
    "pow": lambda a, b: a**b,
    "max": lambda a, b: math.nan if math.isnan(a) or math.isnan(b) else max(a, b),
    "min": lambda a, b: math.nan if math.isnan(a) or math.isnan(b) else min(a, b),
}


def _scan_at(v, t: int) -> float:
    return float(v[t]) if _is_series(v) else float(v)


def _eval_scan(node, values, n) -> np.ndarray:
    """Single-lane recurrence: state[t] = expr(state[t-1], inputs[.][t]).
    `prev` (bare self) starts at the seed on bar 0; `prevh` (x[1]) starts at
    NaN, mirroring Pine history. Both track the committed value afterwards."""
    inputs = [values[i] for i in node["inputs"]]
    out = np.empty(n)
    prev = math.nan if node.get("init") is None else float(node["init"])
    prevh = math.nan

    def ev(e, prev_v, prevh_v, t):
        k = e["k"]
        if k == "const":
            return math.nan if e["v"] is None else float(e["v"])
        if k == "input":
            return _scan_at(inputs[e["i"]], t)
        if k == "prev":
            return prev_v
        if k == "prevh":
            return prevh_v
        if k == "bin":
            return _scalar_binop(e["op"], ev(e["a"], prev_v, prevh_v, t), ev(e["b"], prev_v, prevh_v, t))
        if k == "un":
            a = ev(e["a"], prev_v, prevh_v, t)
            if e["op"] == "-":
                return -a
            if e["op"] == "isna":
                return 1.0 if math.isnan(a) else 0.0
            return 0.0 if _truthy_scalar(a) else 1.0
        if k == "select":
            if _truthy_scalar(ev(e["c"], prev_v, prevh_v, t)):
                return ev(e["t"], prev_v, prevh_v, t)
            return ev(e["e"], prev_v, prevh_v, t)
        if k == "nz":
            a = ev(e["a"], prev_v, prevh_v, t)
            if not math.isnan(a):
                return a
            return ev(e["b"], prev_v, prevh_v, t) if "b" in e else 0.0
        if k == "math":
            fn = e["fn"]
            args = [ev(a, prev_v, prevh_v, t) for a in e["args"]]
            un = _SCAN_MATH_UNARY.get(fn)
            if un is not None and len(args) == 1:
                a0 = args[0]
                return math.nan if math.isnan(a0) and fn not in () else un(a0) if not math.isnan(a0) else math.nan
            bi = _SCAN_MATH_BINARY.get(fn)
            if bi is not None and len(args) == 2:
                return bi(args[0], args[1])
            return math.nan
        return math.nan

    expr = node["expr"]
    for t in range(n):
        cur = ev(expr, prev, prevh, t)
        out[t] = cur
        prev = cur
        prevh = cur
    return out


def _as_series(v, n) -> np.ndarray:
    return v if _is_series(v) else np.full(n, float(v))


def _input_color(inputs: dict, color_input_id, base: str) -> str:
    """Substitute a runtime `input.color` override for a baked-in default hex;
    falls back to `base` when no colorInputId is set or no override exists."""
    if color_input_id is None:
        return base
    v = inputs.get(color_input_id)
    return v if isinstance(v, str) and v else base


def _plot_output(o, series, oid, pane, inputs: dict) -> dict:
    """Mirror the TS collect-outputs plot lowering: bar-style variants become
    histogram outputs (base 0); stepline/area/circles/cross become line-style
    flags. Kind parity with the browser is pinned by tests."""
    style_in = o.get("style", {})
    color = _input_color(inputs, style_in.get("colorInputId"), style_in.get("color", ""))
    variant = style_in.get("variant")
    if variant in ("histogram", "columns"):
        style = {"color": color, "base": 0}
        if variant == "columns":
            style["column"] = True
        return {"kind": "histogram", "id": oid, "title": o["title"], "pane": pane, "values": series, "style": style}
    style = {"color": color, "lineWidth": style_in.get("lineWidth", 1)}
    if style_in.get("lineStyle"):
        style["lineStyle"] = style_in["lineStyle"]
    if variant == "stepline":
        style["step"] = True
    elif variant == "area":
        style["area"] = True
    elif variant in ("circles", "cross"):
        style["markers"] = True
    return {"kind": "line", "id": oid, "title": o["title"], "pane": pane, "values": series, "style": style}


def _dynamic_colors(color_node_id, values, n, ir) -> list[str] | None:
    """Per-bar palette colors for a colorNodeId ('' where the index is NaN)."""
    if color_node_id is None:
        return None
    palette = ir.get("palette", [])
    idx_series = _as_series(values[color_node_id], n)
    colors = []
    for i in range(n):
        v = idx_series[i]
        if math.isnan(v) or not (0 <= int(v) < len(palette)):
            colors.append("")
        else:
            colors.append(palette[int(v)])
    return colors


def _split_plot_by_palette(o, values, n, idx, pane, ir, inputs: dict) -> list[dict]:
    """Mirror of the TS splitPlotByPalette: one masked output per palette
    color; line variants keep a 1-bar connector so segments join."""
    palette = ir.get("palette", [])
    series = _as_series(values[o["nodeId"]], n)
    color_idx = _as_series(values[o["style"]["colorNodeId"]], n)
    variant = o["style"].get("variant")
    is_bar = variant in ("histogram", "columns")
    outputs: list[dict] = []
    for k, hex_color in enumerate(palette):
        masked = np.full(n, np.nan)
        any_here = False
        for i in range(n):
            here = color_idx[i] == k
            connector = (not is_bar) and i + 1 < n and color_idx[i + 1] == k
            if here or connector:
                masked[i] = series[i]
                if here and not math.isnan(series[i]):
                    any_here = True
        if not any_here:
            continue
        sub_style = {key: v for key, v in o["style"].items() if key != "colorNodeId"}
        sub_style["color"] = hex_color
        sub = {**o, "style": sub_style}
        built = _plot_output(sub, masked, f"out_{idx}_c{k}", pane, inputs)
        outputs.append(built)
    return outputs


# ── Drawing materializer (design 0.5 §3/§4/§5/§7/§11) ────────────────────────
# Byte-identical with the TS mirror (openalgo-openscript/src/runtime/collect-outputs.ts).


def _sample_at(v, i: int) -> float:
    """Read a value at a bar (scalar broadcasts, series indexes)."""
    return float(v[i]) if _is_series(v) else float(v)


# Beyond 2^52 a float64 is integer-valued (no fractional bits), so *100 would lose
# precision — format as a full-digit integer instead (mirror of TS DRAW_INT_SAFE).
_DRAW_INT_SAFE = 4503599627370496


def _anchor_time(dataset: dict, bar: int, n: int):
    """The timestamp anchor value for a bar: dataset["time"][bar] when the bar is
    IN the dataset, else None (a left overhang bar<0 or a right edge bar>last not
    yet reached). NEVER a clamp-derived time — a clamped time drifts as the dataset
    grows/rebases and would falsify the §5 zero-diff-on-rebase invariant (Fable
    #1/#2). Falls back to the bar index only when no time column exists."""
    if bar < 0 or bar > n - 1:
        return None
    t = dataset.get("time")
    if t is not None and len(t) == n:
        return float(t[bar])
    return float(bar)


def _spawn_time_of(dataset: dict, s: int, n: int) -> float:
    """The spawn bar's timestamp (the stable-ID carrier). s is always in-dataset."""
    t = dataset.get("time")
    if t is not None and len(t) == n:
        return float(t[s])
    return float(s)


def _time_key(t: float) -> str:
    """Byte-identical (TS<->Python) key for a spawn timestamp — the UNtruncated
    value, so sub-second bars in one integer-second stay DISTINCT (Fable #3).
    Integers render bare (str(int), matching JS String(1002)=='1002'); fractional
    values use repr (matching JS shortest round-trip in the epoch-timestamp
    domain)."""
    return str(int(t)) if float(t).is_integer() else repr(float(t))


def _format_draw_number(v: float) -> str:
    """Deterministic, byte-identical (TS<->Python) number->string for a label
    template (design §11; Fable #4). Rounds to 2 decimals with round-half-to-even
    in the integer count-of-hundredths domain and builds the string from int parts
    so it never uses exponential notation. Mirror of the TS formatDrawNumber; both
    emit the identical string for every input (ties, +-Infinity, NaN, >=1e21)."""
    if math.isnan(v):
        return "NaN"
    if v == math.inf:
        return "Infinity"
    if v == -math.inf:
        return "-Infinity"
    neg = v < 0
    a = abs(v)
    if a >= _DRAW_INT_SAFE:  # integer-valued beyond fractional precision
        return ("-" if neg else "") + str(int(a)) + ".00"
    scaled = a * 100
    fl = math.floor(scaled)
    diff = scaled - fl
    if diff > 0.5:
        r = fl + 1
    elif diff < 0.5:
        r = fl
    else:
        r = fl if (fl % 2 == 0) else fl + 1
    intp, frac = divmod(int(r), 100)
    return ("-" if neg else "") + str(intp) + "." + str(frac).zfill(2)


_FIXED_SPEC = re.compile(r"^\.(\d)f$")


def _format_draw_compact(v: float) -> str:
    """`{n:compact}` -- a magnitude-suffixed number (`6.1M`, `2.5K`, `940`).

    Lives in the ENGINE, not the renderer, for a reason worth stating: it is the
    only way the shared fixture corpus can pin it, and both runtimes must emit
    the identical string. A renderer-side formatter would be invisible to the
    corpus -- exactly the hole that let a marker ship with an unread title.

    It exists at all because Pine gets its suffix from an if/else returning
    different STRINGS per magnitude, and this language has no string series, so
    the branch has to happen after sampling. Here.
    """
    if not math.isfinite(v):
        return _format_draw_number(v)
    neg = v < 0
    a = abs(v)
    if a >= 1e9:
        scaled, suffix = a / 1e9, "B"
    elif a >= 1e6:
        scaled, suffix = a / 1e6, "M"
    elif a >= 1e3:
        scaled, suffix = a / 1e3, "K"
    else:
        scaled, suffix = a, ""
    # One decimal via the same round-half-to-even discipline
    # `_format_draw_number` uses, so a tie never disagrees across runtimes.
    t = scaled * 10
    fl = math.floor(t)
    diff = t - fl
    if diff > 0.5:
        r = fl + 1
    elif diff < 0.5:
        r = fl
    else:
        r = fl if fl % 2 == 0 else fl + 1
    whole, dec = divmod(int(r), 10)
    body = f"{whole}" if dec == 0 else f"{whole}.{dec}"
    return ("-" if neg else "") + body + suffix


def _apply_draw_format(v: float, spec: str | None) -> str:
    """Apply one `{n[:spec]}` format spec. An unknown spec falls back to the
    default numeric rendering rather than raising -- the compiler is where a bad
    spec belongs, and a chart must never show a thrown formatter."""
    if not spec:
        return _format_draw_number(v)
    if spec == "compact":
        return _format_draw_compact(v)
    m = _FIXED_SPEC.match(spec)
    if m:
        return f"{v:.{int(m.group(1))}f}" if math.isfinite(v) else _format_draw_number(v)
    if spec == "#":
        return f"{round(v)}" if math.isfinite(v) else _format_draw_number(v)
    return _format_draw_number(v)


def _render_draw_text(spec: dict, s: int, values: list) -> str:
    """Resolve an IRDrawText at the spawn bar: const verbatim; template formatted
    once from its args sampled at s (design §11)."""
    if spec.get("kind") == "const":
        return spec["value"]
    out = spec["fmt"]
    for k, arg in enumerate(spec["args"]):
        v = _sample_at(values[arg], s)
        # Replace `{k}` and `{k:spec}` in one pass so a spec cannot be left
        # behind as literal text when the plain form also appears.
        pattern = r"\{" + str(k) + r"(?::([^}]*))?\}"
        out = re.sub(pattern, lambda m, _v=v: _apply_draw_format(_v, m.group(1)), out)
    return out


def _terminate_holds(
    term, b: int, top: float, bottom: float, dataset: dict, n: int, calendar: SessionCalendar
) -> bool:
    """Whether the terminate predicate holds at scanned bar b (design §4, G1 §2).
    close_* are STRICT (>/<); cross_*/touch are INCLUSIVE (>=/<=); straddle is the
    STRICT counterpart of touch. A zone tests the NEAR edge in the named direction;
    touch and straddle are non-directional. For a level, top == bottom == the single
    value L.

    `calendar` is read by new_session ONLY. The five price predicates must stay
    calendar-independent by construction (the engine's tests/calendar-isolation.test.ts
    is the standing guard); a local_day_key reachable from any of the five branches
    below is a defect, not a refactor."""
    close = float(dataset["close"][b])
    high = float(dataset["high"][b])
    low = float(dataset["low"][b])
    if term == "close_above":
        return close > top
    if term == "close_below":
        return close < bottom
    if term == "cross_above":
        return high >= top
    if term == "cross_below":
        return low <= bottom
    if term == "touch":
        entered_top = high >= top and low <= top
        entered_bottom = high >= bottom and low <= bottom
        return entered_top or entered_bottom
    if term == "straddle":
        # STRICT, and the strictness is the entire point: an exact retest of an
        # edge must NOT mitigate. Comparisons are > / <, never >= / <=.
        crossed_top = high > top and low < top
        crossed_bottom = high > bottom and low < bottom
        return crossed_top or crossed_bottom
    # G1 §2 -- BOUNDARY-AFTER-BAR: "is b the last bar of its session?", NEVER "did
    # the day change at b?". §3's geometry is inclusive (x2 = b is part of the
    # object), so a day-change-AT-bar test would fire on the NEXT session's OPENING
    # bar and draw every session's levels onto it. The final loaded bar has no
    # successor, so this cannot hold there and the object correctly falls through to
    # the live edge (G1 §4) -- that fallthrough is the whole reason one output yields
    # both bounded history and a live current session.
    if term == "new_session":
        if b + 1 > n - 1:
            return False
        t = dataset.get("time")
        if t is None or len(t) != n:
            return False
        return local_day_key(float(t[b]), calendar) != local_day_key(float(t[b + 1]), calendar)
    return False


def _scan_start_for(term, s: int) -> int:
    """The first bar the extend.until forward scan tests, per predicate (G1 §3 --
    the ONE scoped exception to phase-0.5 §3's blanket s+1).

    The five price predicates start at s+1: an object spawned BECAUSE price did
    something will usually satisfy its own break condition on the spawn bar, so
    testing s would make them self-terminate as an artifact.

    new_session starts at s, inclusive, because it is a calendar predicate with no
    such degeneracy -- a one-bar object IS the correct answer. On daily bars every
    bar is a session, so a level spawned at s must close at s (day(s) != day(s+1));
    starting at s+1 would find the NEXT day's boundary and stretch the object across
    the whole following session. For intraday data the two starts agree, since the
    predicate is false from s up to the session's last bar."""
    return s if term == "new_session" else s + 1


def _sampled_span(o: dict, off: int) -> int:
    """Extra object-bars to charge for a LEFT extension, and only a SAMPLED one.

    A constant `offset` is visible to the plan cost estimator, so it is already
    part of the admitted plan and charging it here would move the budget for
    every indicator that has ever used one. A sampled offset is the genuinely
    new hazard: nothing bounds it before execution, so it pays per object.
    """
    return 0 if o.get("offsetNodeId") is None else abs(off)


def _resolve_span(o: dict, field: str, const_value: int, s: int, values: list) -> int:
    """Shared sampler for the two series-valued geometry spans (`offset`,
    `bars`): const passthrough, else sample at spawn, truncate, clamp. ONE
    function so the two cannot drift in rounding or clamping and produce a box
    whose edges disagree about where they are."""
    node = o.get(field)
    if node is None:
        return const_value
    v = float(_sample_at(_as_series(values[node], s + 1), s))
    if not math.isfinite(v):
        return 0
    max_back = SCRIPT_LIMITS["maximumLookback"]
    return max(-max_back, min(max_back, int(v)))


def _resolve_offset(o: dict, const_offset: int, s: int, values: list) -> int:
    """The object's LEFT shift, sampled at spawn when it is a series (design 3.1).

    `na` falls back to 0 -- the identity, i.e. no shift. That differs from an
    `na` marker PRICE, which drops the marker, and the asymmetry is deliberate:
    there is no defensible price to invent, but "do not move it" is the obvious
    meaning of a missing shift, and dropping the object instead would make it
    vanish exactly while its offset expression was warming up.
    """
    node = o.get("offsetNodeId")
    if node is None:
        return const_offset
    v = float(_sample_at(_as_series(values[node], s + 1), s))
    if not math.isfinite(v):
        return 0
    max_back = SCRIPT_LIMITS["maximumLookback"]
    return max(-max_back, min(max_back, int(v)))


def _resolve_right_edge(
    o: dict, s: int, top: float, bottom: float, dataset: dict, n: int, calendar: SessionCalendar, values: list) -> dict:
    """Resolve the right edge + open/mitigated flags for one object (design §3/§4)."""
    last_bar_index = n - 1
    right_pad = o.get("rightPad", 0)
    x2_0 = s + right_pad
    extend = o.get("extend")
    if extend == "lastbar":
        return {"x2bar": max(last_bar_index, x2_0), "open": True, "mitigated": False, "objBars": 1}
    if extend == "bars":
        const_bars = o["bars"] if isinstance(o.get("bars"), (int, float)) else 0
        bars = _resolve_span(o, "barsNodeId", int(const_bars), s, values)
        return {"x2bar": s + bars, "open": False, "mitigated": False, "objBars": 1}
    # extend == 'until': forward scan from the per-predicate start (see
    # _scan_start_for); x2 = the first terminate bar, INCLUSIVE.
    #
    # The terminate bar is returned DIRECTLY, never clamped up to x2_0 (G1 §8,
    # FROZEN): termination is authoritative for every until predicate and right_pad
    # is only the provisional INITIAL right edge, so x2bar < s + right_pad is a
    # correct answer. Clamping would push a daily new_session object's right edge
    # into the NEXT calendar day -- reintroducing the exact bug this mode exists to
    # prevent, in the one case it was built for.
    term = o.get("terminate")
    obj_bars = 0
    for b in range(_scan_start_for(term, s), last_bar_index + 1):
        obj_bars += 1
        if _terminate_holds(term, b, top, bottom, dataset, n, calendar):
            # Price took the object out, so it closed MITIGATED -- true for
            # every directional predicate, not just the `touch`/`straddle`
            # pair. Only `new_session` reaches here without price doing
            # anything: that is a time expiry, and an object that merely aged
            # out was never mitigated.
            return {
                "x2bar": b,
                "open": False,
                "mitigated": term != "new_session",
                "objBars": obj_bars,
            }
    return {"x2bar": last_bar_index, "open": True, "mitigated": False, "objBars": obj_bars}


def _anchor(dataset: dict, bar: int, n: int) -> dict:
    # TRUE geometric bar (may be <0 for a left overhang or >last for a not-yet
    # -reached edge) with a None time when outside the dataset (Fable #1/#2).
    a = {"bar": int(bar), "time": _anchor_time(dataset, bar, n)}
    # A forward anchor states how far past the last bar it sits, so the renderer
    # can project it instead of degrading it to a chart edge. Only forward: a
    # left overhang has no destination to state.
    if bar > n - 1:
        a["ahead"] = int(bar) - (n - 1)
    return a


def _materialize_drawing(
    o, values, n, idx, pane, dataset, budget, total_state, calendar: SessionCalendar, inputs: dict
) -> dict:
    """Materialize one level/zone output into a levels/zones output dict."""
    oid = f"out_{idx}"
    offset = o.get("offset", 0)
    if budget is not None:
        budget.charge(DRAW_BASE_OPS)

    # Retention: <= the literal exactly (no clamp-up-to-1; maxKept:0 -> 0), <=
    # maximumObjectsPerOutput, <= the remaining cross-output total (§10/§13).
    raw = o.get("maxKept")
    literal = int(raw) if isinstance(raw, (int, float)) and math.isfinite(raw) else 0
    effective_max = max(0, min(literal, SCRIPT_LIMITS["maximumObjectsPerOutput"], total_state["remaining"]))

    # Confirmed-spawn collection: scan cond backwards, keep the newest <= max (§5).
    cond = _as_series(values[o["condNodeId"]], n)
    spawns: list[int] = []
    scanned = 0
    if effective_max > 0:
        for s in range(n - 1, -1, -1):
            scanned += 1
            if _truthy_scalar(cond[s]):
                spawns.append(s)
                if len(spawns) >= effective_max:
                    break
    if budget is not None:
        budget.charge(DRAW_SCAN_WEIGHT * scanned)
    spawns.reverse()  # ascending spawn-bar order (oldest -> newest)
    total_state["remaining"] -= len(spawns)

    kind = o["kind"]
    if kind == "level":
        price_v = values[o["priceNodeId"]]
        items: list[dict] = []
        for k, s in enumerate(spawns):
            spawn_time = _spawn_time_of(dataset, s, n)
            # Malformed dataset: a non-finite spawn time can't form a stable id —
            # skip the object gracefully in BOTH runtimes (never crash; Fable #5).
            if not math.isfinite(spawn_time):
                continue
            price = _sample_at(price_v, s)
            edge = _resolve_right_edge(o, s, price, price, dataset, n, calendar, values)
            off = _resolve_offset(o, offset, s, values)
            if budget is not None:
                budget.charge(DRAW_OBJECT_WEIGHT * (edge["objBars"] + _sampled_span(o, off)))
            item = {
                "id": f"{idx}:{_time_key(spawn_time)}",
                "x1": _anchor(dataset, s + off, n),
                "x2": _anchor(dataset, edge["x2bar"], n),
                "price": price,
                "open": edge["open"],
            }
            label = o.get("label")
            if label and (not o.get("labelLatestOnly") or k == len(spawns) - 1):
                item["label"] = _render_draw_text(label, s, values)
            items.append(item)
        # Built key by key rather than copied: the IR style also carries the G8
        # colorInputId, which is COMPILE-side binding metadata and must not reach
        # the renderer -- the output carries the RESOLVED color.
        style_in = o.get("style", {})
        level_style = {
            "color": _input_color(inputs, style_in.get("colorInputId"), style_in.get("color", ""))
        }
        if style_in.get("lineWidth") is not None:
            level_style["lineWidth"] = style_in["lineWidth"]
        if style_in.get("lineStyle") is not None:
            level_style["lineStyle"] = style_in["lineStyle"]
        return {"kind": "levels", "id": oid, "title": o["title"], "pane": pane, "style": level_style, "items": items}

    # zone
    top_v = values[o["topNodeId"]]
    bottom_v = values[o["bottomNodeId"]]
    items = []
    for _k, s in enumerate(spawns):
        spawn_time = _spawn_time_of(dataset, s, n)
        if not math.isfinite(spawn_time):
            continue
        top = _sample_at(top_v, s)
        bottom = _sample_at(bottom_v, s)
        edge = _resolve_right_edge(o, s, top, bottom, dataset, n, calendar, values)
        off = _resolve_offset(o, offset, s, values)
        if budget is not None:
            budget.charge(DRAW_OBJECT_WEIGHT * (edge["objBars"] + _sampled_span(o, off)))
        item = {
            "id": f"{idx}:{_time_key(spawn_time)}",
            "x1": _anchor(dataset, s + off, n),
            "x2": _anchor(dataset, edge["x2bar"], n),
            "top": top,
            "bottom": bottom,
            "open": edge["open"],
        }
        if edge["mitigated"]:
            item["mitigated"] = True
        text = o.get("text")
        if text:
            item["text"] = _render_draw_text(text, s, values)
        items.append(item)
    # Same as the level branch: resolve each slot, and never emit the binding ids.
    style_in = o.get("style", {})
    style = {"color": _input_color(inputs, style_in.get("colorInputId"), style_in.get("color", ""))}
    if style_in.get("borderColor") is not None:
        style["borderColor"] = _input_color(
            inputs, style_in.get("borderColorInputId"), style_in["borderColor"]
        )
    if style_in.get("borderStyle") is not None:
        style["borderStyle"] = style_in["borderStyle"]
    if o.get("mitigatedColor") is not None:
        style["mitigatedColor"] = _input_color(
            inputs, o.get("mitigatedColorInputId"), o["mitigatedColor"]
        )
    return {"kind": "zones", "id": oid, "title": o["title"], "pane": pane, "style": style, "items": items}


def _collect_outputs(
    ir,
    values,
    n,
    inputs: dict,
    dataset: dict | None = None,
    budget=None,
    calendar: SessionCalendar = IST_CALENDAR,
) -> list[dict]:
    """`calendar` reaches the drawing materializer here (G1 §6) so
    `terminate.new_session` resolves its session boundaries against the SAME
    calendar the run's context fields used. It defaults to IST for the same reason
    `execute_ir` does -- direct callers and existing tests -- and `execute_ir`, the
    ONLY caller, always passes its own explicitly, so no production path relies on
    the default."""
    overlay = ir["declaration"].get("overlay", False)
    pane = "overlay" if overlay else 1
    outputs: list[dict] = []
    dataset = dataset or {}
    # Running retained-object budget across ALL drawing outputs (design §10).
    total_objects = {"remaining": SCRIPT_LIMITS["maximumTotalObjects"]}
    for idx, o in enumerate(ir["outputs"]):
        kind = o["kind"]
        oid = f"out_{idx}"
        if kind == "plot" and o.get("style", {}).get("colorNodeId") is not None:
            outputs.extend(_split_plot_by_palette(o, values, n, idx, pane, ir, inputs))
        elif kind == "plot":
            outputs.append(_plot_output(o, _as_series(values[o["nodeId"]], n), oid, pane, inputs))
        elif kind == "hline":
            outputs.append({"kind": "hline", "id": oid, "title": o["title"], "pane": pane, "price": o["price"]})
        elif kind == "fill":
            top = ir["outputs"][o["topPlotIndex"]]
            bottom = ir["outputs"][o["bottomPlotIndex"]]
            if top.get("kind") == "plot" and bottom.get("kind") == "plot":
                outputs.append(
                    {
                        "kind": "fill",
                        "id": oid,
                        "title": o.get("title", ""),
                        "pane": pane,
                        "topId": f"out_{o['topPlotIndex']}",
                        "bottomId": f"out_{o['bottomPlotIndex']}",
                        "top": _as_series(values[top["nodeId"]], n),
                        "bottom": _as_series(values[bottom["nodeId"]], n),
                        "style": {"color": _input_color(inputs, o.get("colorInputId"), o.get("color", ""))},
                    }
                )
        elif kind in ("plotshape", "plotchar"):
            cond = _as_series(values[o["condNodeId"]], n)
            per_bar = _dynamic_colors(o.get("colorNodeId"), values, n, ir)
            base_color = _input_color(inputs, o.get("colorInputId"), o.get("color", ""))
            price_node = o.get("priceNodeId")
            price_series = _as_series(values[price_node], n) if price_node is not None else None
            text = o.get("char") if kind == "plotchar" else o.get("text")
            shape = "text" if kind == "plotchar" else o.get("shape", "circle")
            location = o.get("location", "aboveBar")
            size = o.get("size")
            # Dynamic color resolving to na hides the marker (Pine color=na).
            bars = []
            marker_items = []
            for i in range(n):
                if not _truthy_scalar(cond[i]):
                    continue
                color = per_bar[i] if per_bar is not None else base_color
                if color == "":
                    continue
                # An `na` price DROPS the marker rather than inventing a level for
                # it. This is the common case, not an edge one: `ta.pivothigh` is
                # na on every bar it has not confirmed a pivot, so a fallback
                # would scatter glyphs at a fabricated price. Same rule as
                # `color=na` hiding a marker.
                price = None
                if price_series is not None:
                    p = float(price_series[i])
                    if not math.isfinite(p):
                        continue
                    price = p
                bars.append(i)
                item: dict = {
                    "barIndex": i,
                    "position": location,
                    "shape": shape,
                    "color": color,
                }
                if price is not None:
                    item["price"] = price
                if isinstance(text, str):
                    item["text"] = text
                if isinstance(size, str):
                    item["size"] = size
                marker_items.append(item)
            # `bars` is kept for existing consumers; `markers` is the full item
            # list the TS runtime emits. Without it the shared fixture corpus
            # could not replay a marker at all, which is exactly how a marker
            # shipped with an unread `title` on both runtimes.
            outputs.append(
                {
                    "kind": kind,
                    "id": oid,
                    "title": o.get("title", ""),
                    "bars": bars,
                    "markers": marker_items,
                }
            )
        elif kind in ("barcolor", "bgcolor"):
            cond = _as_series(values[o["condNodeId"]], n)
            per_bar = _dynamic_colors(o.get("colorNodeId"), values, n, ir)
            static = _input_color(inputs, o.get("colorInputId"), o.get("color", ""))
            colors = [
                (per_bar[i] if per_bar is not None else static) if _truthy_scalar(cond[i]) else ""
                for i in range(n)
            ]
            bars = [i for i in range(n) if colors[i] != ""]
            outputs.append(
                {"kind": kind, "id": oid, "title": o.get("title", ""), "bars": bars, "colors": colors}
            )
        elif kind == "plotcandle":
            style = {
                "upColor": _input_color(inputs, o.get("colorInputId"), o.get("upColor", "")),
                "downColor": _input_color(inputs, o.get("colorInputId"), o.get("downColor", "")),
            }
            if o.get("bar"):
                style["bar"] = True
            outputs.append(
                {
                    "kind": "candle",
                    "id": oid,
                    "title": o.get("title", ""),
                    "pane": pane,
                    "open": _as_series(values[o["openNodeId"]], n),
                    "high": _as_series(values[o["highNodeId"]], n),
                    "low": _as_series(values[o["lowNodeId"]], n),
                    "close": _as_series(values[o["closeNodeId"]], n),
                    "style": style,
                }
            )
        elif kind == "alertcondition":
            cond = _as_series(values[o["condNodeId"]], n)
            fired = [i for i in range(n) if _truthy_scalar(cond[i])]
            outputs.append({"kind": "alert", "id": o["conditionId"], "title": o["title"], "message": o["message"], "firedAtBar": fired})
        elif kind in ("level", "zone"):
            outputs.append(
                _materialize_drawing(
                    o, values, n, idx, pane, dataset, budget, total_objects, calendar, inputs
                )
            )
    return outputs


def execute_ir(
    ir: dict,
    dataset: dict,
    inputs: dict | None = None,
    budget=None,
    calendar: SessionCalendar = IST_CALENDAR,
) -> list[dict]:
    """Run a compiled IRProgram over a dataset (dict of float numpy arrays with
    keys open/high/low/close/volume). Returns a list of output dicts; `line`
    outputs carry their numpy `values`, `alert` outputs carry `firedAtBar`.

    `budget` is an optional `OperationBudget` (see budget.py) — stepped once
    per node before evaluation, exactly like the TS executor, raising
    `BudgetExceeded` (OS4001/OS4002) when a limit is crossed.

    `calendar` is the session calendar the context/time fields resolve against
    (G7). It defaults to IST for direct callers and existing tests; NO production
    path may rely on that default — the caller resolves the instrument's calendar
    with `calendar_for_instrument` and passes it explicitly.
    """
    errors = admit_ir(ir)
    if errors:
        raise IRAdmissionError(errors)
    inputs = inputs or {}
    n = len(dataset["close"])
    # Phase 0.2 Task 7 — recompute the plan cost from the IR nodes and reject an
    # over-budget script BEFORE executing (mode from OPENSCRIPT_PLANCOST_MODE;
    # default 'enforce' since Task 9, override with =observe to shadow only). Runs
    # here every call, where barCount (n) is known — the admission boundary; unlike
    # the TS worker's incremental update path, execute_ir recomputes over the full
    # dataset each call, so this re-checks the CURRENT n every time. NEVER trusts
    # ir["meta"]["planCost"]: the verdict comes purely from the recompute.
    resolution = resolve_plan_cost(ir, n, SCRIPT_LIMITS, plancost_mode())
    if resolution["errors"]:
        raise IRAdmissionError(resolution["errors"])
    decls = {d["id"]: d for d in ir.get("inputs", [])}
    nodes = ir["nodes"]
    values: list = [None] * len(nodes)
    ta_cache: dict = {}
    # Per-RUN resample cache, keyed by (timeframe, calendar). Built fresh here, like
    # the TS `createExecCaches()`: two htf nodes on the same timeframe resample once.
    htf_cache: dict = {}
    # Per-RUN session-string parse cache, keyed by the RAW bound string (design
    # §5.2 "parse once per run"): the nine `field` nodes of one session input —
    # and any other input bound to the same string — share a single parse. Built
    # fresh here beside ta_cache/htf_cache, exactly where the TS
    # `createExecCaches()` scopes its `sessionCache`; a module-level cache would
    # leak entries across runs and datasets.
    session_cache: dict = {}
    for node in nodes:
        if budget is not None:
            budget.step(node)
        value = _eval_node(
            node, values, dataset, inputs, decls, n, ta_cache, calendar, htf_cache, session_cache
        )
        values[node["id"]] = value
        # Deterministic series-buffer accounting + wall-clock checkpoint after
        # each expensive kernel/scan node (design §7 cancellation granularity).
        if budget is not None:
            if isinstance(value, np.ndarray):
                budget.record_bytes(int(value.nbytes))
            if node["op"] in ("call", "scan"):
                budget.checkpoint()
    return _collect_outputs(ir, values, n, inputs, dataset, budget, calendar)
