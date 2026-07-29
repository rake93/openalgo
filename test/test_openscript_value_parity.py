"""Cross-language VALUE parity (engine register C3, steps 2-4).

The shared corpora pin what the two front ends DIAGNOSE and what shape of IR they
emit. They have never pinned what the two executors COMPUTE. Until this file, the
only numeric TS<->Python pins in the project were hand-duplicated per-file
assertions in the SuperTrend Cluster tests -- exactly the duplication C3 exists
to retire.

The fixtures in `fixtures/values/*.json` are authored by the TS side (the
reference DAG executor, per the spine's "one IR, two DAG executors" topology) via
`GEN_VALUE_FIXTURES=1 npx vitest run tests/value-parity.test.ts`. This module
replays them and must match.

Each series carries a `digest` covering EVERY bar and `samples` at the fixture's
`at` indices. The digest is the assertion; the samples exist so a failure can say
WHICH bar drifted, because a bare cross-language hash mismatch is close to
undebuggable.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from services.openscript import openscript
from services.openscript.runtime.executor import execute_ir
from services.openscript.runtime.value_digest import digest_series

ENGINE = Path(__file__).resolve().parents[1].parent / "openalgo-openscript"
VALUES = ENGINE / "fixtures" / "values"
DATASETS = ENGINE / "fixtures" / "datasets"


def _fixtures():
    if not VALUES.is_dir():
        return []
    return sorted(VALUES.glob("*.json"))


FIXTURES = _fixtures()

pytestmark = pytest.mark.skipif(
    not FIXTURES,
    reason="value fixtures not found (engine repo not a sibling)",
)


def _decode(v):
    """JSON -> float, honouring the fixture's explicit NaN / negative-zero encoding."""
    if v is None:
        return float("nan")
    if isinstance(v, str):
        if v == "-0":
            return -0.0
        raise AssertionError(f"unknown sentinel in fixture: {v!r}")
    return float(v)


def _load_dataset(name):
    """Build the dataset from the shared columnar file.

    Uses the file's OWN `time` column, exactly as the TS harness does. Both sides
    must agree here: a script reading `time`, `dayofweek` or any calendar context
    would otherwise compute different values in the two runtimes for reasons that
    have nothing to do with the engine.
    """
    raw = json.loads((DATASETS / f"{name}.json").read_text(encoding="utf-8"))
    return {
        "time": np.asarray(raw["time"], dtype=float),
        "open": np.asarray(raw["open"], dtype=float),
        "high": np.asarray(raw["high"], dtype=float),
        "low": np.asarray(raw["low"], dtype=float),
        "close": np.asarray(raw["close"], dtype=float),
        "volume": np.asarray(raw["volume"], dtype=float),
    }


def _series_by_title(outputs):
    """Numeric-series outputs keyed by title. v1 covers the kinds carrying
    `values`; markers, alerts and drawing streams have their own corpora."""
    series = {}
    for o in outputs:
        values = o.get("values")
        if values is None:
            continue
        assert o["title"] not in series, f"duplicate output title: {o['title']}"
        series[o["title"]] = np.asarray(values, dtype=float)
    return series


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_python_values_match_the_ts_fixture(path):
    fx = json.loads(path.read_text(encoding="utf-8"))

    result = openscript.compile(fx["source"])
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert not errors, f"fixture source must compile: {[d.code for d in errors]}"
    assert result.ir is not None

    outputs = execute_ir(result.ir, _load_dataset(fx["dataset"]), fx.get("inputs") or {})
    series = _series_by_title(outputs)

    assert set(series) == set(fx["outputs"]), (
        "the set of numeric series differs from the TS reference: "
        f"python={sorted(series)} ts={sorted(fx['outputs'])}"
    )

    rel = fx["tolerance"]["rel"]
    for title, values in series.items():
        pinned = fx["outputs"][title]

        # Samples first: when both fail, this is the message that names a bar,
        # which is the entire reason samples sit alongside the digest.
        for k, bar in enumerate(fx["at"]):
            actual = float(values[bar])
            expected = _decode(pinned["samples"][k])
            if math.isnan(expected):
                assert math.isnan(actual), f"{title}[{bar}] expected NaN, got {actual}"
            else:
                assert abs(actual - expected) < rel * max(1.0, abs(expected)), (
                    f"{title}[{bar}]: python {actual} vs TS-pinned {expected}"
                )

        assert digest_series(values) == pinned["digest"], (
            f"{title}: full-series digest diverged from the TS reference. "
            "The sampled bars above localize it; the digest covers every bar."
        )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_fixture_is_not_vacuous(path):
    """A fixture whose every pinned sample is NaN still gates via the digest but
    reports a hash mismatch with no bar to look at. Sparse operators make that
    easy to hit by accident -- the first `at` set chosen for the calibrated-ta
    fixture missed every `ta.pivotlow`, and it would only have surfaced during a
    real divergence, when it is least welcome. Mirrors the TS-side guard.
    """
    fx = json.loads(path.read_text(encoding="utf-8"))
    assert fx["outputs"], f"{path.name}: pins no series at all"
    for title, pinned in fx["outputs"].items():
        finite = [s for s in pinned["samples"] if _decode(s) == _decode(s)]  # NaN != NaN
        assert finite, (
            f"{title}: every pinned sample is NaN — a digest failure would name no bar. "
            "Choose `at` indices that hit real values."
        )
