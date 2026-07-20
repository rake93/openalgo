"""Phase 0.2 — serializable cost-expression DSL (CostExpr) + deterministic evaluator.

Python mirror of openalgo-openscript/src/runtime/cost-expr.ts. Node dict shape is
identical to the TS serialization ({"k": "barCount"}, {"k": "mul", "a": ..., "b": ...},
{"k": "pow", "a": ..., "b": 2}) and the eval semantics MUST stay byte-identical —
shared fixtures assert cross-language equality.

Cross-language arithmetic contract (both sides are IEEE754 float64):
  - add/mul are plain f64 + / * (bit-identical in JS and Python).
  - max is Python max == JS Math.max — safe ONLY because operands are guaranteed
    non-NaN by the per-node guards (the two languages disagree on NaN ordering) and
    -0 leaves are normalized to +0 (the two languages disagree on which zero
    max(-0, 0) returns).
  - pow accepts ONLY a non-negative integer exponent (<= 64) and evaluates by
    REPEATED MULTIPLICATION (r = 1; b times: r *= base), NOT libm pow. Library pow
    (V8's fdlibm port vs the platform libm CPython links) diverges by 1 ulp on many
    inputs — including integer exponents once the exact result exceeds 2^53 (e.g.
    457^6, 123456789^2). A fold of single IEEE multiplies is bit-identical across
    languages BY CONSTRUCTION. v1 has no superlinear kernel, so integer exponents
    lose nothing; a future correctly-rounded sqrt node (IEEE sqrt IS bit-identical
    in both languages) will cover n^1.5 shapes when LC-4/KNN lands. JSON parses may
    deliver an integral float (2.0) — accepted; 1.5 is rejected.

Soundness rules (same as TS):
  - Leaves (lit, barCount, resolved inputBound/argConst) must be finite and
    non-negative, and -0 normalizes to +0. An unresolvable ref (non-finite
    resolution) RAISES; it never silently evaluates to 0/NaN.
  - Derived arithmetic MAY overflow to +inf — an infinite cost exceeds every cap
    and rejects the plan.
  - A NaN produced by arithmetic (only reachable via inf * 0) RAISES: NaN compares
    false against any cap, so it would otherwise silently PASS admission.
  - EVERY failure raises CostExprError (a ValueError) — malformed nodes (missing
    subkeys, unknown kinds), a JSON-bigint lit (Python parses arbitrary-precision
    ints where JS would give Infinity), unresolved refs, NaN arithmetic. Never a
    leaked KeyError/OverflowError/TypeError.
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

# Upper bound on a pow exponent — real cost formulas use tiny exponents (2..4); this
# caps the repeated-multiplication loop so deserialized garbage (b = 1e15) cannot
# stall admission. Identical constant in the TS mirror.
MAX_POW_EXPONENT = 64


class CostExprError(ValueError):
    """Single exception type for every malformed / unresolvable / non-finite
    CostExpr condition (mirrors the TS evaluator throwing plain Error uniformly)."""


@dataclass(frozen=True)
class CostCtx:
    """Admission context: barCount + resolvers. Mirrors the TS `CostCtx` interface."""

    bar_count: float
    input_bound: Callable[[str], float]  # conservative UPPER bound; non-finite = unresolvable
    arg_const: Callable[[int], float]  # resolvable literal arg value; non-finite = unresolvable


def eval_cost_expr(e: CostExpr, ctx: CostCtx) -> float:
    """Evaluate a CostExpr against an admission context. Pure and deterministic.

    Raises CostExprError (a ValueError) on: unresolvable refs, non-finite/negative
    leaves, NaN arithmetic results, non-integer/negative/oversized pow exponents,
    malformed nodes (missing subkeys, unknown kinds), and trees deeper than MAX_DEPTH.
    """
    return _eval_node(e, ctx, 0)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: Any) -> bool:
    """math.isfinite that treats an int too large for float64 (a JSON bigint —
    where JS would have parsed Infinity) as non-finite instead of raising."""
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _eval_node(e: Any, ctx: CostCtx, depth: int) -> float:
    if depth > MAX_DEPTH:
        raise CostExprError(f"CostExpr exceeds max depth {MAX_DEPTH}")
    if not isinstance(e, dict):
        # Also reached when a parent node is missing an a/b subkey (child = None).
        raise CostExprError(f"malformed CostExpr node: {_fmt(e)}")
    k = e.get("k")
    if k == "lit":
        v = e.get("v")
        if not _is_number(v) or not _finite(v):
            raise CostExprError(f"non-finite CostExpr lit: {_fmt(v)}")
        if v < 0:
            raise CostExprError(f"negative CostExpr lit: {_fmt(v)}")
        f = float(v)
        return 0.0 if f == 0 else f  # normalize -0 -> +0 (serialization + max parity)
    if k == "barCount":
        v = ctx.bar_count
        if not _is_number(v) or not _finite(v) or v < 0:
            raise CostExprError(f"invalid barCount: {_fmt(v)}")
        f = float(v)
        return 0.0 if f == 0 else f
    if k == "inputBound":
        ref = e.get("id")
        if not isinstance(ref, str):
            raise CostExprError("malformed CostExpr node: inputBound requires string id")
        v = ctx.input_bound(ref)
        if not _is_number(v) or not _finite(v):
            raise CostExprError(f"unresolved input bound: {ref}")
        if v < 0:
            raise CostExprError(f"negative input bound: {ref}")
        f = float(v)
        return 0.0 if f == 0 else f
    if k == "argConst":
        node_id = e.get("nodeId")
        if not _is_number(node_id):
            raise CostExprError("malformed CostExpr node: argConst requires numeric nodeId")
        v = ctx.arg_const(node_id)
        if not _is_number(v) or not _finite(v):
            raise CostExprError(f"unresolved arg const: {node_id}")
        if v < 0:
            raise CostExprError(f"negative arg const: {node_id}")
        f = float(v)
        return 0.0 if f == 0 else f
    if k == "add":
        a = _eval_node(e.get("a"), ctx, depth + 1)
        b = _eval_node(e.get("b"), ctx, depth + 1)
        return _check_arith(a + b, "add")
    if k == "mul":
        a = _eval_node(e.get("a"), ctx, depth + 1)
        b = _eval_node(e.get("b"), ctx, depth + 1)
        return _check_arith(a * b, "mul")
    if k == "max":
        # Operands are guaranteed non-NaN and -0-free by the guards above, so
        # max == JS Math.max bit-for-bit.
        return max(_eval_node(e.get("a"), ctx, depth + 1), _eval_node(e.get("b"), ctx, depth + 1))
    if k == "pow":
        b = e.get("b")
        # Integer-only: libm pow is NOT bit-identical across languages (see module
        # docstring). Accept an integral float (2.0) — JSON may deliver one.
        if (
            not _is_number(b)
            or (isinstance(b, float) and not b.is_integer())
            or b < 0
            or b > MAX_POW_EXPONENT
        ):
            raise CostExprError(f"invalid pow exponent: {_fmt(b)}")
        n = int(b)
        base = _eval_node(e.get("a"), ctx, depth + 1)
        # Repeated multiplication — n single IEEE f64 multiplies, bit-identical
        # cross-language by construction. With base >= 0 and non-NaN, the fold can
        # overflow to +inf (allowed) but can never produce NaN.
        r = 1.0
        for _ in range(n):
            r = _check_arith(r * base, "pow")
        return r
    raise CostExprError(f"unknown CostExpr kind: {_fmt(k)}")


def _check_arith(v: float, op: str) -> float:
    # +inf is allowed (overflow correctly exceeds caps and rejects); NaN is not —
    # NaN compares false against every cap and would silently pass admission.
    if math.isnan(v):
        raise CostExprError(f"CostExpr arithmetic produced NaN in {op}")
    return v


def _fmt(v: Any) -> str:
    """Format a value for error messages identically to JS String(v) for the
    common cases (NaN, Infinity, None/undefined, oversized JSON ints)."""
    if _is_number(v):
        if isinstance(v, float) and math.isnan(v):
            return "NaN"
        if not _finite(v):
            # inf float, or an int too large for float64 (JS JSON.parse -> Infinity)
            return "Infinity" if v > 0 else "-Infinity"
    if v is None:
        return "undefined"
    return str(v)
