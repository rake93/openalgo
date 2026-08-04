"""Pure Black-76 helpers shared by Gamma Density and GEX Levels."""

import math

import pytest

from services.gex_levels.blackscholes import atm_iv_from, safe_gamma, safe_iv
from services.gex_levels.expiry import expiry_datetime


class _FakeBlack76:
    """Stands in for opengreeks.black76 so these tests need no Rust core."""

    def __init__(self, iv=0.15, gamma=0.0004, raises=False):
        self._iv = iv
        self._gamma = gamma
        self._raises = raises

    def implied_volatility(self, price, F, K, r, t, flag):
        if self._raises:
            raise ValueError("no solution")
        return self._iv

    def gamma(self, flag, F, K, t, r, sigma):
        if self._raises:
            raise ValueError("domain error")
        return self._gamma


class _RecordingBlack76:
    """Records its arguments so a call-site argument-order regression is caught."""

    def __init__(self):
        self.iv_args = None
        self.gamma_args = None

    def implied_volatility(self, price, F, K, r, t, flag):
        self.iv_args = (price, F, K, r, t, flag)
        return 0.18

    def gamma(self, flag, F, K, t, r, sigma):
        self.gamma_args = (flag, F, K, t, r, sigma)
        return 0.0004


def test_safe_iv_returns_the_inverted_value():
    assert safe_iv(_FakeBlack76(iv=0.184), 120.0, 24600.0, 24600.0, 0.065, 0.02, "c") == 0.184


@pytest.mark.parametrize("bad", [0.0, -0.1, 5.5, float("nan"), float("inf")])
def test_safe_iv_rejects_implausible_values(bad):
    assert safe_iv(_FakeBlack76(iv=bad), 120.0, 24600.0, 24600.0, 0.065, 0.02, "c") is None


@pytest.mark.parametrize(
    "price,F,K,t",
    [
        (0.0, 24600.0, 24600.0, 0.02),
        (120.0, 0.0, 24600.0, 0.02),
        (120.0, 24600.0, 0.0, 0.02),
        (120.0, 24600.0, 24600.0, 0.0),
    ],
)
def test_safe_iv_rejects_non_positive_inputs(price, F, K, t):
    assert safe_iv(_FakeBlack76(), price, F, K, 0.065, t, "c") is None


def test_safe_iv_swallows_solver_failure():
    assert safe_iv(_FakeBlack76(raises=True), 120.0, 24600.0, 24600.0, 0.065, 0.02, "c") is None


def test_safe_gamma_returns_the_value():
    assert (
        safe_gamma(_FakeBlack76(gamma=0.00031), "c", 24600.0, 24600.0, 0.02, 0.065, 0.15) == 0.00031
    )


@pytest.mark.parametrize("bad", [-0.001, float("nan"), float("inf")])
def test_safe_gamma_floors_bad_values_at_zero(bad):
    assert safe_gamma(_FakeBlack76(gamma=bad), "c", 24600.0, 24600.0, 0.02, 0.065, 0.15) == 0.0


def test_safe_gamma_swallows_failure():
    assert safe_gamma(_FakeBlack76(raises=True), "c", 24600.0, 24600.0, 0.02, 0.065, 0.15) == 0.0


def test_safe_iv_forwards_its_arguments_in_the_library_order():
    fake = _RecordingBlack76()
    safe_iv(fake, 120.0, 24600.0, 24500.0, 0.065, 0.02, "c")
    assert fake.iv_args == (120.0, 24600.0, 24500.0, 0.065, 0.02, "c")


def test_safe_gamma_forwards_its_arguments_in_the_library_order():
    fake = _RecordingBlack76()
    safe_gamma(fake, "p", 24600.0, 24500.0, 0.02, 0.065, 0.15)
    assert fake.gamma_args == ("p", 24600.0, 24500.0, 0.02, 0.065, 0.15)


def test_atm_iv_prefers_the_atm_strike():
    per_strike = {24500.0: 0.20, 24600.0: 0.17, 24700.0: 0.22}
    assert atm_iv_from(per_strike, atm_strike=24600.0) == 0.17


def test_atm_iv_falls_back_to_the_median_when_atm_is_unpriced():
    per_strike = {24500.0: 0.10, 24600.0: None, 24700.0: 0.30, 24800.0: 0.20}
    assert atm_iv_from(per_strike, atm_strike=24600.0) == 0.20


def test_atm_iv_falls_back_to_the_constant_when_nothing_is_priced():
    assert atm_iv_from({24600.0: None}, atm_strike=24600.0) == 0.15


def test_atm_iv_takes_the_upper_middle_of_an_even_length_sample():
    """Not a true median by design - kept as-is so Gamma Density's numbers do not move."""
    per_strike = {1.0: 0.10, 2.0: 0.20, 3.0: 0.30, 4.0: 0.40}
    assert atm_iv_from(per_strike, atm_strike=None) == 0.30


@pytest.mark.parametrize(
    "exchange,hour,minute",
    [("NFO", 15, 30), ("BFO", 15, 30), ("CDS", 12, 30), ("MCX", 23, 30)],
)
def test_expiry_datetime_uses_the_exchange_close(exchange, hour, minute):
    dt = expiry_datetime("11AUG26", exchange)
    assert (dt.year, dt.month, dt.day) == (2026, 8, 11)
    assert (dt.hour, dt.minute) == (hour, minute)


def test_expiry_datetime_accepts_lowercase_month():
    assert expiry_datetime("11aug26", "NFO").month == 8
