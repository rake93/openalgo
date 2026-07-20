"""Deployment-controlled PlanCost enforcement mode (Python mirror of
openalgo-openscript/src/runtime/config.ts). Default 'enforce' (the 0.2 exit state
— Task 9 flipped it from the shadow-calibration 'observe' default after the
calibration corpus cleared every cap with headroom; see
openalgo-openscript/docs/openscript-phase0.2-plancost-calibration.md). Override via
env OPENSCRIPT_PLANCOST_MODE=observe."""

from __future__ import annotations

import os


def plancost_mode() -> str:
    env = os.environ.get("OPENSCRIPT_PLANCOST_MODE")
    if env in ("observe", "enforce"):
        return env
    return "enforce"  # Task 9 exit state (was 'observe' during 0.2 shadow calibration)
