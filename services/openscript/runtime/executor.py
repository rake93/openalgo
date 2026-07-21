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

import numpy as np

from services.openscript.limits import SCRIPT_LIMITS

from .admit import IRAdmissionError, admit_ir, resolve_plan_cost
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
    {"time", "bar_index", "last_bar_index", "dayofweek", "dayofmonth", "hour", "minute", "month", "year"}
)
_IST_OFFSET_SECONDS = 19800  # +05:30, fixed (no DST)


def _resolve_context(dataset: dict, cid: str) -> np.ndarray:
    """Resolve a context/time series to a full float series.

    `bar_index`/`last_bar_index` derive from the length; `time` and the IST
    calendar fields derive from the dataset `time` column (epoch SECONDS, UTC).
    Calendar math is fixed IST (UTC+05:30, no DST) via Howard Hinnant's
    civil_from_days with floor division throughout — matching the TS runtime
    integer-for-integer. All series are na-free from bar 0.
    """
    n = len(dataset["close"])
    if cid == "bar_index":
        return np.arange(n, dtype=float)
    if cid == "last_bar_index":
        return np.full(n, float(n - 1))
    t_sec = np.asarray(dataset["time"], dtype=np.int64)
    if cid == "time":
        return (t_sec * 1000).astype(float)  # seconds → Pine milliseconds
    ist = t_sec + _IST_OFFSET_SECONDS
    days = ist // 86400  # days since 1970-01-01 in IST (floor)
    sod = ist - days * 86400  # second-of-day, 0..86399
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


def _call(node, values, ta_cache):
    args = [values[i] for i in node["args"]]
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


def _eval_node(node, values, dataset, inputs, decls, n, ta_cache):
    op = node["op"]
    if op == "source":
        src = node["source"]
        if src in _CONTEXT_IDS:
            return _resolve_context(dataset, src)
        return _resolve_source(dataset, src)
    if op == "const":
        return _const_value(node["value"])
    if op == "input":
        return _input_value(node, inputs, decls, dataset)
    if op == "binop":
        return _binop(node["operator"], values[node["args"][0]], values[node["args"][1]])
    if op == "unop":
        return _unop(node["operator"], values[node["arg"]])
    if op == "select":
        return _select(values[node["cond"]], values[node["then"]], values[node["else"]], n)
    if op == "hist":
        return _hist(values[node["arg"]], node["offset"], n)
    if op == "nz":
        return _nz(values[node["arg"]], node.get("replacement", 0), n)
    if op == "scan":
        return _eval_scan(node, values, n)
    return _call(node, values, ta_cache)


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


def _time_at(dataset: dict, bar: int, n: int) -> float:
    """Timestamp anchor for a bar: dataset["time"][bar], clamped to a valid
    index; falls back to the bar index when no time column is available."""
    last = n - 1
    b = 0 if bar < 0 else last if bar > last else bar
    t = dataset.get("time")
    if t is not None and len(t) == n:
        return float(t[b])
    return float(b)


def _format_draw_number(v: float) -> str:
    """Deterministic, cross-language number->string for a label template
    (design §11). Integers render bare; NaN -> "NaN"."""
    if math.isnan(v):
        return "NaN"
    if math.isfinite(v) and float(v).is_integer():
        return str(int(v))
    return f"{v:.2f}"


def _render_draw_text(spec: dict, s: int, values: list) -> str:
    """Resolve an IRDrawText at the spawn bar: const verbatim; template formatted
    once from its args sampled at s (design §11)."""
    if spec.get("kind") == "const":
        return spec["value"]
    out = spec["fmt"]
    for k, arg in enumerate(spec["args"]):
        v = _sample_at(values[arg], s)
        out = out.replace(f"{{{k}}}", _format_draw_number(v))
    return out


def _terminate_holds(term, b: int, top: float, bottom: float, dataset: dict) -> bool:
    """Whether the terminate predicate holds at scanned bar b (design §4).
    close_* are STRICT (>/<); cross_*/touch are INCLUSIVE (>=/<=). A zone tests
    the NEAR edge in the named direction; touch is non-directional. For a level,
    top == bottom == the single value L."""
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
    return False


def _resolve_right_edge(o: dict, s: int, top: float, bottom: float, dataset: dict, n: int) -> dict:
    """Resolve the right edge + open/mitigated flags for one object (design §3/§4)."""
    last_bar_index = n - 1
    right_pad = o.get("rightPad", 0)
    x2_0 = s + right_pad
    extend = o.get("extend")
    if extend == "lastbar":
        return {"x2bar": max(last_bar_index, x2_0), "open": True, "mitigated": False, "objBars": 1}
    if extend == "bars":
        bars = o["bars"] if isinstance(o.get("bars"), (int, float)) else 0
        return {"x2bar": s + int(bars), "open": False, "mitigated": False, "objBars": 1}
    # extend == 'until': forward scan from s+1; x2 = first terminate bar (inclusive).
    term = o.get("terminate")
    obj_bars = 0
    for b in range(s + 1, last_bar_index + 1):
        obj_bars += 1
        if _terminate_holds(term, b, top, bottom, dataset):
            return {"x2bar": b, "open": False, "mitigated": term == "touch", "objBars": obj_bars}
    return {"x2bar": last_bar_index, "open": True, "mitigated": False, "objBars": obj_bars}


def _anchor(dataset: dict, bar: int, n: int) -> dict:
    return {"bar": int(bar), "time": _time_at(dataset, bar, n)}


def _materialize_drawing(o, values, n, idx, pane, dataset, budget, total_state) -> dict:
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
            price = _sample_at(price_v, s)
            edge = _resolve_right_edge(o, s, price, price, dataset, n)
            if budget is not None:
                budget.charge(DRAW_OBJECT_WEIGHT * edge["objBars"])
            item = {
                "id": f"{idx}:{int(_time_at(dataset, s, n))}",
                "x1": _anchor(dataset, max(0, s + offset), n),
                "x2": _anchor(dataset, edge["x2bar"], n),
                "price": price,
                "open": edge["open"],
            }
            label = o.get("label")
            if label and (not o.get("labelLatestOnly") or k == len(spawns) - 1):
                item["label"] = _render_draw_text(label, s, values)
            items.append(item)
        return {"kind": "levels", "id": oid, "title": o["title"], "pane": pane, "style": dict(o.get("style", {})), "items": items}

    # zone
    top_v = values[o["topNodeId"]]
    bottom_v = values[o["bottomNodeId"]]
    items = []
    for _k, s in enumerate(spawns):
        top = _sample_at(top_v, s)
        bottom = _sample_at(bottom_v, s)
        edge = _resolve_right_edge(o, s, top, bottom, dataset, n)
        if budget is not None:
            budget.charge(DRAW_OBJECT_WEIGHT * edge["objBars"])
        item = {
            "id": f"{idx}:{int(_time_at(dataset, s, n))}",
            "x1": _anchor(dataset, max(0, s + offset), n),
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
    style = dict(o.get("style", {}))
    if o.get("mitigatedColor") is not None:
        style["mitigatedColor"] = o["mitigatedColor"]
    return {"kind": "zones", "id": oid, "title": o["title"], "pane": pane, "style": style, "items": items}


def _collect_outputs(ir, values, n, inputs: dict, dataset: dict | None = None, budget=None) -> list[dict]:
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
            # Dynamic color resolving to na hides the marker (Pine color=na).
            bars = [
                i
                for i in range(n)
                if _truthy_scalar(cond[i]) and (per_bar is None or per_bar[i] != "")
            ]
            outputs.append({"kind": kind, "id": oid, "title": o.get("title", ""), "bars": bars})
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
            outputs.append(_materialize_drawing(o, values, n, idx, pane, dataset, budget, total_objects))
    return outputs


def execute_ir(ir: dict, dataset: dict, inputs: dict | None = None, budget=None) -> list[dict]:
    """Run a compiled IRProgram over a dataset (dict of float numpy arrays with
    keys open/high/low/close/volume). Returns a list of output dicts; `line`
    outputs carry their numpy `values`, `alert` outputs carry `firedAtBar`.

    `budget` is an optional `OperationBudget` (see budget.py) — stepped once
    per node before evaluation, exactly like the TS executor, raising
    `BudgetExceeded` (OS4001/OS4002) when a limit is crossed.
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
    for node in nodes:
        if budget is not None:
            budget.step(node)
        value = _eval_node(node, values, dataset, inputs, decls, n, ta_cache)
        values[node["id"]] = value
        # Deterministic series-buffer accounting + wall-clock checkpoint after
        # each expensive kernel/scan node (design §7 cancellation granularity).
        if budget is not None:
            if isinstance(value, np.ndarray):
                budget.record_bytes(int(value.nbytes))
            if node["op"] in ("call", "scan"):
                budget.checkpoint()
    return _collect_outputs(ir, values, n, inputs, dataset, budget)
