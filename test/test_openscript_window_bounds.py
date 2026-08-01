"""G9 / OS5008 — an input-bound window with no ``maxval`` is priced at
``maximumLookback``, and until now nothing said so.

Mirrors the engine's ``tests/window-bounds.test.ts``. The two MUST agree: the
diagnostic set is part of the shared conformance corpus, so a divergence here
fails ``test_openscript_conformance`` rather than showing up as a UX difference.

MEASURED, not theorised: the Super OrderBlock port sat at 20,312 ops/bar and was
REJECTED at 5,000 bars until one ``maxval=200`` was added, after which it was
512 ops/bar and admitted to 66,844.
"""

from services.openscript import openscript
from services.openscript.limits import SCRIPT_LIMITS


def _codes(source: str) -> list[str]:
    return [d.code for d in openscript.compile(source).diagnostics]


def _os5008(source: str) -> list:
    return [d for d in openscript.compile(source).diagnostics if d.code == "OS5008"]


def test_an_unbounded_window_input_warns():
    d = _os5008('n = input.int(14, "N", minval=1)\nplot(ta.sma(close, n))')
    assert len(d) == 1
    assert d[0].severity == "warning"


def test_the_message_names_the_input_and_the_bound_it_fell_back_to():
    # Naming both is the point: the author has to get from "rejected" to "add
    # maxval to n", and a generic message leaves them where they started.
    (d,) = _os5008('n = input.int(14, "N", minval=1)\nplot(ta.sma(close, n))')
    assert "'n'" in d.message
    assert str(SCRIPT_LIMITS["maximumLookback"]) in d.message
    assert "maxval" in d.message


def test_declaring_maxval_silences_it():
    assert "OS5008" not in _codes('n = input.int(14, "N", minval=1, maxval=200)\nplot(ta.sma(close, n))')


def test_a_const_window_length_never_warns():
    assert "OS5008" not in _codes("plot(ta.sma(close, 14))")


def test_an_input_not_used_as_a_window_length_never_warns():
    # The bound only matters where it is priced. Warning on every unbounded
    # numeric input would be noise, and noise gets trained away.
    assert "OS5008" not in _codes('n = input.int(14, "N", minval=1)\nplot(close * n)')


def test_it_is_never_an_error_so_it_cannot_break_a_working_script():
    r = openscript.compile('n = input.int(14, "N", minval=1)\nplot(ta.sma(close, n))')
    assert r.ir is not None
    assert [d for d in r.diagnostics if d.severity == "error"] == []


def test_one_warning_per_input_not_per_call_site():
    d = _os5008(
        'n = input.int(14, "N", minval=1)\n'
        "plot(ta.sma(close, n) + ta.highest(high, n) + ta.lowest(low, n))"
    )
    assert len(d) == 1


def test_two_different_unbounded_inputs_each_warn():
    d = _os5008(
        'a = input.int(14, "A", minval=1)\nb = input.int(20, "B", minval=1)\n'
        "plot(ta.sma(close, a) + ta.highest(high, b))"
    )
    assert len(d) == 2


def test_a_float_input_used_as_a_window_length_warns_too():
    assert len(_os5008('n = input.float(14, "N", minval=1)\nplot(ta.sma(close, n))')) == 1


def test_it_fires_for_a_multi_length_kernel_priced_on_more_than_one_arg():
    # ta.pivothigh(src, left, right) is priced on BOTH left and right.
    assert len(_os5008('l = input.int(5, "L", minval=1)\nplot(ta.pivothigh(high, l, 2))')) == 1


def test_a_bounded_arg_beside_an_unbounded_one_warns_only_for_the_unbounded_one():
    d = _os5008(
        'l = input.int(5, "L", minval=1, maxval=10)\nr = input.int(5, "R", minval=1)\n'
        "plot(ta.pivothigh(high, l, r))"
    )
    assert len(d) == 1
    assert "'r'" in d[0].message
