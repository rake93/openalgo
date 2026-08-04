"""Orchestration: chain fetch to assembled payload. The chain is stubbed."""

import json
from unittest.mock import patch

from services.gex_levels_service import STRIKE_COUNT, get_gex_levels


def _chain_response():
    """Five strikes, both legs, shaped exactly as option_chain_service returns."""
    rows = []
    for strike in (24400, 24500, 24600, 24700, 24800):
        rows.append(
            {
                "strike": float(strike),
                "ce": {
                    "symbol": f"NIFTY11AUG26{strike}CE",
                    "ltp": 120.0,
                    "oi": 100000,
                    "volume": 5000,
                    "lotsize": 75,
                },
                "pe": {
                    "symbol": f"NIFTY11AUG26{strike}PE",
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


def test_a_successful_call_returns_levels_and_a_quality_verdict():
    chain, forward = _patched()
    with chain, forward:
        ok, payload, status = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

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
        _, payload, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    assert "may_draw" in payload["quality"]
    assert isinstance(payload["quality"]["may_draw"], bool)


def test_the_payload_never_contains_a_non_finite_float():
    """float('inf') serialises as Infinity, which JSON.parse rejects outright."""
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    json.dumps(payload, allow_nan=False)


def test_it_falls_back_to_spot_when_the_forward_cannot_be_resolved():
    chain, forward = _patched(forward=None)
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    assert payload["forward_price"] == 24590.0


def test_a_chain_failure_is_passed_through():
    failure = {"status": "error", "message": "No strikes"}
    with patch("services.gex_levels_service.get_option_chain", return_value=(False, failure, 404)):
        ok, payload, status = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    assert ok is False
    assert status == 404


def test_volume_weighting_produces_a_different_profile_than_oi():
    chain, forward = _patched()
    with chain, forward:
        _, by_oi, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")
    chain, forward = _patched()
    with chain, forward:
        _, by_vol, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="volume")

    assert by_oi["net_gex"] != by_vol["net_gex"]
    assert by_vol["weight_by"] == "volume"


def test_regime_follows_the_sign_of_net_gex():
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    expected = "suppressive" if payload["net_gex"] >= 0 else "amplifying"
    assert payload["regime"] == expected


def test_an_unknown_weighting_is_rejected_before_any_broker_call():
    """price_exposures raises on a bad weighting; the service must not have
    already spent a chain fetch to discover that."""
    with patch("services.gex_levels_service.get_option_chain") as fetch:
        ok, payload, status = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="delta")
    assert ok is False
    assert status == 400
    fetch.assert_not_called()
