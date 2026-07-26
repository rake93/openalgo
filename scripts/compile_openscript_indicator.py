"""Compile one OpenScript source file with the SERVER compiler and report.

The Python twin of the engine's ``tools/compile-indicator.mts``. Run both on the
same ``.os`` file and compare the summaries -- that comparison is the point.

WHY BOTH SIDES MATTER. OpenScript has two independent compilers: the TypeScript
one that runs in the browser editor, and this one, which runs on the server when
an indicator is SAVED. A script that compiles in the editor but not here saves
with ``compiled_ir: null`` and can then never be added to a chart or restored
into a layout -- it looks fine right up until the user tries to use it. That is a
recorded asymmetry, not a hypothetical: see G3 in the engine's
docs/openscript-first-conversion-findings.md, and
test_openscript_ir_conformance.py::test_request_security_is_a_recorded_compiler_asymmetry
which pins the one known case.

What it prints, and why each line earns its place:

  diagnostics   every OS**** with its severity. Zero on both sides is the bar.
  outputs       how many outputs the script DECLARES, by kind. This is the
                Style-tab size and a large part of the plan cost, and it is the
                number that showed the F5M port carrying 30 level outputs before
                terminate.new_session let it collapse to 15.
  per-drawing   extend / terminate / bars / max_kept for each level and zone.
                A ``bars`` that is not None is worth a second look: it is a
                compile-time constant standing in for a duration, so it cannot
                follow the chart interval and is wrong on every interval but the
                one it was computed for.

Run:  uv run python scripts/compile_openscript_indicator.py <file.os>
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Run from anywhere: `scripts/` is not the import root, so put the repo root on
# the path before reaching for `services.` (same bootstrap as extract_broker_token.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.openscript import openscript  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: uv run python scripts/compile_openscript_indicator.py <file.os>")
        return 2

    source = Path(argv[1]).read_text(encoding="utf-8")
    result = openscript.compile(source)

    for d in result.diagnostics:
        print(f"{d.severity} {d.code} {d.message}")
    errors = sum(1 for d in result.diagnostics if d.severity == "error")
    print(f"diagnostics: {len(result.diagnostics)} ({errors} error)")
    print(f"ir: {'present' if result.ir else 'null'}")

    if not result.ir:
        return 1 if errors else 0

    outputs = result.ir["outputs"]
    print(f"outputs: {len(outputs)} {dict(Counter(o['kind'] for o in outputs))}")
    for o in outputs:
        if o["kind"] not in ("level", "zone"):
            continue
        lifetime = {
            "extend": o.get("extend"),
            "terminate": o.get("terminate"),
            "bars": o.get("bars"),
            "maxKept": o.get("maxKept"),
            "labelLatestOnly": o.get("labelLatestOnly"),
        }
        print(f"  {o['kind']} {o['title']!r} {lifetime}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
