"""Delta exposure: the Black-76 helper and the per-strike DEX profile."""

import math

import pytest

from services.gex_levels.blackscholes import safe_delta
from services.gex_levels.delta_exposure import StrikeDelta, price_delta_exposures
from services.gex_levels.exposure import ChainRow, resolve_ivs


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


FORWARD = 24600.0
T_YEARS = 0.02
RATE = 0.065


def _rows():
    return [
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=80.0,
            call_oi=1000,
            put_oi=4000,
            call_volume=300,
            put_volume=100,
            lot_size=75,
        ),
        ChainRow(
            strike=24500.0,
            call_price=180.0,
            put_price=40.0,
            call_oi=2000,
            put_oi=500,
            call_volume=50,
            put_volume=25,
            lot_size=75,
        ),
    ]


def _priced(rows=None, weight_by="oi", stub=None):
    stub = stub or _Delta()
    rows = rows if rows is not None else _rows()
    ivs = resolve_ivs(stub, rows, forward=FORWARD, t_years=T_YEARS, r=RATE, atm_strike=24600.0)
    return price_delta_exposures(
        stub, rows, ivs, forward=FORWARD, t_years=T_YEARS, r=RATE, weight_by=weight_by
    )


def test_the_worked_example_is_exact():
    """The one assertion that pins the sign convention, with numbers a reader
    can check by hand. Call delta +0.6 on 1000 OI and put delta -0.4 on 4000 OI
    at a 24600 forward:

        call_dex = +0.6 * 1000 * 24600 =  14,760,000
        put_dex  = -0.4 * 4000 * 24600 = -39,360,000
        net_dex                        = -24,600,000

    Net is NEGATIVE because puts dominate this strike. If a future change makes
    this positive, the dealer-sign trap described in the module docstring has
    been reintroduced."""
    at_atm = next(e for e in _priced() if e.strike == 24600.0)
    assert at_atm.call_dex == pytest.approx(14_760_000.0)
    assert at_atm.put_dex == pytest.approx(-39_360_000.0)
    assert at_atm.net_dex == pytest.approx(-24_600_000.0)


def test_a_call_heavy_strike_is_net_positive():
    """The mirror of the worked example: 2000 call OI against 500 put OI."""
    at_lower = next(e for e in _priced() if e.strike == 24500.0)
    assert at_lower.net_dex > 0


def test_results_are_sorted_by_strike_ascending():
    assert [e.strike for e in _priced()] == [24500.0, 24600.0]


def test_volume_weighting_uses_volume_not_open_interest():
    by_oi = next(e for e in _priced(weight_by="oi") if e.strike == 24600.0)
    by_volume = next(e for e in _priced(weight_by="volume") if e.strike == 24600.0)
    # 300 call volume vs 1000 call OI, 100 put volume vs 4000 put OI.
    assert by_volume.call_dex == pytest.approx(0.6 * 300 * FORWARD)
    assert by_volume.put_dex == pytest.approx(-0.4 * 100 * FORWARD)
    assert by_volume.net_dex != by_oi.net_dex


def test_an_unknown_weighting_raises_rather_than_defaulting():
    with pytest.raises(ValueError, match="weight_by"):
        _priced(weight_by="notional")


def test_a_nan_weight_contributes_nothing_rather_than_poisoning_the_profile():
    rows = _rows()
    rows[0] = ChainRow(
        strike=24600.0,
        call_price=120.0,
        put_price=80.0,
        call_oi=math.nan,
        put_oi=4000,
        call_volume=300,
        put_volume=100,
        lot_size=75,
    )
    at_atm = next(e for e in _priced(rows=rows) if e.strike == 24600.0)
    assert at_atm.call_dex == 0.0
    assert math.isfinite(at_atm.net_dex)


def test_a_non_finite_forward_yields_no_exposure_rather_than_nan():
    stub = _Delta()
    rows = _rows()
    ivs = resolve_ivs(stub, rows, forward=FORWARD, t_years=T_YEARS, r=RATE, atm_strike=24600.0)
    out = price_delta_exposures(
        stub, rows, ivs, forward=math.nan, t_years=T_YEARS, r=RATE, weight_by="oi"
    )
    assert all(e.net_dex == 0.0 for e in out)


def test_rows_not_matching_the_resolved_ivs_raise():
    stub = _Delta()
    ivs = resolve_ivs(stub, _rows(), forward=FORWARD, t_years=T_YEARS, r=RATE, atm_strike=24600.0)
    stranger = [
        ChainRow(
            strike=99999.0,
            call_price=1.0,
            put_price=1.0,
            call_oi=1,
            put_oi=1,
            call_volume=1,
            put_volume=1,
            lot_size=75,
        )
    ]
    with pytest.raises(ValueError, match="resolve"):
        price_delta_exposures(
            stub, stranger, ivs, forward=FORWARD, t_years=T_YEARS, r=RATE, weight_by="oi"
        )


def test_the_raw_deltas_are_carried_through_for_display():
    at_atm = next(e for e in _priced() if e.strike == 24600.0)
    assert at_atm.call_delta == pytest.approx(0.6)
    assert at_atm.put_delta == pytest.approx(-0.4)
    assert isinstance(at_atm, StrikeDelta)
