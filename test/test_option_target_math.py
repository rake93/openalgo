"""Pure-math tests for the Option Target Calculator. No broker, no database."""

import pytest

from services.option_target.models import ForwardAnchor, SmileFit, StrikeQuote


def test_strike_quote_mid_uses_bid_ask_when_both_present():
    q = StrikeQuote(
        strike=24500.0,
        option_type="CE",
        symbol="NIFTY11AUG2624500CE",
        ltp=158.0,
        bid=157.0,
        ask=159.0,
        oi=1000,
        volume=500,
        lot_size=65,
    )
    assert q.mid == 158.0


def test_strike_quote_mid_falls_back_to_ltp_when_book_is_one_sided():
    q = StrikeQuote(
        strike=24500.0,
        option_type="CE",
        symbol="NIFTY11AUG2624500CE",
        ltp=158.0,
        bid=0.0,
        ask=159.0,
        oi=1000,
        volume=500,
        lot_size=65,
    )
    assert q.mid == 158.0


def test_strike_quote_mid_rejects_crossed_book():
    q = StrikeQuote(
        strike=24500.0,
        option_type="CE",
        symbol="NIFTY11AUG2624500CE",
        ltp=158.0,
        bid=160.0,
        ask=159.0,
        oi=1000,
        volume=500,
        lot_size=65,
    )
    assert q.mid == 158.0


def test_strike_quote_half_spread():
    q = StrikeQuote(
        strike=24500.0,
        option_type="CE",
        symbol="NIFTY11AUG2624500CE",
        ltp=158.0,
        bid=157.0,
        ask=159.0,
        oi=1000,
        volume=500,
        lot_size=65,
    )
    assert q.half_spread == 1.0


def test_forward_anchor_basis():
    a = ForwardAnchor(forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity")
    assert a.basis == pytest.approx(138.95, abs=0.01)


from datetime import datetime
from zoneinfo import ZoneInfo

from services.option_target.daycount import SESSIONS, session_minutes_for, year_fraction

IST = ZoneInfo("Asia/Kolkata")


def test_calendar_year_fraction_is_simple_365():
    start = datetime(2026, 8, 4, 12, 0, tzinfo=IST)
    end = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    assert year_fraction(start, end, "calendar") == pytest.approx(7 / 365, rel=1e-9)


def test_calendar_year_fraction_is_zero_when_end_precedes_start():
    start = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    end = datetime(2026, 8, 4, 12, 0, tzinfo=IST)
    assert year_fraction(start, end, "calendar") == 0.0


def test_trading_year_fraction_skips_the_weekend():
    # Fri 2026-08-07 15:30 -> Mon 2026-08-10 15:30 is 3 calendar days but
    # only 1 trading day of decay.
    start = datetime(2026, 8, 7, 15, 30, tzinfo=IST)
    end = datetime(2026, 8, 10, 15, 30, tzinfo=IST)
    trading = year_fraction(start, end, "trading")
    calendar = year_fraction(start, end, "calendar")
    assert trading < calendar
    assert trading == pytest.approx(1 / 252, rel=1e-6)


def test_trading_year_fraction_prorates_within_a_session():
    # 09:15 -> 12:22:30 is half of the 6h15m session.
    start = datetime(2026, 8, 4, 9, 15, tzinfo=IST)
    end = datetime(2026, 8, 4, 12, 22, 30, tzinfo=IST)
    assert year_fraction(start, end, "trading") == pytest.approx(0.5 / 252, rel=1e-6)


def test_unknown_day_count_raises():
    start = datetime(2026, 8, 4, 12, 0, tzinfo=IST)
    end = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    with pytest.raises(ValueError, match="Unknown day_count"):
        year_fraction(start, end, "banana")


def test_strike_quote_locked_book_is_usable_with_zero_spread():
    q = StrikeQuote(
        strike=24500.0,
        option_type="CE",
        symbol="NIFTY11AUG2624500CE",
        ltp=999.0,
        bid=158.0,
        ask=158.0,
        oi=1000,
        volume=500,
        lot_size=65,
    )
    # bid == ask is a locked book, not a crossed one: the quote is usable.
    assert q.mid == 158.0
    assert q.half_spread == 0.0


def test_strike_quote_spread_pct():
    q = StrikeQuote(
        strike=24500.0,
        option_type="CE",
        symbol="NIFTY11AUG2624500CE",
        ltp=100.0,
        bid=95.0,
        ask=105.0,
        oi=1000,
        volume=500,
        lot_size=65,
    )
    # mid 100, spread 10 -> 10%
    assert q.spread_pct == pytest.approx(10.0)


def test_strike_quote_spread_pct_is_zero_without_a_book():
    q = StrikeQuote(
        strike=24500.0,
        option_type="CE",
        symbol="NIFTY11AUG2624500CE",
        ltp=100.0,
        bid=0.0,
        ask=0.0,
        oi=1000,
        volume=500,
        lot_size=65,
    )
    assert q.spread_pct == 0.0


from services.option_target.forward import compute_forward, project_forward


def _quote(strike, opt_type, bid, ask, ltp=None):
    return StrikeQuote(
        strike=strike,
        option_type=opt_type,
        symbol=f"BANKNIFTY25AUG26{int(strike)}{opt_type}",
        ltp=ltp if ltp is not None else (bid + ask) / 2,
        bid=bid,
        ask=ask,
        oi=1000,
        volume=100,
        lot_size=35,
    )


def test_compute_forward_uses_put_call_parity():
    quotes = {
        (57800.0, "CE"): _quote(57800.0, "CE", 700.0, 720.0),
        (57800.0, "PE"): _quote(57800.0, "PE", 570.0, 590.0),
    }
    anchor = compute_forward(quotes, atm_strike=57800.0, spot=57794.90)
    # 57800 + 710 - 580 = 57930
    assert anchor.forward == pytest.approx(57930.0)
    assert anchor.source == "parity"
    assert anchor.basis == pytest.approx(135.1, abs=0.01)


def test_compute_forward_falls_back_to_spot_when_atm_leg_missing():
    quotes = {(57800.0, "CE"): _quote(57800.0, "CE", 700.0, 720.0)}
    anchor = compute_forward(quotes, atm_strike=57800.0, spot=57794.90)
    assert anchor.forward == 57794.90
    assert anchor.source == "spot_fallback"


def test_project_forward_exact_mode_shifts_one_to_one():
    anchor = ForwardAnchor(forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity")
    t = project_forward(
        anchor,
        reference="FUT",
        reference_now=57933.85,
        reference_target=57643.85,
        matched_future=True,
    )
    assert t.mode == "exact"
    assert t.forward == pytest.approx(57643.85)


def test_project_forward_basis_mode_shifts_proportionally():
    anchor = ForwardAnchor(forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity")
    t = project_forward(
        anchor,
        reference="SPOT",
        reference_now=57794.90,
        reference_target=57504.90,
        matched_future=False,
    )
    assert t.mode == "basis_modelled"
    # 57933.85 * (57504.90 / 57794.90)
    assert t.forward == pytest.approx(57643.15, abs=0.5)


def test_project_forward_move_pct():
    anchor = ForwardAnchor(forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity")
    t = project_forward(
        anchor,
        reference="SPOT",
        reference_now=57794.90,
        reference_target=57504.90,
        matched_future=False,
    )
    assert t.move_pct == pytest.approx(-0.5018, abs=0.001)


def test_project_forward_rejects_non_positive_reference():
    anchor = ForwardAnchor(forward=100.0, spot=100.0, atm_strike=100.0, source="parity")
    with pytest.raises(ValueError, match="must be positive"):
        project_forward(
            anchor,
            reference="SPOT",
            reference_now=0.0,
            reference_target=90.0,
            matched_future=False,
        )


import math

from opengreeks import black76

from services.option_target.smile import calibrate_ivs


def _synthetic_chain(forward, t_years, iv, strikes, lot_size=65):
    """Build a chain priced at a known flat IV, so calibration must recover it."""
    quotes = {}
    for k in strikes:
        for opt_type, flag in (("CE", "c"), ("PE", "p")):
            price = black76.black(flag, forward, k, t_years, 0.0, iv)
            quotes[(k, opt_type)] = StrikeQuote(
                strike=k,
                option_type=opt_type,
                symbol=f"X{int(k)}{opt_type}",
                ltp=price,
                bid=price - 0.5,
                ask=price + 0.5,
                oi=1000,
                volume=100,
                lot_size=lot_size,
            )
    return quotes


def test_calibrate_recovers_a_known_flat_iv():
    forward, t, iv = 24500.0, 0.02, 0.11
    strikes = [24300.0, 24400.0, 24500.0, 24600.0, 24700.0]
    quotes = _synthetic_chain(forward, t, iv, strikes)
    points, rejects = calibrate_ivs(quotes, forward=forward, t_years=t, rate=0.0)
    assert len(points) == len(strikes)
    for p in points:
        assert p.iv == pytest.approx(iv, abs=1e-4)
    assert rejects == []


def test_calibrate_uses_otm_wing_on_each_side():
    forward = 24500.0
    quotes = _synthetic_chain(forward, 0.02, 0.11, [24300.0, 24700.0])
    points, _ = calibrate_ivs(quotes, forward=forward, t_years=0.02, rate=0.0)
    by_strike = {p.strike: p.option_type for p in points}
    assert by_strike[24300.0] == "PE"  # below forward -> put is OTM
    assert by_strike[24700.0] == "CE"  # above forward -> call is OTM


def test_calibrate_sets_log_moneyness():
    forward = 24500.0
    quotes = _synthetic_chain(forward, 0.02, 0.11, [24500.0])
    points, _ = calibrate_ivs(quotes, forward=forward, t_years=0.02, rate=0.0)
    assert points[0].log_moneyness == pytest.approx(math.log(24500.0 / 24500.0))


def test_calibrate_rejects_strike_with_no_time_value():
    forward = 24500.0
    # Deep ITM call quoted at pure intrinsic: IV is not recoverable.
    quotes = {
        (23000.0, "CE"): StrikeQuote(
            strike=23000.0,
            option_type="CE",
            symbol="X23000CE",
            ltp=1500.0,
            bid=1499.0,
            ask=1501.0,
            oi=10,
            volume=1,
            lot_size=65,
        ),
        (23000.0, "PE"): StrikeQuote(
            strike=23000.0,
            option_type="PE",
            symbol="X23000PE",
            ltp=0.0,
            bid=0.0,
            ask=0.0,
            oi=10,
            volume=1,
            lot_size=65,
        ),
    }
    points, rejects = calibrate_ivs(quotes, forward=forward, t_years=0.02, rate=0.0)
    assert points == []
    assert len(rejects) == 1
    assert "no market" in rejects[0].lower() or "time value" in rejects[0].lower()


def test_calibrate_returns_positive_vega_for_every_point():
    quotes = _synthetic_chain(24500.0, 0.02, 0.11, [24400.0, 24500.0, 24600.0])
    points, _ = calibrate_ivs(quotes, forward=24500.0, t_years=0.02, rate=0.0)
    assert all(p.vega > 0 for p in points)


from services.option_target.smile import fit_smile, smile_iv


def test_fit_recovers_a_flat_smile():
    quotes = _synthetic_chain(24500.0, 0.02, 0.11, [24300.0, 24400.0, 24500.0, 24600.0, 24700.0])
    points, _ = calibrate_ivs(quotes, forward=24500.0, t_years=0.02, rate=0.0)
    fit = fit_smile(points, atm_iv_fallback=0.11)
    assert not fit.degenerate
    assert fit.a == pytest.approx(0.11, abs=1e-3)
    assert fit.b == pytest.approx(0.0, abs=1e-2)
    assert fit.rms < 1e-3


def test_fit_is_degenerate_with_too_few_points():
    quotes = _synthetic_chain(24500.0, 0.02, 0.11, [24500.0])
    points, _ = calibrate_ivs(quotes, forward=24500.0, t_years=0.02, rate=0.0)
    fit = fit_smile(points, atm_iv_fallback=0.125)
    assert fit.degenerate
    assert fit.a == 0.125
    assert smile_iv(fit, 0.05) == pytest.approx(0.125)


def test_smile_iv_clamps_below_observed_range():
    fit = SmileFit(
        a=0.11, b=-0.24, c=10.79, x_lo=-0.02, x_hi=0.02, rms=0.0005, n_points=25, degenerate=False
    )
    # Far outside the fitted range: must equal the value at x_lo, not explode.
    assert smile_iv(fit, -5.0) == pytest.approx(smile_iv(fit, -0.02))


def test_smile_iv_clamps_above_observed_range():
    fit = SmileFit(
        a=0.11, b=-0.24, c=10.79, x_lo=-0.02, x_hi=0.02, rms=0.0005, n_points=25, degenerate=False
    )
    assert smile_iv(fit, 5.0) == pytest.approx(smile_iv(fit, 0.02))


def test_smile_iv_is_never_non_positive():
    fit = SmileFit(
        a=-1.0, b=0.0, c=0.0, x_lo=-1.0, x_hi=1.0, rms=0.0, n_points=10, degenerate=False
    )
    assert smile_iv(fit, 0.0) > 0


from services.option_target.projection import project_strike, target_iv


def test_target_iv_smile_slide_uses_moneyness_at_target_forward():
    fit = SmileFit(
        a=0.11, b=-0.24, c=10.79, x_lo=-0.05, x_hi=0.05, rms=0.0005, n_points=25, degenerate=False
    )
    iv = target_iv(
        strike=24500.0,
        forward_target=24000.0,
        iv_now=0.115,
        fit=fit,
        iv_model="smile_slide",
        vol_beta=0.0,
        move_pct=0.0,
        vol_shift=0.0,
    )
    assert iv == pytest.approx(smile_iv(fit, math.log(24500.0 / 24000.0)))


def test_target_iv_sticky_strike_keeps_the_strikes_own_iv():
    fit = SmileFit(
        a=0.11, b=-0.24, c=10.79, x_lo=-0.05, x_hi=0.05, rms=0.0005, n_points=25, degenerate=False
    )
    iv = target_iv(
        strike=24500.0,
        forward_target=24000.0,
        iv_now=0.115,
        fit=fit,
        iv_model="sticky_strike",
        vol_beta=0.0,
        move_pct=0.0,
        vol_shift=0.0,
    )
    assert iv == pytest.approx(0.115)


def test_vol_beta_raises_iv_on_a_fall():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05, rms=0.0, n_points=25, degenerate=False
    )
    iv = target_iv(
        strike=24500.0,
        forward_target=24500.0,
        iv_now=0.11,
        fit=fit,
        iv_model="sticky_strike",
        vol_beta=1.5,
        move_pct=-0.502,
        vol_shift=0.0,
    )
    # 0.11 + 1.5 * 0.502 / 100 = 0.11753
    assert iv == pytest.approx(0.11753, abs=1e-5)


def test_vol_beta_lowers_iv_on_a_rally():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05, rms=0.0, n_points=25, degenerate=False
    )
    iv = target_iv(
        strike=24500.0,
        forward_target=24500.0,
        iv_now=0.11,
        fit=fit,
        iv_model="sticky_strike",
        vol_beta=1.5,
        move_pct=+1.0,
        vol_shift=0.0,
    )
    assert iv == pytest.approx(0.095, abs=1e-5)


def test_manual_vol_shift_is_in_vol_points():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05, rms=0.0, n_points=25, degenerate=False
    )
    iv = target_iv(
        strike=24500.0,
        forward_target=24500.0,
        iv_now=0.11,
        fit=fit,
        iv_model="sticky_strike",
        vol_beta=0.0,
        move_pct=0.0,
        vol_shift=2.0,
    )
    assert iv == pytest.approx(0.13)


def test_target_iv_is_floored_positive():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05, rms=0.0, n_points=25, degenerate=False
    )
    iv = target_iv(
        strike=24500.0,
        forward_target=24500.0,
        iv_now=0.11,
        fit=fit,
        iv_model="sticky_strike",
        vol_beta=0.0,
        move_pct=0.0,
        vol_shift=-99.0,
    )
    assert iv > 0


def test_project_strike_call_premium_rises_with_forward():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5, rms=0.0, n_points=25, degenerate=False
    )
    kwargs = {
        "strike": 24500.0,
        "option_type": "CE",
        "t_target": 0.019,
        "rate": 0.0,
        "iv_now": 0.11,
        "fit": fit,
        "iv_model": "sticky_strike",
        "vol_beta": 0.0,
        "vol_shift": 0.0,
    }
    low = project_strike(forward_target=24400.0, move_pct=0.0, **kwargs)
    high = project_strike(forward_target=24600.0, move_pct=0.0, **kwargs)
    assert high > low


def test_project_strike_put_premium_falls_with_forward():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5, rms=0.0, n_points=25, degenerate=False
    )
    kwargs = {
        "strike": 24500.0,
        "option_type": "PE",
        "t_target": 0.019,
        "rate": 0.0,
        "iv_now": 0.11,
        "fit": fit,
        "iv_model": "sticky_strike",
        "vol_beta": 0.0,
        "vol_shift": 0.0,
    }
    low = project_strike(forward_target=24400.0, move_pct=0.0, **kwargs)
    high = project_strike(forward_target=24600.0, move_pct=0.0, **kwargs)
    assert high < low


def test_project_strike_returns_intrinsic_past_expiry():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5, rms=0.0, n_points=25, degenerate=False
    )
    premium = project_strike(
        strike=24500.0,
        option_type="CE",
        forward_target=24700.0,
        t_target=0.0,
        rate=0.0,
        iv_now=0.11,
        fit=fit,
        iv_model="sticky_strike",
        vol_beta=0.0,
        move_pct=0.0,
        vol_shift=0.0,
    )
    assert premium == pytest.approx(200.0)


def test_project_strike_intrinsic_is_zero_when_out_of_the_money_at_expiry():
    fit = SmileFit(
        a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5, rms=0.0, n_points=25, degenerate=False
    )
    premium = project_strike(
        strike=24500.0,
        option_type="CE",
        forward_target=24300.0,
        t_target=0.0,
        rate=0.0,
        iv_now=0.11,
        fit=fit,
        iv_model="sticky_strike",
        vol_beta=0.0,
        move_pct=0.0,
        vol_shift=0.0,
    )
    assert premium == 0.0


from services.option_target.projection import attribute_pnl


def _attribution_case(forward_target, iv_target, entry_extra=0.0, exit_penalty=0.0):
    strike, opt_type, rate = 24500.0, "CE", 0.0
    forward_now, t_now, t_target, iv_now = 24500.0, 0.02, 0.019, 0.11
    premium_now = black76.black("c", forward_now, strike, t_now, rate, iv_now)
    premium_target = black76.black("c", forward_target, strike, t_target, rate, iv_target)
    return attribute_pnl(
        strike=strike,
        option_type=opt_type,
        forward_now=forward_now,
        forward_target=forward_target,
        t_now=t_now,
        t_target=t_target,
        rate=rate,
        iv_now=iv_now,
        iv_target=iv_target,
        premium_now=premium_now,
        premium_target=premium_target,
        entry_cost=premium_now + entry_extra,
        exit_value=premium_target - exit_penalty,
    )


def test_attribution_terms_sum_to_total():
    a = _attribution_case(forward_target=24700.0, iv_target=0.11)
    assert a.delta + a.gamma + a.theta + a.vega + a.spread + a.residual == pytest.approx(
        a.total, abs=1e-9
    )


def test_attribution_delta_dominates_a_small_move():
    a = _attribution_case(forward_target=24510.0, iv_target=0.11)
    assert abs(a.delta) > abs(a.gamma)


def test_attribution_theta_is_negative_for_a_long_option():
    a = _attribution_case(forward_target=24500.0, iv_target=0.11)
    assert a.theta < 0


def test_attribution_vega_is_positive_when_vol_rises():
    a = _attribution_case(forward_target=24500.0, iv_target=0.13)
    assert a.vega > 0


def test_attribution_spread_is_negative_when_crossing_the_book():
    a = _attribution_case(forward_target=24700.0, iv_target=0.11, entry_extra=2.0, exit_penalty=2.0)
    assert a.spread == pytest.approx(-4.0, abs=1e-9)


def test_attribution_gamma_grows_with_the_square_of_the_move():
    small = _attribution_case(forward_target=24600.0, iv_target=0.11)
    large = _attribution_case(forward_target=24700.0, iv_target=0.11)
    assert large.gamma == pytest.approx(4 * small.gamma, rel=0.05)


def test_session_minutes_nse_equity_and_derivatives():
    for exchange in ("NSE", "BSE", "NFO", "BFO"):
        assert session_minutes_for(exchange) == 375


def test_session_minutes_mcx_runs_to_late_evening():
    # MCX trades 09:00-23:55 IST, verified against the platform timings API.
    assert session_minutes_for("MCX") == 895
    assert session_minutes_for("NCO") == 895


def test_session_minutes_currency_closes_at_five():
    # CDS/BCD trade 09:00-17:00 IST.
    assert session_minutes_for("CDS") == 480
    assert session_minutes_for("BCD") == 480


def test_session_lookup_is_case_insensitive():
    assert session_minutes_for("mcx") == session_minutes_for("MCX")


def test_unknown_exchange_falls_back_to_nse_session():
    assert session_minutes_for("NOSUCHEXCHANGE") == 375


def test_mcx_full_session_is_one_trading_day():
    start = datetime(2026, 8, 4, 9, 0, tzinfo=IST)
    end = datetime(2026, 8, 4, 23, 55, tzinfo=IST)
    assert year_fraction(start, end, "trading", exchange="MCX") == pytest.approx(1 / 252, rel=1e-6)


def test_mcx_evening_time_is_counted_not_discarded():
    # 15:30 -> 20:00 is dead time on NFO but 270 live minutes on MCX.
    start = datetime(2026, 8, 4, 15, 30, tzinfo=IST)
    end = datetime(2026, 8, 4, 20, 0, tzinfo=IST)
    assert year_fraction(start, end, "trading", exchange="NFO") == 0.0
    assert year_fraction(start, end, "trading", exchange="MCX") == pytest.approx(
        (270 / 895) / 252, rel=1e-6
    )


def test_exchange_is_ignored_for_calendar_day_count():
    start = datetime(2026, 8, 4, 12, 0, tzinfo=IST)
    end = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    assert year_fraction(start, end, "calendar", exchange="MCX") == year_fraction(
        start, end, "calendar", exchange="NFO"
    )


def test_sessions_table_covers_the_supported_exchanges():
    for exchange in ("NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX", "NCO"):
        assert exchange in SESSIONS


def test_session_provider_overrides_the_static_table():
    # Provider says this Tuesday ran an evening-only session, 17:00-23:55.
    def provider(day):
        return ((17, 0), (23, 55))

    start = datetime(2026, 8, 4, 17, 0, tzinfo=IST)
    end = datetime(2026, 8, 4, 23, 55, tzinfo=IST)
    assert year_fraction(
        start, end, "trading", exchange="MCX", session_provider=provider
    ) == pytest.approx(1 / 252, rel=1e-6)


def test_session_provider_returning_none_means_market_shut():
    def provider(day):
        return None

    start = datetime(2026, 8, 4, 9, 0, tzinfo=IST)
    end = datetime(2026, 8, 4, 23, 55, tzinfo=IST)
    assert year_fraction(start, end, "trading", exchange="MCX", session_provider=provider) == 0.0


def test_session_provider_is_authoritative_over_the_holiday_calendar():
    # A Sunday. The static path would count zero; the provider says it traded.
    def provider(day):
        return ((9, 15), (15, 30))

    start = datetime(2026, 8, 9, 9, 15, tzinfo=IST)
    end = datetime(2026, 8, 9, 15, 30, tzinfo=IST)
    assert year_fraction(start, end, "trading", exchange="NFO") == 0.0
    assert year_fraction(
        start, end, "trading", exchange="NFO", session_provider=provider
    ) == pytest.approx(1 / 252, rel=1e-6)


def test_session_provider_normalises_partial_days_against_its_own_session():
    # Half of a 17:00-23:55 session (415 min) is 207.5 min.
    def provider(day):
        return ((17, 0), (23, 55))

    start = datetime(2026, 8, 4, 17, 0, tzinfo=IST)
    end = datetime(2026, 8, 4, 20, 27, 30, tzinfo=IST)
    assert year_fraction(
        start, end, "trading", exchange="MCX", session_provider=provider
    ) == pytest.approx(0.5 / 252, rel=1e-6)
