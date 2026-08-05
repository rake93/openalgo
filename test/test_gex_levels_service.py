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


def test_every_strike_carries_delta_exposure_alongside_gamma():
    """The Metric toggle switches which field the bar column reads, so both
    must be present on every strike of the same payload - not fetched twice.

    Also pins strike count against quality.strikes_used, folded in here rather
    than kept in a separate test now that both check the same "strikes" list.
    """
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert len(payload["strikes"]) == payload["quality"]["strikes_used"]
    for item in payload["strikes"]:
        assert set(item) == {
            "strike",
            "call_gex",
            "put_gex",
            "net_gex",
            "call_dex",
            "put_dex",
            "net_dex",
        }


def test_delta_exposure_is_signed_by_leg_not_by_dealer_convention():
    """Every call leg is non-negative and every put leg non-positive, matching
    Black-76 delta's own sign rather than GEX's dealer convention. That holds
    at every strike regardless of forward or OI, which makes it the strongest
    tripwire here: a dealer sign flip applied to delta would turn put_dex
    positive at the very first strike, and it also catches a call_dex/put_dex
    mapping swap in the payload dict - a swap that leaves net_dex, and so
    every other assertion in this file, unchanged.

    net_dex additionally straddles zero on this fixture - the low strike nets
    positive, the high strike negative - but not because either strike is deep
    enough for delta to saturate. At 30 DTE the five strikes sit within about
    +/-0.85% of the forward (measured call/put delta: 0.770/-0.332 at 24400,
    0.357/-0.742 at 24800 - nowhere near +1/0 or 0/-1). The crossing instead
    comes from the fixture's 100000/90000 call-to-put OI ratio, which places
    the OI-weighted zero crossing essentially at the forward. Because that
    depends on the stub forward sitting inside the strike range, a fixture
    change could make this half of the test fail spuriously without the sign
    convention being wrong - the call_dex/put_dex assertions above carry no
    such dependency.
    """
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert all(s["call_dex"] >= 0 for s in payload["strikes"])
    assert all(s["put_dex"] <= 0 for s in payload["strikes"])

    net = [item["net_dex"] for item in payload["strikes"]]
    assert any(v < 0 for v in net), f"no negative net_dex in {net}"
    assert any(v > 0 for v in net), f"no positive net_dex in {net}"


# ------------------------------------------------- the recorder / live-path seam


def test_the_recorder_seam_reproduces_the_live_payload_exactly():
    """The failure this design exists to prevent: a recorder that reimplements
    the maths and drifts. /gex drifted from the study exactly that way and
    shipped three defects. If these two ever differ, one of them is computing
    something the other is not."""
    from services.gex_levels_service import build_snapshot, fetch_snapshot_inputs

    chain, forward = _patched()
    with chain, forward:
        ok, live_payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")
    assert ok is True

    chain, forward = _patched()
    with chain, forward:
        from opengreeks import black76

        inputs = fetch_snapshot_inputs("NIFTY", "NFO", EXPIRY, "key")
        seam_payload = build_snapshot(black76, inputs, "oi")

    # `source` and `as_of` are provenance, stamped by each wrapper rather than by
    # the compute core - the recorder's rows are not "live". Everything the study
    # actually draws from must match exactly.
    assert seam_payload == {k: v for k, v in live_payload.items() if k not in ("source", "as_of")}
    assert live_payload["source"] == "live"


def test_one_fetch_serves_both_weightings_with_one_iv_solve():
    """The recorder writes OI and volume columns from a single tick. resolve_ivs
    is weighting-independent and is the expensive half - two solver calls per
    strike - so it must be paid once, not twice."""
    from services.gex_levels import exposure
    from services.gex_levels_service import build_snapshot, fetch_snapshot_inputs

    chain, forward = _patched()
    with (
        chain,
        forward,
        patch("services.gex_levels_service.resolve_ivs", wraps=exposure.resolve_ivs) as solve,
    ):
        from opengreeks import black76

        inputs = fetch_snapshot_inputs("NIFTY", "NFO", EXPIRY, "key")
        by_oi = build_snapshot(black76, inputs, "oi")
        by_vol = build_snapshot(black76, inputs, "volume")

    assert solve.call_count == 1
    assert [s["strike"] for s in by_oi["strikes"]] == [s["strike"] for s in by_vol["strikes"]]
    assert by_oi["weight_by"] == "oi"
    assert by_vol["weight_by"] == "volume"


def test_an_unusable_chain_raises_a_typed_error_not_a_bare_valueerror():
    """The wrapper maps this to 404 and the recorder maps it to 'skip this tick'.
    A bare ValueError would be caught by the wrapper's broad except and reported
    as a 500 - an operator would go looking for a crash that never happened."""
    from services.gex_levels_service import UnusableChain, fetch_snapshot_inputs

    empty = {"status": "success", "chain": [], "atm_strike": None, "underlying_ltp": 0}
    with (
        patch("services.gex_levels_service.get_option_chain", return_value=(True, empty, 200)),
        patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0),
    ):
        with pytest.raises(UnusableChain):
            fetch_snapshot_inputs("NIFTY", "NFO", EXPIRY, "key")


def test_a_failed_chain_fetch_raises_a_typed_error_carrying_the_brokers_response():
    """The endpoint passes the broker's own message and status straight through,
    so the seam must not flatten them into a generic error."""
    from services.gex_levels_service import ChainFetchFailed, fetch_snapshot_inputs

    failure = {"status": "error", "message": "No strikes"}
    with patch("services.gex_levels_service.get_option_chain", return_value=(False, failure, 404)):
        with pytest.raises(ChainFetchFailed) as exc:
            fetch_snapshot_inputs("NIFTY", "NFO", EXPIRY, "key")

    assert exc.value.status_code == 404
    assert exc.value.response == failure
