"""
OI Tracker Service

Provides Open Interest data aggregation and Max Pain calculation
for option chains. Reuses the existing option chain service for OI data.

Functions:
    get_oi_data() - Get OI data for all strikes with PCR and forward price
    calculate_max_pain() - Calculate max pain strike and pain distribution
"""

from typing import Any

from database.auth_db import get_auth_token_broker
from database.token_db_enhanced import fno_search_symbols
from services.gex_levels.expiry import expiry_datetime
from services.option_chain_service import get_option_chain
from services.option_greeks_service import _resolve_forward_price, get_underlying_exchange
from services.quotes_service import import_broker_module
from utils.constants import CRYPTO_EXCHANGES, INSTRUMENT_PERPFUT
from utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_display_forward(
    underlying: str, exchange: str, expiry_date: str, api_key: str
) -> float | None:
    """
    Per-expiry forward price, for display on OI Tracker, Max Pain and OI Range.

    Every F&O exchange resolves the SYNTHETIC future via the shared
    `_resolve_forward_price` - the same number `option_greeks_service` prices
    Black-76 off, so the badge agrees with the Greeks. CRYPTO keeps its
    perpetual lookup, which has no expiry to build a synthetic from.

    This replaced a lookup of the listed FUT contract, which never returned
    anything on Indian exchanges. It filtered `fno_search_symbols(underlying=)`,
    which matches on `SymToken.name`, and `name` is NULL for NFO rows on at
    least one broker's master contract - so the primary lookup AND the
    nearest-month fallback both came back empty and the pages silently dropped
    the badge. A weekly expiry could never have matched the primary lookup
    anyway: weeklies have no listed future. Put-call parity has neither problem.

    Args:
        underlying: Base symbol (e.g., NIFTY, BANKNIFTY)
        exchange: Options exchange (NFO, BFO, etc.)
        expiry_date: Expiry in DDMMMYY format (e.g., 30JAN26)
        api_key: OpenAlgo API key

    Returns:
        Forward price, or None when it cannot be resolved (pages hide the badge)
    """
    try:
        if exchange.upper() in CRYPTO_EXCHANGES:
            # CRYPTO perpetuals are stored as PERPFUT — no expiry filter
            _perp = fno_search_symbols(
                query=f"{underlying}USDFUT", exchange=exchange, instrumenttype=INSTRUMENT_PERPFUT, limit=1
            )
            if not _perp:
                logger.warning(f"No perpetual futures contracts found for {underlying} on {exchange}")
                return None

            fut_symbol = _perp[0]["symbol"]
            fut_exchange = _perp[0]["exchange"]

            # CRYPTO: bypass validate_symbol_exchange (in-memory cache miss → 400)
            auth_token, broker = get_auth_token_broker(api_key)
            if auth_token is None:
                logger.warning("Could not retrieve auth token for CRYPTO futures quote")
                return None
            logger.info(f"Fetching perpetual futures price for {fut_symbol} on {fut_exchange} via broker={broker}")
            broker_module = import_broker_module(broker)
            BrokerData = broker_module.BrokerData
            quote_response = BrokerData(auth_token).get_quotes(fut_symbol, fut_exchange)
            if isinstance(quote_response, dict) and "data" in quote_response:
                return quote_response["data"].get("ltp")
            return None

        expiry_dt = expiry_datetime(expiry_date, exchange)
        return _resolve_forward_price(
            underlying,
            exchange,
            get_underlying_exchange(underlying, exchange),
            expiry_dt,
            api_key,
        )
    except Exception as e:
        logger.warning(f"Error resolving forward price: {e}")
        return None


def get_oi_data(
    underlying: str, exchange: str, expiry_date: str, api_key: str
) -> tuple[bool, dict[str, Any], int]:
    """
    Get Open Interest data for all strikes of an underlying/expiry.

    Uses the option chain service to fetch OI data, then computes:
    - Total CE/PE OI and overall PCR
    - Per-strike PCR for PCR line
    - Futures price for the matching expiry

    Args:
        underlying: Underlying symbol (e.g., NIFTY, BANKNIFTY)
        exchange: Exchange (NSE_INDEX, BSE_INDEX, NFO, BFO)
        expiry_date: Expiry in DDMMMYY format
        api_key: OpenAlgo API key

    Returns:
        Tuple of (success, response_data, status_code)
    """
    try:
        # Fetch option chain (23 each side of ATM = 47 strikes, 94 symbols).
        # Sized to fit the fyers multiquote OI bucket (<=100 symbols) so OI is populated.
        success, chain_response, status_code = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=23,
            api_key=api_key,
        )

        if not success:
            return False, chain_response, status_code

        full_chain = chain_response.get("chain", [])
        atm_strike = chain_response.get("atm_strike")
        spot_price = chain_response.get("underlying_ltp")

        # Compute PCR and totals from the full chain
        total_ce_oi = 0
        total_pe_oi = 0
        total_ce_volume = 0
        total_pe_volume = 0
        lot_size = None

        for item in full_chain:
            if item.get("ce"):
                total_ce_oi += item["ce"].get("oi", 0) or 0
                total_ce_volume += item["ce"].get("volume", 0) or 0
                if lot_size is None and item["ce"].get("lotsize"):
                    lot_size = item["ce"]["lotsize"]
            if item.get("pe"):
                total_pe_oi += item["pe"].get("oi", 0) or 0
                total_pe_volume += item["pe"].get("volume", 0) or 0
                if lot_size is None and item["pe"].get("lotsize"):
                    lot_size = item["pe"]["lotsize"]

        pcr_oi = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0
        pcr_volume = round(total_pe_volume / total_ce_volume, 2) if total_ce_volume > 0 else 0

        # Build OI chain for chart
        oi_chain = []
        for item in full_chain:
            ce_oi = 0
            pe_oi = 0
            if item.get("ce"):
                ce_oi = item["ce"].get("oi", 0) or 0
            if item.get("pe"):
                pe_oi = item["pe"].get("oi", 0) or 0
            oi_chain.append(
                {
                    "strike": item["strike"],
                    "ce_oi": ce_oi,
                    "pe_oi": pe_oi,
                }
            )

        # Per-expiry forward for display.
        # exchange is already the options exchange (NFO/BFO) from the frontend
        forward_price = _resolve_display_forward(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            api_key=api_key,
        )

        return (
            True,
            {
                "status": "success",
                "underlying": chain_response.get("underlying", underlying),
                "spot_price": spot_price,
                "forward_price": forward_price,
                "lot_size": lot_size or 1,
                "pcr_oi": pcr_oi,
                "pcr_volume": pcr_volume,
                "total_ce_oi": total_ce_oi,
                "total_pe_oi": total_pe_oi,
                "atm_strike": atm_strike,
                "expiry_date": expiry_date,
                "chain": oi_chain,
            },
            200,
        )

    except Exception as e:
        logger.exception(f"Error in get_oi_data: {e}")
        return (
            False,
            {"status": "error", "message": "Error fetching OI data"},
            500,
        )


def calculate_max_pain(
    underlying: str, exchange: str, expiry_date: str, api_key: str
) -> tuple[bool, dict[str, Any], int]:
    """
    Calculate Max Pain for an underlying/expiry.

    Max Pain is the strike price at which option writers (sellers) would
    experience the least financial loss. For each candidate strike:
    - CE writer loss = sum of (candidate - strike) * ce_oi for all strikes below candidate
    - PE writer loss = sum of (strike - candidate) * pe_oi for all strikes above candidate
    - Total pain = CE loss + PE loss
    - Max pain = strike with minimum total pain

    Args:
        underlying: Underlying symbol
        exchange: Exchange
        expiry_date: Expiry in DDMMMYY format
        api_key: OpenAlgo API key

    Returns:
        Tuple of (success, response_data, status_code)
    """
    try:
        # First get OI data
        success, oi_response, status_code = get_oi_data(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            api_key=api_key,
        )

        if not success:
            return False, oi_response, status_code

        chain = oi_response.get("chain", [])
        lot_size = oi_response.get("lot_size", 1)

        if not chain:
            return False, {"status": "error", "message": "No OI data available"}, 404

        # Filter out invalid entries
        chain = [item for item in chain if isinstance(item.get("strike"), (int, float)) and item["strike"] > 0]
        if not chain:
            return False, {"status": "error", "message": "No valid strike data available"}, 404

        # Calculate pain at each strike
        pain_data = []
        for candidate in chain:
            candidate_strike = candidate["strike"]
            ce_pain = 0
            pe_pain = 0

            for item in chain:
                strike = item["strike"]
                ce_oi = item["ce_oi"]
                pe_oi = item["pe_oi"]

                # CE writers lose when underlying > strike (CE is ITM)
                if candidate_strike > strike and ce_oi > 0:
                    ce_pain += (candidate_strike - strike) * ce_oi

                # PE writers lose when underlying < strike (PE is ITM)
                if candidate_strike < strike and pe_oi > 0:
                    pe_pain += (strike - candidate_strike) * pe_oi

            total_pain = ce_pain + pe_pain

            pain_data.append(
                {
                    "strike": candidate_strike,
                    "ce_pain": round(ce_pain, 2),
                    "pe_pain": round(pe_pain, 2),
                    "total_pain": round(total_pain, 2),
                    # Convert to Crores for display
                    "total_pain_cr": round(total_pain / 10000000, 2),
                }
            )

        # Find max pain strike (minimum total pain)
        max_pain_entry = min(pain_data, key=lambda x: x["total_pain"])
        max_pain_strike = max_pain_entry["strike"]

        return (
            True,
            {
                "status": "success",
                "underlying": oi_response.get("underlying", underlying),
                "spot_price": oi_response.get("spot_price"),
                "forward_price": oi_response.get("forward_price"),
                "atm_strike": oi_response.get("atm_strike"),
                "max_pain_strike": max_pain_strike,
                "lot_size": lot_size,
                "pcr_oi": oi_response.get("pcr_oi"),
                "pcr_volume": oi_response.get("pcr_volume"),
                "expiry_date": expiry_date,
                "pain_data": pain_data,
            },
            200,
        )

    except Exception as e:
        logger.exception(f"Error calculating max pain: {e}")
        return (
            False,
            {"status": "error", "message": "Error calculating max pain"},
            500,
        )
