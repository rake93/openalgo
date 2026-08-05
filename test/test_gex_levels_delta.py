"""Delta exposure: the Black-76 helper and the per-strike DEX profile."""

import math

from services.gex_levels.blackscholes import safe_delta


class _Delta:
    """Delta independent of strike, so DEX arithmetic is checkable by hand."""

    def __init__(self, call=0.6, put=-0.4):
        self._call = call
        self._put = put

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def delta(self, flag, F, K, t, r, sigma):
        return self._call if flag == "c" else self._put


class _Raises:
    def delta(self, flag, F, K, t, r, sigma):
        raise RuntimeError("solver blew up")


class _RecordingBlack76:
    """Records its arguments so a call-site argument-order regression is caught."""

    def __init__(self):
        self.delta_args = None

    def delta(self, flag, F, K, t, r, sigma):
        self.delta_args = (flag, F, K, t, r, sigma)
        return -0.4


def test_a_put_delta_stays_negative():
    """The single most important guard in this file. safe_gamma rejects
    negatives because gamma cannot be negative; copying that rule here would
    silently delete the entire put side of every delta profile."""
    d = safe_delta(_Delta(), "p", 24600.0, 24600.0, 0.02, 0.065, 0.20)
    assert d == -0.4


def test_a_call_delta_is_returned_unchanged():
    assert safe_delta(_Delta(), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.6


def test_non_positive_inputs_yield_zero_rather_than_raising():
    stub = _Delta()
    assert safe_delta(stub, "c", 0.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(stub, "c", 24600.0, 0.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(stub, "c", 24600.0, 24600.0, 0.0, 0.065, 0.20) == 0.0
    assert safe_delta(stub, "c", 24600.0, 24600.0, 0.02, 0.065, 0.0) == 0.0


def test_a_solver_exception_yields_zero():
    assert safe_delta(_Raises(), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0


def test_a_non_finite_delta_yields_zero():
    assert safe_delta(_Delta(call=math.nan), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(_Delta(call=math.inf), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0


def test_an_implausible_delta_yields_zero():
    """Black-76 delta is bounded by +/-1. Anything well outside that is a solver
    artefact, not a position."""
    assert safe_delta(_Delta(call=7.5), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(_Delta(put=-7.5), "p", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0


def test_delta_forwards_its_arguments_in_the_library_order():
    """_Delta ignores every argument, so a call-site transposition (e.g. t and
    r, which safe_iv orders the other way round from safe_gamma/safe_delta)
    would pass every other test while feeding the real library garbage."""
    stub = _RecordingBlack76()
    safe_delta(stub, "p", 24600.0, 24500.0, 0.02, 0.065, 0.20)
    assert stub.delta_args == ("p", 24600.0, 24500.0, 0.02, 0.065, 0.20)


def test_delta_at_the_plausibility_boundary_is_pinned_from_both_sides():
    """A deep-ITM leg can legitimately sit right at +/-1 - the reviewer
    measured black76.delta('c', 24600, 10000, 0.0001, 0.065, 0.05) =
    0.9999935 - and those carry the heaviest weight in a delta profile, so
    +/-1.0 must pass through unchanged. +/-1.6 is past the plausibility bound
    and must be rejected, for both signs."""
    assert safe_delta(_Delta(call=1.0), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 1.0
    assert safe_delta(_Delta(put=-1.0), "p", 24600.0, 24600.0, 0.02, 0.065, 0.20) == -1.0
    assert safe_delta(_Delta(call=1.6), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(_Delta(put=-1.6), "p", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
