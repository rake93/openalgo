"""Compile OpenScript sources with the SERVER's Python compiler and print the IR.

Exists so a Node test can obtain genuinely Python-produced IR rather than a
hand-written approximation of it. `frontend/src/lib/charts/ir-consumability.test.ts`
spawns this, feeds the built worker the result, and compares its outputs against
the same sources compiled by the TS front end.

Reads `[{"name": ..., "source": ...}, ...]` on stdin and writes
`{"<name>": {"ir": <ir or null>, "diagnostics": [...]}}` on stdout. Nothing else
is written to stdout, so the caller can parse it directly.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.openscript import openscript  # noqa: E402


def main() -> int:
    requests = json.load(sys.stdin)
    out = {}
    for request in requests:
        result = openscript.compile(request["source"])
        out[request["name"]] = {
            "ir": result.ir,
            "diagnostics": [d.to_dict() for d in result.diagnostics],
        }
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
