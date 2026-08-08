"""N16 -- `defval` resolution for every `input.*` constructor.

Python port of `tests/input-defval.test.ts` (openalgo-openscript). `defval` is
admitted as a named argument (`INPUT_NAMED_ARGS`), so
`input.int(title="Length", defval=14)` is legal OpenScript. Before this fix,
`_lower_input` (ir_gen.py) read `call.args[0]` -- the first argument IN CALL
ORDER -- for every arm except `session`, and `_check_string_options`
(semantic.py) did the same for `input.string`'s options check. For a
named-first call that index is `title`, not `defval`, so:
  - int/float/bool silently substituted 0/0/False for the user's declared
    default, with ZERO diagnostics.
  - string's options check false-positived OS2004 by comparing `title`'s
    string value against the declared options.

`input.session` was fixed first, in isolation: named-first, else the first
UNNAMED (positional) argument -- never `call.args[0]` blindly. That rule is
now `defval_of` (services/openscript/openscript/input_defval.py), shared by
both compiler passes and every `input.*` arm, per N16.
"""

import pytest

from services.openscript import openscript

CASES = [
    pytest.param(
        "int",
        'input.int(5, "Length")',
        5,
        'input.int(title="Length", defval=14)',
        14,
        "input.int(5, defval=14)",
        14,
        id="int",
    ),
    pytest.param(
        "float",
        'input.float(1.5, "F")',
        1.5,
        'input.float(title="F", defval=2.5)',
        2.5,
        "input.float(1.5, defval=2.5)",
        2.5,
        id="float",
    ),
    pytest.param(
        "bool",
        'input.bool(true, "B")',
        True,
        'input.bool(title="B", defval=true)',
        True,
        "input.bool(false, defval=true)",
        True,
        id="bool",
    ),
    pytest.param(
        "string",
        'input.string("RSI", "Method")',
        "RSI",
        'input.string(title="Method", defval="MACD")',
        "MACD",
        'input.string("RSI", defval="MACD")',
        "MACD",
        id="string",
    ),
    pytest.param(
        "color",
        'input.color(#ff0000, "C")',
        "#ff0000",
        'input.color(title="C", defval=#00ff00)',
        "#00ff00",
        "input.color(#ff0000, defval=#00ff00)",
        "#00ff00",
        id="color",
    ),
    pytest.param(
        "timeframe",
        'input.timeframe("D", "TF")',
        "D",
        'input.timeframe(title="TF", defval="60")',
        "60",
        'input.timeframe("D", defval="60")',
        "60",
        id="timeframe",
    ),
    pytest.param(
        "source",
        'input.source(close, "Source")',
        "close",
        'input.source(title="Source", defval=open)',
        "open",
        "input.source(close, defval=open)",
        "open",
        id="source",
    ),
    pytest.param(
        "session",
        # Already fixed in isolation before N16 -- included so the shared
        # `defval_of` refactor is proven not to regress it.
        'input.session("0915-1530", "Session")',
        "0915-1530",
        'input.session(title="Session", defval="0930-1530")',
        "0930-1530",
        'input.session("0915-1530", defval="0930-1530")',
        "0930-1530",
        id="session",
    ),
]


def _compile_one(stmt: str):
    return openscript.compile(f'indicator("x")\nx = {stmt}\nplot(close)')


@pytest.mark.parametrize(
    "type_,control,control_expected,named_first,named_first_expected,conflict,conflict_expected",
    CASES,
)
def test_positional_only_call_still_resolves_correctly(
    type_, control, control_expected, named_first, named_first_expected, conflict, conflict_expected
):
    result = _compile_one(control)
    assert [d for d in result.diagnostics if d.severity == "error"] == []
    assert result.ir["inputs"][0]["defaultValue"] == control_expected


@pytest.mark.parametrize(
    "type_,control,control_expected,named_first,named_first_expected,conflict,conflict_expected",
    CASES,
)
def test_named_first_call_resolves_defval_by_name_not_call_position(
    type_, control, control_expected, named_first, named_first_expected, conflict, conflict_expected
):
    result = _compile_one(named_first)
    assert [d for d in result.diagnostics if d.severity == "error"] == []
    assert result.ir["inputs"][0]["defaultValue"] == named_first_expected


@pytest.mark.parametrize(
    "type_,control,control_expected,named_first,named_first_expected,conflict,conflict_expected",
    CASES,
)
def test_conflict_positional_and_named_defval_both_supplied_named_wins(
    type_, control, control_expected, named_first, named_first_expected, conflict, conflict_expected
):
    result = _compile_one(conflict)
    assert [d for d in result.diagnostics if d.severity == "error"] == []
    assert result.ir["inputs"][0]["defaultValue"] == conflict_expected


def test_string_named_first_default_valid_among_options_no_os2004():
    result = _compile_one('input.string(title="Method", defval="MACD", options=["RSI", "MACD"])')
    assert [d for d in result.diagnostics if d.code == "OS2004"] == []
    assert result.ir["inputs"][0]["defaultValue"] == "MACD"
    assert result.ir["inputs"][0]["options"] == ["RSI", "MACD"]


def test_string_named_first_default_invalid_among_options_still_os2004():
    result = _compile_one('input.string(title="Method", defval="STOCH", options=["RSI", "MACD"])')
    err = next((d for d in result.diagnostics if d.code == "OS2004"), None)
    assert err is not None
    assert "input.string default must be one of its declared options" in err.message
