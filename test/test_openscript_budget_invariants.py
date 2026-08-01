"""Phase 0.2 Task 6 — runtime weighted-charger SECURITY invariants (Python
mirror of openalgo-openscript/tests/budget-invariants.test.ts).

The load-bearing rule that makes admission sound: the total the runtime charges
for a run (`budget.spent()`) must NEVER exceed the admission-time symbolic
estimate (`eval_cost_expr(estimate_plan_cost(ir)["totalOperations"], ctx)` with
`input_bound = decl.max`). These tests attack that invariant directly: weighted
ordering, construction/total OS4001, checkpoint OS4002, exact
`spent()`/`peak_bytes()`, `charged <= estimate` (incl. a 3-output macd), and the
input clamp that stops an oversized caller period from breaking it. The exact
pinned integers (spent == 12, peak_bytes == 96) are IDENTICAL to the TS side.
"""

import math
import time

import numpy as np
import pytest

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.budget import BudgetExceeded, OperationBudget
from services.openscript.runtime.cost_expr import CostCtx, eval_cost_expr
from services.openscript.runtime.executor import execute_ir
from services.openscript.runtime.plancost import (
    estimate_plan_cost,
    per_node_weights,
    runtime_cost_ctx,
)


@pytest.fixture(scope="module")
def dataset():
    rng = np.random.default_rng(42)
    n = 300
    close = 100 + np.cumsum(rng.standard_normal(n))
    high = close + np.abs(rng.standard_normal(n))
    low = close - np.abs(rng.standard_normal(n))
    open_ = close + rng.standard_normal(n) * 0.3
    volume = 1000 + rng.integers(0, 500, n).astype(float)
    return {
        "open": open_.astype(float),
        "high": high.astype(float),
        "low": low.astype(float),
        "close": close.astype(float),
        "volume": volume.astype(float),
    }


def _limits(**overrides):
    merged = dict(SCRIPT_LIMITS)
    merged.update(overrides)
    return merged


def _ir(nodes, outputs=None, inputs=None):
    """Wrap hand nodes in an admissible IRProgram (valid header)."""
    return {
        "version": 1,
        "compilerVersion": "openscript-1.0",
        "sourceHash": "test",
        "header": {
            "major": 1,
            "minor": 0,
            "compilerVersion": "openscript-1.0",
            "requiredFeatures": [],
            "numericMode": "f64-strict",
        },
        "declaration": {"name": "Cost", "overlay": False},
        "inputs": inputs or [],
        "nodes": nodes,
        "outputs": outputs or [],
        "meta": {"warmupBars": 0, "spans": {}},
    }


def _compile_ir(source):
    result = openscript.compile(source)
    # G9's OS5008 is ADVISORY and fires on exactly the shape these cases exist
    # to exercise -- an input-bound window with no `maxval`. Declaring one would
    # destroy the subject, so it is filtered here rather than avoided. Anything
    # else must still be empty; OS5008 itself is covered by
    # test_openscript_window_bounds.py.
    unexpected = [d for d in result.diagnostics if d.code != "OS5008"]
    assert unexpected == [], f"unexpected diagnostics: {unexpected}"
    assert result.ir is not None
    return result.ir


def _admission_ctx(ir, bars):
    """Admission-side ctx: input_bound = decl.max (fallback maximumLookback)."""
    decls = {d["id"]: d for d in ir.get("inputs", [])}

    def input_bound(id_):
        d = decls.get(id_)
        if d is not None and d.get("type") in ("integer", "float") and "max" in d:
            return d["max"]
        return SCRIPT_LIMITS["maximumLookback"]

    def arg_const(node_id):
        nodes = ir["nodes"]
        if 0 <= node_id < len(nodes):
            n = nodes[node_id]
            v = n.get("value")
            if n.get("op") == "const" and isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return math.nan

    return CostCtx(bar_count=bars, input_bound=input_bound, arg_const=arg_const)


# 4-bar fully-controlled dataset for the exactly-hand-checkable cases.
_TINY = {
    "time": np.asarray([0.0, 1.0, 2.0, 3.0]),
    "open": np.asarray([1.0, 2.0, 3.0, 4.0]),
    "high": np.asarray([2.0, 3.0, 4.0, 5.0]),
    "low": np.asarray([0.5, 1.5, 2.5, 3.5]),
    "close": np.asarray([1.0, 2.0, 3.0, 4.0]),
    "volume": np.asarray([10.0, 20.0, 30.0, 40.0]),
}

# close(0) -> (close+close)(1) -> -(close+close)(2): three element nodes, one f64
# buffer each. Pure DAG, no kernel.
_ELEMENT_IR = _ir(
    [
        {"id": 0, "op": "source", "source": "close"},
        {"id": 1, "op": "binop", "operator": "+", "args": [0, 0]},
        {"id": 2, "op": "unop", "operator": "-", "arg": 1},
    ],
    [{"kind": "plot", "nodeId": 2, "title": "x", "style": {"color": "#ffffff"}}],
)


# ── weighted ordering ──────────────────────────────────────────────────────


def test_window_node_charges_more_than_elementwise():
    ir = _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 50},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
            {"id": 3, "op": "binop", "operator": "+", "args": [0, 0]},
        ]
    )
    w = per_node_weights(ir, runtime_cost_ctx(ir, {}, 1000))
    assert w[2] == 51_000  # 50·1000 compute + 1000 projection
    assert w[3] == 1000
    assert w[2] > w[3]


# ── OS4001 / OS4002 ────────────────────────────────────────────────────────


def test_construction_os4001_on_perbar_over_cap():
    ir = _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 50},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
        ]
    )
    ctx = runtime_cost_ctx(ir, {}, 1000)
    with pytest.raises(BudgetExceeded) as ei:
        OperationBudget(ir, ctx, _limits(maximumOperationsPerBar=10))
    assert ei.value.code == "OS4001"


def test_step_os4001_on_total_over_cap():
    ctx = runtime_cost_ctx(_ELEMENT_IR, {}, len(_TINY["close"]))  # weights [4,4,4]
    budget = OperationBudget(_ELEMENT_IR, ctx, _limits(maximumTotalOperations=6))
    with pytest.raises(BudgetExceeded) as ei:
        execute_ir(_ELEMENT_IR, _TINY, {}, budget=budget)
    assert ei.value.code == "OS4001"


def test_checkpoint_os4002_when_time_budget_spent():
    ir = _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 50},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
        ]
    )
    budget = OperationBudget(ir, runtime_cost_ctx(ir, {}, 1000), _limits(maximumExecutionMilliseconds=0))
    # perf_counter advances past 0ms immediately; burn a hair to be safe.
    start = time.perf_counter()
    while (time.perf_counter() - start) * 1000 < 2:
        pass
    with pytest.raises(BudgetExceeded) as ei:
        budget.checkpoint()
    assert ei.value.code == "OS4002"


# ── exact spent() and peak_bytes() ─────────────────────────────────────────


def test_spent_exact():
    budget = OperationBudget(_ELEMENT_IR, runtime_cost_ctx(_ELEMENT_IR, {}, len(_TINY["close"])))
    execute_ir(_ELEMENT_IR, _TINY, {}, budget=budget)
    assert budget.spent() == 12  # 3 element nodes · 4 bars — SAME integer as TS


def test_peak_bytes_exact():
    budget = OperationBudget(_ELEMENT_IR, runtime_cost_ctx(_ELEMENT_IR, {}, len(_TINY["close"])))
    execute_ir(_ELEMENT_IR, _TINY, {}, budget=budget)
    assert budget.peak_bytes() == 96  # 3 series · 4 bars · 8 bytes — SAME integer as TS


# ── CRUX — charged <= estimate ─────────────────────────────────────────────


def _charged_vs_estimate(ir, dataset, inputs):
    bars = len(dataset["close"])
    budget = OperationBudget(ir, runtime_cost_ctx(ir, inputs, bars))
    execute_ir(ir, dataset, inputs, budget=budget)
    estimate = eval_cost_expr(estimate_plan_cost(ir)["totalOperations"], _admission_ctx(ir, bars))
    return budget.spent(), estimate


def test_macd_charged_equals_estimate(dataset):
    ir = _compile_ir("[m, s, h] = ta.macd(close, 12, 26, 9)\nplot(m)\nplot(s)\nplot(h)")
    spent, estimate = _charged_vs_estimate(ir, dataset, {})
    assert spent <= estimate
    assert spent == estimate


def test_input_at_max_charged_equals_estimate(dataset):
    ir = _compile_ir('len = input.int(200, "Length", maxval=200)\nplot(ta.sma(close, len))')
    spent, estimate = _charged_vs_estimate(ir, dataset, {})
    assert spent <= estimate
    assert spent == estimate


def test_input_below_max_charged_strictly_less(dataset):
    ir = _compile_ir('len = input.int(10, "Length", maxval=200)\nplot(ta.sma(close, len))')
    spent, estimate = _charged_vs_estimate(ir, dataset, {})
    assert spent <= estimate
    assert spent < estimate


def test_elementwise_charged_le_estimate(dataset):
    ir = _compile_ir("plot(close + close - open)")
    spent, estimate = _charged_vs_estimate(ir, dataset, {})
    assert spent <= estimate


# ── CRUX — input clamp ─────────────────────────────────────────────────────


def test_oversized_period_is_clamped(dataset):
    ir = _compile_ir('len = input.int(50, "Length", maxval=200)\nplot(ta.sma(close, len))')
    bars = len(dataset["close"])
    # Caller supplies 999999 (>> maxval 200) — clamp must charge as if 200.
    budget = OperationBudget(ir, runtime_cost_ctx(ir, {"len": 999999}, bars))
    for node in ir["nodes"]:  # charge only; do not run the kernel with an absurd period
        budget.step(node)
    assert ir["inputs"][0]["id"]

    estimate = eval_cost_expr(estimate_plan_cost(ir)["totalOperations"], _admission_ctx(ir, bars))
    unclamped_ctx = CostCtx(bar_count=bars, input_bound=lambda _id: 999999, arg_const=lambda _n: math.nan)
    unclamped = eval_cost_expr(estimate_plan_cost(ir)["totalOperations"], unclamped_ctx)

    assert budget.spent() == estimate  # clamped to maxval → equals the max-bounded estimate
    assert budget.spent() <= estimate
    assert budget.spent() < unclamped  # proves 999999 was NOT used


def _line_values(outputs):
    lines = [o for o in outputs if o["kind"] == "line"]
    assert lines, "no line output"
    return lines[0]["values"]


# ── F1 (review): min>max must never exceed the admission upper bound ────────


def test_f1_minval_above_maxval_clamps_to_maxval(dataset):
    ir = _compile_ir('len = input.int(100, "Length", minval=500, maxval=200)\nplot(ta.sma(close, len))')
    bars = len(dataset["close"])
    ctx = runtime_cost_ctx(ir, {}, bars)
    input_id = ir["inputs"][0]["id"]
    assert ctx.input_bound(input_id) == 200  # NOT 500 (minval)
    budget = OperationBudget(ir, ctx)
    for node in ir["nodes"]:
        budget.step(node)
    estimate = eval_cost_expr(estimate_plan_cost(ir)["totalOperations"], _admission_ctx(ir, bars))
    assert budget.spent() <= estimate
    assert budget.spent() == estimate


def test_f1_minval_above_maximumlookback_no_maxval(dataset):
    minv = SCRIPT_LIMITS["maximumLookback"] + 5000
    ir = _compile_ir(f'len = input.int(100, "Length", minval={minv})\nplot(ta.sma(close, len))')
    bars = len(dataset["close"])
    ctx = runtime_cost_ctx(ir, {}, bars)
    input_id = ir["inputs"][0]["id"]
    assert ctx.input_bound(input_id) == SCRIPT_LIMITS["maximumLookback"]  # hi=fallback, min>hi → hi
    budget = OperationBudget(ir, ctx)
    for node in ir["nodes"]:
        budget.step(node)
    estimate = eval_cost_expr(estimate_plan_cost(ir)["totalOperations"], _admission_ctx(ir, bars))
    assert budget.spent() <= estimate


# ── F2 (review): execution uses the clamped period, not the raw caller value ─


def test_f2_execution_uses_clamped_period(dataset):
    ir = _compile_ir('len = input.int(50, "Length", maxval=200)\nplot(ta.sma(close, len))')
    clamped = _line_values(execute_ir(ir, dataset, {"len": 999999}))  # must behave as len=200
    ref = _line_values(execute_ir(ir, dataset, {"len": 200}))
    np.testing.assert_array_equal(clamped, ref)
    # …and NOT the degenerate all-NaN period-999999 result.
    assert np.isfinite(clamped).any()


# ── F3 (review): Python parity hardening — malformed IR must not raise ──────


def test_f3_malformed_ir_does_not_crash():
    # node id >= len(nodes); a bigint const arg; a null max — TS returns clean
    # NaN/fallback for all of these, so the Python mirror must not raise.
    ir = _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 999, "op": "const", "value": 10**400},  # out-of-range id + JSON-bigint value
        ],
        inputs=[{"id": "p", "type": "integer", "label": "P", "defaultValue": 5, "max": None}],
    )
    ctx = runtime_cost_ctx(ir, {}, 100)
    assert math.isnan(ctx.arg_const(999))  # bigint const → NaN (not OverflowError)
    # null max → falls back to maximumLookback hi WITHOUT raising TypeError; the
    # default (5) clamps to itself under that hi (proving the null-max path ran).
    assert ctx.input_bound("p") == 5.0
    # per_node_weights must not IndexError on the out-of-range node id.
    w = per_node_weights(ir, ctx)
    assert isinstance(w, list)


# ── review: null numeric input == default (TS↔Python parity) ────────────────


def test_null_input_equals_default_parity(dataset):
    # An explicit None must resolve to the declared default, IDENTICALLY to TS's
    # `inputs[id] ?? default`. `{"len": None}` must charge and execute exactly as
    # `{}` (omitted) — NOT the maxval fallback (200) that pre-fix float(None) hit.
    ir = _compile_ir('len = input.int(50, "Length", maxval=200)\nplot(ta.sma(close, len))')
    # spent() is data-independent — pin it at the SAME fixed barCount as the engine.
    null_budget = OperationBudget(ir, runtime_cost_ctx(ir, {"len": None}, 300))
    def_budget = OperationBudget(ir, runtime_cost_ctx(ir, {}, 300))
    for node in ir["nodes"]:
        null_budget.step(node)
        def_budget.step(node)
    assert null_budget.spent() == def_budget.spent()
    assert null_budget.spent() == 15601  # 300 + 1 + (50·300 + 300); SAME integer as TS
    null_out = _line_values(execute_ir(ir, dataset, {"len": None}))
    def_out = _line_values(execute_ir(ir, dataset, {}))
    np.testing.assert_array_equal(null_out, def_out)
