"""OpenScript server executor parity — the numpy executor must reproduce direct
`openalgo.ta` calls exactly (same kernels, same args), proving the IR DAG wiring
(arg assembly, implicit sources, tuple output slicing) is correct. Mirrors the TS
executor test that checks against the wasm facade.
"""

import numpy as np
import pytest
from openalgo import ta

from services.indicator_engine import openscript
from services.indicator_engine.runtime.executor import execute_ir


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


def test_input_override(dataset):
    out = _run('len = input.int(9, "Length")\nplot(ta.ema(close, len))', dataset, {"len": 20})
    _close(_line(out), ta.ema(dataset["close"], 20))
    out_default = _run('len = input.int(9, "Length")\nplot(ta.ema(close, len))', dataset)
    _close(_line(out_default), ta.ema(dataset["close"], 9))
