"""
GEX Levels Service

The single IO boundary for the "GEX Levels" chart study: fetch one option
chain, resolve the per-expiry forward, run the pure math in
`services/gex_levels/`, and assemble a JSON-safe payload for the `/charts`
workspace. Every other module under `services/gex_levels/` is pure - no
network, no broker calls - so this is the only place that can fail on IO, and
the only place that needs a try/except around the whole pipeline.

Why this is built on the Gamma Density pipeline, not `gex_service.py`:
`services/gex_service.py` (the `/gex` Tools page) calls `calculate_greeks`
once per strike - up to 90 service calls for a 45-strike chain - and prices
Black-76 with SPOT as the forward. Neither is acceptable for a study that
refreshes on a timer: the per-strike service-call loop does not scale to a
polling refresh, and pricing at spot displaces both the call wall and the
put wall away from where dealer gamma actually concentrates (gamma peaks at
the ATM-forward strike, not the ATM-spot strike). `services/gamma_density_service.py`
already solved both problems - one `get_option_chain` call, then `black76`
directly - so this module follows that same shape.
"""

from dataclasses import asdict
from typing import Any

from services.gex_levels.expiry import expiry_datetime
from services.gex_levels.exposure import ChainRow, compute_exposures
from services.gex_levels.levels import find_walls, scan_zero_gamma
from services.gex_levels.quality import assess_quality
from services.gex_levels.sentiment import read_sentiment
from services.option_chain_service import get_option_chain
from services.option_greeks_service import (
    DEFAULT_INTEREST_RATES,
    _resolve_forward_price,
    calculate_time_to_expiry,
    get_underlying_exchange,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# 23 strikes each side of ATM = 47 strikes = 94 option symbols. This is a hard
# broker limit, not a preference: oi_tracker_service.py documents that it is
# sized to fit the fyers multiquote OI bucket (<=100 symbols) so OI comes back
# populated. Exceeding it does not raise - it silently returns EMPTY open
# interest, which would zero every exposure in this feature without an error
# anywhere. Never raise this number.
STRIKE_COUNT = 23


def get_gex_levels(
    underlying: str,
    exchange: str,
    expiry_date: str,
    api_key: str,
    weight_by: str = "oi",
    interest_rate: float | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """
    Fetch one option chain and assemble the GEX Levels payload for it.

    Pipeline:
      1. Validate `weight_by` before spending a broker round trip.
      2. Fetch the option chain (23 strikes each side of ATM).
      3. Derive time-to-expiry and the per-expiry synthetic forward.
      4. Run the pure math: exposures, walls, zero-gamma, quality.
      5. Assemble a rounded, JSON-safe payload.

    Args:
        underlying: Underlying symbol (e.g. NIFTY, BANKNIFTY).
        exchange: Options exchange (NFO, BFO, CDS, MCX, ...).
        expiry_date: Expiry in DDMMMYY format (e.g. 11AUG26).
        api_key: OpenAlgo API key.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.
        interest_rate: Optional risk-free rate (annualized %); default per exchange.

    Returns:
        Tuple of (success, response_data, status_code).
    """
    # Validated first, before the chain fetch: price_exposures raises on a bad
    # weighting, and discovering that after a broker round trip has already
    # been spent would waste it for no reason - the value never depends on
    # the chain.
    if weight_by not in ("oi", "volume"):
        return (
            False,
            {
                "status": "error",
                "message": f"weight_by must be 'oi' or 'volume', got {weight_by!r}",
            },
            400,
        )

    try:
        try:
            from opengreeks import black76
        except ImportError:
            logger.error("opengreeks library not installed.")
            return (
                False,
                {
                    "status": "error",
                    "message": "GEX Levels requires the opengreeks library. Install with: pip install opengreeks",
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
        spot_price = chain_response.get("underlying_ltp")
        atm_strike = chain_response.get("atm_strike")

        if not spot_price or spot_price <= 0 or not full_chain:
            return (
                False,
                {"status": "error", "message": "Spot price or option chain unavailable"},
                404,
            )

        expiry_dt = expiry_datetime(expiry_date, exchange)
        t_years, dte_days = calculate_time_to_expiry(expiry_dt)

        if interest_rate is None:
            interest_rate = DEFAULT_INTEREST_RATES.get(exchange.upper(), 0)
        r = interest_rate / 100.0

        # Forward, never spot: gamma peaks at the ATM-FORWARD strike, so
        # pricing off spot displaces both walls by the cash-future basis. The
        # measured BANKNIFTY 21-day basis is +138.9 points - large enough to
        # land a wall on the wrong strike.
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
            weight_by=weight_by,
        )
        walls = find_walls(exposures)
        zero_gamma = scan_zero_gamma(
            black76,
            rows,
            forward=F,
            t_years=t_years,
            r=r,
            atm_strike=atm_strike,
            weight_by=weight_by,
        )

        use_volume = weight_by == "volume"
        total_weight = sum(
            (row.call_volume + row.put_volume) if use_volume else (row.call_oi + row.put_oi)
            for row in rows
        )
        quality = assess_quality(exposures, walls, forward=F, total_weight=total_weight)

        total_call_gex = sum(e.call_gex for e in exposures)
        total_put_gex = sum(e.put_gex for e in exposures)
        net_gex = total_call_gex + total_put_gex
        regime = "suppressive" if net_gex >= 0 else "amplifying"

        # Sentiment is a SEPARATE directional read from Regime, never derived
        # from net_gex's sign - see services/gex_levels/sentiment.py's module
        # docstring for why that sign is deliberately unused here.
        sentiment = read_sentiment(
            exposures,
            walls,
            rows,
            spot=spot_price,
            forward=F,
            weight_by=weight_by,
        )

        return (
            True,
            {
                "status": "success",
                "underlying": base_symbol,
                "exchange": exchange,
                "expiry_date": expiry_date,
                "weight_by": weight_by,
                "spot_price": round(spot_price, 2),
                "forward_price": round(F, 2),
                "atm_strike": atm_strike,
                "lot_size": rows[0].lot_size if rows else 1,
                "dte_days": round(dte_days, 2),
                "interest_rate": round(interest_rate, 2),
                # The per-strike profile the chart's bar column is drawn from.
                # Without it the study renders levels but no distribution, so a
                # trader cannot see how concentrated a wall actually is.
                "strikes": [
                    {
                        "strike": e.strike,
                        "call_gex": round(e.call_gex, 2),
                        "put_gex": round(e.put_gex, 2),
                        "net_gex": round(e.net_gex, 2),
                    }
                    for e in exposures
                ],
                "total_call_gex": round(total_call_gex, 2),
                "total_put_gex": round(total_put_gex, 2),
                "call_wall": walls.call_wall,
                "put_wall": walls.put_wall,
                # None is a normal outcome - a chain can be long or short gamma
                # across its whole plausible range - and the UI shows
                # "No local cross" rather than treating it as missing data.
                "zero_gamma": round(zero_gamma, 2) if zero_gamma is not None else None,
                "net_gex": round(net_gex, 2),
                "regime": regime,
                "quality": _quality_payload(quality),
                "sentiment": {
                    "bias": sentiment.bias,
                    "score": round(sentiment.score, 3),
                    "agreeing": sentiment.agreeing,
                    "participating": sentiment.participating,
                    "signals": [
                        {
                            "key": x.key,
                            "label": x.label,
                            "detail": x.detail,
                            "bias": x.bias,
                            "why": x.why,
                            "weight": x.weight,
                        }
                        for x in sentiment.signals
                    ],
                },
            },
            200,
        )

    except Exception:
        logger.exception("Error in get_gex_levels")
        return (
            False,
            {"status": "error", "message": "Error computing GEX levels"},
            500,
        )


def _build_chain_rows(full_chain: list[dict[str, Any]]) -> list[ChainRow]:
    """
    Flatten the option-chain response into `ChainRow`s for the pure math.

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
        lot_size = ce.get("lotsize") or pe.get("lotsize") or 1

        rows.append(
            ChainRow(
                strike=strike,
                call_price=ce.get("ltp", 0) or 0,
                put_price=pe.get("ltp", 0) or 0,
                call_oi=ce.get("oi", 0) or 0,
                put_oi=pe.get("oi", 0) or 0,
                call_volume=ce.get("volume", 0) or 0,
                put_volume=pe.get("volume", 0) or 0,
                lot_size=lot_size,
            )
        )
    return rows


def _quality_payload(quality) -> dict[str, Any]:
    """
    Build the quality dict explicitly, including `may_draw`.

    `Quality.may_draw` is a `@property`, not a dataclass field, so
    `dataclasses.asdict(quality)` silently drops it. An absent key reads as
    `undefined` in TypeScript - falsy - which would render every good
    snapshot as "do not draw": a silent failure in the safe-looking
    direction. Building the dict by hand keeps `may_draw` in the payload.

    Args:
        quality: The `Quality` verdict from `assess_quality`.

    Returns:
        A JSON-safe dict carrying every field, including `may_draw`.
    """
    payload = asdict(quality)
    payload["may_draw"] = quality.may_draw
    return payload
