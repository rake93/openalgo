"""Per-strike signed dealer gamma exposure."""

import json

import pytest

from services.gex_levels.exposure import ChainRow, compute_exposures


class _FlatGamma:
    """Gamma independent of strike, so exposure arithmetic is checkable by hand."""

    def __init__(self, gamma=0.001):
        self._gamma = gamma

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def gamma(self, flag, F, K, t, r, sigma):
        return self._gamma


def _rows():
    return [
        ChainRow(
            strike=24500.0,
            call_price=180.0,
            put_price=40.0,
            call_oi=1000,
            put_oi=4000,
            call_volume=100,
            put_volume=900,
            lot_size=75,
        ),
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=80.0,
            call_oi=3000,
            put_oi=3000,
            call_volume=500,
            put_volume=500,
            lot_size=75,
        ),
    ]


def test_calls_are_positive_and_puts_negative():
    out = compute_exposures(
        _FlatGamma(),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    first = out[0]
    assert first.call_gex > 0
    assert first.put_gex < 0


def test_net_is_the_signed_sum():
    out = compute_exposures(
        _FlatGamma(),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    for row in out:
        assert row.net_gex == pytest.approx(row.call_gex + row.put_gex)


def test_a_balanced_strike_nets_to_zero():
    """Equal call and put OI at one gamma must cancel exactly."""
    out = compute_exposures(
        _FlatGamma(),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    assert out[1].net_gex == pytest.approx(0.0)


def test_the_notional_scaling_is_applied():
    """GEX = gamma * weight * lot * F^2 * 0.01, calls positive."""
    out = compute_exposures(
        _FlatGamma(gamma=0.001),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    expected = 0.001 * 1000 * 75 * (24600.0**2) * 0.01
    assert out[0].call_gex == pytest.approx(expected)


def test_volume_weighting_uses_volume_not_oi():
    oi = compute_exposures(
        _FlatGamma(),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    vol = compute_exposures(
        _FlatGamma(),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="volume",
    )
    # Row 0 carries OI 1000/4000 against volume 100/900 - different magnitudes.
    assert vol[0].call_gex == pytest.approx(oi[0].call_gex / 10.0)


def test_rows_are_returned_in_ascending_strike_order():
    shuffled = list(reversed(_rows()))
    out = compute_exposures(
        _FlatGamma(),
        shuffled,
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    assert [r.strike for r in out] == [24500.0, 24600.0]


def test_every_field_is_json_serialisable_without_nan():
    """float('inf') serialises as Infinity, which JSON.parse rejects outright."""
    out = compute_exposures(
        _FlatGamma(),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    payload = [
        {"strike": r.strike, "call_gex": r.call_gex, "put_gex": r.put_gex, "net_gex": r.net_gex}
        for r in out
    ]
    json.dumps(payload, allow_nan=False)


def test_an_unpriced_strike_contributes_zero_rather_than_being_dropped():
    rows = [
        ChainRow(
            strike=24500.0,
            call_price=0.0,
            put_price=0.0,
            call_oi=0,
            put_oi=0,
            call_volume=0,
            put_volume=0,
            lot_size=75,
        )
    ]
    out = compute_exposures(
        _FlatGamma(),
        rows,
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    assert len(out) == 1
    assert out[0].net_gex == 0.0
