"""Indicator drawing-geometry fixture harness -- Python replay.

Port of ``openalgo-openscript/tests/drawing-geometry.test.ts``. Both runtimes replay
the SAME fixtures, so a divergence in how TypeScript and Python materialize drawing
objects fails here rather than on a user's chart.

WHY THIS EXISTS. The first Pine conversion taken to live pixels shipped four
authoring bugs -- an over-capturing opening window, a session reset that spawned only
once, an off-by-one that ended every session's lines on the NEXT session's opening
bar, and two non-exclusive output sets that drew the current session twice. All four
are drawing geometry; none was expressible as a test. The instructive one is the
session reset: an early check passed because it asserted against the LATEST spawn,
which was the only spawn that existed. So a fixture here pins the WHOLE ascending
item list of every drawing output, never a selected item.

See the TypeScript header for the full fixture-shape contract. In brief, each fixture
carries bars, instrument metadata (which selects the execution calendar via G7's
resolver -- no calendar ever enters the IR), inputs, EITHER ``source`` OR an
authoritative ``ir``, and the normalized expected geometry:

    levels item  {x1bar, x2bar, price, open, label}
    zones  item  {x1bar, x2bar, top, bottom, open, mitigated, text}

Bar indices and booleans only. ``time`` anchors are deliberately NOT encoded (they
are a pure function of the bar index and the fixture's own ``bars.time``); the
invariant is derived and asserted structurally by ``_check_anchors`` instead.
"""

import json
from pathlib import Path

import numpy as np
import pytest

# The compiler package must be imported before `runtime.executor`, exactly as every
# sibling openscript test does. `runtime.plancost` and `openscript.ir_gen` import each
# other, so whichever side is entered first wins; entering from `runtime` raises a
# partially-initialized-module ImportError. Pre-existing fragility, not introduced here.
from services.openscript import openscript
from services.openscript.runtime.calendar import calendar_for_instrument
from services.openscript.runtime.executor import execute_ir

# The platform carries its OWN committed copy so the parity guard cannot vanish on a
# machine where the engine repo is not checked out beside it (platform CI, Docker, a
# client box). The engine copy stays authoritative: when the sibling IS present, the
# drift test below FAILS if the two differ, which is what keeps the copy honest.
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "drawing-geometry"
ENGINE_FIXTURE_DIR = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "drawing-geometry"
)

FIXTURE_FILES = sorted(FIXTURE_DIR.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_local_fixture_copy_matches_the_engine_original():
    """The platform's committed copy must not drift from the engine's authoritative one.

    Skips only when the engine repo is genuinely absent, and the replay below still
    runs off the local copy, so no coverage is lost when it skips.
    """
    if not ENGINE_FIXTURE_DIR.is_dir():
        pytest.skip("engine repo not beside the platform; the local copy is replayed anyway")
    local = sorted(p.name for p in FIXTURE_DIR.glob("*.json"))
    engine = sorted(p.name for p in ENGINE_FIXTURE_DIR.glob("*.json"))
    assert local == engine, "fixture files added/removed on one side only"
    for name in local:
        # Parsed equality catches semantic drift; the normalized-text comparison also
        # catches comment/ordering churn, since these are meant to be byte-for-byte
        # copies (line endings normalized so a git autocrlf checkout cannot fail it).
        local_path = FIXTURE_DIR / name
        engine_path = ENGINE_FIXTURE_DIR / name
        assert _load(local_path) == _load(engine_path), f"{name}: parsed content differs"
        assert local_path.read_bytes().replace(b"\r\n", b"\n") == engine_path.read_bytes().replace(
            b"\r\n", b"\n"
        ), f"{name}: bytes differ"


def test_the_fixture_case_count_is_exact():
    """Exact, not >=: these fixtures are the only thing preventing TS/Python drift and
    the only expression of the G1 matrix, so deleting one must fail loudly rather than
    quietly shrink the suite."""
    assert len(FIXTURE_FILES) == 17


def test_every_fixture_name_matches_its_filename():
    for path in FIXTURE_FILES:
        assert _load(path)["name"] == path.stem


def _dataset(bars: dict) -> dict:
    return {
        key: np.asarray(bars[key], dtype=float)
        for key in ("time", "open", "high", "low", "close", "volume")
    }


def _normalize_drawings(outputs: list[dict]) -> list[dict]:
    """Reduce the drawing outputs to the language-neutral shape the fixtures encode."""
    result = []
    for o in outputs:
        if o["kind"] == "levels":
            result.append(
                {
                    "kind": "levels",
                    "title": o["title"],
                    "items": [
                        {
                            "x1bar": int(it["x1"]["bar"]),
                            "x2bar": int(it["x2"]["bar"]),
                            "price": float(it["price"]),
                            "open": bool(it["open"]),
                            "label": it.get("label"),
                        }
                        for it in o["items"]
                    ],
                }
            )
        elif o["kind"] == "zones":
            result.append(
                {
                    "kind": "zones",
                    "title": o["title"],
                    "items": [
                        {
                            "x1bar": int(it["x1"]["bar"]),
                            "x2bar": int(it["x2"]["bar"]),
                            "top": float(it["top"]),
                            "bottom": float(it["bottom"]),
                            "open": bool(it["open"]),
                            "mitigated": bool(it.get("mitigated", False)),
                            "text": it.get("text"),
                        }
                        for it in o["items"]
                    ],
                }
            )
    return result


def _check_anchors(outputs: list[dict], dataset: dict) -> None:
    """The `time` invariant the fixtures deliberately do not encode (see the module
    docstring): an anchor's time is the bar's time when the bar is in the dataset,
    else None."""
    last = len(dataset["close"]) - 1

    def expected(bar: int):
        return float(dataset["time"][bar]) if 0 <= bar <= last else None

    for o in outputs:
        if o["kind"] not in ("levels", "zones"):
            continue
        for it in o["items"]:
            for anchor in (it["x1"], it["x2"]):
                actual = anchor["time"]
                want = expected(int(anchor["bar"]))
                if want is None:
                    assert actual is None
                else:
                    assert actual is not None and float(actual) == want


def _run_fixture(fx: dict):
    """Compile-or-take the program, execute it under the fixture's instrument
    calendar, and return the normalized geometry plus the raw outputs."""
    assert (fx["source"] is None) != (
        fx["ir"] is None
    ), f"{fx['name']}: exactly one of source/ir must be set"
    if fx["source"] is not None:
        result = openscript.compile(fx["source"])
        errors = [d for d in result.diagnostics if d.severity == "error"]
        assert result.ir is not None and not errors, (
            f"{fx['name']}: compile failed: "
            + "; ".join(f"{d.code} {d.message}" for d in errors)
        )
        ir = result.ir
    else:
        ir = fx["ir"]
    dataset = _dataset(fx["bars"])
    calendar = calendar_for_instrument(
        exchange=fx["instrument"]["exchange"], symbol=fx["instrument"]["symbol"]
    ).calendar
    outputs = execute_ir(ir, dataset, fx["inputs"], calendar=calendar)
    return _normalize_drawings(outputs), outputs, dataset


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_drawing_geometry_matches_the_shared_fixture(path):
    fx = _load(path)
    geometry, outputs, dataset = _run_fixture(fx)
    assert geometry == fx["expect"]["drawings"], fx["note"]
    _check_anchors(outputs, dataset)


# -- the UTC/IST acceptance pair ----------------------------------------------------
#
# G1's acceptance claim (design §9, item 5) is a statement about a PAIR of fixtures and
# so cannot live inside either one: the same bars and the same program must produce
# different geometry under different calendars. These guard the pair's integrity -- if
# someone ever "fixes" the two red fixtures by reconciling their expectations, that is
# not a fix and this fails.


def _pair():
    return (
        _load(FIXTURE_DIR / "red-new-session-calendar-ist.json"),
        _load(FIXTURE_DIR / "red-new-session-calendar-utc.json"),
    )


def test_the_acceptance_pair_differs_only_by_instrument():
    ist, utc = _pair()
    assert utc["bars"] == ist["bars"]
    assert utc["source"] == ist["source"]
    assert utc["instrument"] != ist["instrument"]


def test_the_acceptance_pair_resolves_to_genuinely_different_calendars():
    ist, utc = _pair()
    a = calendar_for_instrument(**{k: ist["instrument"][k] for k in ("exchange", "symbol")})
    b = calendar_for_instrument(**{k: utc["instrument"][k] for k in ("exchange", "symbol")})
    assert a.calendar.utc_offset_seconds == 19800
    assert b.calendar.utc_offset_seconds == 0
    # Neither may be a silent fallback: an IST result that is a fallback and an IST
    # result that is a real mapping are the same number and not the same answer.
    assert a.provenance == "mapped"
    assert b.provenance == "mapped"


def test_the_acceptance_pair_expects_different_geometry():
    ist, utc = _pair()
    assert utc["expect"]["drawings"] != ist["expect"]["drawings"]
