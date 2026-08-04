"""Pure-math tests for the Option Target Calculator. No broker, no database."""

import pytest

from services.option_target.models import ForwardAnchor, StrikeQuote


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

from services.option_target.daycount import year_fraction

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
