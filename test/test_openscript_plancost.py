"""Phase 0.2 PlanCost mode config — mirrors
openalgo-openscript/tests/plancost-config.test.ts.
"""

import math

import pytest

from services.openscript.runtime.cost_expr import CostCtx, eval_cost_expr
from services.openscript.runtime.plancost_config import plancost_mode


def test_plancost_mode_defaults_to_observe():
    assert plancost_mode() == "observe"


# --- CostExpr DSL + evaluator — mirrors openalgo-openscript/tests/cost-expr.test.ts ---

_CTX = CostCtx(
    bar_count=2000,
    input_bound=lambda i: 50 if i == "len" else math.nan,
    arg_const=lambda n: 20 if n == 3 else math.nan,
)


def test_cost_expr_evaluates_every_node_kind_deterministically():
    assert eval_cost_expr({"k": "lit", "v": 7}, _CTX) == 7
    assert eval_cost_expr({"k": "barCount"}, _CTX) == 2000
    assert eval_cost_expr({"k": "inputBound", "id": "len"}, _CTX) == 50
    assert eval_cost_expr({"k": "argConst", "nodeId": 3}, _CTX) == 20
    assert eval_cost_expr({"k": "mul", "a": {"k": "barCount"}, "b": {"k": "lit", "v": 8}}, _CTX) == 16000
    assert eval_cost_expr({"k": "add", "a": {"k": "lit", "v": 1}, "b": {"k": "barCount"}}, _CTX) == 2001
    assert eval_cost_expr({"k": "pow", "a": {"k": "barCount"}, "b": 2}, _CTX) == 4_000_000
    assert eval_cost_expr({"k": "max", "a": {"k": "lit", "v": 1}, "b": {"k": "lit", "v": 5}}, _CTX) == 5


def test_cost_expr_nested_composition_superlinear():
    e = {
        "k": "mul",
        "a": {"k": "pow", "a": {"k": "barCount"}, "b": 1.5},
        "b": {"k": "inputBound", "id": "len"},
    }
    assert eval_cost_expr(e, _CTX) == 2000.0**1.5 * 50


def test_cost_expr_unresolvable_ref_raises():
    with pytest.raises(ValueError, match="unresolved input bound: missing"):
        eval_cost_expr({"k": "inputBound", "id": "missing"}, _CTX)
    with pytest.raises(ValueError, match="unresolved arg const: 99"):
        eval_cost_expr({"k": "argConst", "nodeId": 99}, _CTX)


def test_cost_expr_overflow_to_infinity_is_allowed():
    huge = {"k": "pow", "a": {"k": "lit", "v": 1e308}, "b": 2}
    assert eval_cost_expr(huge, _CTX) == math.inf


def test_cost_expr_arithmetic_nan_raises():
    # inf * 0 = NaN in IEEE754 — must not slip through as "cost unknown"
    e = {
        "k": "mul",
        "a": {"k": "pow", "a": {"k": "lit", "v": 1e308}, "b": 2},
        "b": {"k": "lit", "v": 0},
    }
    with pytest.raises(ValueError, match="NaN"):
        eval_cost_expr(e, _CTX)


def test_cost_expr_nan_literal_raises():
    with pytest.raises(ValueError, match="NaN"):
        eval_cost_expr({"k": "lit", "v": math.nan}, _CTX)


def test_cost_expr_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown CostExpr kind"):
        eval_cost_expr({"k": "div", "a": 1}, _CTX)
