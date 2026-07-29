"""OpenScript server executor parity — the numpy executor must reproduce direct
`openalgo.ta` calls exactly (same kernels, same args), proving the IR DAG wiring
(arg assembly, implicit sources, tuple output slicing) is correct. Mirrors the TS
executor test that checks against the wasm facade.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from openalgo import ta

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.budget import BudgetExceeded, OperationBudget
from services.openscript.runtime.executor import execute_ir
from services.openscript.runtime.plancost import runtime_cost_ctx


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
    # Only ERRORS block a clean compile; OS5xxx repaint warnings (e.g. plotting a
    # provisional pivot) are advisory and expected on some executor cases.
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert errors == [], f"unexpected diagnostics: {errors}"
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


def test_degenerate_from_gradient_warmup_is_untinted(dataset):
    # bottom_value == top_value = 50 -> the hi==lo path. v = sma(5) is NaN on the
    # first 4 bars: those must map to '' (na), not the bottom bucket (the bug was
    # NaN >= 50 -> falsy -> a wrong black tint on warmup).
    out = _run(
        "v = ta.sma(close, 5)\nbgcolor(high >= low, color = color.from_gradient(v, 50.0, 50.0, #000000, #ffffff))",
        dataset,
    )
    bg = next(o for o in out if o["kind"] == "bgcolor")
    assert bg["colors"][:4] == ["", "", "", ""]  # warmup NaN -> no tint
    assert any(c != "" for c in bg["colors"][4:])  # real bars ARE tinted


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


def test_from_gradient_const_value_folds_to_bucket_color(dataset):
    out = _run(
        "plot(close, color = color.from_gradient(0.5, 0.0, 1.0, #000000, #ffffff))",
        dataset,
    )
    assert out[0]["style"]["color"] == "#888888ff"


def test_from_gradient_maps_endpoints_to_extreme_buckets(dataset):
    out = _run(
        'plot(close - open, "H", style = plot.style_histogram, '
        "color = color.from_gradient(close > open ? 100.0 : 0.0, 0.0, 100.0, #000000, #ffffff))",
        dataset,
    )
    histos = [o for o in out if o["kind"] == "histogram"]
    assert len(histos) == 2
    assert sorted(h["style"]["color"] for h in histos) == ["#000000ff", "#ffffffff"]
    white = next(h for h in histos if h["style"]["color"] == "#ffffffff")
    black = next(h for h in histos if h["style"]["color"] == "#000000ff")
    up = dataset["close"] > dataset["open"]
    assert np.isnan(white["values"][~up]).all()
    assert np.isnan(black["values"][up]).all()


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


# ── P4.4 input.color runtime substitution ──────────────────────────────────


def test_plot_uses_color_input_default(dataset):
    out = _run('c = input.color(color.red, "C")\nplot(close, color=c)', dataset)
    assert out[0]["style"]["color"] == "#ef5350"


def test_plot_runtime_input_overrides_baked_color(dataset):
    out = _run('c = input.color(color.red, "C")\nplot(close, color=c)', dataset, {"c": "#00ff00"})
    assert out[0]["style"]["color"] == "#00ff00"


def test_bgcolor_honors_color_input_override(dataset):
    out = _run('c = input.color(color.blue, "C")\nbgcolor(volume > 0, color=c)', dataset, {"c": "#123456"})
    o = out[0]
    assert o["kind"] == "bgcolor"
    assert any(c == "#123456" for c in o["colors"])
    assert all(c in ("", "#123456") for c in o["colors"])


def test_plotcandle_color_input_overrides_both_colors(dataset):
    out = _run(
        'c = input.color(color.green, "C")\nplotcandle(open, high, low, close, "PC", color=c)',
        dataset,
        {"c": "#abcdef"},
    )
    o = out[0]
    assert o["kind"] == "candle"
    assert o["style"]["upColor"] == "#abcdef"
    assert o["style"]["downColor"] == "#abcdef"


def test_empty_string_override_falls_back_to_default(dataset):
    out = _run('c = input.color(color.red, "C")\nplot(close, color=c)', dataset, {"c": ""})
    assert out[0]["style"]["color"] == "#ef5350"


# ── P1.4 plotcandle / plotbar ──────────────────────────────────────────────


def test_plotcandle_emits_candle_output(dataset):
    out = _run('plotcandle(open, high, low, close, "PC")', dataset)
    o = out[0]
    assert o["kind"] == "candle"
    _close(o["open"], dataset["open"])
    _close(o["high"], dataset["high"])
    _close(o["low"], dataset["low"])
    _close(o["close"], dataset["close"])
    assert o["title"] == "PC"
    assert not o["style"].get("bar")


def test_plotbar_sets_bar_flag(dataset):
    out = _run('plotbar(open, high, low, close, "PB")', dataset)
    assert out[0]["kind"] == "candle"
    assert out[0]["style"]["bar"] is True


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


# ── P3 scan primitive (var + :=) ───────────────────────────────────────────


def test_scan_counter_from_seed(dataset):
    vals = _line(_run("var c = 0\nc := c + 1\nplot(c)", dataset))
    for i in range(len(vals)):
        assert vals[i] == i + 1


def test_scan_prevh_is_na_on_bar0(dataset):
    vals = _line(_run("var x = 0\nx := na(x[1]) ? 100 : x[1] + 1\nplot(x)", dataset))
    assert vals[0] == 100
    for i in range(1, len(vals)):
        assert vals[i] == 100 + i


def test_scan_signal_persistence(dataset):
    vals = _line(
        _run("var s = 0\ns := close > open ? 1 : close < open ? 0 - 1 : nz(s[1])\nplot(s)", dataset)
    )
    expected = 0
    for i in range(len(vals)):
        c, o = dataset["close"][i], dataset["open"][i]
        expected = 1 if c > o else -1 if c < o else expected
        assert vals[i] == expected


def test_scan_counter_with_reset(dataset):
    vals = _line(_run("var held = 0\nheld := close > open ? 0 : held + 1\nplot(held)", dataset))
    expected = 0
    for i in range(len(vals)):
        expected = 0 if dataset["close"][i] > dataset["open"][i] else expected + 1
        assert vals[i] == expected


def test_scan_running_max(dataset):
    vals = _line(_run("var hi = 0\nhi := math.max(nz(hi[1], low), high)\nplot(hi)", dataset))
    expected = math.nan
    for i in range(len(vals)):
        seed = dataset["low"][i] if math.isnan(expected) else expected
        expected = max(seed, dataset["high"][i])
        assert abs(vals[i] - expected) < 1e-9


def test_scan_supertrend_band_carry(dataset):
    source = "\n".join(
        [
            "basis = hlc3",
            "dev = 2 * ta.atr(10)",
            "ub0 = basis + dev",
            "lb0 = basis - dev",
            "var ub = 0",
            "var lb = 0",
            "var d = 0",
            "ub := na(ub[1]) ? ub0 : (ub0 < ub[1] or close[1] > ub[1] ? ub0 : ub[1])",
            "lb := na(lb[1]) ? lb0 : (lb0 > lb[1] or close[1] < lb[1] ? lb0 : lb[1])",
            "d := na(d[1]) ? 1 : d[1] == 0 - 1 and close > ub[1] ? 1 : "
            "d[1] == 1 and close < lb[1] ? 0 - 1 : d[1]",
            "plot(ub)",
            "plot(lb)",
            "plot(d)",
        ]
    )
    out = _run(source, dataset)
    ub_vals, lb_vals, d_vals = _line(out, 0), _line(out, 1), _line(out, 2)

    atr = ta.atr(dataset["high"], dataset["low"], dataset["close"], 10)
    pu = pl = pd_ = math.nan
    for i in range(len(dataset["close"])):
        basis = (dataset["high"][i] + dataset["low"][i] + dataset["close"][i]) / 3
        dev = 2 * atr[i]
        ub0, lb0 = basis + dev, basis - dev
        prev_close = dataset["close"][i - 1] if i > 0 else math.nan
        cu = ub0 if math.isnan(pu) else (ub0 if (ub0 < pu or prev_close > pu) else pu)
        cl = lb0 if math.isnan(pl) else (lb0 if (lb0 > pl or prev_close < pl) else pl)
        if math.isnan(pd_):
            cd = 1.0
        elif pd_ == -1 and dataset["close"][i] > pu:
            cd = 1.0
        elif pd_ == 1 and dataset["close"][i] < pl:
            cd = -1.0
        else:
            cd = pd_
        if math.isnan(cu):
            assert math.isnan(ub_vals[i])
        else:
            assert abs(ub_vals[i] - cu) < 1e-9
        if math.isnan(cl):
            assert math.isnan(lb_vals[i])
        else:
            assert abs(lb_vals[i] - cl) < 1e-9
        assert d_vals[i] == cd
        pu, pl, pd_ = cu, cl, cd


def test_scan_output_feeds_ta(dataset):
    vals = _line(_run("var c = 0\nc := c + 1\nplot(ta.sma(c, 5))", dataset))
    for i in range(4, 20):
        assert abs(vals[i] - (5 * (i + 1) - 10) / 5) < 1e-9


def test_na_predicate_in_dag(dataset):
    vals = _line(_run("plot(na(close[1]) ? 1 : 0)", dataset))
    assert vals[0] == 1
    assert all(v == 0 for v in vals[1:10])


def test_scan_diagnostics_os2016(dataset):
    for src in (
        "x := 1",
        "var x = close\nx := x + 1",
        "var x = 0\ny = x + 1\nx := x + 1\nplot(y)",
        "var x = 0\nx := ta.sma(x, 5)",
        "var x = 0\nx := x[2] + 1",
    ):
        result = openscript.compile(src)
        assert "OS2016" in [d.code for d in result.diagnostics], src


# ── LC-1 ta/math plumbing ──────────────────────────────────────────────────


def test_barssince_reference(dataset):
    vals = _line(_run("plot(ta.barssince(close > open))", dataset))
    last = -1
    for i in range(len(dataset["close"])):
        if dataset["close"][i] > dataset["open"][i]:
            last = i
        if last == -1:
            assert math.isnan(vals[i])
        else:
            assert vals[i] == i - last


def test_cum_running_sum(dataset):
    vals = _line(_run("plot(ta.cum(volume))", dataset))
    expected = np.cumsum(dataset["volume"])
    _close(vals, expected)


def test_math_sum_rolling(dataset):
    vals = _line(_run("plot(math.sum(close, 14))", dataset))
    x = dataset["close"]
    n = len(x)
    expected = np.full(n, np.nan)
    rolling = x[:14].sum()
    expected[13] = rolling
    for i in range(14, n):
        rolling = rolling + x[i] - x[i - 14]
        expected[i] = rolling
    _close(vals, expected)


def test_cci_source_form(dataset):
    vals = _line(_run("plot(ta.cci(close, 20))", dataset))
    _close(vals, ta.cci(dataset["close"], dataset["close"], dataset["close"], 20))


# ── LC-2 kernels.* — Nadaraya-Watson FIR (startAtBar+2 window quirk) ───────


def _nw_reference(src, start_at_bar, weight):
    """Literal transcription of the Pine KernelFunctions loop (NOT a cleaned-up
    reformulation): `_size = array.size(array.from(_src))` is always 1, so the
    sum runs i = 0..startAtBar+1 inclusive; off-edge history is na and poisons
    the whole bar."""
    n = len(src)
    out = np.empty(n)
    for t in range(n):
        current_weight = 0.0
        cumulative_weight = 0.0
        size = 1
        for i in range(size + start_at_bar + 1):
            y = src[t - i] if t - i >= 0 else float("nan")
            w = weight(i)
            current_weight += y * w
            cumulative_weight += w
        out[t] = current_weight / cumulative_weight
    return out


def test_kernels_rational_quadratic_matches_pine_loop(dataset):
    vals = _line(_run("plot(kernels.rationalQuadratic(close, 8, 2.5, 25))", dataset))
    expected = _nw_reference(
        dataset["close"], 25, lambda i: (1 + (i * i) / (8 * 8 * 2 * 2.5)) ** -2.5
    )
    _close(vals, expected)


def test_kernels_gaussian_matches_pine_loop(dataset):
    vals = _line(_run("plot(kernels.gaussian(close, 6, 25))", dataset))
    expected = _nw_reference(dataset["close"], 25, lambda i: math.exp(-(i * i) / (2 * 6 * 6)))
    _close(vals, expected)


def test_kernels_window_is_start_at_bar_plus_two(dataset):
    # startAtBar=0 → exactly 2 bars regardless of lookback (4).
    vals = _line(_run("plot(kernels.gaussian(close, 4, 0))", dataset))
    x = dataset["close"]
    w1 = math.exp(-1 / (2 * 4 * 4))
    assert math.isnan(vals[0])
    _close(vals[1:], (x[1:] + x[:-1] * w1) / (1 + w1))


def test_kernels_warmup_is_na_through_start_at_bar(dataset):
    vals = _line(_run("plot(kernels.rationalQuadratic(close, 8, 8.0, 25))", dataset))
    assert all(math.isnan(v) for v in vals[:26])
    assert math.isfinite(vals[26])


def test_kernels_zero_lookback_is_all_na_not_error(dataset):
    # IEEE parity with the wasm kernel: 0/0 weights are NaN, never a crash.
    vals = _line(_run("plot(kernels.gaussian(close, 0, 3))", dataset))
    assert all(math.isnan(v) for v in vals)


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


# ── OS4001/OS4002 weighted-budget parity with the TS OperationBudget ───────
#
# Task 6 redefined OperationBudget to the WEIGHTED model:
# OperationBudget(ir, ctx, limits) precomputes per-node weights and charges
# weights[node id] per step. (Security invariants: charged <= estimate, input
# clamp, exact spent/peak_bytes live in test_openscript_budget_invariants.py.)


def _limits(**overrides):
    merged = dict(SCRIPT_LIMITS)
    merged.update(overrides)
    return merged


def _budget_ir(nodes, inputs=None):
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
        "declaration": {"name": "B", "overlay": False},
        "inputs": inputs or [],
        "nodes": nodes,
        "outputs": [],
        "meta": {"warmupBars": 0, "spans": {}},
    }


# source(0), const 50(1), sma(2): weighted perBar = 1 + 0 + 50 + 1(proj) = 52.
_WINDOW_IR = _budget_ir(
    [
        {"id": 0, "op": "source", "source": "close"},
        {"id": 1, "op": "const", "value": 50},
        {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
    ]
)
# close(0) -> close+close(1) -> -(...)(2): three element nodes, weight barCount each.
_ELEMENT_BUDGET_IR = _budget_ir(
    [
        {"id": 0, "op": "source", "source": "close"},
        {"id": 1, "op": "binop", "operator": "+", "args": [0, 0]},
        {"id": 2, "op": "unop", "operator": "-", "arg": 1},
    ]
)


def test_budget_ops_per_bar_exceeded_at_construction():
    ctx = runtime_cost_ctx(_WINDOW_IR, {}, 100)
    with pytest.raises(BudgetExceeded) as ei:
        OperationBudget(_WINDOW_IR, ctx, _limits(maximumOperationsPerBar=2))
    assert ei.value.code == "OS4001"


def test_budget_total_operations_exceeded_on_step():
    # weights over a 100-bar barCount are [100, 100, 100]; cap 250.
    budget = OperationBudget(_ELEMENT_BUDGET_IR, runtime_cost_ctx(_ELEMENT_BUDGET_IR, {}, 100), _limits(maximumTotalOperations=250))
    nodes = _ELEMENT_BUDGET_IR["nodes"]
    budget.step(nodes[0])  # 100
    budget.step(nodes[1])  # 200
    with pytest.raises(BudgetExceeded) as ei:
        budget.step(nodes[2])  # 300 > 250
    assert ei.value.code == "OS4001"
    assert budget.spent() == 300


def test_budget_time_exceeded():
    budget = OperationBudget(_ELEMENT_BUDGET_IR, runtime_cost_ctx(_ELEMENT_BUDGET_IR, {}, 1), _limits(maximumExecutionMilliseconds=0))
    with pytest.raises(BudgetExceeded) as ei:
        budget.step(_ELEMENT_BUDGET_IR["nodes"][0])
    assert ei.value.code == "OS4002"


def test_execute_ir_enforces_budget(dataset):
    result = openscript.compile("plot(ta.ema(close, 20))")
    assert result.ir is not None
    n = len(dataset["close"])
    ctx = runtime_cost_ctx(result.ir, {}, n)
    budget = OperationBudget(result.ir, ctx, _limits(maximumTotalOperations=1))
    with pytest.raises(BudgetExceeded) as ei:
        execute_ir(result.ir, dataset, {}, budget=budget)
    assert ei.value.code == "OS4001"


def test_execute_ir_without_budget_unchanged(dataset):
    # budget is optional — omitting it keeps the pre-parity behavior.
    out = _run("plot(ta.ema(close, 20))", dataset)
    _close(_line(out), ta.ema(dataset["close"], 20))


# ── P-time: time / bar_index / IST calendar series ─────────────────────────
#
# Mirrors the TS executor time/context tests. Instants are independently
# hand-computed (fixed +05:30 offset, verified against a datetime oracle —
# NOT the implementation):
#   I1 2026-07-13 09:15 IST (Mon)                    es=1783914300
#   I2 2026-07-13 00:30 IST (Mon; UTC = Jul-12 Sun)  es=1783882800 (midnight)
#   I3 2026-01-01 08:00 IST (Thu)                    es=1767234600
#   I4 2024-02-29 23:45 IST (leap day, Thu)          es=1709230500
_I1, _I2, _I3, _I4 = 1783914300, 1783882800, 1767234600, 1709230500


def _ctx_dataset(times):
    """Dataset from explicit epoch-SECOND (UTC) bar-open times; OHLC synthetic."""
    n = len(times)
    idx = np.arange(n, dtype=float)
    return {
        "time": np.asarray(times, dtype=float),
        "open": 100.0 + idx,
        "high": 101.0 + idx,
        "low": 99.0 + idx,
        "close": 100.0 + idx,
        "volume": 1000.0 + idx,
    }


def test_time_is_epoch_milliseconds():
    ds = _ctx_dataset([_I1, _I2, _I3, _I4])
    vals = _line(_run("plot(time)", ds))
    np.testing.assert_array_equal(vals, np.asarray([_I1, _I2, _I3, _I4]) * 1000)


def test_bar_index_is_zero_based_ramp():
    ds = _ctx_dataset([_I1, _I2, _I3, _I4, _I1])
    vals = _line(_run("plot(bar_index)", ds))
    np.testing.assert_array_equal(vals, np.arange(len(ds["close"])))


def test_last_bar_index_is_constant_n_minus_1():
    ds = _ctx_dataset([_I1, _I2, _I3, _I4, _I1])
    vals = _line(_run("plot(last_bar_index)", ds))
    last = len(ds["close"]) - 1
    np.testing.assert_array_equal(vals, np.full(len(ds["close"]), last))


def test_calendar_fields_on_known_instants():
    ds = _ctx_dataset([_I1, _I2, _I3, _I4])
    year = _line(_run("plot(year)", ds))
    month = _line(_run("plot(month)", ds))
    dom = _line(_run("plot(dayofmonth)", ds))
    hour = _line(_run("plot(hour)", ds))
    minute = _line(_run("plot(minute)", ds))
    dow = _line(_run("plot(dayofweek)", ds))
    # (year, month, dayofmonth, hour, minute, dayofweek) per bar
    expected = [
        (2026, 7, 13, 9, 15, 2),  # I1 Monday
        (2026, 7, 13, 0, 30, 2),  # I2 Monday, UTC date is Jul-12 (Sunday)
        (2026, 1, 1, 8, 0, 5),  # I3 Thursday
        (2024, 2, 29, 23, 45, 5),  # I4 leap-day Thursday
    ]
    for i, (y, m, d, h, mi, w) in enumerate(expected):
        assert year[i] == y, f"year[{i}]"
        assert month[i] == m, f"month[{i}]"
        assert dom[i] == d, f"dayofmonth[{i}]"
        assert hour[i] == h, f"hour[{i}]"
        assert minute[i] == mi, f"minute[{i}]"
        assert dow[i] == w, f"dayofweek[{i}]"


def test_context_series_are_na_free_from_bar0():
    ds = _ctx_dataset([_I1, _I2, _I3])
    for cid in ("time", "bar_index", "last_bar_index", "dayofweek", "dayofmonth",
                "hour", "minute", "month", "year"):
        vals = _line(_run(f"plot({cid})", ds))
        assert not np.isnan(vals).any(), f"{cid} has leading na"


def test_dayofweek_hist_offset_is_na_on_bar0():
    ds = _ctx_dataset([_I1, _I3, _I4])  # dow = [2, 5, 5]
    prev = _line(_run("plot(dayofweek[1])", ds))
    assert math.isnan(prev[0])
    assert prev[1] == 2
    assert prev[2] == 5


def test_new_day_idiom_change_dayofmonth():
    # 3 intraday bars on 2026-07-13, then 2 on 2026-07-14 (dayofmonth 13,13,13,14,14)
    ds = _ctx_dataset([1783914300, 1783914600, 1783914900, 1784000700, 1784001000])
    vals = _line(_run("plot(ta.change(dayofmonth) != 0 ? 1 : 0)", ds))
    assert list(vals) == [0, 0, 0, 1, 0]


def test_new_week_idiom_across_holiday_skipped_monday():
    # Daily bars Mon-Fri (2026-07-13..17), then a holiday-skipped week whose
    # first bar is Tuesday 2026-07-21, then Wed. dayofweek = [2,3,4,5,6,3,4].
    ds = _ctx_dataset(
        [1783902600, 1783989000, 1784075400, 1784161800, 1784248200, 1784593800, 1784680200]
    )
    vals = _line(_run("plot(dayofweek < dayofweek[1] ? 1 : 0)", ds))
    assert list(vals) == [0, 0, 0, 0, 0, 1, 0]


# ── SuperTrend Cluster migration (P1 Pri2) ─────────────────────────────────
#
# Python mirror of the TS `executor: SuperTrend Cluster migration` block. Reads
# the SAME shared conformance fixture, appends the SAME diagnostic plots, runs it
# over the SAME deterministic dataset, and pins the SAME sampled numbers — so
# both suites agreeing on these literals is the cross-language TS == Python proof.

_STC_FIXTURE = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "openscript"
    / "positive-supertrend-cluster.json"
)
_STC_DIAG_PLOTS = [
    'plot(dLast, "dLast")',
    'plot(d3, "d3")',
    'plot(st3, "st3")',
    'plot(scBu, "scBu")',
    'plot(scBe, "scBe")',
]

# [title, barIndex, expected] — pinned IDENTICALLY in tests/executor.test.ts.
_STC_SAMPLES = [
    ("Cluster Up Trend", 45, 129.6569647584731),
    ("Cluster Up Trend", 60, 144.78293382617522),
    ("Cluster Up Trend", 79, 163.98128373946182),
    ("Cluster Up Trend", 90, 164.3919125830897),
    ("Cluster Down Trend", 100, 170.6285456899888),
    ("Cluster Down Trend", 120, 153.63667524601811),
    ("Cluster Down Trend", 140, 134.12277181214623),
    ("Cluster Down Trend", 155, 119.33904528892262),
    ("dLast", 60, 1),
    ("dLast", 100, -1),
    ("dLast", 155, -1),
    ("d3", 60, 1),
    ("d3", 155, -1),
    ("st3", 45, 132.1622455351587),
    ("st3", 60, 147.26052937214482),
    ("st3", 100, 171.43253219043487),
    ("st3", 155, 116.77278030936407),
    ("scBu", 60, 1),
    ("scBu", 90, 0.6774193548387096),
    ("scBu", 100, 0.2580645161290323),
    ("scBe", 90, 0.32258064516129037),
    ("scBe", 100, 0.7419354838709677),
    ("scBe", 155, 1),
]
_STC_NAN_SAMPLES = [
    ("Cluster Up Trend", 30),  # member-5 ATR(34) still in warmup
    ("Cluster Up Trend", 100),  # dLast == -1 -> up line gated off
    ("Cluster Down Trend", 60),  # dLast == 1 -> down line gated off
]


def _stc_dataset():
    """160-bar triangle wave (up 100->179, down 179->100) with 0.25-step noise —
    the IDENTICAL integer formula the TS mirror uses, so both runtimes see
    bit-identical input."""
    n = 160
    o = np.empty(n)
    h = np.empty(n)
    lo = np.empty(n)
    c = np.empty(n)
    v = np.empty(n)
    for i in range(n):
        mid = 100 + i if i < 80 else 180 - (i - 79)
        close = mid + (((i * 37) % 15) - 7) * 0.25
        open_ = mid + (((i * 53) % 11) - 5) * 0.25
        hi = max(open_, close)
        low = min(open_, close)
        o[i] = open_
        c[i] = close
        h[i] = hi + 1.0
        lo[i] = low - 1.0
        v[i] = 1000 + (i % 50)
    return {"open": o, "high": h, "low": lo, "close": c, "volume": v}


def _stc_source(with_diag=True):
    base = json.loads(_STC_FIXTURE.read_text(encoding="utf-8"))["source"].rstrip()
    return "\n".join([base, *_STC_DIAG_PLOTS]) if with_diag else base


def _stc_lines(source, ds):
    result = openscript.compile(source)
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert errors == [], errors
    outputs = execute_ir(result.ir, ds, {})
    return {o["title"]: o["values"] for o in outputs if o["kind"] == "line"}


@pytest.mark.skipif(not _STC_FIXTURE.is_file(), reason="engine fixture not a sibling")
def test_stc_sampled_values_match_typescript():
    lines = _stc_lines(_stc_source(), _stc_dataset())
    for title, bar, expected in _STC_SAMPLES:
        got = lines[title][bar]
        assert abs(got - expected) < 1e-9 * max(1, abs(expected)), f"{title}[{bar}]: {got} != {expected}"
    for title, bar in _STC_NAN_SAMPLES:
        assert math.isnan(lines[title][bar]), f"{title}[{bar}] expected na"


@pytest.mark.skipif(not _STC_FIXTURE.is_file(), reason="engine fixture not a sibling")
def test_stc_base_member_direction_flips_with_trend():
    d3 = _stc_lines(_stc_source(), _stc_dataset())["d3"]
    # Deep in the uptrend the base member is long; deep in the downtrend, short.
    assert all(d3[i] == 1 for i in range(40, 80)), "uptrend should be long"
    assert all(d3[i] == -1 for i in range(110, 160)), "downtrend should be short"


@pytest.mark.skipif(not _STC_FIXTURE.is_file(), reason="engine fixture not a sibling")
def test_stc_member3_matches_independent_supertrend_reference():
    ds = _stc_dataset()
    lines = _stc_lines(_stc_source(), ds)
    st3, d3 = lines["st3"], lines["d3"]
    # Reference member 3: SMA(hlc3, 8) + ATR(14), factor 2.5, carried per Pine's
    # SuperTrend band logic — from openalgo.ta, independent of the port's scans.
    hlc3 = (ds["high"] + ds["low"] + ds["close"]) / 3.0
    s3 = ta.sma(hlc3, 8)
    atr3 = ta.atr(ds["high"], ds["low"], ds["close"], 14)
    ub = lb = dd = math.nan
    for i in range(len(ds["close"])):
        ub0 = s3[i] + 2.5 * atr3[i]
        lb0 = s3[i] - 2.5 * atr3[i]
        ps = s3[i - 1] if i > 0 else math.nan
        cu = ub0 if math.isnan(ub) else (ub0 if (ub0 < ub or ps > ub) else ub)
        cl = lb0 if math.isnan(lb) else (lb0 if (lb0 > lb or ps < lb) else lb)
        if math.isnan(dd):
            cd = 1.0
        elif dd == -1 and s3[i] > ub:
            cd = 1.0
        elif dd == 1 and s3[i] < lb:
            cd = -1.0
        else:
            cd = dd
        cst = cl if cd == 1 else cu
        if math.isnan(cst):
            assert math.isnan(st3[i]), f"st3[{i}] na"
        else:
            assert abs(st3[i] - cst) < 1e-9 * max(1, abs(cst)), f"st3[{i}]"
            assert d3[i] == cd, f"d3[{i}]"
        ub, lb, dd = cu, cl, cd


@pytest.mark.skipif(not _STC_FIXTURE.is_file(), reason="engine fixture not a sibling")
def test_stc_base_fixture_runs_end_to_end():
    result = openscript.compile(_stc_source(with_diag=False))
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert errors == []
    outputs = execute_ir(result.ir, _stc_dataset(), {})
    lines = sorted(o["title"] for o in outputs if o["kind"] == "line")
    assert lines == ["Cluster Down Trend", "Cluster Up Trend"]
    assert len([o for o in outputs if o["kind"] == "alert"]) == 3


# --- a scalar const argued where a `ta.*` kernel expects a series -------------
#
# Pine allows `ta.crossover(series, 450)` and it is the idiomatic threshold-cross
# test, so it compiles clean by design. The executor passed the raw scalar to a
# kernel that indexes its arguments positionally, which RAISED here (IndexError /
# TypeError) while the TypeScript twin silently produced wrong values. Mirrors
# `tests/executor.test.ts`'s "a scalar const argued where a series is expected".


def _crossover_ref(a, b, n):
    """Pine crossover, longhand: strictly above now, at-or-below on the prior bar."""
    out = np.zeros(n)
    for i in range(1, n):
        if a[i] > b[i] and a[i - 1] <= b[i - 1]:
            out[i] = 1.0
    return out


def _threshold(dataset):
    """Derived from the data, not picked.

    A constant outside the close range is never crossed, so the expectation would
    be all-zeros == all-zeros -- passing while proving nothing. The median is
    crossed many times in both directions; every test still asserts non-vacuity.
    """
    return round(float(np.median(dataset["close"])), 6)


def test_crossover_with_a_scalar_second_argument(dataset):
    n = len(dataset["close"])
    t = _threshold(dataset)
    expected = _crossover_ref(dataset["close"], np.full(n, t), n)
    assert expected.sum() > 0, "vacuous: threshold is never crossed upward"
    values = _line(_run(f'indicator("x")\nplot(ta.crossover(close, {t}) ? 1 : 0)', dataset))
    assert np.array_equal(np.nan_to_num(np.asarray(values)), expected)


def test_crossover_with_a_scalar_first_argument(dataset):
    n = len(dataset["close"])
    t = _threshold(dataset)
    expected = _crossover_ref(np.full(n, t), dataset["close"], n)
    assert expected.sum() > 0, "vacuous: threshold is never crossed downward"
    values = _line(_run(f'indicator("x")\nplot(ta.crossover({t}, close) ? 1 : 0)', dataset))
    assert np.array_equal(np.nan_to_num(np.asarray(values)), expected)


def test_crossunder_and_cross_with_a_scalar(dataset):
    n = len(dataset["close"])
    t = _threshold(dataset)
    over = _crossover_ref(dataset["close"], np.full(n, t), n)
    under = _crossover_ref(np.full(n, t), dataset["close"], n)
    assert over.sum() > 0 and under.sum() > 0

    values = _line(_run(f'indicator("x")\nplot(ta.crossunder(close, {t}) ? 1 : 0)', dataset))
    assert np.array_equal(np.nan_to_num(np.asarray(values)), under)

    values = _line(_run(f'indicator("x")\nplot(ta.cross(close, {t}) ? 1 : 0)', dataset))
    assert np.array_equal(np.nan_to_num(np.asarray(values)), np.maximum(over, under))


# --- G2: timeframe.in_seconds ------------------------------------------------
#
# The shared fixture pins IR shape across the two compilers; it cannot pin the
# VALUE. These do, against the same intervals the TS suite uses, so a divergence
# in the median-delta inference shows up as a value mismatch rather than as an
# identical-looking IR that computes something else.


def _spaced_dataset(step_seconds, n=20):
    time = np.arange(n, dtype=float) * step_seconds + 1_700_000_000
    close = np.full(n, 10.0)
    return {
        "time": time,
        "open": close.copy(),
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.ones(n),
    }


@pytest.mark.parametrize("step", [30, 60, 180, 300, 86_400])
def test_timeframe_in_seconds_reads_the_bar_interval(step):
    outputs = _run('indicator("x")\nplot(timeframe.in_seconds)', _spaced_dataset(step))
    values = np.asarray(_line(outputs))
    assert np.all(values == float(step))


def test_timeframe_in_seconds_ignores_overnight_gaps():
    """The median must not be dragged by weekend/holiday jumps."""
    times, t = [], 1_700_000_000
    for i in range(30):
        times.append(t)
        t += 86_400 if i % 10 == 9 else 300
    n = len(times)
    dataset = {
        "time": np.asarray(times, dtype=float),
        "open": np.full(n, 10.0),
        "high": np.full(n, 11.0),
        "low": np.full(n, 9.0),
        "close": np.full(n, 10.0),
        "volume": np.ones(n),
    }
    values = np.asarray(_line(_run('indicator("x")\nplot(timeframe.in_seconds)', dataset)))
    assert values[0] == 300.0


def test_timeframe_in_seconds_is_na_without_an_inferable_interval():
    values = np.asarray(_line(_run('indicator("x")\nplot(timeframe.in_seconds)', _spaced_dataset(60, n=1))))
    assert np.isnan(values[0])


def test_timeframe_in_seconds_converts_a_duration_to_a_bar_count():
    """The recorded G2 use case: 'how many bars in 30 minutes on this chart'."""
    values = np.asarray(_line(_run('indicator("x")\nplot(1800 / timeframe.in_seconds)', _spaced_dataset(300))))
    assert values[0] == 6.0
