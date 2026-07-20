"""Phase 0.2 Task 7 — admission PlanCost resolver SECURITY tests (Python mirror
of openalgo-openscript/tests/admit-plancost.test.ts).

The load-bearing property: resolve_plan_cost RECOMPUTES the plan cost from the IR
nodes (via estimate_plan_cost) and NEVER trusts ir["meta"]["planCost"] for its
decision. A forged TINY meta.planCost on an expensive IR is still rejected; a
forged HUGE meta.planCost on a cheap IR is still admitted. These tests attack
that directly, plus each budget code (dataset/perBar/total/memory), the
unpriced-operator catch, observe-vs-enforce, and a wiring smoke test proving the
resolver is on the real execute_ir path (not just unit-tested).
"""

import numpy as np
import pytest

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.admit import IRAdmissionError, resolve_plan_cost
from services.openscript.runtime.executor import execute_ir
from services.openscript.runtime.plancost import admission_cost_ctx, runtime_cost_ctx


def _limits(**overrides):
    merged = dict(SCRIPT_LIMITS)
    merged.update(overrides)
    return merged


def _ir(nodes, outputs=None, inputs=None, plan_cost=None):
    meta = {"warmupBars": 0, "spans": {}}
    if plan_cost is not None:
        meta["planCost"] = plan_cost
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
        "meta": meta,
    }


def _compile_ir(source):
    result = openscript.compile(source)
    assert result.diagnostics == [], f"unexpected diagnostics: {result.diagnostics}"
    assert result.ir is not None
    return result.ir


def _lit(v):
    return {"k": "lit", "v": v}


def _fake_plan_cost(total, per_bar, byts):
    """A hand-forged embedded planCost — proves the resolver NEVER reads it for a
    decision (only as embeddedMismatch telemetry)."""
    return {
        "costModelVersion": 1,
        "totalOperations": _lit(total),
        "perBarOperations": _lit(per_bar),
        "estimatedPeakBytes": _lit(byts),
        "breakdown": {"element": _lit(0), "window": _lit(0), "scan": _lit(0), "call": _lit(0)},
        "dims": {"eventChecks": "n/a", "objectLifecycleChecks": "n/a", "requestedDataPoints": "n/a"},
    }


def _plot(node_id):
    return {"kind": "plot", "nodeId": node_id, "title": "x", "style": {"color": "#ffffff"}}


def _sma50(plan_cost=None):
    """ta.sma(close, 50): @barCount=100 total=5201, perBar=52, bytes=5704."""
    return _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 50},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
        ],
        [_plot(2)],
        plan_cost=plan_cost,
    )


def _cheap(plan_cost=None):
    """source(0) only — total=barCount, perBar=1 (cheapest priced IR)."""
    return _ir([{"id": 0, "op": "source", "source": "close"}], [_plot(0)], plan_cost=plan_cost)


def _codes(res):
    return [e["code"] for e in res["errors"]]


# ── CRUX: forged meta.planCost is NEVER a decision input (both directions) ───


def test_forged_tiny_meta_on_expensive_ir_still_rejected():
    ir = _sma50(plan_cost=_fake_plan_cost(1, 1, 1))  # forge a near-zero cost
    res = resolve_plan_cost(ir, 100, _limits(maximumTotalOperations=1000), "enforce")
    # The forged tiny meta did NOT save it: recompute (5201) > cap (1000).
    assert _codes(res) == ["IR_OPERATION_BUDGET_EXCEEDED"]
    assert res["recomputed"]["totalOperations"] == 5201
    assert res["embeddedMismatch"] is True  # 1 != 5201


def test_forged_huge_meta_on_cheap_ir_still_admitted():
    ir = _cheap(plan_cost=_fake_plan_cost(1e12, 1e9, 1e15))  # forge an enormous cost
    res = resolve_plan_cost(ir, 100, SCRIPT_LIMITS, "enforce")
    # The forged huge meta did NOT sink it: recompute (100) is under every cap.
    assert res["errors"] == []
    assert res["recomputed"]["totalOperations"] == 100
    assert res["embeddedMismatch"] is True  # 1e12 != 100


# ── each budget code, isolated by a tiny cap override ────────────────────────


def test_barcount_over_history_bars():
    res = resolve_plan_cost(_cheap(), 2000, _limits(maximumHistoryBars=1000), "enforce")
    assert _codes(res) == ["IR_DATASET_TOO_LARGE"]


def test_peak_bytes_over_memory_cap():
    # sma50 bytes @100 = 5704 > 0.001·1024·1024 (=1048.576); other caps default.
    res = resolve_plan_cost(_sma50(), 100, _limits(maximumExecutionMemoryMb=0.001), "enforce")
    assert _codes(res) == ["IR_MEMORY_BUDGET_EXCEEDED"]


def test_perbar_over_cap():
    # sma50 perBar @100 = 52 > 10; total 5201 < default 100M so only perBar fires.
    res = resolve_plan_cost(_sma50(), 100, _limits(maximumOperationsPerBar=10), "enforce")
    assert _codes(res) == ["IR_OPERATION_BUDGET_EXCEEDED"]


def test_unpriced_operator_is_caught_not_leaked():
    ir = _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 5},
            {"id": 2, "op": "call", "namespace": "ta", "function": "bogus_fn", "args": [0, 1]},
        ],
        [_plot(2)],
    )
    res = resolve_plan_cost(ir, 100, SCRIPT_LIMITS, "enforce")
    assert _codes(res) == ["IR_UNPRICED_OPERATOR"]
    assert res["recomputed"] is None


# ── observe vs enforce ───────────────────────────────────────────────────────


def test_observe_vs_enforce_same_ir():
    over_limits = _limits(maximumTotalOperations=1000)
    obs = resolve_plan_cost(_sma50(), 100, over_limits, "observe")
    assert obs["errors"] == []  # observe NEVER blocks
    assert [e["code"] for e in obs["observed"]] == ["IR_OPERATION_BUDGET_EXCEEDED"]  # verdict recorded

    enf = resolve_plan_cost(_sma50(), 100, over_limits, "enforce")
    assert [e["code"] for e in enf["errors"]] == ["IR_OPERATION_BUDGET_EXCEEDED"]
    assert enf["observed"] == enf["errors"]  # enforce: observed == errors


# ── a normal compiled IR admits in both modes ────────────────────────────────


def test_realistic_compiled_ir_admits():
    ir = _compile_ir("plot(ta.ema(close, 20))")
    for mode in ("observe", "enforce"):
        res = resolve_plan_cost(ir, 2000, SCRIPT_LIMITS, mode)
        assert res["errors"] == [], mode


# ── admission ctx upper bound == Task 6 runtime_cost_ctx hi ───────────────────


def test_admission_ctx_bound_equals_runtime_hi_declared_max():
    ir = _compile_ir('len = input.int(50, "Length", maxval=200)\nplot(ta.sma(close, len))')
    id_ = ir["inputs"][0]["id"]
    admit = admission_cost_ctx(ir, 1000)
    assert admit.input_bound(id_) == 200
    # runtime_cost_ctx clamps an oversized caller value to the SAME hi.
    rt = runtime_cost_ctx(ir, {id_: 999999}, 1000)
    assert rt.input_bound(id_) == 200
    assert admit.input_bound(id_) == rt.input_bound(id_)


def test_admission_ctx_bound_falls_back_to_maximum_lookback():
    ir = _compile_ir('len = input.int(50, "Length")\nplot(ta.sma(close, len))')
    id_ = ir["inputs"][0]["id"]
    assert admission_cost_ctx(ir, 1000).input_bound(id_) == SCRIPT_LIMITS["maximumLookback"]


# ── wiring smoke test: resolver is on the real execute_ir path ────────────────


@pytest.fixture
def _dataset():
    rng = np.random.default_rng(7)
    n = 2100
    close = 100 + np.cumsum(rng.standard_normal(n))
    return {
        "open": close.astype(float),
        "high": (close + 1).astype(float),
        "low": (close - 1).astype(float),
        "close": close.astype(float),
        "volume": np.full(n, 1000.0),
    }


def test_wiring_enforce_blocks_over_cap_execution(_dataset, monkeypatch):
    # 2100 bars; ta.sma(close, 50000) → recompute total 105,004,201 > real 100M cap.
    monkeypatch.setenv("OPENSCRIPT_PLANCOST_MODE", "enforce")
    over_cap = _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 50000},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
        ],
        [_plot(2)],
    )
    with pytest.raises(IRAdmissionError) as ei:
        execute_ir(over_cap, _dataset, {})
    assert [e["code"] for e in ei.value.errors] == ["IR_OPERATION_BUDGET_EXCEEDED"]


def test_wiring_observe_does_not_block(monkeypatch):
    # An over-cap IR whose kernel is STILL executable (period <= barCount): in
    # observe (default) mode execute_ir must NOT raise IRAdmissionError, so it
    # runs to completion. ta.sma(close, 9000) @12000 bars → recompute total
    # 108,024,001 > real 100M cap (would block in enforce), yet period 9000 <=
    # 12000 so the kernel executes.
    monkeypatch.setenv("OPENSCRIPT_PLANCOST_MODE", "observe")
    n = 12000
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.standard_normal(n))
    dataset = {
        "open": close.astype(float),
        "high": (close + 1).astype(float),
        "low": (close - 1).astype(float),
        "close": close.astype(float),
        "volume": np.full(n, 1000.0),
    }
    over_cap = _ir(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 9000},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
        ],
        [_plot(2)],
    )
    # Guard: this IR really is over the total-ops cap (so the test is meaningful).
    verdict = resolve_plan_cost(over_cap, n, SCRIPT_LIMITS, "enforce")
    assert [e["code"] for e in verdict["errors"]] == ["IR_OPERATION_BUDGET_EXCEEDED"]
    # observe must let it execute (no IRAdmissionError raised).
    outputs = execute_ir(over_cap, dataset, {})
    assert any(o["kind"] == "line" for o in outputs)
