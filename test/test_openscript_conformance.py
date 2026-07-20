"""OpenScript conformance — the drift guard between the Python compiler port and
the TypeScript front end (design decision D3).

Replays the SAME `fixtures/openscript/*.json` the TS side asserts on (they live in
the sibling engine repo) and requires identical diagnostic-code sets. This is what
keeps `services/openscript/openscript/` behaviorally equivalent to
`openalgo-openscript/src/compiler/`.
"""

import json
from pathlib import Path

import pytest

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS

FIXTURES_DIR = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "openscript"
)


def _load_fixtures():
    if not FIXTURES_DIR.is_dir():
        return []
    params = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        params.append(pytest.param(data, id=data["name"]))
    return params


FIXTURES = _load_fixtures()


@pytest.mark.skipif(not FIXTURES, reason="shared openscript fixtures not found (engine repo not a sibling)")
@pytest.mark.parametrize("fixture", FIXTURES)
def test_openscript_conformance(fixture):
    result = openscript.compile(fixture["source"])
    got = sorted({d.code for d in result.diagnostics})
    want = sorted(set(fixture["expectDiagnostics"]))
    assert got == want, f"{fixture['name']}: got {got}, want {want}"


def test_script_limits_match_typescript():
    # These MUST stay identical to openalgo-openscript/src/types/limits.ts.
    assert SCRIPT_LIMITS["maximumSourceBytes"] == 100_000
    assert SCRIPT_LIMITS["maximumAstNodes"] == 10_000
    assert SCRIPT_LIMITS["maximumOutputs"] == 64
    assert SCRIPT_LIMITS["maximumInputs"] == 100
    assert SCRIPT_LIMITS["maximumVariables"] == 2_000
    assert SCRIPT_LIMITS["maximumLookback"] == 20_000
    assert SCRIPT_LIMITS["maximumHistoryBars"] == 100_000
    assert SCRIPT_LIMITS["maximumExecutionMemoryMb"] == 256
    assert SCRIPT_LIMITS["maximumObjectsPerOutput"] == 100
    assert SCRIPT_LIMITS["maximumTotalObjects"] == 500
