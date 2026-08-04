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
