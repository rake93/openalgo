"""Phase 1 Pri 4 — drawing object-stream materializer value parity (Python port
of openalgo-openscript/tests/materializer.test.ts).

Replays the SAME fixtures/materializer/*.json from the sibling engine checkout
and asserts the numpy executor emits byte-identical ``items[]`` — the drift guard
proving TS and Python materialize the four archetypes + the strict/inclusive
terminate boundary edges identically. Skips (not fails) when the engine repo is
not a sibling checkout, matching the other shared-fixture ports.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from services.openscript import openscript  # noqa: F401  (first, avoids plancost<->ir_gen cycle)
from services.openscript.runtime.executor import execute_ir

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
