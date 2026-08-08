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


# -- session lowering: a targeted probe, not a registry entry -------------------------
#
# The session lowering (ir_gen.py `_session_*`/`_day_mask_*`) synthesizes several
# integer-semantic constants: the session's open/close minutes, the day constants
# 1..7 (both the contiguous-range bounds and the sparse OR-chain), the `60`
# hour->minute multiplier, `first_bar`'s bar-zero `0`, and `bars_in`'s scan seed
# `0` and its `+1`. All of these are correct ints today.
#
# They cannot be added to INTEGER_SEMANTIC, though. Every one of them is emitted
# through `_const_num` into a plain `{"op": "const", "value": N}` node — the exact
# same shape as EVERY OTHER numeric literal in the IR (palette indices, a plain
# `x = 5`, a plotted `1`/`0`), and `_emit`'s content-addressed CSE means a
# session's `0` and an unrelated literal `0` elsewhere in the same program can
# collapse onto the very same node. Nothing on the node records where it came
# from. `INTEGER_SEMANTIC` is keyed by field path and walked corpus-wide
# (`integer_semantic_values` above), so declaring `nodes.const.value` would force
# EVERY const in EVERY fixture — including genuinely-float literals like
# `input.float`'s own value — to be strictly int, which is exactly the
# over-broad inference `test_a_float_input_keeps_float_metadata` exists to rule
# out. Provenance the registry could key on does not survive `_emit`, so this
# stays a targeted probe instead of a registry entry.
def _scan_expr_const_values(expr) -> list:
    """Every scalar under a scan-mini-language `{"k": "const", "v": ...}` leaf,
    found by walking the expr tree generically (its dict keys — `c`/`t`/`e` for
    `select`, `a`/`b` for `bin` — are an implementation detail this need not
    know)."""
    if not isinstance(expr, dict):
        return []
    values = [expr["v"]] if expr.get("k") == "const" else []
    for v in expr.values():
        if isinstance(v, dict):
            values.extend(_scan_expr_const_values(v))
    return values


SESSION_PROBE = (
    # Contiguous day range ("23456" -> Mon-Fri) -> the `_day_mask_literal`
    # lo/hi-bound specialization, plus open/close minutes and the `60`
    # multiplier via `_minute_of_day_node`.
    'plot(session.contains("0915-1530:23456"), "C1")\n'
    # Sparse day mask ("1357") -> the OR-chain specialization: one const per
    # admitted day.
    'plot(session.contains("0915-1530:1357"), "C2")\n'
    # `first_bar`'s bar-zero `0`.
    'plot(session.first_bar("0915-1530:23456"), "F")\n'
    # `bars_in`'s scan: seed `0`, and the mini-language's own `1`/`0` consts.
    'plot(session.bars_in("0915-1530:23456"), "B")\n'
    # Input-bound path: `_day_mask_input`'s full 1..7 disjunction and the
    # `_field_node`-based open/close (no consts there, by design).
    'sess = input.session("0915-1530:23456", title="Session")\n'
    'plot(session.contains(sess), "C3")\n'
    'plot(session.bars_in(sess), "B2")\n'
)


def test_session_lowering_emits_its_constants_as_ints():
    ir = _compile(SESSION_PROBE)

    const_nodes = [n for n in ir["nodes"] if n["op"] == "const" and n["value"] is not None]
    assert const_nodes, "the probe compiled to no const nodes; this test would be vacuous"
    non_int = [(n["id"], n["value"]) for n in const_nodes if not isinstance(n["value"], int)]
    assert non_int == [], f"session constant(s) emitted as float, not int: {non_int}"

    scan_nodes = [n for n in ir["nodes"] if n["op"] == "scan"]
    assert scan_nodes, "the probe compiled to no scan node; this test would be vacuous"
    for n in scan_nodes:
        assert isinstance(n["init"], int), (n["id"], n["init"])
        expr_values = _scan_expr_const_values(n["expr"])
        assert expr_values, "the scan expr yielded no const leaves; this test would be vacuous"
        non_int_expr = [v for v in expr_values if not isinstance(v, int)]
        assert non_int_expr == [], f"scan node {n['id']} expr const(s) emitted as float: {non_int_expr}"
