"""execution_service — history→dataset conversion and alert-condition extraction
over a compiled OpenScript IR (the pure core the alert scheduler orchestrates).
"""

import numpy as np

from services.openscript import openscript
from services.openscript.execution_service import (
    bar_timestamps,
    find_alert_output,
    fired_on_last_bar,
    history_to_dataset,
)
from services.openscript.runtime.executor import execute_ir


def _rows(closes, opens):
    return [
        {"timestamp": 1_700_000_000 + i * 300, "open": opens[i], "high": max(opens[i], closes[i]) + 1,
         "low": min(opens[i], closes[i]) - 1, "close": closes[i], "volume": 1000 + i}
        for i in range(len(closes))
    ]


def test_history_to_dataset_shapes():
    rows = _rows([100, 101, 102], [99, 100, 103])
    ds = history_to_dataset(rows)
    assert set(ds) == {"open", "high", "low", "close", "volume", "time"}
    np.testing.assert_array_equal(ds["close"], np.array([100.0, 101.0, 102.0]))
    assert ds["close"].dtype == np.float64
    # `time` is the bar-open epoch SECONDS (UTC) — the P-time context series
    # (`time` → ms, calendar fields in IST) derive from this column.
    np.testing.assert_array_equal(
        ds["time"], np.array([1_700_000_000.0, 1_700_000_300.0, 1_700_000_600.0])
    )
    assert bar_timestamps(rows) == [1_700_000_000, 1_700_000_300, 1_700_000_600]


def test_history_to_dataset_time_feeds_context_series():
    # A row timestamped 2026-07-13 09:15 IST (epoch 1783914300) → the `time`
    # context series is ms and the IST calendar fields resolve correctly.
    from services.openscript.runtime.executor import execute_ir

    rows = [
        {"timestamp": 1783914300, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
    ]
    ds = history_to_dataset(rows)
    ir = openscript.compile(
        "plot(time)\nplot(hour * 100 + minute)\nplot(dayofweek)"
    ).ir
    outs = [o for o in execute_ir(ir, ds) if o["kind"] == "line"]
    assert outs[0]["values"][0] == 1783914300 * 1000  # ms
    assert outs[1]["values"][0] == 9 * 100 + 15  # 09:15 IST
    assert outs[2]["values"][0] == 2  # Monday (Pine Sun=1..Sat=7)


def test_alert_fires_on_last_bar():
    # last bar has close(105) > open(100) → the "up" condition fires
    rows = _rows([100, 100, 105], [101, 101, 100])
    ds = history_to_dataset(rows)
    ir = openscript.compile('alertcondition(close > open, "up", "crossed up")').ir
    outputs = execute_ir(ir, ds)
    alert = find_alert_output(outputs, "up")
    assert alert is not None
    assert alert["message"] == "crossed up"
    assert fired_on_last_bar(alert, len(rows)) is True


def test_alert_does_not_fire_when_condition_false_on_last_bar():
    # last bar has close(98) < open(100) → does not fire
    rows = _rows([100, 105, 98], [99, 100, 100])
    ds = history_to_dataset(rows)
    ir = openscript.compile('alertcondition(close > open, "up", "m")').ir
    outputs = execute_ir(ir, ds)
    alert = find_alert_output(outputs, "up")
    assert fired_on_last_bar(alert, len(rows)) is False


def test_unknown_condition_id_returns_none():
    rows = _rows([100, 101], [99, 100])
    ds = history_to_dataset(rows)
    ir = openscript.compile('alertcondition(close > open, "up", "m")').ir
    outputs = execute_ir(ir, ds)
    assert find_alert_output(outputs, "nope") is None
