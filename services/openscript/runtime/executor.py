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


def _const_value(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return math.nan  # None (na) or string


def _input_value(node, inputs, decls, dataset):
    input_id = node["inputId"]
    decl = decls.get(input_id)
    raw = inputs.get(input_id, decl.get("defaultValue") if decl else None)
    dtype = decl.get("type") if decl else None
    if dtype in ("integer", "float"):
        return float(raw)
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
        result = invoke_kernel(node["function"], [_ta_arg(a) for a in args])
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
        return _resolve_source(dataset, node["source"])
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
    return _call(node, values, ta_cache)


def _as_series(v, n) -> np.ndarray:
    return v if _is_series(v) else np.full(n, float(v))


def _plot_output(o, series, oid, pane) -> dict:
    """Mirror the TS collect-outputs plot lowering: bar-style variants become
    histogram outputs (base 0); stepline/area/circles/cross become line-style
    flags. Kind parity with the browser is pinned by tests."""
    style_in = o.get("style", {})
    variant = style_in.get("variant")
    if variant in ("histogram", "columns"):
        style = {"color": style_in.get("color", ""), "base": 0}
        if variant == "columns":
            style["column"] = True
        return {"kind": "histogram", "id": oid, "title": o["title"], "pane": pane, "values": series, "style": style}
    style = {"color": style_in.get("color", ""), "lineWidth": style_in.get("lineWidth", 1)}
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


def _split_plot_by_palette(o, values, n, idx, pane, ir) -> list[dict]:
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
        built = _plot_output(sub, masked, f"out_{idx}_c{k}", pane)
        outputs.append(built)
    return outputs


def _collect_outputs(ir, values, n) -> list[dict]:
    overlay = ir["declaration"].get("overlay", False)
    pane = "overlay" if overlay else 1
    outputs: list[dict] = []
    for idx, o in enumerate(ir["outputs"]):
        kind = o["kind"]
        oid = f"out_{idx}"
        if kind == "plot" and o.get("style", {}).get("colorNodeId") is not None:
            outputs.extend(_split_plot_by_palette(o, values, n, idx, pane, ir))
        elif kind == "plot":
            outputs.append(_plot_output(o, _as_series(values[o["nodeId"]], n), oid, pane))
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
                        "style": {"color": o.get("color", "")},
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
            static = o.get("color", "")
            colors = [
                (per_bar[i] if per_bar is not None else static) if _truthy_scalar(cond[i]) else ""
                for i in range(n)
            ]
            bars = [i for i in range(n) if colors[i] != ""]
            outputs.append(
                {"kind": kind, "id": oid, "title": o.get("title", ""), "bars": bars, "colors": colors}
            )
        elif kind == "plotcandle":
            style = {"upColor": o.get("upColor", ""), "downColor": o.get("downColor", "")}
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
    return outputs


def execute_ir(ir: dict, dataset: dict, inputs: dict | None = None, budget=None) -> list[dict]:
    """Run a compiled IRProgram over a dataset (dict of float numpy arrays with
    keys open/high/low/close/volume). Returns a list of output dicts; `line`
    outputs carry their numpy `values`, `alert` outputs carry `firedAtBar`.

    `budget` is an optional `OperationBudget` (see budget.py) — stepped once
    per node before evaluation, exactly like the TS executor, raising
    `BudgetExceeded` (OS4001/OS4002) when a limit is crossed.
    """
    inputs = inputs or {}
    n = len(dataset["close"])
    decls = {d["id"]: d for d in ir.get("inputs", [])}
    nodes = ir["nodes"]
    values: list = [None] * len(nodes)
    ta_cache: dict = {}
    for node in nodes:
        if budget is not None:
            budget.step()
        values[node["id"]] = _eval_node(node, values, dataset, inputs, decls, n, ta_cache)
    return _collect_outputs(ir, values, n)
