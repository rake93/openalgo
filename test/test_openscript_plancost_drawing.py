"""Phase 0.5 — IR-based drawing-cost conformance (Python mirror of
tests/plancost-drawing.test.ts). Replays the SAME fixtures/plancost-drawing/*.json
from the sibling engine checkout. Skips (not fails) when the engine repo is not a
sibling checkout, matching test_openscript_plancost_conformance.py."""
import json
from pathlib import Path

import pytest

from services.openscript import openscript  # noqa: F401  (imported first to avoid the plancost↔ir_gen circular import)
from services.openscript.runtime.cost_expr import eval_cost_expr
from services.openscript.runtime.plancost import admission_cost_ctx, estimate_plan_cost

_FIXDIR = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "plancost-drawing"
)


def _load_fixtures():
    if not _FIXDIR.is_dir():
        return []
    return sorted(_FIXDIR.glob("*.json"))


_FIXTURES = _load_fixtures()


@pytest.mark.skipif(not _FIXTURES, reason="engine repo not a sibling checkout")
@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.stem)
def test_drawing_cost(path):
    fx = json.loads(path.read_text())
    dim = estimate_plan_cost(fx["ir"])["dims"]["objectLifecycleChecks"]
    if fx["expectObjectLifecycleChecks"] == "n/a":
        assert dim == "n/a"
    else:
        got = eval_cost_expr(dim, admission_cost_ctx(fx["ir"], fx["barCount"]))
        assert got == fx["expectObjectLifecycleChecks"]
