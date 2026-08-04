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
