"""Regression coverage for the commodity-underlying fix in `get_option_chain`.

Runs without a broker session and without any production database. Every
seam that would otherwise touch the DB or a broker is patched:

- `get_quotes` (underlying LTP fetch) -- also the seam used to capture the
  exact (symbol, exchange) pair the service decided to quote.
- `get_available_strikes` (would query `SymToken` on a cache miss).
- `get_option_symbols_for_chain` (would query `SymToken` for each CE/PE).
- `resolve_pricing_underlying`, for the commodity-path tests only, per the
  task brief -- it is patched to return a synthetic `UnderlyingRef` so the
  test needs no symbol master. It is intentionally left un-patched for the
  NFO/BFO tests below: `resolve_pricing_underlying` is proven never to touch
  the database for non-commodity exchanges in `test_pricing_underlying.py`
  (`test_nfo_resolves_to_spot_without_touching_the_database` /
  `test_bfo_resolves_to_spot`), so calling the real function there is both
  hermetic and a stronger assertion of the metadata's correctness.

`parse_underlying_symbol`, `get_option_exchange`, `find_atm_strike_from_actual`
and `get_strikes_with_labels` are pure (no DB, no I/O) and are used for real.

All chains are fetched with `with_quotes=False`, which skips the per-strike
broker multiquote step entirely (Step 8 in `get_option_chain`) -- irrelevant
to what these tests check: which underlying gets quoted, and what the
response's `underlying_ltp` / `underlying_ref` fields report.
"""

from unittest.mock import MagicMock, patch

from services.option_chain_service import get_option_chain
from services.pricing_underlying import UnderlyingRef


def _quotes_mock(ltp: float, prev_close: float = 0.0) -> MagicMock:
    """A `get_quotes` stand-in returning a fixed LTP/prev_close, success case."""
    return MagicMock(
        return_value=(
            True,
            {"status": "success", "data": {"ltp": ltp, "prev_close": prev_close}},
            200,
        )
    )


def _fake_chain_symbols(base_symbol, expiry_date, strikes_with_labels, exchange):
    """Stand-in for `get_option_symbols_for_chain`: one strike, both legs present.

    Real `get_option_symbols_for_chain` queries `SymToken` per CE/PE; these
    tests only care about the underlying resolution and the response's
    top-level fields, so a single canned strike is enough to let the chain
    build succeed without a database.
    """
    strike = strikes_with_labels[0]["strike"] if strikes_with_labels else 0.0
    return [
        {
            "strike": strike,
            "ce": {
                "symbol": f"{base_symbol}{expiry_date}{int(strike)}CE",
                "label": "ATM",
                "exists": True,
                "lotsize": 75,
                "tick_size": 0.05,
            },
            "pe": {
                "symbol": f"{base_symbol}{expiry_date}{int(strike)}PE",
                "label": "ATM",
                "exists": True,
                "lotsize": 75,
                "tick_size": 0.05,
            },
        }
    ]


def test_nfo_index_still_quotes_the_index_on_nse_index():
    quotes_mock = _quotes_mock(ltp=24250.0, prev_close=24100.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[24200.0, 24250.0, 24300.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, status_code = get_option_chain(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    assert status_code == 200
    quotes_mock.assert_called_once_with(symbol="NIFTY", exchange="NSE_INDEX", api_key="test-key")
    assert response["underlying_ref"]["symbol"] == "NIFTY"
    assert response["underlying_ref"]["exchange"] == "NSE_INDEX"
    assert response["underlying_ref"]["kind"] == "SPOT"
    assert response["underlying_ref"]["method"] == "spot_default"


def test_nfo_stock_still_quotes_the_equity_on_nse():
    quotes_mock = _quotes_mock(ltp=2500.0, prev_close=2480.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[2450.0, 2500.0, 2550.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, status_code = get_option_chain(
            underlying="RELIANCE",
            exchange="NFO",
            expiry_date="25AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    assert status_code == 200
    quotes_mock.assert_called_once_with(symbol="RELIANCE", exchange="NSE", api_key="test-key")
    assert response["underlying_ref"]["symbol"] == "RELIANCE"
    assert response["underlying_ref"]["exchange"] == "NSE"
    assert response["underlying_ref"]["kind"] == "SPOT"
    assert response["underlying_ref"]["method"] == "spot_default"


def test_bfo_index_still_quotes_on_bse_index():
    quotes_mock = _quotes_mock(ltp=81100.0, prev_close=80900.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[81000.0, 81100.0, 81200.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, status_code = get_option_chain(
            underlying="SENSEX",
            exchange="BFO",
            expiry_date="06AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    assert status_code == 200
    quotes_mock.assert_called_once_with(symbol="SENSEX", exchange="BSE_INDEX", api_key="test-key")
    assert response["underlying_ref"]["symbol"] == "SENSEX"
    assert response["underlying_ref"]["exchange"] == "BSE_INDEX"
    assert response["underlying_ref"]["kind"] == "SPOT"
    assert response["underlying_ref"]["method"] == "spot_default"


def test_commodity_quotes_the_linked_future_not_the_bare_root():
    synthetic_ref = UnderlyingRef(
        symbol="CRUDEOIL19AUG26FUT",
        exchange="MCX",
        kind="FUTURE",
        option_expiry="17AUG26",
        underlying_expiry="19-AUG-26",
        method="linked_future_nearest_on_or_after_option_expiry",
    )
    quotes_mock = _quotes_mock(ltp=6350.0, prev_close=6300.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.resolve_pricing_underlying",
            return_value=synthetic_ref,
        ) as resolve_mock,
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[6300.0, 6350.0, 6400.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, status_code = get_option_chain(
            underlying="CRUDEOIL",
            exchange="MCX",
            expiry_date="17AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    assert status_code == 200
    # The defect: this used to be called with the bare root ("CRUDEOIL", "MCX"),
    # which does not exist as a quotable instrument on MCX.
    quotes_mock.assert_called_once_with(
        symbol="CRUDEOIL19AUG26FUT", exchange="MCX", api_key="test-key"
    )
    resolve_mock.assert_called_once_with("CRUDEOIL", "MCX", "17AUG26")


def test_response_keeps_underlying_ltp_for_compatibility():
    quotes_mock = _quotes_mock(ltp=24250.0, prev_close=24100.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[24200.0, 24250.0, 24300.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, _status_code = get_option_chain(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    # Pre-existing fields, unchanged in both value and type: seven pages and
    # the documented /api/v1/optionchain contract (docs/api/options-services/
    # optionchain.md) read `underlying` as the plain base-symbol string and
    # `underlying_ltp` as the price. Neither is touched by this change --
    # the new structured metadata is added under the separate `underlying_ref`
    # key instead of overloading `underlying` (see report for why).
    assert response["underlying"] == "NIFTY"
    assert response["underlying_ltp"] == 24250.0
    assert response["underlying_prev_close"] == 24100.0


def test_response_reports_underlying_metadata():
    quotes_mock = _quotes_mock(ltp=24250.0, prev_close=24100.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[24200.0, 24250.0, 24300.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, _status_code = get_option_chain(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    assert response["underlying_ref"] == {
        "symbol": "NIFTY",
        "exchange": "NSE_INDEX",
        "kind": "SPOT",
        "option_expiry": "11AUG26",
        "underlying_expiry": None,
        "method": "spot_default",
    }


def test_commodity_metadata_reports_kind_future_and_both_expiries():
    synthetic_ref = UnderlyingRef(
        symbol="CRUDEOIL19AUG26FUT",
        exchange="MCX",
        kind="FUTURE",
        option_expiry="17AUG26",
        underlying_expiry="19-AUG-26",
        method="linked_future_nearest_on_or_after_option_expiry",
    )
    quotes_mock = _quotes_mock(ltp=6350.0, prev_close=6300.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.resolve_pricing_underlying",
            return_value=synthetic_ref,
        ),
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[6300.0, 6350.0, 6400.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, _status_code = get_option_chain(
            underlying="CRUDEOIL",
            exchange="MCX",
            expiry_date="17AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    assert response["underlying_ref"]["kind"] == "FUTURE"
    assert response["underlying_ref"]["symbol"] == "CRUDEOIL19AUG26FUT"
    assert response["underlying_ref"]["option_expiry"] == "17AUG26"
    assert response["underlying_ref"]["underlying_expiry"] == "19-AUG-26"


def test_resolver_failure_does_not_break_a_normal_chain():
    """A defect in the metadata-only resolver call must not fail an otherwise
    working NFO/BFO chain -- it only records what was already decided, it
    never influences the quote that was already successfully fetched."""
    quotes_mock = _quotes_mock(ltp=24250.0, prev_close=24100.0)
    with (
        patch("services.option_chain_service.get_quotes", quotes_mock),
        patch(
            "services.option_chain_service.resolve_pricing_underlying",
            side_effect=RuntimeError("resolver exploded"),
        ),
        patch(
            "services.option_chain_service.get_available_strikes",
            return_value=[24200.0, 24250.0, 24300.0],
        ),
        patch(
            "services.option_chain_service.get_option_symbols_for_chain",
            side_effect=_fake_chain_symbols,
        ),
    ):
        success, response, status_code = get_option_chain(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            strike_count=1,
            api_key="test-key",
            with_quotes=False,
        )

    assert success is True
    assert status_code == 200
    assert response["underlying"] == "NIFTY"
    assert response["underlying_ltp"] == 24250.0
    assert response["chain"]
    # Metadata degrades gracefully rather than taking the whole chain down.
    assert response["underlying_ref"] is None
