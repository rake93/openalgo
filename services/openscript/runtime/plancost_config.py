"""Deployment-controlled PlanCost enforcement mode (Python mirror of
openalgo-openscript/src/runtime/config.ts). Default 'observe' during 0.2 shadow
calibration; Task 9 flips the default to 'enforce'. Override via env
OPENSCRIPT_PLANCOST_MODE."""

from __future__ import annotations

import os


def plancost_mode() -> str:
    env = os.environ.get("OPENSCRIPT_PLANCOST_MODE")
    if env in ("observe", "enforce"):
        return env
    return "observe"  # Task 9 -> 'enforce'
