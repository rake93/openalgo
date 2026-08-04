"""Per-strike signed dealer gamma exposure."""

import json
import math

import pytest

from services.gex_levels.exposure import ChainRow, compute_exposures, price_exposures, resolve_ivs
from services.gex_levels.levels import find_walls


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
    """GEX = gamma * weight * F^2 * 0.01, calls positive.

    No lot-size factor: the chain reports OI and volume already multiplied
    by the lot size, so the contract multiplier is not applied again here.
    """
    out = compute_exposures(
        _FlatGamma(gamma=0.001),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    expected = 0.001 * 1000 * (24600.0**2) * 0.01
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


def test_the_fallback_volatility_reaches_gamma_for_an_unpriced_leg():
    """The headline behaviour: an unpriced leg is priced, not zeroed.

    The existing unpriced test uses zero weight, so it would pass even if the
    fallback never reached safe_gamma. This one carries real open interest.
    """
    rows = [
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=0.0,
            call_oi=5000,
            put_oi=5000,
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
    assert out[0].put_iv is None
    assert out[0].put_gex < 0, "the unpriced put contributed nothing"


def test_one_leg_inverting_does_not_suppress_the_other():
    rows = [
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=0.0,
            call_oi=1000,
            put_oi=1000,
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
    assert out[0].call_iv is not None
    assert out[0].put_iv is None


def test_an_unknown_weighting_is_rejected():
    with pytest.raises(ValueError, match="weight_by"):
        compute_exposures(
            _FlatGamma(),
            _rows(),
            forward=24600.0,
            t_years=0.02,
            r=0.065,
            atm_strike=24600.0,
            weight_by="delta",
        )


def test_pricing_with_mismatched_ivs_is_rejected():
    """A strike missing from `ivs` must not silently read as 'did not invert'."""
    rows = _rows()
    ivs = resolve_ivs(
        _FlatGamma(),
        rows[:1],
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
    )
    with pytest.raises(ValueError, match="resolve_ivs"):
        price_exposures(
            _FlatGamma(),
            rows,
            ivs,
            forward=24600.0,
            t_years=0.02,
            r=0.065,
            weight_by="oi",
        )


def test_a_non_finite_weight_contributes_nothing():
    rows = [
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=80.0,
            call_oi=float("nan"),
            put_oi=1000,
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
    assert out[0].call_gex == 0.0
    assert math.isfinite(out[0].net_gex)


def test_open_interest_is_treated_as_units_not_lots():
    """The chain reports OI already multiplied by the lot size - verified across
    42 live legs, every one exactly divisible by it. Multiplying by lot_size
    again would double-count by 65x on NIFTY.

    Walls and the zero-gamma level are unaffected either way, since a uniform
    scale factor cannot move an argmax or a zero crossing - only the reported
    magnitudes change.
    """
    rows = [
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=80.0,
            call_oi=1000,
            put_oi=0,
            call_volume=0,
            put_volume=0,
            lot_size=75,
        )
    ]
    out = compute_exposures(
        _FlatGamma(gamma=0.001),
        rows,
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )
    expected = 0.001 * 1000 * (24600.0**2) * 0.01
    assert out[0].call_gex == pytest.approx(expected)
    # The lot size must NOT appear as a factor.
    assert out[0].call_gex != pytest.approx(expected * 75)


def _walls_chain(lot_size):
    """A chain whose strikes carry distinct OI, so the walls are non-degenerate -
    not all tied at one strike, and not symmetric in a way that would hide a
    scale bug. Only `lot_size` varies between callers of this helper.
    """
    return [
        ChainRow(
            strike=24400.0,
            call_price=250.0,
            put_price=20.0,
            call_oi=500,
            put_oi=1500,
            call_volume=50,
            put_volume=300,
            lot_size=lot_size,
        ),
        ChainRow(
            strike=24500.0,
            call_price=180.0,
            put_price=40.0,
            call_oi=1000,
            put_oi=6000,
            call_volume=100,
            put_volume=900,
            lot_size=lot_size,
        ),
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=80.0,
            call_oi=8000,
            put_oi=3000,
            call_volume=500,
            put_volume=500,
            lot_size=lot_size,
        ),
        ChainRow(
            strike=24700.0,
            call_price=70.0,
            put_price=130.0,
            call_oi=2000,
            put_oi=1000,
            call_volume=200,
            put_volume=100,
            lot_size=lot_size,
        ),
    ]


def test_the_walls_do_not_move_when_the_lot_size_changes():
    """A uniform scale factor cannot move an argmax. This is why dropping the
    lot factor changes magnitudes without changing a single level.

    Two identical chains, differing only in `lot_size` (75 vs 150), must
    produce identical walls even though `lot_size` is no longer part of the
    GEX formula - if it were still a factor here, this would still pass
    (uniform scaling never moves an argmax), so the real value of this test
    is documenting the invariant the fix in `price_exposures` relies on.
    """
    gamma = _FlatGamma(gamma=0.001)
    kwargs = {
        "forward": 24600.0,
        "t_years": 0.02,
        "r": 0.065,
        "atm_strike": 24600.0,
        "weight_by": "oi",
    }

    exposures_75 = compute_exposures(gamma, _walls_chain(75), **kwargs)
    exposures_150 = compute_exposures(gamma, _walls_chain(150), **kwargs)

    walls_75 = find_walls(exposures_75)
    walls_150 = find_walls(exposures_150)

    assert walls_75.call_wall == walls_150.call_wall
    assert walls_75.put_wall == walls_150.put_wall
    assert walls_75.call_wall_at_edge == walls_150.call_wall_at_edge
    assert walls_75.put_wall_at_edge == walls_150.put_wall_at_edge


def test_the_gammas_are_carried_through_to_the_exposure():
    """The `/gex` Tools page displays gamma per strike, so `price_exposures`
    must surface the values `safe_gamma` returned rather than discarding them
    after the multiplication. A silently-zero gamma column would read as a
    dead chain on a page whose GEX numbers are non-zero."""
    gamma = 0.00042
    rows = _rows()
    out = compute_exposures(
        _FlatGamma(gamma=gamma),
        rows,
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )

    for exposure in out:
        assert exposure.call_gamma == gamma
        assert exposure.put_gamma == gamma

    # And they are genuinely the gammas the exposure was built from: with a
    # flat gamma the call leg is exactly gamma * oi * F^2 * 0.01.
    scale = 24600.0 * 24600.0 * 0.01
    by_strike = {row.strike: row for row in rows}
    for exposure in out:
        row = by_strike[exposure.strike]
        assert exposure.call_gex == pytest.approx(exposure.call_gamma * row.call_oi * scale)
        assert exposure.put_gex == pytest.approx(-exposure.put_gamma * row.put_oi * scale)


def test_a_gamma_that_cannot_be_computed_is_reported_as_zero_not_omitted():
    """safe_gamma fails to 0.0 rather than raising; that zero must reach the
    payload, because the page's gamma column is read positionally against the
    strike list."""

    class _Broken:
        def implied_volatility(self, price, F, K, r, t, flag):
            return 0.20

        def gamma(self, flag, F, K, t, r, sigma):
            raise ValueError("solver diverged")

    out = compute_exposures(
        _Broken(),
        _rows(),
        forward=24600.0,
        t_years=0.02,
        r=0.065,
        atm_strike=24600.0,
        weight_by="oi",
    )

    for exposure in out:
        assert exposure.call_gamma == 0.0
        assert exposure.put_gamma == 0.0
        assert exposure.net_gex == 0.0
