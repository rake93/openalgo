"""`request.security` compile surface, cross-language (register C4 step 2).

Replays `fixtures/htf/surface.json`, authored by the TS compiler. The shared
conformance corpora pin diagnostic CODES; this pins the MESSAGE TEXT that the
parity backlog asks to be identical, plus the builtins-table entries the feature
adds.

Why the tables matter as much as the messages: a table that drifts silently is how
one front end starts accepting what the other rejects. That is the exact failure
mode `request.security` already caused once -- G3, where the browser compiled it
and the server did not, so a script previewed fine and then saved with a null IR.
"""

import json
from pathlib import Path

import pytest

from services.openscript.openscript import builtins_table
from services.openscript.openscript.diagnostics import DIAGNOSTIC_CODES

FIXTURE = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "htf"
    / "surface.json"
)


def _load():
    if not FIXTURE.is_file():
        return None
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


FX = _load()

pytestmark = pytest.mark.skipif(
    FX is None, reason="HTF surface fixture not found (engine repo not a sibling)"
)


@pytest.mark.parametrize(
    "code", [pytest.param(c, id=c) for c in ("OS2024", "OS2025", "OS2026", "OS2027", "OS2028")]
)
def test_diagnostic_message_matches_the_ts_text(code):
    expected = FX["diagnostics"][code]
    assert code in DIAGNOSTIC_CODES, f"{code} is missing from the Python diagnostics table"
    assert DIAGNOSTIC_CODES[code] == expected, (
        f"{code} message diverged:\n  python: {DIAGNOSTIC_CODES[code]!r}\n  ts:     {expected!r}"
    )


def test_the_fixture_pins_all_five_codes():
    """Non-vacuity: a fixture that lost its diagnostics section would make every
    parametrized case above vanish rather than fail."""
    assert len(FX["diagnostics"]) == 5
    assert all(m for m in FX["diagnostics"].values()), "an empty message pins nothing"


def test_request_functions_match():
    assert sorted(builtins_table.REQUEST_FUNCTIONS) == FX["requestFunctions"]


def test_htf_source_kinds_match():
    """The set of series a same-symbol HTF request may sample. A missing kind here
    means a script that compiles in the browser is rejected on the server."""
    assert sorted(builtins_table.HTF_SOURCE_KINDS) == FX["htfSourceKinds"]


@pytest.mark.parametrize("ns", ["syminfo", "barmerge"])
def test_constant_namespace_members_match(ns):
    assert sorted(builtins_table.CONSTANT_NAMESPACES[ns]) == FX["constantNamespaces"][ns]


def test_the_new_namespaces_are_known():
    """`request`, `syminfo` and `barmerge` must resolve as member-expression objects.

    Only the namespaces THIS feature adds are compared. The full sets legitimately
    differ between the two front ends: TS carries editor-only entries (`timeframe`)
    that drive completion and never reach compilation, which is why this asserts
    membership rather than set equality.
    """
    for ns in FX["namespacesAddedByThisFeature"]:
        assert ns in builtins_table.KNOWN_NAMESPACES, f"{ns} is not a known namespace in Python"


def test_lookahead_on_is_a_rejectable_member_not_an_absent_one():
    """`barmerge.lookahead_on` must RESOLVE and then fail the semantic check.

    If it were simply absent from the table the user would get OS2001 "unknown
    identifier", which says nothing about lookahead being deliberately unsupported.
    Knowing the identifier is what lets OS2028 say so.
    """
    assert "lookahead_on" in builtins_table.CONSTANT_NAMESPACES["barmerge"]
    assert "lookahead_off" in builtins_table.CONSTANT_NAMESPACES["barmerge"]
    assert "lookahead_off" in DIAGNOSTIC_CODES["OS2028"]
