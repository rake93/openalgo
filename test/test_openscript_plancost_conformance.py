"""OpenScript PlanCost conformance — the cross-language drift guard for the
Phase 0.2 weighted-budget admission gate (design doc, Task 8).

Replays the SAME `fixtures/plancost/*.json` the TS side asserts on
(openalgo-openscript/tests/plancost-conformance.test.ts; they live in the
sibling engine repo) and requires:
  - `openscript.compile(source)` produces a clean IR (no diagnostics);
  - the three PlanCost dimensions (totalOperations, perBarOperations,
    estimatedPeakBytes), evaluated via `eval_cost_expr` against the
    `admission_cost_ctx` for the fixture's barCount/limits, are BYTE-IDENTICAL
    to `expectCostVector` — the same numbers the TS side asserts, proving
    cross-language exact parity;
  - `resolve_plan_cost(ir, barCount, mergedLimits, "enforce")` produces the
    expected verdict;
  - when `forgedMetaPlanCost` is present, stamping it onto
    `ir["meta"]["planCost"]` leaves the verdict UNCHANGED and sets
    `embeddedMismatch` — admission never trusts the embedded telemetry.

Skips (not fails) when the engine repo is not a sibling checkout, matching
`test_openscript_conformance.py`.
"""

import json
from pathlib import Path

import pytest

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.admit import resolve_plan_cost
from services.openscript.runtime.cost_expr import eval_cost_expr
from services.openscript.runtime.plancost import admission_cost_ctx, estimate_plan_cost

FIXTURES_DIR = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "plancost"
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


def _merged_limits(overrides):
    merged = dict(SCRIPT_LIMITS)
    if overrides:
        merged.update(overrides)
    return merged


def _assert_verdict(res, expect_verdict, name):
    codes = [e["code"] for e in res["errors"]]
    if expect_verdict == "admit":
        assert res["errors"] == [], f"{name}: expected admit, got {codes}"
    else:
        assert expect_verdict in codes, f"{name}: expected {expect_verdict} in {codes}"


@pytest.mark.skipif(
    not FIXTURES, reason="shared plancost fixtures not found (engine repo not a sibling)"
)
@pytest.mark.parametrize("fixture", FIXTURES)
def test_plancost_conformance(fixture):
    name = fixture["name"]
    result = openscript.compile(fixture["source"])
    assert result.diagnostics == [], f"{name}: unexpected diagnostics: {result.diagnostics}"
    assert result.ir is not None
    ir = result.ir

    bar_count = fixture["barCount"]
    limits = _merged_limits(fixture.get("limits"))

    ctx = admission_cost_ctx(ir, bar_count, limits)
    cost = estimate_plan_cost(ir)
    vector = {
        "totalOperations": eval_cost_expr(cost["totalOperations"], ctx),
        "perBarOperations": eval_cost_expr(cost["perBarOperations"], ctx),
        "estimatedPeakBytes": eval_cost_expr(cost["estimatedPeakBytes"], ctx),
    }
    assert vector == fixture["expectCostVector"], (
        f"{name}: cost vector mismatch — got {vector}, want {fixture['expectCostVector']}"
    )

    res = resolve_plan_cost(ir, bar_count, limits, "enforce")
    _assert_verdict(res, fixture["expectVerdict"], name)

    forged = fixture.get("forgedMetaPlanCost")
    if forged is not None:
        forged_ir = dict(ir)
        forged_ir["meta"] = dict(ir["meta"])
        forged_ir["meta"]["planCost"] = forged
        forged_res = resolve_plan_cost(forged_ir, bar_count, limits, "enforce")
        _assert_verdict(forged_res, fixture["expectVerdict"], f"{name} (forged)")
        assert forged_res["embeddedMismatch"] is True, f"{name}: expected embeddedMismatch"
