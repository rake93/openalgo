"""Semantic-pass diagnostics that have no shared-fixture home.

The cross-language identity of OS2010 is guarded by
`fixtures/openscript/warn-unknown-named-arg.json` (replayed by the conformance
suite). What lives here is the property a diagnostic-code fixture cannot express:
that a semantic WARNING still yields IR while a semantic ERROR still does not.
"""

from services.openscript import openscript

# --- OS2010: a named argument the compiler does not read ---------------------


def test_unknown_named_arg_warns_but_still_produces_ir():
    """The load-bearing non-breaking property of the FU-1 migration.

    `compile` used to return `ir=None` whenever the semantic pass produced ANY
    diagnostic, which was indistinguishable from errors-only while semantic
    emitted nothing but errors. OS2010 is the first semantic WARNING, so under
    that gate this advisory would have produced no IR -- a hard break rather than
    a migration warning.
    """
    result = openscript.compile('indicator("x")\nplotlevel(close > open, close, bogus_arg=5)')
    codes = [d.code for d in result.diagnostics]
    assert codes == ["OS2010"]
    assert [d.severity for d in result.diagnostics] == ["warning"]
    assert result.ir is not None


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
