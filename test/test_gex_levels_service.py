"""Orchestration: chain fetch to assembled payload. The chain is stubbed."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from services.gex_levels_service import STRIKE_COUNT, get_gex_levels

# Derived, never hardcoded. A fixed date silently expires: once it is past,
# calculate_time_to_expiry returns 0, safe_gamma returns 0.0 for every leg,
# and the magnitude assertions below start comparing zero to zero.
EXPIRY = (datetime.now() + timedelta(days=30)).strftime("%d%b%y").upper()


def _chain_response():
    """Five strikes, both legs, shaped exactly as option_chain_service returns."""
    rows = []
    for strike in (24400, 24500, 24600, 24700, 24800):
        rows.append(
            {
                "strike": float(strike),
                "ce": {
                    "symbol": f"NIFTY{EXPIRY}{strike}CE",
                    "ltp": 120.0,
                    "oi": 100000,
                    "volume": 5000,
                    "lotsize": 75,
                },
                "pe": {
                    "symbol": f"NIFTY{EXPIRY}{strike}PE",
                    "ltp": 110.0,
                    "oi": 90000,
                    "volume": 4000,
                    "lotsize": 75,
                },
            }
        )
    return {
        "status": "success",
        "chain": rows,
        "atm_strike": 24600.0,
        "underlying_ltp": 24590.0,
        "underlying": "NIFTY",
    }


def _patched(forward=24610.0):
    """Patch the two IO boundaries: the chain fetch and the forward resolution."""
    return (
        patch(
            "services.gex_levels_service.get_option_chain",
            return_value=(True, _chain_response(), 200),
        ),
        patch("services.gex_levels_service._resolve_forward_price", return_value=forward),
    )


def test_the_chain_is_fetched_at_the_multiquote_safe_strike_count():
    """23 each side = 94 symbols, inside the Fyers OI bucket. Never raise it."""
    assert STRIKE_COUNT == 23


def test_get_option_chain_is_actually_called_with_strike_count():
    """A future edit could hardcode a different number at the call site while
    leaving STRIKE_COUNT untouched - the constant alone would keep passing.
    Empty OI from an oversized request zeroes the whole study with no error,
    so the call itself has to be checked, not just the constant."""
    with (
        patch(
            "services.gex_levels_service.get_option_chain",
            return_value=(True, _chain_response(), 200),
        ) as fetch,
        patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0),
    ):
        get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    fetch.assert_called_once()
    assert fetch.call_args.kwargs["strike_count"] == STRIKE_COUNT


def test_a_successful_call_returns_levels_and_a_quality_verdict():
    chain, forward = _patched()
    with chain, forward:
        ok, payload, status = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert ok is True
    assert status == 200
    assert payload["status"] == "success"
    assert payload["forward_price"] == 24610.0
    assert payload["call_wall"] is not None
    assert payload["put_wall"] is not None
    assert payload["regime"] in ("suppressive", "amplifying")
    assert payload["quality"]["strikes_used"] == 5


def test_the_payload_carries_may_draw_explicitly():
    """may_draw is a @property, so asdict() drops it. Absent reads as falsy in
    TypeScript and would suppress every good snapshot."""
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert "may_draw" in payload["quality"]
    assert isinstance(payload["quality"]["may_draw"], bool)


def test_the_payload_never_contains_a_non_finite_float():
    """float('inf') serialises as Infinity, which JSON.parse rejects outright."""
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    json.dumps(payload, allow_nan=False)


def test_it_falls_back_to_spot_when_the_forward_cannot_be_resolved():
    chain, forward = _patched(forward=None)
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert payload["forward_price"] == 24590.0


def test_a_chain_failure_is_passed_through():
    failure = {"status": "error", "message": "No strikes"}
    with patch("services.gex_levels_service.get_option_chain", return_value=(False, failure, 404)):
        ok, payload, status = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert ok is False
    assert status == 404


def test_volume_weighting_produces_a_different_profile_than_oi():
    chain, forward = _patched()
    with chain, forward:
        _, by_oi, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")
    chain, forward = _patched()
    with chain, forward:
        _, by_vol, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="volume")

    assert by_oi["net_gex"] != by_vol["net_gex"]
    assert by_vol["weight_by"] == "volume"


def test_regime_follows_the_sign_of_net_gex():
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    expected = "suppressive" if payload["net_gex"] >= 0 else "amplifying"
    assert payload["regime"] == expected


def test_an_unknown_weighting_is_rejected_before_any_broker_call():
    """price_exposures raises on a bad weighting; the service must not have
    already spent a chain fetch to discover that."""
    with patch("services.gex_levels_service.get_option_chain") as fetch:
        ok, payload, status = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="delta")
    assert ok is False
    assert status == 400
    fetch.assert_not_called()


def test_the_payload_carries_every_field_the_frontend_reads():
    """The TypeScript GEXLevelsResponse is the contract; a silently absent key
    reads as undefined rather than failing, so pin the whole shape.

    This test exists because the first implementation omitted `strikes`,
    `lot_size` and the two totals. Nothing failed - the chart simply drew
    levels with no bar column and a dashboard with two blank rows.
    """
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    for key in (
        "status",
        "underlying",
        "exchange",
        "expiry_date",
        "weight_by",
        "spot_price",
        "forward_price",
        "atm_strike",
        "lot_size",
        "dte_days",
        "strikes",
        "call_wall",
        "put_wall",
        "zero_gamma",
        "total_call_gex",
        "total_put_gex",
        "net_gex",
        "regime",
        "quality",
        "sentiment",
    ):
        assert key in payload, f"payload is missing {key!r}, which the frontend reads"


def test_sentiment_bias_is_one_of_the_three_values_and_participation_is_bounded():
    """Sentiment.bias must never be 'unavailable' - that state is only for the
    individual signals - and participating can never exceed how many signals
    exist, or the panel would claim more agreement than the data supports."""
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    sentiment = payload["sentiment"]
    assert sentiment["bias"] in ("bullish", "bearish", "neutral")
    assert sentiment["participating"] <= len(sentiment["signals"])
    for signal in sentiment["signals"]:
        assert signal["bias"] in ("bullish", "bearish", "neutral", "unavailable")


def test_every_sentiment_signal_carries_all_five_fields():
    """`why` and `weight` are what let the panel explain a verdict on hover -
    a signal missing either would silently render an empty tooltip line."""
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    for signal in payload["sentiment"]["signals"]:
        assert set(signal) == {"key", "label", "detail", "bias", "why", "weight"}
        assert isinstance(signal["why"], str) and signal["why"].strip() != ""
        assert isinstance(signal["weight"], (int, float))


def test_the_strike_profile_is_returned_with_one_entry_per_strike():
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert len(payload["strikes"]) == payload["quality"]["strikes_used"]
    for row in payload["strikes"]:
        assert set(row) == {"strike", "call_gex", "put_gex", "net_gex"}


def test_the_totals_agree_with_the_per_strike_profile():
    """net_gex must be the sum of the profile the chart draws, or the dashboard
    and the bars would tell the reader two different stories."""
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert payload["total_call_gex"] == pytest.approx(
        sum(r["call_gex"] for r in payload["strikes"]), rel=1e-6
    )
    assert payload["total_put_gex"] == pytest.approx(
        sum(r["put_gex"] for r in payload["strikes"]), rel=1e-6
    )
    assert payload["net_gex"] == pytest.approx(
        payload["total_call_gex"] + payload["total_put_gex"], rel=1e-6
    )
