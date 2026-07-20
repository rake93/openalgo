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

from services.openscript.runtime.cost_expr import CostExpr
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
