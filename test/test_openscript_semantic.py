"""Semantic-pass diagnostics that have no shared-fixture home.

The cross-language identity of OS2010 is guarded by
`fixtures/openscript/sem-unknown-named-arg.json` (replayed by the conformance
suite). What lives here is the property a diagnostic-code fixture cannot express:
that a semantic error suppresses IR, and that arguments the lowering DOES
read are not reported.
"""

from services.openscript import openscript

# --- OS2010: a named argument the compiler does not read ---------------------


def test_unknown_named_arg_is_an_error_and_stops_the_build():
    """FU-1's second half.

    It shipped as a warning first so nothing broke mid-flight. An argument the
    compiler ignores is now refused outright: silently dropping it is what let
    `label_size` sit advertised-and-inert.
    """
    result = openscript.compile('indicator("x")\nplotlevel(close > open, close, bogus_arg=5)')
    codes = [d.code for d in result.diagnostics]
    assert codes == ["OS2010"]
    assert [d.severity for d in result.diagnostics] == ["error"]
    assert result.ir is None


def test_a_semantic_error_still_suppresses_ir():
    """The other half: errors must keep halting, or the gate change went too far."""
    result = openscript.compile('indicator("x")\nplot(nope_undefined)')
    assert any(d.severity == "error" for d in result.diagnostics)
    assert result.ir is None


def test_no_warning_for_arguments_the_lowering_reads():
    src = (
        'indicator("x")\n'
        'plotlevel(close > open, close, "R", color=color.red, width=2, offset=-1,'
        ' right_pad=1, max_kept=3, label="R", label_size=size.large, label_latest_only=true)\n'
        'plot(close, "C", color=color.blue, linewidth=2)\n'
        'y = input.float(1.5, "F", group="G", inline="i", tooltip="t", minval=0, maxval=9, step=0.1)\n'
        "plot(y)"
    )
    assert [d.code for d in openscript.compile(src).diagnostics if d.code == "OS2010"] == []
