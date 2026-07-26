"""IR conformance — the drift guard between what the TS front end BUILDS and
what the server's Python compiler builds.

`test_openscript_conformance.py` proves the two front ends agree on what counts
as an error. That is not enough for the reopen contract: after a reload the
browser executes IR that the *Python* compiler produced, so the two must also
agree on the graph itself. Two compilers can emit identical diagnostics for
every fixture and still build different programs.

The reference side is `fixtures/openscript/ir/*.json` in the engine repo,
regenerated and freshness-checked by its own `tests/ir-goldens.test.ts`.

Comparison is canonical rather than literal — see `helpers/ir_canonical.py` for
which differences are conceded and why. Everything else must match: node ops,
operators, kernel names, wiring, outputs, inputs, declaration, header.
"""

import json
from pathlib import Path

import pytest

from services.openscript import openscript
from helpers.ir_canonical import (
    DIVERGENCES,
    canonical_ir,
    canonical_outputs,
    unexplained_fields,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[1].parent / "openalgo-openscript" / "fixtures" / "openscript"
)
GOLDENS_DIR = FIXTURES_DIR / "ir"


def _load_goldens():
    if not GOLDENS_DIR.is_dir():
        return []
    params = []
    for path in sorted(GOLDENS_DIR.glob("*.json")):
        golden = json.loads(path.read_text(encoding="utf-8"))
        source = json.loads((FIXTURES_DIR / path.name).read_text(encoding="utf-8"))["source"]
        params.append(pytest.param(golden, source, id=path.stem))
    return params


GOLDENS = _load_goldens()

pytestmark = pytest.mark.skipif(
    not GOLDENS, reason="TS IR goldens not found (engine repo not a sibling, or not generated)"
)


@pytest.mark.parametrize("golden,source", GOLDENS)
def test_python_ir_matches_the_ts_golden(golden, source):
    result = openscript.compile(source)
    assert result.ir is not None, (
        f"{golden['name']}: the TS compiler produced IR for this source but the Python "
        f"compiler did not. Diagnostics: {[d.code for d in result.diagnostics]}"
    )
    assert canonical_ir(result.ir) == canonical_ir(golden["ir"]), (
        f"{golden['name']}: Python and TS built structurally different programs"
    )


@pytest.mark.parametrize("golden,source", GOLDENS)
def test_no_ir_field_escapes_the_comparison(golden, source):
    """The canonical projection must account for every top-level IR field.

    Without this, adding a field to the IR would silently fall outside the
    comparison on both sides and drift there forever.
    """
    result = openscript.compile(source)
    assert unexplained_fields(result.ir) == set()
    assert unexplained_fields(golden["ir"]) == set()


def test_every_conceded_divergence_is_documented():
    """A field is allowed out of the comparison only with a recorded reason."""
    assert set(DIVERGENCES) == {"sourceHash", "compilerVersion", "meta"}
    for field, reason in DIVERGENCES.items():
        assert reason.strip(), f"{field} is excluded from comparison with no reason recorded"


def test_source_hash_divergence_is_real_and_not_incidental():
    """Pin the conceded `sourceHash` divergence so it cannot quietly change.

    The TS hash is a 53-bit non-sha-256 preview hash; the server's is the
    canonical sha-256 of the same bytes. If TS ever adopts sha-256 this fails,
    and the concession should be removed rather than kept out of habit.
    """
    golden, source = GOLDENS[0].values
    py_ir = openscript.compile(source).ir
    assert py_ir["sourceHash"] != golden["ir"]["sourceHash"]
    assert len(py_ir["sourceHash"]) == 64
    assert len(golden["ir"]["sourceHash"]) == 16


def test_canonical_form_still_distinguishes_a_real_difference():
    """Mutation proof for the guard itself.

    A canonicalizer that collapsed too much would pass every fixture while
    proving nothing. Change one operator in a golden and the comparison must
    fail.
    """
    golden, source = next(
        (g.values for g in GOLDENS if any(n.get("op") == "binop" for n in g.values[0]["ir"]["nodes"])),
        (None, None),
    )
    assert golden is not None, "no fixture with a binop node to mutate"
    mutated = json.loads(json.dumps(golden["ir"]))
    for node in mutated["nodes"]:
        if node.get("op") == "binop":
            node["operator"] = "*" if node["operator"] != "*" else "+"
            break
    assert canonical_outputs(mutated) != canonical_outputs(golden["ir"])


def test_a_reordered_output_is_a_difference():
    """Output order is behaviour: `out_<idx>` style overrides and fill plot
    indices are positional."""
    golden = next((g.values[0] for g in GOLDENS if len(g.values[0]["ir"]["outputs"]) > 1), None)
    assert golden is not None, "no fixture with multiple outputs"
    swapped = json.loads(json.dumps(golden["ir"]))
    swapped["outputs"][0], swapped["outputs"][1] = swapped["outputs"][1], swapped["outputs"][0]
    assert canonical_outputs(swapped) != canonical_outputs(golden["ir"])


def test_request_security_is_a_recorded_compiler_asymmetry():
    """`request.security` compiles on the TS side and does NOT on the server.

    This is the one divergence that changes what a user can do, so it is pinned
    here rather than left to be discovered on a live chart: an HTF script
    previews in the editor, then saves with a null `compiled_ir`, and is
    therefore not reopenable. No shared fixture exercises HTF for exactly this
    reason. If the Python port gains `request.security`, this test fails and the
    limitation should be lifted everywhere it is documented.
    """
    source = (
        'indicator("HTF", overlay=true)\n'
        'htfClose = request.security(syminfo.tickerid, "60", close)\n'
        'plot(htfClose, "H")\n'
    )
    result = openscript.compile(source)
    assert result.ir is None
    assert "OS2002" in {d.code for d in result.diagnostics}
