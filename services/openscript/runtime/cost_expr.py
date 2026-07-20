"""Phase 0.2 — serializable cost-expression DSL (CostExpr) + deterministic evaluator.

Python mirror of openalgo-openscript/src/runtime/cost-expr.ts. Node dict shape is
identical to the TS serialization ({"k": "barCount"}, {"k": "mul", "a": ..., "b": ...},
{"k": "pow", "a": ..., "b": 2}) and the eval semantics MUST stay byte-identical —
shared fixtures assert cross-language equality.

Cross-language arithmetic contract (both sides are IEEE754 float64):
  - add/mul are plain f64 + / * (bit-identical in JS and Python).
  - max is Python max == JS Math.max — safe ONLY because operands are guaranteed
    non-NaN by the per-node guards (the two languages disagree on NaN ordering).
  - pow is Python ** == JS ** with a FINITE, NON-NEGATIVE numeric exponent (enforced).
    Restricting to base >= 0 and finite exponent >= 0 removes every case where the two
    languages' ** semantics diverge (negative base -> complex vs NaN, 0 ** -1 ->
    ZeroDivisionError vs Infinity, 1 ** Infinity -> 1.0 vs NaN). Python float ** raises
    OverflowError where JS returns Infinity, so overflow is mapped to +inf here.

Soundness rules (same as TS):
  - Leaves (lit, barCount, resolved inputBound/argConst) must be finite and
    non-negative. An unresolvable ref (non-finite resolution) RAISES; it never
    silently evaluates to 0/NaN.
  - Derived arithmetic MAY overflow to +inf — an infinite cost exceeds every cap
    and rejects the plan.
  - A NaN produced by arithmetic (only reachable via inf * 0) RAISES: NaN compares
    false against any cap, so it would otherwise silently PASS admission.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Same node dict shape as the TS `CostExpr` union (keys stay camelCase: "nodeId").
CostExpr = dict[str, Any]

# Cost trees are compiler-generated and bounded by IR size; 512 is far above any real
# tree, and an explicit limit keeps deep-garbage behaviour identical cross-language
# (Python RecursionError vs JS RangeError trigger at very different depths).
MAX_DEPTH = 512


@dataclass(frozen=True)
class CostCtx:
    """Admission context: barCount + resolvers. Mirrors the TS `CostCtx` interface."""

    bar_count: float
    input_bound: Callable[[str], float]  # conservative UPPER bound; non-finite = unresolvable
    arg_const: Callable[[int], float]  # resolvable literal arg value; non-finite = unresolvable


def eval_cost_expr(e: CostExpr, ctx: CostCtx) -> float:
    """Evaluate a CostExpr against an admission context. Pure and deterministic.

    Raises ValueError on: unresolvable refs, non-finite/negative leaves, NaN
    arithmetic results, invalid pow exponents, unknown node kinds, and trees
    deeper than MAX_DEPTH.
    """
    return _eval_node(e, ctx, 0)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _eval_node(e: CostExpr, ctx: CostCtx, depth: int) -> float:
    if depth > MAX_DEPTH:
        raise ValueError(f"CostExpr exceeds max depth {MAX_DEPTH}")
    k = e.get("k") if isinstance(e, dict) else None
    if k == "lit":
        v = e.get("v")
        if not _is_number(v) or not math.isfinite(v):
            raise ValueError(f"non-finite CostExpr lit: {_fmt(v)}")
        if v < 0:
            raise ValueError(f"negative CostExpr lit: {_fmt(v)}")
        return float(v)
    if k == "barCount":
        v = ctx.bar_count
        if not _is_number(v) or not math.isfinite(v) or v < 0:
            raise ValueError(f"invalid barCount: {_fmt(v)}")
        return float(v)
    if k == "inputBound":
        ref = e["id"]
        v = ctx.input_bound(ref)
        if not _is_number(v) or not math.isfinite(v):
            raise ValueError(f"unresolved input bound: {ref}")
        if v < 0:
            raise ValueError(f"negative input bound: {ref}")
        return float(v)
    if k == "argConst":
        node_id = e["nodeId"]
        v = ctx.arg_const(node_id)
        if not _is_number(v) or not math.isfinite(v):
            raise ValueError(f"unresolved arg const: {node_id}")
        if v < 0:
            raise ValueError(f"negative arg const: {node_id}")
        return float(v)
    if k == "add":
        return _check_arith(_eval_node(e["a"], ctx, depth + 1) + _eval_node(e["b"], ctx, depth + 1), "add")
    if k == "mul":
        return _check_arith(_eval_node(e["a"], ctx, depth + 1) * _eval_node(e["b"], ctx, depth + 1), "mul")
    if k == "max":
        # Operands are guaranteed non-NaN by the guards above, so max == JS Math.max.
        return max(_eval_node(e["a"], ctx, depth + 1), _eval_node(e["b"], ctx, depth + 1))
    if k == "pow":
        b = e.get("b")
        if not _is_number(b) or not math.isfinite(b) or b < 0:
            raise ValueError(f"invalid pow exponent: {_fmt(b)}")
        a = _eval_node(e["a"], ctx, depth + 1)
        try:
            r = a ** float(b)
        except OverflowError:
            # JS ** overflows to Infinity; Python float ** raises. Match JS: with
            # base >= 0 and exponent >= 0 the overflow sign is always positive.
            r = math.inf
        return _check_arith(r, "pow")
    raise ValueError(f"unknown CostExpr kind: {_fmt(k)}")


def _check_arith(v: float, op: str) -> float:
    # +inf is allowed (overflow correctly exceeds caps and rejects); NaN is not —
    # NaN compares false against every cap and would silently pass admission.
    if math.isnan(v):
        raise ValueError(f"CostExpr arithmetic produced NaN in {op}")
    return v


def _fmt(v: Any) -> str:
    """Format a value for error messages identically to JS String(v) for the
    common cases (NaN, Infinity, None/undefined)."""
    if _is_number(v):
        if isinstance(v, float) and math.isnan(v):
            return "NaN"
        if v == math.inf:
            return "Infinity"
        if v == -math.inf:
            return "-Infinity"
    if v is None:
        return "undefined"
    return str(v)
