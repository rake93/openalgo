"""Integer-semantic IR fields are emitted as INTs, corpus-wide.

The defect this closes: the Python lexer makes every numeric literal a float, so
`offset=-2` lowered to `-2.0`, `x1 = spawn + offset` became a float, and indexing
the numpy time column raised `IndexError` — every drawing with a non-zero offset
crashed the server while previewing fine in the browser.

It was invisible to the IR-conformance guard because Python compares
`-2 == -2.0` as equal, so the golden "matched" while the serialized IR differed,
against a contract that says byte-identical. `_as_bar_count` fixed `offset`,
`right_pad` and `max_kept` at source — and its own docstring claimed `bars` too,
which never routed through it. That is the failure mode this file exists to make
impossible: a fix whose coverage is asserted in prose.

Measured before writing this: of 13 integer-semantic fields, **6 were emitted as
float** — `bars`, `lineWidth` on both plot and level, and all three
`inputs[integer]` metadata fields.
"""

import json
from pathlib import Path

import pytest
from helpers.ir_integers import (
    EXERCISED_BY,
    INTEGER_SEMANTIC,
    float_valued,
    integer_semantic_values,
)

from services.openscript import openscript

FIXTURES_DIR = (
    Path(__file__).resolve().parents[1].parent / "openalgo-openscript" / "fixtures" / "openscript"
)


def _compile(source: str) -> dict:
    result = openscript.compile(source)
    assert result.ir is not None, [d.code for d in result.diagnostics]
    return result.ir


def _sources():
    """Every conformance-fixture source that compiles, as (id, source)."""
    if not FIXTURES_DIR.is_dir():
        return []
    out = []
    for path in sorted(FIXTURES_DIR.glob("positive-*.json")):
        out.append(pytest.param(json.loads(path.read_text(encoding="utf-8"))["source"], id=path.stem))
    return out


SOURCES = _sources()


# -- the registry itself --------------------------------------------------------------


def test_every_declared_field_is_actually_exercised():
    """NON-VACUITY, and the reason this file is not just prose.

    `_as_bar_count`'s docstring listed `bars` as covered while `bars` bypassed
    it entirely. A registry nobody reaches is that same defect with more
    ceremony, so every entry must fire on `EXERCISED_BY`.
    """
    reached = {field for field, _ in integer_semantic_values(_compile(EXERCISED_BY))}
    missing = sorted(INTEGER_SEMANTIC - reached)
    assert not missing, f"declared integer-semantic but never exercised: {missing}"


def test_the_extractor_reports_nothing_outside_the_registry():
    reached = {field for field, _ in integer_semantic_values(_compile(EXERCISED_BY))}
    assert reached <= INTEGER_SEMANTIC, f"extracted an undeclared field: {sorted(reached - INTEGER_SEMANTIC)}"


# -- the invariant --------------------------------------------------------------------


def test_the_probe_script_emits_every_integer_semantic_field_as_an_int():
    pairs = integer_semantic_values(_compile(EXERCISED_BY))
    assert pairs, "the probe compiled to nothing; this test would be vacuous"
    assert float_valued(pairs) == []


@pytest.mark.skipif(not SOURCES, reason="engine fixtures not present beside the platform")
@pytest.mark.parametrize("source", SOURCES)
def test_no_shipped_script_emits_an_integer_semantic_field_as_a_float(source):
    """Corpus sweep. The probe proves the coercions exist; this proves no real
    script reaches an uncoerced path the probe happens to miss."""
    assert float_valued(integer_semantic_values(_compile(source))) == []


def test_a_float_input_keeps_float_metadata():
    """The registry is about MEANING, not about whole numbers.

    `input.float(1.5, minval=0)` serializes its min as `0` on the TS side purely
    because JS has no int type. Coercing it here would be inferring the contract
    from JSON rather than from what the field means, and would make the engine
    claim an integer bound it does not have.
    """
    ir = _compile('f = input.float(1.5, "F", minval=0, maxval=10)\nplot(close)')
    decl = next(d for d in ir["inputs"] if d["id"] == "f")
    assert decl["type"] == "float"
    assert isinstance(decl["min"], float)
    assert isinstance(decl["max"], float)


def test_bars_is_an_int_because_it_is_added_to_a_bar_index():
    """`extend.bars` is the same class as `offset`: `x2 = spawn + bars`. It did
    not crash — verified — but it was the one field `_as_bar_count` promised and
    skipped, so it was the next one waiting."""
    ir = _compile('plotlevel(bar_index == 1, close, "L", extend=extend.bars, bars=4)')
    assert isinstance(ir["outputs"][0]["bars"], int)
