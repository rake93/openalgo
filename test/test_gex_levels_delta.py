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
