"""The zero-gamma scan: re-price the profile across hypothetical forwards."""

import pytest

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


class _RecordingGamma:
    """Records every sigma it is priced with, to prove the scan holds them fixed."""

    def __init__(self):
        self.sigmas = []

    def implied_volatility(self, price, F, K, r, t, flag):
        # A forward-dependent IV, so re-inverting at a hypothetical forward
        # would produce a DIFFERENT sigma and the assertion below would fail.
        return 0.10 + (F / 1_000_000.0)

    def gamma(self, flag, F, K, t, r, sigma):
        self.sigmas.append(sigma)
        return 1.0 / (1.0 + abs(F - K))


def test_the_scan_holds_volatility_fixed_at_the_real_forward():
    fake = _RecordingGamma()
    scan_zero_gamma(
        fake,
        _split_chain(),
        forward=24500.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24500.0,
        weight_by="oi",
    )
    assert fake.sigmas, "the scan priced nothing"
    # Every sigma must be one resolved at the REAL forward, not at a scan level.
    assert len(set(fake.sigmas)) <= 2, (
        f"volatility moved with the scan: {sorted(set(fake.sigmas))[:5]}"
    )


class _TwiceCrossing:
    """Aggregate gamma engineered to change sign at 21000 and again at 25000.

    safe_gamma clamps a negative gamma to zero, so neither leg may go negative.
    The sign pattern is carried by the DIFFERENCE between the two legs instead:
    both stay comfortably positive while call minus put traces a parabola whose
    roots are the two crossings.
    """

    LOW_ROOT = 21000.0
    HIGH_ROOT = 25000.0

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def gamma(self, flag, F, K, t, r, sigma):
        shape = (F - self.LOW_ROOT) * (F - self.HIGH_ROOT) / 1e8
        return 1.0 + shape / 2.0 if flag == "c" else 1.0 - shape / 2.0


def _balanced_strike():
    """One strike, equal call and put weight, so the net is the leg difference."""
    return [
        ChainRow(
            strike=24500.0,
            call_price=100.0,
            put_price=100.0,
            call_oi=1000,
            put_oi=1000,
            call_volume=0,
            put_volume=0,
            lot_size=75,
        )
    ]


def test_the_crossing_nearest_the_forward_wins():
    """A profile can flip more than once. The regime boundary is the adjacent
    crossing, not the lowest-priced one."""
    level = scan_zero_gamma(
        _TwiceCrossing(),
        _balanced_strike(),
        forward=24500.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24500.0,
        weight_by="oi",
    )
    assert level is not None
    # 25000 is 500 away from the forward; 21000 is 3500 away and is the one a
    # first-crossing scan walking up from 19600 would have returned.
    assert level == pytest.approx(25000.0, abs=10.0)
    assert abs(level - 24500.0) < abs(_TwiceCrossing.LOW_ROOT - 24500.0)


def test_both_crossings_are_genuinely_in_the_window():
    """Guards the fixture itself: if only one root were reachable the test above
    would pass for the wrong reason."""
    forward = 24500.0
    for root in (_TwiceCrossing.LOW_ROOT, _TwiceCrossing.HIGH_ROOT):
        assert forward * 0.8 < root < forward * 1.2

    # Centre the scan on the low root and it must be the one that comes back.
    level = scan_zero_gamma(
        _TwiceCrossing(),
        _balanced_strike(),
        forward=_TwiceCrossing.LOW_ROOT,
        t_years=0.02,
        r=0.065,
        atm_strike=24500.0,
        weight_by="oi",
    )
    assert level == pytest.approx(_TwiceCrossing.LOW_ROOT, abs=10.0)


def test_a_single_crossing_profile_is_unchanged_by_the_nearest_rule():
    """Conservative: with exactly one crossing, nearest and first agree."""
    level = scan_zero_gamma(
        _PeakedGamma(),
        _split_chain(),
        forward=24500.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24500.0,
        weight_by="oi",
    )
    assert level == pytest.approx(24499.436949877047, abs=1e-6)
