"""OpenScript server executor parity — the numpy executor must reproduce direct
`openalgo.ta` calls exactly (same kernels, same args), proving the IR DAG wiring
(arg assembly, implicit sources, tuple output slicing) is correct. Mirrors the TS
executor test that checks against the wasm facade.
"""

import math

import numpy as np
import pytest
from openalgo import ta

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.budget import BudgetExceeded, OperationBudget
from services.openscript.runtime.executor import execute_ir


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


def _run(source, dataset, inputs=None):
    result = openscript.compile(source)
    assert result.diagnostics == [], f"unexpected diagnostics: {result.diagnostics}"
    assert result.ir is not None
    return execute_ir(result.ir, dataset, inputs or {})


def _line(outputs, idx=0):
    lines = [o for o in outputs if o["kind"] == "line"]
    return lines[idx]["values"]


def _close(a, b):
    np.testing.assert_allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True)


def test_ema_matches_ta(dataset):
    _close(_line(_run("plot(ta.ema(close, 20))", dataset)), ta.ema(dataset["close"], 20))


def test_implicit_atr_matches_ta(dataset):
    out = _run("plot(ta.atr(14))", dataset)
    _close(_line(out), ta.atr(dataset["high"], dataset["low"], dataset["close"], 14))


def test_macd_tuple_components(dataset):
    out = _run("[m, s, h] = ta.macd(close, 12, 26, 9)\nplot(m)\nplot(s)\nplot(h)", dataset)
    macd = ta.macd(dataset["close"], 12, 26, 9)
    _close(_line(out, 0), macd[0])
    _close(_line(out, 1), macd[1])
    _close(_line(out, 2), macd[2])


def test_binary_and_broadcast(dataset):
    out = _run("plot(close - open)", dataset)
    _close(_line(out), dataset["close"] - dataset["open"])
    out2 = _run("plot(close + 1)", dataset)
    _close(_line(out2), dataset["close"] + 1)


def test_ternary_select(dataset):
    out = _run("plot(close > open ? 1 : 0)", dataset)
    expected = (dataset["close"] > dataset["open"]).astype(float)
    _close(_line(out), expected)


def test_historical_reference(dataset):
    out = _run("plot(close[1])", dataset)
    vals = _line(out)
    assert np.isnan(vals[0])
    _close(vals[1:], dataset["close"][:-1])


def test_alertcondition_fired_bars(dataset):
    out = _run('alertcondition(close > open, "up", "msg")', dataset)
    alert = next(o for o in out if o["kind"] == "alert")
    expected = [i for i in range(len(dataset["close"])) if dataset["close"][i] > dataset["open"][i]]
    assert alert["firedAtBar"] == expected


def test_cpr_matches_wasm_formula(dataset):
    """ta.cpr must execute server-side (openalgo.ta has no cpr — local numpy
    fallback) and match the oa_composites::cpr formula exactly:
    pivot=(h+l+c)/3, bc=(h+l)/2, tc=2*pivot-bc, elementwise per bar."""
    out = _run("[p, b, t] = ta.cpr()\nplot(p)\nplot(b)\nplot(t)", dataset)
    h, low, c = dataset["high"], dataset["low"], dataset["close"]
    pivot = (h + low + c) / 3.0
    bc = (h + low) / 2.0
    tc = 2.0 * pivot - bc
    _close(_line(out, 0), pivot)
    _close(_line(out, 1), bc)
    _close(_line(out, 2), tc)


def test_input_override(dataset):
    out = _run('len = input.int(9, "Length")\nplot(ta.ema(close, len))', dataset, {"len": 20})
    _close(_line(out), ta.ema(dataset["close"], 20))
    out_default = _run('len = input.int(9, "Length")\nplot(ta.ema(close, len))', dataset)
    _close(_line(out_default), ta.ema(dataset["close"], 9))


# ── P2 calibrated ta additions (rma/linreg/valuewhen/pivots) ───────────────


def test_rma_matches_wilder_formula(dataset):
    vals = _line(_run("plot(ta.rma(close, 14))", dataset))
    x = dataset["close"]
    n = len(x)
    expected = np.full(n, np.nan)
    expected[13] = x[:14].sum() / 14.0
    for i in range(14, n):
        expected[i] = (expected[i - 1] * 13 + x[i]) / 14.0
    _close(vals, expected)


def test_linreg_matches_sdk(dataset):
    vals = _line(_run("plot(ta.linreg(close, 14))", dataset))
    _close(vals, ta.linreg(dataset["close"], 14))


def test_valuewhen_occurrence_zero_is_most_recent(dataset):
    vals = _line(_run("plot(ta.valuewhen(close > open, close, 0))", dataset))
    n = len(dataset["close"])
    expected = np.full(n, np.nan)
    last = np.nan
    for i in range(n):
        if dataset["close"][i] > dataset["open"][i]:
            last = dataset["close"][i]
        expected[i] = last
    _close(vals, expected)


def test_pivothigh_strict_late_confirmation(dataset):
    vals = _line(_run("plot(ta.pivothigh(2, 2))", dataset))
    h = dataset["high"]
    n = len(h)
    expected = np.full(n, np.nan)
    for i in range(4, n):
        p = i - 2
        window = np.concatenate([h[p - 2 : p], h[p + 1 : p + 3]])
        if (window < h[p]).all():
            expected[i] = h[p]
    _close(vals, expected)


def test_pivotlow_explicit_series(dataset):
    vals = _line(_run("plot(ta.pivotlow(low, 3, 3))", dataset))
    lo = dataset["low"]
    n = len(lo)
    expected = np.full(n, np.nan)
    for i in range(6, n):
        p = i - 3
        window = np.concatenate([lo[p - 3 : p], lo[p + 1 : p + 4]])
        if (window > lo[p]).all():
            expected[i] = lo[p]
    _close(vals, expected)


# ── P1.3 dynamic per-bar colors ────────────────────────────────────────────


def test_color_new_const_folds_static(dataset):
    out = _run('plot(close, "C", color = color.new(color.green, 20))', dataset)
    assert out[0]["kind"] == "line"
    assert out[0]["style"]["color"] == "#4caf50cc"


def test_conditional_histogram_color_palette_split(dataset):
    out = _run(
        'plot(close - open, "H", style = plot.style_histogram, '
        "color = close > open ? color.green : color.red)",
        dataset,
    )
    histos = [o for o in out if o["kind"] == "histogram"]
    assert len(histos) == 2
    assert sorted(h["style"]["color"] for h in histos) == ["#4caf50", "#ef5350"]
    green = next(h for h in histos if h["style"]["color"] == "#4caf50")
    red = next(h for h in histos if h["style"]["color"] == "#ef5350")
    up = dataset["close"] > dataset["open"]
    assert np.isnan(green["values"][~up]).all()
    assert np.isnan(red["values"][up]).all()


def test_conditional_line_color_keeps_connector(dataset):
    out = _run('plot(close, "L", color = close > open ? color.green : color.red)', dataset)
    lines = [o for o in out if o["kind"] == "line"]
    assert len(lines) == 2
    green = next(o for o in lines if o["style"]["color"] == "#4caf50")
    n = len(dataset["close"])
    idx = (dataset["close"] <= dataset["open"]).astype(int)  # 0 = green, 1 = red
    for i in range(n):
        keep = idx[i] == 0 or (i + 1 < n and idx[i + 1] == 0)
        assert math.isnan(green["values"][i]) == (not keep)


def test_bgcolor_dynamic_colors(dataset):
    out = _run("bgcolor(volume > 0, color = close > open ? color.green : color.red)", dataset)
    o = out[0]
    assert o["kind"] == "bgcolor"
    for i in range(len(dataset["close"])):
        expected = "#4caf50" if dataset["close"][i] > dataset["open"][i] else "#ef5350"
        assert o["colors"][i] == expected


def test_marker_na_color_hides_marker(dataset):
    out = _run(
        'plotshape(volume > 0, "M", location=location.abovebar, shape=shape.circle, '
        "color = close > open ? color.lime : na)",
        dataset,
    )
    o = out[0]
    assert o["kind"] == "plotshape"
    expected = [
        i
        for i in range(len(dataset["close"]))
        if dataset["volume"][i] > 0 and dataset["close"][i] > dataset["open"][i]
    ]
    assert o["bars"] == expected


# ── P1.2 fill() with plot handles ──────────────────────────────────────────


def test_fill_between_plot_handles(dataset):
    out = _run(
        '[mid, up, lo] = ta.bb(close, 20, 2)\n'
        'pu = plot(up, "U")\n'
        'pl = plot(lo, "L")\n'
        'fill(pu, pl, color = #2962ff33, title = "Band")',
        dataset,
    )
    fill = next(o for o in out if o["kind"] == "fill")
    bb = ta.bbands(dataset["close"], 20, 2)
    _close(fill["top"], bb[0])  # upper
    _close(fill["bottom"], bb[2])  # lower
    assert fill["style"]["color"] == "#2962ff33"
    assert fill["title"] == "Band"
    assert len([o for o in out if o["kind"] == "line"]) == 2


def test_plot_handle_misuse_is_os2012(dataset):
    result = openscript.compile("p = plot(close)\nplot(p + 1)")
    assert "OS2012" in [d.code for d in result.diagnostics]


# ── P1.1 plot-style variants: kind parity with the TS collect-outputs ──────


def test_style_histogram_kind(dataset):
    out = _run('plot(close - open, "H", style = plot.style_histogram)', dataset)
    assert out[0]["kind"] == "histogram"
    assert out[0]["style"]["base"] == 0
    assert not out[0]["style"].get("column")
    _close(out[0]["values"], dataset["close"] - dataset["open"])


def test_style_columns_kind(dataset):
    out = _run('plot(volume, "V", style = plot.style_columns)', dataset)
    assert out[0]["kind"] == "histogram"
    assert out[0]["style"]["column"] is True


def test_style_area_and_markers_flags(dataset):
    out = _run('plot(close, "A", style = plot.style_area)', dataset)
    assert out[0]["kind"] == "line"
    assert out[0]["style"]["area"] is True
    for variant in ("style_circles", "style_cross"):
        out = _run(f'plot(close, "P", style = plot.{variant})', dataset)
        assert out[0]["kind"] == "line"
        assert out[0]["style"]["markers"] is True


def test_style_stepline_flag(dataset):
    out = _run('plot(close, "S", style = plot.style_stepline)', dataset)
    assert out[0]["kind"] == "line"
    assert out[0]["style"]["step"] is True


# ── OS4001/OS4002 budget parity with the TS OperationBudget ────────────────


def _limits(**overrides):
    merged = dict(SCRIPT_LIMITS)
    merged.update(overrides)
    return merged


def test_budget_ops_per_bar_exceeded_at_construction():
    with pytest.raises(BudgetExceeded) as ei:
        OperationBudget(100, 3, _limits(maximumOperationsPerBar=2))
    assert ei.value.code == "OS4001"


def test_budget_total_operations_exceeded_on_step():
    budget = OperationBudget(100, 1, _limits(maximumTotalOperations=250))
    budget.step()  # 100
    budget.step()  # 200
    with pytest.raises(BudgetExceeded) as ei:
        budget.step()  # 300 > 250
    assert ei.value.code == "OS4001"
    assert budget.spent() == 300


def test_budget_time_exceeded():
    budget = OperationBudget(1, 1, _limits(maximumExecutionMilliseconds=0))
    with pytest.raises(BudgetExceeded) as ei:
        budget.step()
    assert ei.value.code == "OS4002"


def test_execute_ir_enforces_budget(dataset):
    result = openscript.compile("plot(ta.ema(close, 20))")
    assert result.ir is not None
    n = len(dataset["close"])
    budget = OperationBudget(n, len(result.ir["nodes"]), _limits(maximumTotalOperations=n))
    with pytest.raises(BudgetExceeded) as ei:
        execute_ir(result.ir, dataset, {}, budget=budget)
    assert ei.value.code == "OS4001"


def test_execute_ir_without_budget_unchanged(dataset):
    # budget is optional — omitting it keeps the pre-parity behavior.
    out = _run("plot(ta.ema(close, 20))", dataset)
    _close(_line(out), ta.ema(dataset["close"], 20))
