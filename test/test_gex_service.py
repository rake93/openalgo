"""The `/gex` Tools page: chain fetch to assembled payload. The chain is stubbed.

This page had no test at all until the migration onto `services/gex_levels/`,
which is how three defects survived in it: open interest multiplied by the lot
size, Black-76 priced off spot instead of the forward, and one
`calculate_greeks` service call per strike. The first two are pinned here.
"""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import opengreeks
import pytest

from services.gex_service import STRIKE_COUNT, get_gex_data

# Resolved at import rather than hardcoded: a fixed expiry string silently
# turns into an EXPIRED option once that date passes, and
# calculate_time_to_expiry then returns t=0, which makes safe_gamma return 0.0
# for every leg - every magnitude assertion here would start comparing zero
# against zero and pass for the wrong reason.
EXPIRY = (datetime.now() + timedelta(days=30)).strftime("%d%b%y").upper()

SPOT = 24590.0
FORWARD = 24680.0
LOT_SIZE = 75


class _FlatGamma:
    """Gamma independent of strike, so the GEX arithmetic is checkable by hand."""

    def __init__(self, gamma=0.001):
        self._gamma = gamma

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def gamma(self, flag, F, K, t, r, sigma):
        return self._gamma


def _chain_response(strikes=(24400, 24500, 24600, 24700, 24800), ce_oi=100000, pe_oi=90000):
    """Shaped exactly as option_chain_service.get_option_chain returns."""
    rows = [
        {
            "strike": float(strike),
            "ce": {
                "symbol": f"NIFTY{EXPIRY}{strike}CE",
                "ltp": 120.0,
                "oi": ce_oi,
                "volume": 5000,
                "lotsize": LOT_SIZE,
            },
            "pe": {
                "symbol": f"NIFTY{EXPIRY}{strike}PE",
                "ltp": 110.0,
                "oi": pe_oi,
                "volume": 4000,
                "lotsize": LOT_SIZE,
            },
        }
        for strike in strikes
    ]
    return {
        "status": "success",
        "chain": rows,
        "atm_strike": 24600.0,
        "underlying_ltp": SPOT,
        "underlying": "NIFTY",
    }


@contextmanager
def _patched(chain=None, forward=FORWARD, gamma=None):
    """Patch every IO boundary: chain fetch, forward, futures price.

    `gamma` swaps the real black76 for a flat-gamma stub. It is patched on the
    `opengreeks` package rather than on this service because the service
    imports the module lazily inside the call, which is the ImportError guard
    every options service uses.
    """
    chain_response = _chain_response() if chain is None else chain
    with (
        patch(
            "services.gex_service.get_option_chain",
            return_value=(True, chain_response, 200),
        ),
        patch("services.gex_service._resolve_forward_price", return_value=forward),
        patch("services.gex_service._get_nearest_futures_price", return_value=24700.0),
    ):
        if gamma is None:
            yield
        else:
            with patch.object(opengreeks, "black76", _FlatGamma(gamma), create=True):
                yield


def _call(**kwargs):
    return get_gex_data("NIFTY", "NFO", EXPIRY, "key", **kwargs)


def test_the_chain_is_fetched_at_forty_five_strikes():
    """Measured on this broker: of the 94 legs common to a 23-strike and a
    45-strike request, none lose their open interest, and every empty leg at
    45 is a genuinely dead deep-OTM strike. Narrowing this window would drop
    real strikes from the page to fix a problem this broker does not have."""
    assert STRIKE_COUNT == 45

    with (
        patch(
            "services.gex_service.get_option_chain",
            return_value=(True, _chain_response(), 200),
        ) as fetch,
        patch("services.gex_service._resolve_forward_price", return_value=FORWARD),
        patch("services.gex_service._get_nearest_futures_price", return_value=None),
    ):
        _call()

    fetch.assert_called_once()
    assert fetch.call_args.kwargs["strike_count"] == STRIKE_COUNT


def test_the_payload_carries_every_field_the_frontend_reads():
    """The TypeScript GEXDataResponse is the contract, and a silently absent
    key reads as `undefined` rather than failing - that exact bug shipped once
    already in this feature. Pin the whole shape, top level and per strike."""
    with _patched():
        ok, payload, status = _call()

    assert ok is True
    assert status == 200
    for key in (
        "status",
        "underlying",
        "spot_price",
        "futures_price",
        "lot_size",
        "atm_strike",
        "expiry_date",
        "pcr_oi",
        "total_ce_oi",
        "total_pe_oi",
        "total_ce_gex",
        "total_pe_gex",
        "total_net_gex",
        "chain",
    ):
        assert key in payload, f"payload is missing {key!r}, which the frontend reads"

    assert payload["status"] == "success"
    assert payload["underlying"] == "NIFTY"
    assert payload["spot_price"] == SPOT
    assert payload["futures_price"] == 24700.0
    assert payload["lot_size"] == LOT_SIZE
    assert payload["atm_strike"] == 24600.0
    assert payload["expiry_date"] == EXPIRY

    assert len(payload["chain"]) == 5
    for item in payload["chain"]:
        assert set(item) == {
            "strike",
            "ce_oi",
            "pe_oi",
            "ce_gamma",
            "pe_gamma",
            "ce_gex",
            "pe_gex",
            "net_gex",
        }


def test_the_strike_profile_is_sorted_and_carries_raw_open_interest():
    """The page divides ce_oi/pe_oi by lot_size itself to display lots, so the
    payload must stay in the broker's units - already lot-multiplied."""
    with _patched():
        _, payload, _ = _call()

    strikes = [item["strike"] for item in payload["chain"]]
    assert strikes == sorted(strikes)
    for item in payload["chain"]:
        assert item["ce_oi"] == 100000
        assert item["pe_oi"] == 90000


def test_put_gex_is_signed_negative_and_call_gex_positive():
    """The deliberate contract change: puts are negative, matching
    price_exposures' convention and the GEX Levels chart study, so the two
    surfaces report the same quantity the same way."""
    with _patched():
        _, payload, _ = _call()

    assert payload["total_ce_gex"] > 0
    assert payload["total_pe_gex"] < 0
    for item in payload["chain"]:
        assert item["ce_gex"] > 0, f"call GEX must be positive at {item['strike']}"
        assert item["pe_gex"] < 0, f"put GEX must be negative at {item['strike']}"


def test_net_gex_is_the_sum_of_the_two_signed_totals():
    """An addition now, not a subtraction. `ce - pe` with a positive put
    equals `ce + pe` with a signed one, so net is numerically unchanged by the
    sign flip - if this ever fails, the flip was applied twice."""
    with _patched():
        _, payload, _ = _call()

    assert payload["total_net_gex"] == pytest.approx(
        payload["total_ce_gex"] + payload["total_pe_gex"], rel=1e-9
    )
    assert payload["total_ce_gex"] == pytest.approx(
        sum(item["ce_gex"] for item in payload["chain"]), rel=1e-6
    )
    assert payload["total_pe_gex"] == pytest.approx(
        sum(item["pe_gex"] for item in payload["chain"]), rel=1e-6
    )
    for item in payload["chain"]:
        # abs=0.02 because each column is rounded to 2dp independently, so the
        # two roundings can disagree with the rounded net by one cent each.
        assert item["net_gex"] == pytest.approx(item["ce_gex"] + item["pe_gex"], abs=0.02)


def test_gex_carries_no_lot_size_factor():
    """The defect this migration exists to fix. The broker reports OI in units,
    already lot-multiplied (188 live NIFTY values, every one an exact multiple
    of 65), so multiplying by lot_size again inflated every figure by 65x on
    NIFTY. One strike, known OI, flat gamma, checked by hand."""
    gamma = 0.0004
    ce_oi = 100000
    chain = _chain_response(strikes=(24600,), ce_oi=ce_oi, pe_oi=0)

    with _patched(chain=chain, gamma=gamma):
        _, payload, _ = _call()

    expected = gamma * ce_oi * FORWARD * FORWARD * 0.01
    assert payload["chain"][0]["ce_gex"] == pytest.approx(expected, rel=1e-9)
    assert payload["chain"][0]["ce_gex"] != pytest.approx(expected * LOT_SIZE, rel=1e-6)
    assert payload["total_ce_gex"] == pytest.approx(expected, rel=1e-9)
    # lot_size is still reported - it is display data, not a multiplier.
    assert payload["lot_size"] == LOT_SIZE


def test_the_gamma_columns_are_populated_and_non_negative():
    """`ce_gamma`/`pe_gamma` are read positionally against the strike list, so
    a silently-absent or negative gamma would render as a dead chain beside
    non-zero GEX figures."""
    with _patched():
        _, payload, _ = _call()

    for item in payload["chain"]:
        assert item["ce_gamma"] > 0, f"call gamma missing at {item['strike']}"
        assert item["pe_gamma"] > 0, f"put gamma missing at {item['strike']}"


def test_greeks_are_computed_against_the_forward_not_spot():
    """Gamma peaks at the ATM-FORWARD strike. The measured BANKNIFTY 21-day
    basis is +138.9 points, wider than one strike, so pricing off spot moves
    the walls rather than merely their scale. The cleanest proof the forward
    reached the maths is that the profile changes when it is taken away."""
    with _patched(forward=FORWARD):
        _, at_forward, _ = _call()
    with _patched(forward=None):
        _, at_spot, _ = _call()

    # Spot is still reported verbatim either way - only the pricing moved.
    assert at_forward["spot_price"] == SPOT
    assert at_spot["spot_price"] == SPOT

    assert at_forward["total_net_gex"] != at_spot["total_net_gex"]
    assert [item["ce_gamma"] for item in at_forward["chain"]] != [
        item["ce_gamma"] for item in at_spot["chain"]
    ]


def test_pcr_is_put_over_call_open_interest():
    with _patched():
        _, payload, _ = _call()

    assert payload["total_ce_oi"] == 5 * 100000
    assert payload["total_pe_oi"] == 5 * 90000
    assert payload["pcr_oi"] == 0.9


def test_pcr_is_zero_rather_than_a_division_error_on_an_empty_call_side():
    chain = _chain_response(ce_oi=0)
    with _patched(chain=chain):
        _, payload, _ = _call()

    assert payload["pcr_oi"] == 0


def test_the_payload_never_contains_a_non_finite_float():
    """float('inf') and NaN serialise as Infinity/NaN, which JSON.parse rejects
    outright - taking the whole page down rather than one strike."""
    chain = _chain_response()
    chain["chain"][0]["ce"]["oi"] = float("nan")
    chain["chain"][1]["pe"]["ltp"] = float("inf")

    with _patched(chain=chain):
        _, payload, _ = _call()

    json.dumps(payload, allow_nan=False)


def test_a_chain_failure_is_passed_through():
    failure = {"status": "error", "message": "No strikes"}
    with patch("services.gex_service.get_option_chain", return_value=(False, failure, 404)):
        ok, payload, status = _call()

    assert ok is False
    assert status == 404
    assert payload is failure


def test_a_missing_spot_price_is_an_error_rather_than_a_zero_profile():
    chain = _chain_response()
    chain["underlying_ltp"] = 0

    with patch(
        "services.gex_service.get_option_chain",
        return_value=(True, chain, 200),
    ):
        ok, payload, status = _call()

    assert ok is False
    assert status == 500
    assert payload["status"] == "error"
