"""Task 7 review — Finding 1 (HIGH, live in observe): a windowed kernel must not
do unbounded work from an UNCLAMPED length arg (Python mirror of
openalgo-openscript/tests/kernel-arg-clamp.test.ts).

A no-max numeric input (or a computed-expression length) used as a window length
is CHARGED as maximumLookback (the cost model's no-max fallback), but the
executor otherwise passes the RAW caller value to the kernel — so
kernels.gaussian(close, 8.0, sab) with {sab: 4_000_000} was admitted (charge
clamped to 20000, under caps) yet allocated/worked O(4,000,000) at runtime. The
fix clamps every SCALAR numeric arg of a WINDOW-family kernel to maximumLookback
at the dispatch boundary, so real work <= charge <= estimate.

These tests run on a dataset just larger than maximumLookback so a window of
maximumLookback + 2 FILLS (finite tail) while an unclamped huge window does not
(all NaN) — the finite tail is the proof the kernel saw the clamped length.
"""

import numpy as np

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.budget import OperationBudget
from services.openscript.runtime.cost_expr import eval_cost_expr
from services.openscript.runtime.executor import execute_ir
from services.openscript.runtime.plancost import (
    admission_cost_ctx,
    estimate_plan_cost,
    runtime_cost_ctx,
)

LOOKBACK = SCRIPT_LIMITS["maximumLookback"]  # 20_000
N = LOOKBACK + 50  # 20_050 — a window of LOOKBACK+2 fills; a huge one does not.


def _make_dataset(n):
    i = np.arange(n, dtype=float)
    price = 100 + np.cumsum(np.sin(i / 7.0) * 0.5 + 0.1)
    return {
        "open": (price - 0.1).astype(float),
        "high": (price + 0.3).astype(float),
        "low": (price - 0.3).astype(float),
        "close": price.astype(float),
        "volume": (1000 + i).astype(float),
    }


def _compile_ir(source):
    result = openscript.compile(source)
    assert result.diagnostics == [], f"unexpected diagnostics: {result.diagnostics}"
    assert result.ir is not None
    return result.ir


def _line(outputs):
    lines = [o for o in outputs if o["kind"] == "line"]
    assert lines, "no line output"
    return lines[0]["values"]


def _nan_equal(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        return False
    return bool(np.all((a == b) | (np.isnan(a) & np.isnan(b))))


def test_no_max_nw_start_at_bar_is_clamped(monkeypatch):
    # Exercises the RUNTIME dispatch clamp (Finding 1, which was live IN OBSERVE):
    # a maximumLookback-scale window (charged 20_000) over a 20_050-bar dataset is
    # ~1.6B total ops, legitimately over maximumTotalOperations (100M). Python's
    # execute_ir embeds the admission resolver, so with the Task-9 enforce default
    # it would be (correctly) rejected pre-execution before the runtime clamp under
    # test is reached. Pin observe to test the clamp itself.
    monkeypatch.setenv("OPENSCRIPT_PLANCOST_MODE", "observe")
    dataset = _make_dataset(N)
    exploit = _compile_ir('sab = input.int(20, "S")\nplot(kernels.gaussian(close, 8.0, sab))')
    got = _line(execute_ir(exploit, dataset, {"sab": 4_000_000}))
    # Reference: the SAME kernel with start_at_bar pinned to maximumLookback.
    ref = _line(execute_ir(_compile_ir(f"plot(kernels.gaussian(close, 8.0, {LOOKBACK}))"), dataset, {}))
    assert _nan_equal(got, ref)  # kernel saw the clamped 20_000
    assert np.isfinite(got).any()  # window filled at 20_000, NOT 4_000_000


def test_computed_expr_length_is_clamped(monkeypatch):
    # See test_no_max_nw_start_at_bar_is_clamped: a maximumLookback-scale window
    # over a 20_050-bar dataset is legitimately over maximumTotalOperations, so this
    # runtime-clamp test pins observe (enforce would correctly reject it first).
    monkeypatch.setenv("OPENSCRIPT_PLANCOST_MODE", "observe")
    dataset = _make_dataset(N)
    # a+b = 25_000 is a binop node — NOT a const and NOT an input, so the cost
    # model charges the maximumLookback fallback; the executed length must agree.
    ir = _compile_ir('a = input.int(15000,"a")\nb = input.int(10000,"b")\nplot(kernels.gaussian(close, 8.0, a + b))')
    got = _line(execute_ir(ir, dataset, {}))
    ref = _line(execute_ir(_compile_ir(f"plot(kernels.gaussian(close, 8.0, {LOOKBACK}))"), dataset, {}))
    assert _nan_equal(got, ref)
    assert np.isfinite(got).any()


def test_small_period_is_noop_and_charged_le_estimate():
    dataset = _make_dataset(300)
    ir = _compile_ir("plot(ta.sma(close, 20))")
    budget = OperationBudget(ir, runtime_cost_ctx(ir, {}, len(dataset["close"])))
    got = _line(execute_ir(ir, dataset, {}, budget=budget))
    estimate = eval_cost_expr(
        estimate_plan_cost(ir)["totalOperations"], admission_cost_ctx(ir, len(dataset["close"]))
    )
    assert budget.spent() <= estimate  # clamp only reduces real work
    # no-op: sma(20) last value equals the simple mean of the last 20 closes.
    closes = dataset["close"]
    assert abs(float(got[-1]) - float(closes[-20:].mean())) < 1e-6
