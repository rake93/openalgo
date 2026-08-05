"""
GEX (Gamma Exposure) Service - the `/gex` Tools page.

Computes signed dealer gamma exposure across strikes from one live option
chain, using the shared maths in `services/gex_levels/`:

    GEX_k = gamma_k(call) * OI_k(call) * F^2 * 0.01
          - gamma_k(put)  * OI_k(put)  * F^2 * 0.01

This module is the IO boundary - chain fetch and forward resolution - and
`services/gex_levels/exposure.py` is the pure maths. It is the
same pipeline the GEX Levels chart study runs, deliberately: before this, the
Tools page computed the same quantity three ways worse.

1. It multiplied open interest by the lot size. The broker reports OI in
   units, already lot-multiplied (verified across 188 live NIFTY values, every
   one an exact multiple of 65), so that inflated every figure by the lot
   size. `lot_size` survives in the response as a display value only - the
   page divides raw OI by it to show lots - and is not part of the maths.
2. It priced Black-76 off spot rather than the per-expiry forward. Gamma peaks
   at the ATM-FORWARD strike, and the measured BANKNIFTY 21-day basis is
   +138.9 points, more than one strike wide, so the walls landed on the wrong
   strikes rather than merely at the wrong scale.
3. It called `calculate_greeks` once per strike - up to 182 service calls for
   a 45-strike chain, each re-parsing the symbol and recomputing time to
   expiry. It is now one chain fetch and one direct `black76` pass.

CONTRACT NOTE - put GEX is signed. `pe_gex` and `total_pe_gex` are NEGATIVE,
matching `price_exposures`' calls-positive/puts-negative convention and the
chart study's dashboard, and `total_net_gex = total_ce_gex + total_pe_gex`.
This page previously reported puts as a positive magnitude and netted with a
subtraction. Per-strike `net_gex` and `total_net_gex` are numerically
unchanged by the flip - `ce - pe` with a positive put equals `ce + pe` with a
signed one - only the put columns change sign. It exists so that `/gex` and
the chart study report the same quantity the same way.
"""

import math
from typing import Any

from services.gex_levels.expiry import expiry_datetime
from services.gex_levels.exposure import ChainRow, compute_exposures
from services.option_chain_service import get_option_chain
from services.option_greeks_service import (
    DEFAULT_INTEREST_RATES,
    _resolve_forward_price,
    calculate_time_to_expiry,
    get_underlying_exchange,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# 45 strikes each side of ATM = 91 strikes = 182 option symbols. That is past
# the <=100-symbol fyers multiquote OI bucket that oi_tracker_service.py
# documents, which is why the chart study caps itself at 23 - but it is NOT a
# problem on this broker, and narrowing the window here would be a product
# regression rather than a fix. Measured on a live NIFTY chain: of the 94 legs
# common to a 23-strike and a 45-strike request, ZERO lose their open
# interest, and all 28 empty legs at 45 are genuinely dead deep-OTM strikes in
# the outer ring. The bucket limit is fyers-specific and remains unverified
# there. Do not "fix" this to 23 without re-measuring on the broker in front
# of you.
STRIKE_COUNT = 45


def get_gex_data(
    underlying: str, exchange: str, expiry_date: str, api_key: str
) -> tuple[bool, dict[str, Any], int]:
    """
    Get Gamma Exposure data for all strikes.

    Fetches one option chain, resolves the per-expiry forward, and runs the
    shared exposure maths over it. Returns raw CE/PE open interest (the OI
    walls) and signed GEX per strike.

    Args:
        underlying: Underlying symbol (e.g., NIFTY, BANKNIFTY)
        exchange: Exchange (NFO, BFO)
        expiry_date: Expiry in DDMMMYY format
        api_key: OpenAlgo API key

    Returns:
        Tuple of (success, response_data, status_code)
    """
    try:
        try:
            from opengreeks import black76
        except ImportError:
            logger.error("opengreeks library not installed.")
            return (
                False,
                {
                    "status": "error",
                    "message": "GEX requires the opengreeks library. Install with: pip install opengreeks",
                },
                500,
            )

        success, chain_response, status_code = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=STRIKE_COUNT,
            api_key=api_key,
        )

        if not success:
            return False, chain_response, status_code

        full_chain = chain_response.get("chain", [])
        atm_strike = chain_response.get("atm_strike")
        spot_price = chain_response.get("underlying_ltp")

        if not spot_price or spot_price <= 0:
            return False, {"status": "error", "message": "Could not determine spot price"}, 500

        expiry_dt = expiry_datetime(expiry_date, exchange)
        t_years, _dte_days = calculate_time_to_expiry(expiry_dt)

        interest_rate = DEFAULT_INTEREST_RATES.get(exchange.upper(), 0)
        r = interest_rate / 100.0

        # Forward, never spot: gamma peaks at the ATM-FORWARD strike, so
        # pricing off spot displaces both walls by the cash-future basis. The
        # measured BANKNIFTY 21-day basis is +138.9 points - wider than one
        # strike, so it moves the levels, not just their scale. None means the
        # synthetic could not be built (missing ATM quotes), and spot is the
        # documented fallback.
        base_symbol = chain_response.get("underlying", underlying)
        forward = _resolve_forward_price(
            base_symbol,
            exchange,
            get_underlying_exchange(base_symbol, exchange),
            expiry_dt,
            api_key,
        )
        F = forward or spot_price

        rows = _build_chain_rows(full_chain)

        exposures = compute_exposures(
            black76,
            rows,
            forward=F,
            t_years=t_years,
            r=r,
            atm_strike=atm_strike,
            weight_by="oi",
        )

        by_strike = {row.strike: row for row in rows}
        gex_chain = []
        for exposure in exposures:
            row = by_strike[exposure.strike]
            gex_chain.append(
                {
                    "strike": exposure.strike,
                    # Raw units, exactly as the broker reported them. The page
                    # divides by lot_size itself to display lots.
                    "ce_oi": row.call_oi,
                    "pe_oi": row.put_oi,
                    "ce_gamma": round(exposure.call_gamma, 6),
                    "pe_gamma": round(exposure.put_gamma, 6),
                    "ce_gex": round(exposure.call_gex, 2),
                    "pe_gex": round(exposure.put_gex, 2),
                    "net_gex": round(exposure.net_gex, 2),
                }
            )

        total_ce_oi = sum(item["ce_oi"] for item in gex_chain)
        total_pe_oi = sum(item["pe_oi"] for item in gex_chain)
        total_ce_gex = round(sum(e.call_gex for e in exposures), 2)
        total_pe_gex = round(sum(e.put_gex for e in exposures), 2)
        # An addition, not a subtraction: put GEX is already signed negative.
        total_net_gex = round(total_ce_gex + total_pe_gex, 2)
        pcr_oi = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0

        return (
            True,
            {
                "status": "success",
                "underlying": base_symbol,
                "spot_price": spot_price,
                # The forward the GEX above was actually priced off, not a
                # separate lookup: the badge and the maths cannot disagree.
                # None when the synthetic could not be built, which is the same
                # condition under which F fell back to spot.
                "forward_price": round(forward, 2) if forward else None,
                # Display only. The page divides OI by it to show lots; it is
                # deliberately NOT a factor in the GEX maths - see the module
                # docstring and the note in gex_levels/exposure.py.
                "lot_size": _display_lot_size(rows),
                "atm_strike": atm_strike,
                "expiry_date": expiry_date,
                "pcr_oi": pcr_oi,
                "total_ce_oi": total_ce_oi,
                "total_pe_oi": total_pe_oi,
                "total_ce_gex": total_ce_gex,
                "total_pe_gex": total_pe_gex,
                "total_net_gex": total_net_gex,
                "chain": gex_chain,
            },
            200,
        )

    except Exception:
        logger.exception("Error in get_gex_data")
        return (
            False,
            {"status": "error", "message": "Error fetching GEX data"},
            500,
        )


def _build_chain_rows(full_chain: list[dict[str, Any]]) -> list[ChainRow]:
    """
    Flatten the option-chain response into `ChainRow`s for the pure maths.

    Rows whose strike is missing or non-positive are skipped - they carry no
    usable premium or position data and would only inject a bad strike into
    the profile.

    Args:
        full_chain: The `chain` list from `option_chain_service.get_option_chain`.

    Returns:
        One ChainRow per usable strike.
    """
    rows: list[ChainRow] = []
    for item in full_chain:
        strike = item.get("strike")
        if not isinstance(strike, (int, float)) or strike <= 0:
            continue
        ce = item.get("ce") or {}
        pe = item.get("pe") or {}

        rows.append(
            ChainRow(
                strike=strike,
                call_price=_clean(ce.get("ltp")),
                put_price=_clean(pe.get("ltp")),
                call_oi=_clean(ce.get("oi")),
                put_oi=_clean(pe.get("oi")),
                call_volume=_clean(ce.get("volume")),
                put_volume=_clean(pe.get("volume")),
                lot_size=ce.get("lotsize") or pe.get("lotsize") or 1,
            )
        )
    return rows


def _display_lot_size(rows: list[ChainRow]) -> int:
    """
    The lot size to show on the page, and to divide OI by for a lots figure.

    Prefers the first row actually reporting one over `rows[0]`, because a
    deep-OTM edge strike can come back with neither leg present and would
    default the whole page to 1 - which would then render open interest in
    units under a "lots" heading.

    Args:
        rows: Chain rows, any order.

    Returns:
        The lot size, or 1 when no row reports one.
    """
    return next((row.lot_size for row in rows if row.lot_size > 1), 1)


def _clean(value: Any) -> float:
    """
    Coerce a chain field to a usable number, mapping absent and NaN to zero.

    Broker adapters do emit NaN open interest, and a null field read as a
    float arrives the same way. NaN reaching the payload would make
    `json.dumps` emit `NaN`, which `JSON.parse` rejects outright - taking the
    whole page down rather than one strike.

    Args:
        value: A raw `ltp`, `oi` or `volume` from the chain response.

    Returns:
        The value, or 0 when it is absent, null or non-finite.
    """
    number = value or 0
    if isinstance(number, float) and not math.isfinite(number):
        return 0
    return number
