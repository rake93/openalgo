"""The zero-gamma scan: re-price the profile across hypothetical forwards."""

from services.gex_levels.exposure import ChainRow
from services.gex_levels.levels import scan_zero_gamma


class _PeakedGamma:
    """
    Gamma peaked at the strike, so the sign of the aggregate profile depends on
    where the hypothetical forward sits relative to the call and put strikes.
    Crude but monotone, which is all the scan needs to be exercised.
    """

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def gamma(self, flag, F, K, t, r, sigma):
        return 1.0 / (1.0 + abs(F - K))


def _split_chain():
    """Puts concentrated low, calls concentrated high - a profile that must cross."""
    return [
        ChainRow(
            strike=24000.0,
            call_price=10.0,
            put_price=100.0,
            call_oi=0,
            put_oi=10000,
            call_volume=0,
            put_volume=10000,
            lot_size=75,
        ),
        ChainRow(
            strike=25000.0,
            call_price=100.0,
            put_price=10.0,
            call_oi=10000,
            put_oi=0,
            call_volume=10000,
            put_volume=0,
            lot_size=75,
        ),
    ]


def test_a_crossing_profile_returns_a_price_between_the_strikes():
    level = scan_zero_gamma(
        _PeakedGamma(),
        _split_chain(),
        forward=24500.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24500.0,
        weight_by="oi",
    )
    assert level is not None
    assert 24000.0 < level < 25000.0


def test_the_level_need_not_land_on_a_strike():
    """Interpolation between scan steps is what makes a sub-strike level possible."""
    level = scan_zero_gamma(
        _PeakedGamma(),
        _split_chain(),
        forward=24500.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24500.0,
        weight_by="oi",
    )
    assert level not in (24000.0, 25000.0)


def test_a_one_sided_profile_reports_no_crossing():
    """All calls, no puts: gamma is positive everywhere, so there is no flip."""
    calls_only = [
        ChainRow(
            strike=24000.0,
            call_price=100.0,
            put_price=0.0,
            call_oi=10000,
            put_oi=0,
            call_volume=10000,
            put_volume=0,
            lot_size=75,
        ),
        ChainRow(
            strike=25000.0,
            call_price=100.0,
            put_price=0.0,
            call_oi=10000,
            put_oi=0,
            call_volume=10000,
            put_volume=0,
            lot_size=75,
        ),
    ]
    assert (
        scan_zero_gamma(
            _PeakedGamma(),
            calls_only,
            forward=24500.0,
            t_years=0.02,
            r=0.065,
            atm_strike=24500.0,
            weight_by="oi",
        )
        is None
    )


def test_an_empty_chain_reports_no_crossing():
    assert (
        scan_zero_gamma(
            _PeakedGamma(),
            [],
            forward=24500.0,
            t_years=0.02,
            r=0.065,
            atm_strike=24500.0,
            weight_by="oi",
        )
        is None
    )


def test_a_non_positive_forward_reports_no_crossing():
    assert (
        scan_zero_gamma(
            _PeakedGamma(),
            _split_chain(),
            forward=0.0,
            t_years=0.02,
            r=0.065,
            atm_strike=24500.0,
            weight_by="oi",
        )
        is None
    )


def test_the_level_lies_inside_the_scan_range():
    forward = 24500.0
    level = scan_zero_gamma(
        _PeakedGamma(),
        _split_chain(),
        forward=forward,
        t_years=0.02,
        r=0.065,
        atm_strike=24500.0,
        weight_by="oi",
    )
    assert forward * 0.8 <= level <= forward * 1.2
