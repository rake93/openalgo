"""Phase 0.2 — versioned operator-cost registry (design spec §3/§4).

Python mirror of openalgo-openscript/src/runtime/operator-cost.ts. Maps every
IR operator and every builtins-table function to a cost estimator returning
{"totalCost", "perBarCost", "bytesCost"} as serializable CostExpr dicts,
evaluated at admission when barCount / input bounds are known. The KERNEL_COST
values MUST stay identical to the TS registry — the coverage + parity tests pin
both sides.

Load-bearing rules (§3):
  - NO default cost factor. A function absent from KERNEL_COST raises
    "unpriced operator: ns.fn" — surfaced as IR_UNPRICED_OPERATOR at admission.
    The conformance test asserts the registry covers exactly the builtins-table
    surface (both languages).
  - v1 costs are linear and integer-valued only. NO fractional pow exponents
    (cross-platform ulp risk); in fact v1 emits no pow node at all. Superlinear
    kernels (e.g. a future LC-4 KNN) must register an explicit CostExpr built
    from integer pow exponents or mul compositions (e.g.
    mul(barCount, inputBound("k"))) — never a fractional exponent.

Cost families (v1, calibrated in shadow per design §4 — the classification is
structural, the constants are placeholders until calibration):
  - element (source / series input / binop / unop / select / hist / nz):
    1 unit/bar. `hist` is a lag *access*, priced O(1) regardless of offset.
  - elementwise call (math.* except sum): 2 units/bar.
  - stream call (recurrence / lag kernels with NO window-length parameter):
    small explicit constant/bar, sized by internal stages.
  - window call (length-parameterized ta.* / math.sum / kernels.*):
    sum(multiplier * length) (+ fixed) units/bar. The length arg is referenced
    symbolically: argConst(nodeId) for a literal const period, inputBound(id)
    for an input-bound period, else the conservative lit(maximumLookback)
    bound. Recurrence kernels that take a length (ema, rma, atr, macd, ...)
    are deliberately kept in the window family — their length bounds the
    warmup/lookback work, so charging length/bar is the conservative v1 upper
    bound (admission may over-estimate, never under).
  - scan: ScanExpr tree size units/bar (design §1).
"""

from __future__ import annotations

import math
from typing import Any

from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.openscript.builtins_table import (
    KERNELS_FUNCTIONS,
    MATH_FUNCTIONS,
    TA_FUNCTIONS,
)
from services.openscript.runtime.cost_expr import CostExpr

# Bumped whenever weights/estimators change (design §3).
COST_MODEL_VERSION = 1

# Every materialized series buffer is 8 bytes (f64) per bar (design §5).
_SERIES_BYTES_PER_BAR = 8
# Fixed footprint charged for a scalar node (const / non-source input).
_SCALAR_BYTES = 8
#: Per-bar cost charged to an `htf` (request.security) node: bucketing +
#: per-bucket aggregation + alignment are a small constant number of passes over
#: the base bars. Upper-bounds real work so `real <= charged <= estimate`
#: (Phase 3 design §7). NO COST_MODEL_VERSION bump: purely additive, every
#: existing cost is unchanged.
_HTF_COST_PER_BAR = 4
# Conservative window length when the arg is neither a const nor an input: a
# window is a lookback, and lookbacks beyond maximumLookback are rejected by
# the runtime — so this is a true upper bound.
_WINDOW_LEN_FALLBACK = SCRIPT_LIMITS["maximumLookback"]


def _lit(v: float) -> CostExpr:
    return {"k": "lit", "v": v}


def _bar_count() -> CostExpr:
    return {"k": "barCount"}


def _add(a: CostExpr, b: CostExpr) -> CostExpr:
    return {"k": "add", "a": a, "b": b}


def _mul(a: CostExpr, b: CostExpr) -> CostExpr:
    return {"k": "mul", "a": a, "b": b}


def _scaled(k: int, e: CostExpr) -> CostExpr:
    """k·e with the identity multiplier elided — trees stay minimal and
    byte-identical with the TS builder."""
    return e if k == 1 else _mul(_lit(k), e)


def _elem(units: int) -> dict:
    return {"family": "elementwise", "units": units}


def _stream(units: int) -> dict:
    return {"family": "stream", "units": units}


def _window(lens: dict[int, list[tuple[int, int]]], plus: int = 0) -> dict:
    # lens: accepted arity -> [(user-arg index, integer per-element multiplier)]
    return {"family": "window", "units": 0, "lens": lens, "plus": plus}


# The versioned per-function cost table, keyed "namespace.function". EVERY
# builtins-table function has an explicit entry — no fallback exists. Values
# MUST stay identical to the TS KERNEL_COST.
#
# Window `lens` keys are KERNEL arities — the arity of the IR the compiler
# actually emits. ir_gen's ta lowering assembles the matched overload's
# kernelArgs, injecting implicit source series (high/low/close/volume) and
# constants into call.args, so the emitted arity is len(kernelArgs), NOT the
# user-param count (e.g. ta.cci(20) emits 4 args: h, l, c, len). The period
# indices below are kernel-arg positions, valid for EVERY overload of the
# function (each verified against the builtins-table kernelArgs). A
# user-param-arity call node never occurs in real IR (the server recompiles
# source, 0.3) and is deliberately unpriced — it raises, since the executor
# could not run that shape either. math.* passes user args through unchanged
# (kernel arity == user arity).
#
# Window multipliers reflect logical passes over the window per bar
# (1 = single pass, 2 = double pass e.g. mean+deviation, 3 = triple);
# stream constants reflect internal per-bar stages. All integers.
KERNEL_COST: dict[str, dict] = {
    # --- ta.* single-length moving averages / window statistics ---
    "ta.sma": _window({2: [(1, 1)]}),
    "ta.ema": _window({2: [(1, 1)]}),
    "ta.wma": _window({2: [(1, 1)]}),
    "ta.hma": _window({2: [(1, 2)]}),  # wma(n) + wma(n/2) + wma(√n) ≤ 2n
    "ta.dema": _window({2: [(1, 2)]}),  # 2 stacked EMAs
    "ta.tema": _window({2: [(1, 3)]}),  # 3 stacked EMAs
    "ta.zlema": _window({2: [(1, 2)]}),  # lag-adjust + EMA
    "ta.stdev": _window({2: [(1, 2)]}),  # mean pass + deviation pass
    "ta.rsi": _window({2: [(1, 2)]}),  # up/down RMA pair
    "ta.roc": _stream(4),  # lag ratio — O(1)/bar like `hist`
    "ta.trix": _window({2: [(1, 3)]}),  # 3 stacked EMAs (+O(1) roc)
    "ta.change": _stream(2),  # lag diff — O(1)/bar like `hist`
    "ta.vwma": _window({3: [(2, 2)]}),  # (src, VOLUME, len) — rolling Σpv and Σv
    "ta.highest": _window({2: [(1, 1)]}),  # (src|HIGH, len)
    "ta.lowest": _window({2: [(1, 1)]}),  # (src|LOW, len)
    "ta.rising": _window({2: [(1, 1)]}),
    "ta.falling": _window({2: [(1, 1)]}),
    "ta.rma": _window({2: [(1, 1)]}),
    "ta.linreg": _window({2: [(1, 2)]}),  # Σxy and Σy passes
    "ta.barssince": _stream(2),  # counter recurrence
    "ta.cum": _stream(2),  # running sum
    # valuewhen keeps a ring of the last occurrence+1 matched values; a matching
    # bar shifts the ring O(occurrence) — worst case O(≤1000)/bar at the
    # kernel's occurrence bound, so a flat O(1) constant under-represents it.
    # 64 is the conservative calibrated constant (typical occurrence ≤ 2;
    # OS4002 wall-clock is the physical backstop for the bounded worst case;
    # re-tuned in shadow calibration, Task 9).
    "ta.valuewhen": _stream(64),
    # pivots inspect left + right + 1 (the candidate bar) elements per bar;
    # both overloads emit (src|HIGH/LOW, left, right)
    "ta.pivothigh": _window({3: [(1, 1), (2, 1)]}, 1),
    "ta.pivotlow": _window({3: [(1, 1), (2, 1)]}, 1),
    "ta.atr": _window({4: [(3, 1)]}, 2),  # (h, l, c, len) — RMA(len) + TR pass
    "ta.cci": _window({4: [(3, 2)]}),  # (h|src, l|src, c|src, len) — SMA + mean |dev|
    "ta.tr": _stream(4),  # max of 3 elementwise diffs
    "ta.obv": _stream(4),  # signed running sum
    "ta.mfi": _window({5: [(4, 2)]}, 2),  # (h, l, c, VOLUME, len) — ± flow sums
    "ta.crossover": _stream(4),
    "ta.crossunder": _stream(4),
    "ta.cross": _stream(4),
    "ta.macd": _window({4: [(1, 1), (2, 1), (3, 1)]}),  # fast + slow + signal EMAs
    "ta.bb": _window({3: [(1, 3)]}),  # SMA + 2-pass stdev
    "ta.ppo": _window({4: [(1, 1), (2, 1), (3, 1)]}),
    "ta.adx": _window({4: [(3, 3)]}, 2),  # (h, l, c, len) — 3 RMA smoothings + DM pass
    "ta.cpr": _stream(6),  # 3 elementwise outputs
    "ta.donchian": _window({3: [(2, 2)]}),  # (HIGH, LOW, len) — highest + lowest
    "ta.keltner": _window({6: [(3, 1), (4, 1)]}, 2),  # (h,l,c, emaLen, atrLen, mult)
    "ta.stochastic": _window({6: [(3, 2), (4, 1), (5, 1)]}),  # (h,l,c, kLen, kSmooth, dLen)
    "ta.supertrend": _window({5: [(3, 1)]}, 4),  # (h|src…, atrLen, factor) + band recurrence
    "ta.tsi": _window({4: [(1, 2), (2, 2), (3, 1)]}),  # double-smoothed m and |m|
    # (h, l, c, conv, base, spanB, displacement) — 3 hi+lo windows; the
    # displacement (index 6) is an O(1) shift and stays uncharged
    "ta.ichimoku": _window({7: [(3, 2), (4, 2), (5, 2)]}),
    "ta.pivotpoints": _stream(14),  # 7 elementwise outputs
    # --- math.* ---
    "math.abs": _elem(2),
    "math.sign": _elem(2),
    "math.sqrt": _elem(2),
    "math.exp": _elem(2),
    "math.log": _elem(2),
    "math.log10": _elem(2),
    "math.round": _elem(2),
    "math.floor": _elem(2),
    "math.ceil": _elem(2),
    "math.pow": _elem(2),
    "math.max": _elem(2),
    "math.min": _elem(2),
    "math.sum": _window({2: [(1, 1)]}),  # rolling window sum
    # --- kernels.* — Nadaraya-Watson regressions. The shipped Pine window quirk
    # covers exactly startAtBar + 2 bars; each element costs ~4 units (distance +
    # weight + accumulate). 4·(startAtBar + 2) = 4·startAtBar + 8. ---
    "kernels.rationalQuadratic": _window({4: [(3, 4)]}, 8),
    "kernels.gaussian": _window({3: [(2, 4)]}, 8),
}


# Fail fast if the registry ever drifts from the compiler's builtins surface —
# coverage is a hard invariant (§3), enforced at module load, not only in tests:
# a builtin added without a cost entry (or a cost entry for a removed builtin)
# raises on import, so it can never ship as a latent admission failure.
_BUILTIN_SURFACE: frozenset[str] = frozenset(
    [f"ta.{f}" for f in TA_FUNCTIONS]
    + [f"math.{f}" for f in MATH_FUNCTIONS]
    + [f"kernels.{f}" for f in KERNELS_FUNCTIONS]
)
for _key in _BUILTIN_SURFACE:
    if _key not in KERNEL_COST:
        raise ValueError(f"unpriced operator: {_key}")
for _key in KERNEL_COST:
    if _key not in _BUILTIN_SURFACE:
        raise ValueError(f"cost entry for unknown builtin: {_key}")


def window_length_arg_indices(namespace: str, fn: str, arity: int) -> list[int]:
    """The argument positions a windowed kernel is PRICED on, for a given arity.

    Exported so the G9 compiler warning is driven by the same table that causes
    the cost it explains. Duplicating these positions in the compiler would let
    the two drift, and a diagnostic pointing at an argument the cost model does
    not price is worse than none -- it sends the author to fix the wrong thing.

    Empty for a non-window kernel, an unknown key, or an unpriced arity.
    Mirrors the TS `windowLengthArgIndices`.
    """
    spec = KERNEL_COST.get(f"{namespace}.{fn}")
    if spec is None or spec.get("family") != "window":
        return []
    return [arg_idx for arg_idx, _k in (spec.get("lens") or {}).get(arity, ())]


def has_cost(namespace: str, fn: str) -> bool:
    """Registry membership — the admission-side pre-check for IR_UNPRICED_OPERATOR."""
    return f"{namespace}.{fn}" in KERNEL_COST


def cost_family_of(namespace: str, fn: str) -> str:
    """The registry cost family of a call ('elementwise' | 'stream' | 'window').

    The PlanCost estimator's breakdown bucketing key (window -> breakdown
    "window"; elementwise/stream -> breakdown "call"). Same registry lookup and
    same raise path as cost_of: an absent function raises
    "unpriced operator: ns.fn" (IR_UNPRICED_OPERATOR).
    """
    key = f"{namespace}.{fn}"
    spec = KERNEL_COST.get(key)
    if spec is None:
        raise ValueError(f"unpriced operator: {key}")
    return spec["family"]


# The exact covered "ns.fn" surface, for the coverage conformance test.
COVERED_FUNCTIONS: frozenset[str] = frozenset(KERNEL_COST)


def scan_expr_size(e: dict) -> int:
    """Number of nodes in a ScanExpr tree — the scan's units/bar (design §1)."""
    k = e.get("k") if isinstance(e, dict) else None
    if k in ("const", "input", "prev", "prevh"):
        return 1
    if k == "bin":
        return 1 + scan_expr_size(e["a"]) + scan_expr_size(e["b"])
    if k == "un":
        return 1 + scan_expr_size(e["a"])
    if k == "select":
        return 1 + scan_expr_size(e["c"]) + scan_expr_size(e["t"]) + scan_expr_size(e["e"])
    if k == "nz":
        b = e.get("b")
        return 1 + scan_expr_size(e["a"]) + (scan_expr_size(b) if b is not None else 0)
    if k == "math":
        return 1 + sum(scan_expr_size(a) for a in e["args"])
    raise ValueError(f"unpriced operator: scan.{k}")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _len_expr_of(node: dict, ir: dict, arg_idx: int) -> CostExpr:
    """Symbolic window-length expr for a call's length arg (see module doc)."""
    args = node["args"]
    nodes = ir["nodes"]
    arg_node = (
        nodes[args[arg_idx]] if arg_idx < len(args) and 0 <= args[arg_idx] < len(nodes) else None
    )
    if arg_node is None:
        raise ValueError(f"missing length arg {arg_idx} for {node['namespace']}.{node['function']}")
    if (
        arg_node["op"] == "const"
        and _is_number(arg_node.get("value"))
        and math.isfinite(arg_node["value"])
    ):
        return {"k": "argConst", "nodeId": arg_node["id"]}
    if arg_node["op"] == "input":
        return {"k": "inputBound", "id": arg_node["inputId"]}
    return _lit(_WINDOW_LEN_FALLBACK)


def _call_per_bar(key: str, spec: dict, node: dict, ir: dict) -> CostExpr:
    """Per-bar cost of a call node per its registry spec."""
    if spec["family"] != "window":
        return _lit(spec["units"])
    terms = spec["lens"].get(len(node["args"]))
    if terms is None:
        raise ValueError(f"unpriced arity for {key}: {len(node['args'])}")
    e: CostExpr | None = None
    for arg_idx, k in terms:
        term = _scaled(k, _len_expr_of(node, ir, arg_idx))
        e = term if e is None else _add(e, term)
    plus = spec.get("plus", 0)
    if plus > 0:
        e = _lit(plus) if e is None else _add(e, _lit(plus))
    if e is None:
        raise ValueError(f"empty window spec for {key}")
    return e


def _series_element() -> dict:
    """Element-class series node: 1 unit/bar, one f64 buffer."""
    return {
        "perBarCost": _lit(1),
        "totalCost": _bar_count(),
        "bytesCost": _mul(_lit(_SERIES_BYTES_PER_BAR), _bar_count()),
    }


def _scalar() -> dict:
    """Scalar node: fixed one-time unit, no per-bar work, fixed small footprint."""
    return {"perBarCost": _lit(0), "totalCost": _lit(1), "bytesCost": _lit(_SCALAR_BYTES)}


def cost_of(node: dict, ir: dict) -> dict:
    """The authoritative cost of one IR node (design §3/§4).

    Returns {"totalCost", "perBarCost", "bytesCost"} CostExpr dicts (single
    output per node — the estimator task handles cache-key grouping). Raises
    ValueError "unpriced operator: ..." for any op kind or function absent
    from the registry — surfaced as IR_UNPRICED_OPERATOR at admission, never
    a silent default charge.
    """
    op = node.get("op")
    if op == "source":
        return _series_element()
    if op == "const":
        return _scalar()
    if op == "input":
        input_id = node["inputId"]
        decl = next((d for d in ir["inputs"] if d["id"] == input_id), None)
        if decl is None:
            raise ValueError(f"unknown input id: {input_id}")
        return _series_element() if decl["type"] == "source" else _scalar()
    if op == "htf":
        # Resample (bucketing) + per-bucket aggregation + alignment: a few O(n)
        # passes per node. The per-timeframe resample is SHARED across nodes, so a
        # barCount-per-node charge upper-bounds real work (Phase 3 design §7).
        #
        # An INNER kernel adds O(K · Leff) over K <= barCount buckets, so charging
        # Leff per BASE bar upper-bounds it -- the compression factor n/K is
        # deliberate over-charge. Under-charging is the G9 failure mode;
        # over-charging is caution, and `real <= charged <= estimate` is the
        # shipped invariant (design §8). No new CostExpr kind, so cost_expr.py is
        # untouched by this feature.
        inner = node.get("inner")
        if inner is None:
            per_bar = _lit(_HTF_COST_PER_BAR)
        else:
            length_input_id = inner.get("lengthInputId")
            len_term = (
                # Priced at the input's declared MAXVAL, never its default:
                # admission has to hold for every value the setting can take.
                {"k": "inputBound", "id": length_input_id}
                if length_input_id is not None
                else _lit(int(inner["length"]))
            )
            per_bar = _add(_lit(_HTF_COST_PER_BAR), len_term)
        return {
            "perBarCost": per_bar,
            "totalCost": _mul(per_bar, _bar_count()),
            # Aligned out + shifted + kernelArr, each <= n entries, when inner runs.
            "bytesCost": _mul(
                _lit(_SERIES_BYTES_PER_BAR * (1 if inner is None else 3)), _bar_count()
            ),
        }
    if op in ("binop", "unop", "select", "hist", "nz"):
        return _series_element()
    if op == "call":
        key = f"{node['namespace']}.{node['function']}"
        spec = KERNEL_COST.get(key)
        if spec is None:
            raise ValueError(f"unpriced operator: {key}")
        per_bar = _call_per_bar(key, spec, node, ir)
        return {
            "perBarCost": per_bar,
            "totalCost": _mul(per_bar, _bar_count()),
            "bytesCost": _mul(_lit(_SERIES_BYTES_PER_BAR), _bar_count()),
        }
    if op == "scan":
        per_bar = _lit(scan_expr_size(node["expr"]))
        return {
            "perBarCost": per_bar,
            "totalCost": _mul(per_bar, _bar_count()),
            "bytesCost": _mul(_lit(_SERIES_BYTES_PER_BAR), _bar_count()),
        }
    # Malformed / future op kinds reject, never zero-charge (design §2).
    raise ValueError(f"unpriced operator: {op}")
