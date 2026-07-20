"""Phase 0.2 Task 9 — PlanCost CALIBRATION over a compiled-IR corpus (no raw
.pine), Python side. Mirrors openalgo-openscript/tests/plancost-calibration.test.ts.

At the two boundary bar counts the plan calls out — 2_000 and 100_000 (=
maximumHistoryBars) — asserts the invariants that justify flipping the default
mode to `enforce`:
  1. runtime_charged <= estimate — an OperationBudget built over the RUNTIME
     CostCtx (window lengths clamped to [min, max]) stepped over every node never
     charges more than the admission estimate (input_bound = max);
  2. every dimension is under its cap, with headroom (no cap weakened to fit).

TS<->Python EXACT estimate parity is pinned separately by the shared
fixtures/plancost/cal-*.json replayed by test_openscript_plancost_conformance.py.
The headroom snapshot lives in
openalgo-openscript/docs/openscript-phase0.2-plancost-calibration.md.
"""

import pytest

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.budget import OperationBudget
from services.openscript.runtime.cost_expr import eval_cost_expr
from services.openscript.runtime.plancost import (
    admission_cost_ctx,
    estimate_plan_cost,
    runtime_cost_ctx,
)

# (name, source, inputs)
_CORPUS = [
    ("ema9", 'indicator("EMA", overlay=true)\nplot(ta.ema(close, 9), "EMA")', {}),
    (
        "macd",
        'indicator("MACD", overlay=false)\n[macd, signal, hist] = ta.macd(close, 12, 26, 9)\n'
        'plot(macd, "MACD")\nplot(signal, "Signal")',
        {},
    ),
    (
        "dedup-ema20-x8",
        'indicator("Dedup", overlay=true)\n'
        + "\n".join(f'plot(ta.ema(close, 20), "E{i}")' for i in range(8)),
        {},
    ),
    ("bbands", "[u, m, l] = ta.bb(close, 20, 2)\nplot(u)\nplot(m)\nplot(l)", {}),
    ("rsi14", "plot(ta.rsi(close, 14))", {}),
    ("supertrend", "[st, d] = ta.supertrend(3, 10)\nplot(st)\nplot(d)", {}),
    ("atr14", "plot(ta.atr(14))", {}),
    ("stochastic", "[k, d] = ta.stochastic(14, 3, 3)\nplot(k)\nplot(d)", {}),
    ("input-sma", 'len = input.int(14, "Len", minval=1, maxval=50)\nplot(ta.sma(close, len))', {}),
]

_BARCOUNTS = (2_000, 100_000)
_CAP_TOTAL = SCRIPT_LIMITS["maximumTotalOperations"]
_CAP_PERBAR = SCRIPT_LIMITS["maximumOperationsPerBar"]
_CAP_BYTES = SCRIPT_LIMITS["maximumExecutionMemoryMb"] * 1024 * 1024


def _compile_ir(source):
    result = openscript.compile(source)
    assert result.diagnostics == [], f"unexpected diagnostics: {result.diagnostics}"
    assert result.ir is not None
    return result.ir


@pytest.mark.parametrize("name,source,inputs", _CORPUS, ids=[c[0] for c in _CORPUS])
def test_calibration_charged_le_estimate_and_under_caps(name, source, inputs):
    ir = _compile_ir(source)
    for bars in _BARCOUNTS:
        cost = estimate_plan_cost(ir)
        admit = admission_cost_ctx(ir, bars)
        est_total = eval_cost_expr(cost["totalOperations"], admit)
        est_per_bar = eval_cost_expr(cost["perBarOperations"], admit)
        est_bytes = eval_cost_expr(cost["estimatedPeakBytes"], admit)

        # (1) runtime charged <= admission estimate.
        rt = runtime_cost_ctx(ir, inputs, bars)
        budget = OperationBudget(ir, rt, SCRIPT_LIMITS)
        for node in ir["nodes"]:
            budget.step(node)
        assert budget.spent() <= est_total, f"{name}@{bars} charged>estimate"

        # (2) under every cap.
        assert est_total <= _CAP_TOTAL, f"{name}@{bars} total"
        assert est_per_bar <= _CAP_PERBAR, f"{name}@{bars} perBar"
        assert est_bytes <= _CAP_BYTES, f"{name}@{bars} bytes"


def test_calibration_input_bound_charges_below_estimate():
    # Declared maxval=50, default 14 → admission uses 50, runtime clamps to 14.
    ir = _compile_ir('len = input.int(14, "Len", minval=1, maxval=50)\nplot(ta.sma(close, len))')
    bars = 100_000
    est_total = eval_cost_expr(estimate_plan_cost(ir)["totalOperations"], admission_cost_ctx(ir, bars))
    budget = OperationBudget(ir, runtime_cost_ctx(ir, {}, bars), SCRIPT_LIMITS)
    for node in ir["nodes"]:
        budget.step(node)
    assert budget.spent() < est_total
