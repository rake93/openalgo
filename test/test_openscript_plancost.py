"""Phase 0.2 PlanCost mode config — mirrors
openalgo-openscript/tests/plancost-config.test.ts.
"""

from services.openscript.runtime.plancost_config import plancost_mode


def test_plancost_mode_defaults_to_observe():
    assert plancost_mode() == "observe"
