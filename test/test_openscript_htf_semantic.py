"""Semantic checks for `request.security` (register C4 step 3).

The five checks the design specifies, each with its own diagnostic:

    OS2024  symbol must be `syminfo.tickerid` (same-symbol only)
    OS2025  timeframe must be a compile-time constant string (or an input.timeframe var)
    OS2026  the timeframe string does not parse
    OS2027  the expression must be a source series, optionally `[n]`
    OS2028  lookahead must be `barmerge.lookahead_off`

These are Python-local for now. The SHARED conformance fixtures land with step 4
(ir-gen), per the parity-backlog rule that a shared fixture must not precede the
port: a positive fixture has to produce IR on both sides, which it cannot until
lowering exists.

The step-3 boundary is deliberate and asserted at the bottom: a *valid* call now
passes semantics with no diagnostics but still yields no IR.
"""

import pytest

from services.openscript import openscript

HEAD = 'indicator("HTF", overlay=true)\n'


def _codes(source):
    return sorted({d.code for d in openscript.compile(HEAD + source).diagnostics})


def _errors(source):
    result = openscript.compile(HEAD + source)
    return sorted({d.code for d in result.diagnostics if d.severity == "error"})


# ── OS2024: same-symbol only ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "symbol",
    ['"NSE:INFY"', "syminfo.ticker", "close", "1", '""'],
    ids=["string-literal", "wrong-member", "series", "number", "empty-string"],
)
def test_os2024_symbol_must_be_syminfo_tickerid(symbol):
    assert "OS2024" in _codes(f'x = request.security({symbol}, "60", close)\nplot(x)\n')


def test_os2024_accepts_syminfo_tickerid():
    assert "OS2024" not in _codes('x = request.security(syminfo.tickerid, "60", close)\nplot(x)\n')


# ── OS2025 / OS2026: the timeframe argument ────────────────────────────────


@pytest.mark.parametrize(
    "tf", ["60", "close", "tfVar"], ids=["bare-number", "series", "undeclared-identifier"]
)
def test_os2025_timeframe_must_be_a_const_string(tf):
    assert "OS2025" in _codes(f"x = request.security(syminfo.tickerid, {tf}, close)\nplot(x)\n")


@pytest.mark.parametrize(
    "tf", ['"5m"', '"x"', '"0"', '"-5"', '"D1"', '""', '"2D"'],
    ids=["5m", "junk", "zero", "negative", "D1", "empty", "multi-day"],
)
def test_os2026_unparseable_timeframe_string(tf):
    """`"2D"` is here on purpose: `parse_timeframe` rejects multi-unit calendar
    timeframes (only D/1D, W/1W, M/1M parse), so it is an OS2026 at the surface even
    though `bucket_key` implements the arithmetic. Same behaviour both sides."""
    assert "OS2026" in _codes(f"x = request.security(syminfo.tickerid, {tf}, close)\nplot(x)\n")


@pytest.mark.parametrize("tf", ['"5"', '"60"', '"1440"', '"D"', '"1D"', '"W"', '"M"'])
def test_valid_timeframe_strings_are_accepted(tf):
    codes = _codes(f"x = request.security(syminfo.tickerid, {tf}, close)\nplot(x)\n")
    assert "OS2025" not in codes
    assert "OS2026" not in codes


def test_an_input_timeframe_variable_is_accepted():
    """A runtime-resolved timeframe: the value is not known at compile time, so the
    const-string rule is relaxed for a variable bound to input.timeframe."""
    src = 'i_tf = input.timeframe("60", "TF")\nx = request.security(syminfo.tickerid, i_tf, close)\nplot(x)\n'
    codes = _codes(src)
    assert "OS2025" not in codes
    assert "OS2026" not in codes


def test_an_input_string_variable_is_also_accepted():
    src = 'i_tf = input.string("60", "TF")\nx = request.security(syminfo.tickerid, i_tf, close)\nplot(x)\n'
    assert "OS2025" not in _codes(src)


def test_a_plain_string_variable_is_NOT_accepted():
    """Only input-bound identifiers are exempt. An ordinary variable holding a
    string literal is still OS2025 -- the compiler does not const-fold through
    assignment here, and both sides must agree on that limit."""
    src = 'x = request.security(syminfo.tickerid, tfLocal, close)\nplot(x)\n'
    assert "OS2025" in _codes(src)


# ── OS2027: the source expression ──────────────────────────────────────────


@pytest.mark.parametrize(
    "expr",
    ["open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "time",
     "close[1]", "high[5]"],
)
def test_valid_htf_source_expressions(expr):
    assert "OS2027" not in _codes(
        f'x = request.security(syminfo.tickerid, "60", {expr})\nplot(x)\n'
    )


@pytest.mark.parametrize(
    "expr",
    ["close + 1", "1", '"close"', "bar_index"],
    ids=["arithmetic", "number", "string", "context-series"],
)
def test_os2027_rejects_a_non_source_expression(expr):
    """OS2027 stays the FALLBACK for genuinely arbitrary expressions."""
    assert "OS2027" in _codes(f'x = request.security(syminfo.tickerid, "60", {expr})\nplot(x)\n')


def test_os2027_rejects_an_empty_array_form():
    src = '[a] = request.security(syminfo.tickerid, "60", [])\nplot(a)\n'
    assert "OS2027" in _codes(src)


def test_the_tuple_array_form_is_accepted():
    src = '[h, l] = request.security(syminfo.tickerid, "60", [high, low])\nplot(h)\nplot(l)\n'
    assert "OS2027" not in _codes(src)


def test_os2034_rejects_a_bad_element_inside_the_array_form():
    """The tuple path must agree with the single form -- both route through one
    `_admit_htf_source`, which is why they cannot disagree."""
    src = '[h, x] = request.security(syminfo.tickerid, "60", [high, ta.ema(close, 9)])\nplot(h)\nplot(x)\n'
    assert "OS2034" in _codes(src)


# ── OS2028: lookahead ──────────────────────────────────────────────────────


def test_os2028_rejects_lookahead_on_named():
    src = 'x = request.security(syminfo.tickerid, "60", close, lookahead = barmerge.lookahead_on)\nplot(x)\n'
    assert "OS2028" in _codes(src)


def test_os2028_rejects_lookahead_on_positional():
    src = 'x = request.security(syminfo.tickerid, "60", close, barmerge.lookahead_on)\nplot(x)\n'
    assert "OS2028" in _codes(src)


def test_lookahead_off_is_accepted():
    src = 'x = request.security(syminfo.tickerid, "60", close, lookahead = barmerge.lookahead_off)\nplot(x)\n'
    assert "OS2028" not in _codes(src)


def test_omitting_lookahead_is_accepted():
    assert "OS2028" not in _codes('x = request.security(syminfo.tickerid, "60", close)\nplot(x)\n')


def test_a_gaps_member_is_not_mistaken_for_lookahead():
    """`barmerge.gaps_*` is a different policy. Passing it where lookahead goes is
    still OS2028 (it is not lookahead_off), which is the TS behaviour: the check is
    'any barmerge member that is not lookahead_off'."""
    src = 'x = request.security(syminfo.tickerid, "60", close, lookahead = barmerge.gaps_on)\nplot(x)\n'
    assert "OS2028" in _codes(src)


# ── unknown request.* function ─────────────────────────────────────────────


def test_an_unknown_request_function_is_os2002():
    assert "OS2002" in _codes('x = request.dividends(syminfo.tickerid, "60")\nplot(x)\n')


# ── the step-3 boundary ────────────────────────────────────────────────────


def test_a_valid_call_compiles_to_an_htf_node():
    """Semantics AND lowering, together.

    This test originally asserted `ir is None` -- the expected half-way state after
    semantics but before lowering. That state turned out to be actively DANGEROUS
    and the two steps had to land together: with semantics passing and no `htf`
    lowering, Python's ir-gen fell through to its `const null` default, so an HTF
    script compiled clean and produced a plot that was silently all-na. No
    diagnostic, no rejection -- strictly worse than the loud OS2002 it replaced, and
    a direct violation of the spine's "no silent degradation" rule.

    Hence the assertion here is the real end state: a valid call yields an `htf`
    node and declares the feature.
    """
    result = openscript.compile(
        HEAD + 'x = request.security(syminfo.tickerid, "60", close)\nplot(x)\n'
    )
    assert [d.code for d in result.diagnostics if d.severity == "error"] == []
    assert result.ir is not None
    htf = [n for n in result.ir["nodes"] if n["op"] == "htf"]
    assert len(htf) == 1, f"expected exactly one htf node, got {len(htf)}"
    assert htf[0]["source"] == "close"
    assert htf[0]["offset"] == 0
    assert htf[0]["timeframe"] == {"unit": "min", "multiple": 60}
    assert "request-security" in result.ir["header"]["requiredFeatures"]


def test_no_output_node_is_ever_a_bare_const_na_for_a_valid_htf_call():
    """The regression guard for the hazard described above.

    If the `htf` lowering is ever removed or bypassed while semantics still accept
    the call, ir-gen's fallback produces `{"op": "const", "value": None}` and the
    plot goes silently blank. This asserts the plotted node is a real htf node, so
    that failure mode cannot come back quietly.
    """
    result = openscript.compile(
        HEAD + 'x = request.security(syminfo.tickerid, "60", close)\nplot(x, "H")\n'
    )
    nodes = {n["id"]: n for n in result.ir["nodes"]}
    plotted = [o for o in result.ir["outputs"] if o["kind"] == "plot"]
    assert plotted, "no plot output"
    for out in plotted:
        node = nodes[out["nodeId"]]
        assert node["op"] == "htf", (
            f"the plotted node is {node['op']} (value={node.get('value')!r}), not htf — "
            "the request.security lowering is being bypassed and this plot is silently na"
        )


def test_os2034_rejects_a_ta_call_outside_the_inner_allowlist():
    """Was OS2027 until 2026-08-18. `ta.ema(...)` is no longer an "arbitrary
    expression": it is a RECOGNISED kernel the v1 inner subset declines, and
    OS2034 names the admitted set instead of leaving the author to guess. The
    parametrised OS2027 cases above are the non-vacuity proof that the fallback
    still fires."""
    src = 'x = request.security(syminfo.tickerid, "60", ta.ema(close, 9))\nplot(x)\n'
    assert "OS2034" in _codes(src)
