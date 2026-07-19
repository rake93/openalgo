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
    assert set(ds) == {"open", "high", "low", "close", "volume"}
    np.testing.assert_array_equal(ds["close"], np.array([100.0, 101.0, 102.0]))
    assert ds["close"].dtype == np.float64
    assert bar_timestamps(rows) == [1_700_000_000, 1_700_000_300, 1_700_000_600]


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
