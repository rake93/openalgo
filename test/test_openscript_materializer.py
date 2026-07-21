"""Phase 1 Pri 4 — drawing object-stream materializer value parity (Python port
of openalgo-openscript/tests/materializer.test.ts).

Replays the SAME fixtures/materializer/*.json from the sibling engine checkout
and asserts the numpy executor emits byte-identical ``items[]`` — the drift guard
proving TS and Python materialize the four archetypes + the strict/inclusive
terminate boundary edges identically. Skips (not fails) when the engine repo is
not a sibling checkout, matching the other shared-fixture ports.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from services.openscript import openscript  # noqa: F401  (first, avoids plancost<->ir_gen cycle)
from services.openscript.runtime.executor import (
    _format_draw_number,
    _time_key,
    execute_ir,
)
from services.openscript.runtime.object_diff import diff_object_streams

_FIXDIR = Path(__file__).resolve().parents[1].parent / "openalgo-openscript" / "fixtures" / "materializer"


def _load():
    if not _FIXDIR.is_dir():
        return []
    return sorted(_FIXDIR.glob("*.json"))


_FIXTURES = _load()


def _dataset(d: dict) -> dict:
    return {k: np.asarray(v, dtype=float) for k, v in d.items()}


@pytest.mark.skipif(not _FIXTURES, reason="engine repo not a sibling checkout")
@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.stem)
def test_materializer_items(path):
    fx = json.loads(path.read_text(encoding="utf-8"))
    outputs = execute_ir(fx["ir"], _dataset(fx["dataset"]), {})
    # Native Python numerics throughout (int/float compare equal to the JSON
    # integers), so a direct structural equality is the byte-identical check.
    assert outputs == fx["expectOutputs"], f"{fx['name']}: {outputs} != {fx['expectOutputs']}"


# ── confirmed-spawn finality classification (design 0.5 §5) ───────────────────


def _finality(src: str) -> list:
    result = openscript.compile(src)
    assert result.ir is not None, f"compile failed: {[d.code for d in result.diagnostics]}"
    return result.ir["meta"].get("outputFinality", [])


def test_plain_plotlevel_floors_at_confirmed():
    f = _finality('plotlevel(close > open, close, "L", extend=extend.lastbar)')
    assert f[0] == "confirmed"


def test_lookahead_tainted_level_is_provisional():
    f = _finality('ph = ta.pivothigh(2, 2)\nplotlevel(not na(ph), ph, "PH", extend=extend.lastbar)')
    assert f[0] == "provisional"


# ── source-compiled smoke (real compiler -> materializer) ─────────────────────


def test_compiled_plotlevel_smoke():
    n = 120
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.standard_normal(n))
    dataset = {
        "time": (1_600_000_000 + np.arange(n) * 60).astype(float),
        "open": close - rng.standard_normal(n) * 0.2,
        "high": close + np.abs(rng.standard_normal(n)),
        "low": close - np.abs(rng.standard_normal(n)),
        "close": close.astype(float),
        "volume": np.full(n, 1000.0),
    }
    result = openscript.compile(
        "p = ta.crossover(close, ta.sma(close, 20))\n"
        'plotlevel(p, close, "SR", extend=extend.until, terminate=terminate.close_above)'
    )
    assert result.ir is not None
    outputs = execute_ir(result.ir, dataset, {})
    levels = [o for o in outputs if o["kind"] == "levels"]
    assert len(levels) == 1
    items = levels[0]["items"]
    assert len(items) <= 20  # default max_kept
    assert len({it["id"] for it in items}) == len(items)  # unique ids
    last = n - 1
    for it in items:
        assert it["x1"]["bar"] <= it["x2"]["bar"]
        assert it["x1"]["time"] == dataset["time"][it["x1"]["bar"]]
        if it["open"]:
            assert it["x2"]["bar"] == last
        else:
            assert it["x2"]["bar"] <= last


# ── formatDrawNumber / timeKey byte-identity (Fable #3/#4) ────────────────────
# The SAME (input, output) pairs the TS suite asserts (materializer.test.ts).

_FORMAT_CASES = [
    (0.125, "0.12"),
    (0.625, "0.62"),
    (0.375, "0.38"),
    (-0.125, "-0.12"),
    (2.5, "2.50"),
    (100, "100.00"),
    (0, "0.00"),
    (3.14159, "3.14"),
    (math.inf, "Infinity"),
    (-math.inf, "-Infinity"),
    (1e21, "1000000000000000000000.00"),
    (1e16, "10000000000000000.00"),
    (1e-5, "0.00"),
]
_TIMEKEY_CASES = [
    (1002, "1002"),
    (1000.25, "1000.25"),
    (1000.5, "1000.5"),
    (1600000000, "1600000000"),
    (1600000000.5, "1600000000.5"),
]


@pytest.mark.parametrize("v,s", _FORMAT_CASES)
def test_format_draw_number(v, s):
    assert _format_draw_number(float(v)) == s


def test_format_draw_number_nan():
    assert _format_draw_number(math.nan) == "NaN"


@pytest.mark.parametrize("v,s", _TIMEKEY_CASES)
def test_time_key(v, s):
    assert _time_key(float(v)) == s


# ── malformed non-finite spawn time is skipped, never crashes (Fable #5) ──────


def _level_ir(spawn_lt, extend, bars=None):
    out = {
        "kind": "level", "condNodeId": 2, "priceNodeId": 3, "title": "T",
        "style": {"color": "#000"}, "offset": 0, "rightPad": 0, "extend": extend,
        "maxKept": 20, "labelLatestOnly": False,
    }
    if bars is not None:
        out["bars"] = bars
    return {
        "version": 1, "compilerVersion": "openscript-1.0", "sourceHash": "x",
        "header": {"major": 1, "minor": 0, "compilerVersion": "openscript-1.0",
                   "requiredFeatures": ["drawing-streams"], "numericMode": "f64-strict"},
        "declaration": {"name": "T", "overlay": True}, "inputs": [],
        "nodes": [
            {"id": 0, "op": "source", "source": "bar_index"},
            {"id": 1, "op": "const", "value": spawn_lt},
            {"id": 2, "op": "binop", "operator": "<", "args": [0, 1]},
            {"id": 3, "op": "source", "source": "close"},
        ],
        "outputs": [out], "meta": {"warmupBars": 0, "spans": {}},
    }


def test_malformed_spawn_time_is_skipped():
    dataset = {
        "time": np.array([1000.0, np.nan, 1002.0, 1003.0]),  # bar 1 non-finite
        "open": np.array([10.0, 11.0, 12.0, 13.0]),
        "high": np.array([11.0, 12.0, 13.0, 14.0]),
        "low": np.array([9.0, 10.0, 11.0, 12.0]),
        "close": np.array([10.0, 11.0, 12.0, 13.0]),
        "volume": np.array([1.0, 1.0, 1.0, 1.0]),
    }
    outputs = execute_ir(_level_ir(3, "lastbar"), dataset, {})  # cond bar_index<3
    lv = [o for o in outputs if o["kind"] == "levels"][0]
    assert [it["id"] for it in lv["items"]] == ["0:1000", "0:1002"]


# ── extend.bars projected endpoint: rebase-stable, then commits (Fable #1/#2) ──


def _run_bars(n_bars: int) -> dict:
    ir = _level_ir(2, "bars", 10)
    ir["nodes"][2]["operator"] = "=="  # cond bar_index == 1 → single spawn (id 0:1001)
    ir["nodes"][1]["value"] = 1
    idx = np.arange(n_bars)
    dataset = {
        "time": (1000 + idx).astype(float),
        "open": (10 + idx).astype(float),
        "high": (11 + idx).astype(float),
        "low": (9 + idx).astype(float),
        "close": (10 + idx).astype(float),
        "volume": np.ones(n_bars),
    }
    outs = execute_ir(ir, dataset, {})
    return [o for o in outs if o["kind"] == "levels"][0]


def test_bars_projected_rebase_then_commit():
    a = _run_bars(6)  # lastBarIndex 5 < x2bar 11 → x2.time None
    b = _run_bars(8)  # lastBarIndex 7 < 11 → still None
    c = _run_bars(12)  # lastBarIndex 11 >= 11 → committed
    a1 = next(it for it in a["items"] if it["id"] == "0:1001")
    c1 = next(it for it in c["items"] if it["id"] == "0:1001")
    assert a1["x2"]["bar"] == 11
    assert a1["x2"]["time"] is None
    assert diff_object_streams(a["items"], b["items"]) == []  # rebase-stable
    assert c1["x2"]["time"] == 1011.0
    assert [d["op"] for d in diff_object_streams(b["items"], c["items"])] == ["update"]
