"""Phase 0.2 — symbolic PlanCost estimator (design spec §5).

Python mirror of openalgo-openscript/src/runtime/plancost.ts — the single
authoritative "recompute from IR" source. The admission gate calls
estimate_plan_cost(ir) and NEVER trusts a client-supplied meta.planCost.

The estimator walks the post-CSE IR DAG once, in node-id order, and sums
per-node cost as serializable CostExpr formula trees — NOT numbers. Concrete
numbers are produced only later, at admission, via eval_cost_expr when
barCount / input bounds are known. MUST stay byte-identical with the TS
mirror — both languages iterate identically and fold identically, so the
emitted trees are equal as JSON.

Cache-key grouping (mirrors the runtime kernel cache, executor._call): the
runtime keys MULTI-OUTPUT kernel results by "facade#args" and computes the
kernel ONCE, slicing one block per output node. Grouping is gated on the
multi-output surface (FIELD_COUNTS) because that is exactly what the TS
executor caches (its `if (fields)` branch): a single-output kernel recomputes
at EVERY call node there, so two identical single-output nodes must charge
two computes — grouping them would under-estimate and break the
charged <= estimate admission invariant. (Unreachable via compiler IR — CSE
merges identical single-output calls — but admission also accepts hand IR,
and the estimate must stay an upper bound for BOTH runtimes; the Python
executor caches all ta.* results, for which each-singleton-charged is the
conservative side.) Charges:
  - multi-output kernels: the kernel compute ONCE per
    "namespace.function#args" group, at the first (representative) node —
    so a 3-output macd costs kernel + 3*barCount, not 3*kernel;
  - every other call node is its own singleton group (its own compute);
  - one O(1) projection (1 unit/bar) per output node — including the
    representative;
  - ALL the kernel's output blocks once per group (FIELD_COUNTS; the kernel
    allocates every block even if only some outputs are referenced).

An unpriced operator raises (same path as cost_of/cost_family_of) — surfaced
as IR_UNPRICED_OPERATOR at admission, never a silent default.
"""

from __future__ import annotations

import math

from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.cost_expr import CostCtx, CostExpr, eval_cost_expr
from services.openscript.runtime.operator_cost import (
    COST_MODEL_VERSION,
    cost_family_of,
    cost_of,
)

# Output-block counts of the multi-output kernels, keyed by IR function name.
# Everything absent is single-output (1 block). This table is ALSO the
# grouping gate: only functions listed here share a compute group, mirroring
# the runtime cache surface (the TS executor caches exactly the TA_FIELDS
# kernels). Pinned against the TS runtime's TA_FIELDS field-order table by the
# engine conformance test (openalgo-openscript/tests/plancost.test.ts);
# mirrored EXACTLY here — the Python runtime slices kernel tuples by index and
# has no field table of its own, so the platform test pins this table against
# the TS values directly.
FIELD_COUNTS: dict[str, int] = {
    "macd": 3,
    "bb": 3,
    "keltner": 3,
    "donchian": 3,
    "ppo": 3,
    "adx": 3,
    "cpr": 3,
    "stochastic": 2,
    "supertrend": 2,
    "tsi": 2,
    "ichimoku": 5,
    "pivotpoints": 7,
}

# Small fixed execution base charged once per plan (design §5; calibrated
# later). Identical constant in the TS mirror.
_FIXED_BASE_BYTES = 4096

# Every materialized series buffer is 8 bytes (f64) per bar (design §5).
_SERIES_BYTES_PER_BAR = 8


def _lit(v: float) -> CostExpr:
    return {"k": "lit", "v": v}


def _bar_count() -> CostExpr:
    return {"k": "barCount"}


def _add(a: CostExpr, b: CostExpr) -> CostExpr:
    return {"k": "add", "a": a, "b": b}


def _mul(a: CostExpr, b: CostExpr) -> CostExpr:
    return {"k": "mul", "a": a, "b": b}


def _sum(exprs: list[CostExpr]) -> CostExpr:
    """Deterministic right fold: _sum([a, b, c]) = add(a, add(b, c)) — no
    trailing lit(0) for non-empty lists, lit(0) for empty ones. Both languages
    fold identically so the trees stay byte-identical."""
    if not exprs:
        return _lit(0)
    acc = exprs[-1]
    for i in range(len(exprs) - 2, -1, -1):
        acc = _add(exprs[i], acc)
    return acc


def _blocks_of(fn: str) -> int:
    """Buffers a kernel allocates for one compute: its output block count."""
    return FIELD_COUNTS.get(fn, 1)


def estimate_plan_cost(ir: dict) -> dict:
    """Estimate the symbolic plan cost of an IR program (design §5).

    Walks ir["nodes"] in id order, emitting contributions deterministically;
    raises ValueError "unpriced operator: ..." on any operator absent from the
    cost registry.
    """
    total: list[CostExpr] = []
    per_bar: list[CostExpr] = []
    bytes_: list[CostExpr] = []
    buckets: dict[str, list[CostExpr]] = {"element": [], "window": [], "scan": [], "call": []}
    seen: set[str] = set()

    for node in ir["nodes"]:
        op = node.get("op")
        if op != "call":
            c = cost_of(node, ir)
            per_bar.append(c["perBarCost"])
            total.append(c["totalCost"])
            bytes_.append(c["bytesCost"])
            buckets["scan" if op == "scan" else "element"].append(c["totalCost"])
            continue
        # .get so a malformed node (missing namespace/function) flows into the
        # SAME clean unpriced-operator ValueError as TS, never a bare KeyError.
        namespace = node.get("namespace")
        fn = node.get("function")
        family = cost_family_of(namespace, fn)
        bucket = "window" if family == "window" else "call"
        # Only multi-output kernels share a compute group — that is exactly the
        # runtime cache surface. Single-output calls are singleton groups: the
        # TS executor recomputes them at every node, so identical duplicates
        # must each charge their own compute (upper bound; see module doc).
        multi_output = fn in FIELD_COUNTS
        key = f"{namespace}.{fn}#{','.join(str(a) for a in node['args'])}"
        if not multi_output or key not in seen:
            # Compute-once: the kernel's work is charged at the representative only.
            if multi_output:
                seen.add(key)
            c = cost_of(node, ir)
            per_bar.append(c["perBarCost"])
            total.append(c["totalCost"])
            buckets[bucket].append(c["totalCost"])
            # Group bytes: the kernel allocates ALL its output blocks, referenced
            # or not — charged once per group (NOT cost_of's single-block bytesCost).
            # NOTE: crossover-class kernels return a bool block (1 B/bar) that is
            # transiently held alongside the converted float64 series, so true
            # peak can exceed the charged 8*barCount by <= 1*barCount for one
            # conversion at a time — absorbed by _FIXED_BASE_BYTES below ~4k
            # bars and accounted for in the Task 9 memory-constant calibration.
            for _ in range(_blocks_of(fn)):
                bytes_.append(_mul(_lit(_SERIES_BYTES_PER_BAR), _bar_count()))
        # Projection: every output node (incl. the representative) is an O(1)/bar
        # slice of the cached kernel result.
        per_bar.append(_lit(1))
        total.append(_bar_count())
        buckets[bucket].append(_bar_count())

    return {
        "costModelVersion": COST_MODEL_VERSION,
        "totalOperations": _sum(total),
        "perBarOperations": _sum(per_bar),
        "estimatedPeakBytes": _sum([*bytes_, _lit(_FIXED_BASE_BYTES)]),
        "breakdown": {
            "element": _sum(buckets["element"]),
            "window": _sum(buckets["window"]),
            "scan": _sum(buckets["scan"]),
            "call": _sum(buckets["call"]),
        },
        # v1 measures none of these — the literal string "n/a", NEVER 0 (a zero
        # would read as "measured and found empty").
        "dims": {
            "eventChecks": "n/a",
            "objectLifecycleChecks": "n/a",
            "requestedDataPoints": "n/a",
        },
    }


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _safe_finite(v) -> bool:
    """math.isfinite that returns False (never raises) for a JSON-bigint int too
    large for float64 (where JS JSON.parse would have produced Infinity) — so the
    Python side matches the TS non-crashing behaviour on malformed IR."""
    try:
        return math.isfinite(v)
    except (OverflowError, TypeError, ValueError):
        return False


def declared_max(decl: dict) -> float | None:
    """A numeric input's declared finite max, or None if none is declared —
    mirror of the TS `declaredMax`."""
    v = decl.get("max")
    return float(v) if _is_number(v) and _safe_finite(v) else None


def clamp_numeric_input(decl: dict, raw, hi: float) -> float:
    """UPPER-authoritative clamp of a raw caller value to a numeric input's
    bounds: min(max(v, lo), hi) — mirror of the TS `clampNumericInput`. Ordering
    the upper clamp LAST is load-bearing: the result can NEVER exceed `hi`, even
    for a compile-legal but degenerate min>max (a naïve "clamp-up-to-min last"
    would return min>hi and break charged <= estimate). Non-numeric/non-finite
    raw resolves to `hi`. Shared by input_bound (the CHARGE) and the executor's
    input resolution (the EXECUTED value) so declared-max numeric inputs clamp
    identically — no drift between what is charged and what actually runs."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = hi
    if not _safe_finite(v):
        v = hi
    raw_lo = decl.get("min")
    lo = float(raw_lo) if _is_number(raw_lo) and _safe_finite(raw_lo) else float("-inf")
    return min(max(v, lo), hi)


def per_node_weights(ir: dict, ctx: CostCtx) -> list[float]:
    """Concrete per-node weight (logical work units) charged by the runtime
    budget, indexed by node id — Python mirror of the TS `perNodeWeights`.

    Reuses the EXACT same cache-key grouping walk as estimate_plan_cost (a
    multi-output kernel's compute is attributed to its representative node once;
    every output node carries one O(1) projection), so BY CONSTRUCTION

        sum(per_node_weights(ir, ctx)) == eval_cost_expr(
            estimate_plan_cost(ir)["totalOperations"], ctx)

    under the SAME ctx (all logical units are non-negative integers well below
    2**53, so float64 addition is exact and re-association loses nothing).
    Feeding the budget these weights with a RUNTIME ctx whose window lengths are
    clamped to [min, max] is what makes charged <= estimate (estimate uses
    input_bound = max) true by construction — every term is monotonic
    non-decreasing in its input_bound. The invariants test pins the sum equality.
    """
    nodes = ir["nodes"]
    weights: list[float] = [0.0] * len(nodes)
    projection = eval_cost_expr(_bar_count(), ctx)  # one O(1)/bar slice per output node
    seen: set[str] = set()

    for node in nodes:
        op = node.get("op")
        if op != "call":
            w = eval_cost_expr(cost_of(node, ir)["totalCost"], ctx)
        else:
            namespace = node.get("namespace")
            fn = node.get("function")
            # Only multi-output kernels share a compute group (same gate as the
            # estimator); single-output calls each charge their own compute.
            multi_output = fn in FIELD_COUNTS
            key = f"{namespace}.{fn}#{','.join(str(a) for a in node['args'])}"
            w = 0.0
            if not multi_output or key not in seen:
                if multi_output:
                    seen.add(key)
                w += eval_cost_expr(cost_of(node, ir)["totalCost"], ctx)
            w += projection
        # Guard the index write so a malformed hand IR (id >= len(nodes)) does
        # not IndexError — TS silently grows the array; here we skip. The
        # compiler never emits such IR (id == index); admission will gate it.
        nid = node.get("id")
        if isinstance(nid, int) and 0 <= nid < len(weights):
            weights[nid] = w
    return weights


def runtime_cost_ctx(ir: dict, inputs: dict, bar_count: int, limits=SCRIPT_LIMITS) -> CostCtx:
    """Build the RUNTIME CostCtx for a concrete execution (design §7) — Python
    mirror of the TS `runtimeCostCtx`.

    Unlike the admission ctx (input_bound -> declared max), this resolves each
    input-bound window length to the input's ACTUAL value CLAMPED to its
    declared [min, max]:
      - bar_count = dataset length;
      - input_bound(id) = clamp(actual or default, min, max) — when the decl has
        NO max, the upper clamp is maximumLookback (the SAME fallback admission
        uses). The clamp is SECURITY-CRITICAL: it stops an oversized caller
        period (e.g. 999999) from charging more than the max-bounded admission
        estimate, preserving charged <= estimate.
      - arg_const(node_id) = a numeric const node's value, else non-finite
        (identical to admission — const args do not vary at runtime).
    """
    decls = {d["id"]: d for d in ir.get("inputs", [])}
    nodes = ir["nodes"]
    fallback = limits["maximumLookback"]

    def input_bound(id_: str) -> float:
        decl = decls.get(id_)
        if decl is None or decl.get("type") not in ("integer", "float"):
            return math.nan
        # No max declared (or a null/garbage max) → conservative fallback. The
        # clamp is upper-authoritative, so min>max still yields hi.
        max_ = declared_max(decl)
        hi = max_ if max_ is not None else float(fallback)
        raw = inputs.get(id_)
        if raw is None:  # mirror TS `inputs[id] ?? default`: explicit null == absent
            raw = decl.get("defaultValue")
        return clamp_numeric_input(decl, raw, hi)

    def arg_const(node_id) -> float:
        if isinstance(node_id, int) and 0 <= node_id < len(nodes):
            node = nodes[node_id]
            val = node.get("value")
            # _safe_finite (not bare math.isfinite) so a JSON-bigint const →
            # clean NaN, matching TS (JSON.parse would give Infinity → NaN).
            if node.get("op") == "const" and _is_number(val) and _safe_finite(val):
                return float(val)
        return math.nan

    return CostCtx(bar_count=bar_count, input_bound=input_bound, arg_const=arg_const)
