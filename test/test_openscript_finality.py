"""OpenScript finality/repaint conformance — the cross-language drift guard for
Phase 0.4 (design §2–§7).

The first three tests pin the finality lattice primitives. The replay block
loads the SAME `fixtures/finality/*.json` the TS side asserts on
(openalgo-openscript/tests/finality.test.ts; they live in the sibling engine
repo) and requires, byte-identical to TypeScript:
  - the sorted diagnostic-code set (OS5002/OS5003) `openscript.compile(source)`
    produces == `expectDiagnostics`;
  - `ir["meta"]["outputFinality"]` == `expectOutputFinality`;
  - the `ir["meta"]["repaintRisks"]`, normalized span-insensitively to
    target + finality + [{operator, confirmationDelay}], == `expectRepaintRisks`.

Skips (not fails) when the engine repo is not a sibling checkout, matching
`test_openscript_conformance.py`.
"""

import json
from pathlib import Path

import pytest

from services.openscript import openscript
from services.openscript.openscript.finality import LOOKAHEAD_OPS, lub, source_finality


def test_lub():
    assert lub("historical-final", "confirmed") == "confirmed"
    assert lub("confirmed", "provisional") == "provisional"
    assert lub("provisional", "confirmed") == "provisional"


def test_source_finality():
    assert source_finality("close") == "confirmed"
    assert source_finality("hlc3") == "confirmed"
    assert source_finality("bar_index") == "historical-final"


def test_lookahead_ops():
    assert "ta.pivothigh" in LOOKAHEAD_OPS
    assert "ta.sma" not in LOOKAHEAD_OPS


FINALITY_FIXTURES_DIR = (
    Path(__file__).resolve().parents[1].parent / "openalgo-openscript" / "fixtures" / "finality"
)


def _load_finality_fixtures():
    if not FINALITY_FIXTURES_DIR.is_dir():
        return []
    params = []
    for path in sorted(FINALITY_FIXTURES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        params.append(pytest.param(data, id=data["name"]))
    return params


FINALITY_FIXTURES = _load_finality_fixtures()


def _norm_risk(r: dict) -> dict:
    # span-insensitive: target + finality + (operator, delay) per source
    return {
        "target": r["target"],
        "finality": r["finality"],
        "sources": [
            {"operator": s["operator"], "confirmationDelay": s["confirmationDelay"]}
            for s in r["sources"]
        ],
    }


@pytest.mark.skipif(
    not FINALITY_FIXTURES, reason="shared finality fixtures not found (engine repo not a sibling)"
)
@pytest.mark.parametrize("fixture", FINALITY_FIXTURES)
def test_finality_conformance(fixture):
    name = fixture["name"]
    result = openscript.compile(fixture["source"])

    got_codes = sorted(d.code for d in result.diagnostics)
    want_codes = sorted(fixture["expectDiagnostics"])
    assert got_codes == want_codes, f"{name}: got {got_codes}, want {want_codes}"

    assert result.ir is not None, f"{name}: expected a compiled IR"
    meta = result.ir["meta"]
    assert meta.get("outputFinality") == fixture["expectOutputFinality"], (
        f"{name}: outputFinality mismatch — got {meta.get('outputFinality')}, "
        f"want {fixture['expectOutputFinality']}"
    )

    got_risks = [_norm_risk(r) for r in meta.get("repaintRisks", [])]
    want_risks = [_norm_risk(r) for r in fixture.get("expectRepaintRisks", [])]
    assert got_risks == want_risks, f"{name}: repaintRisks mismatch — got {got_risks}, want {want_risks}"
