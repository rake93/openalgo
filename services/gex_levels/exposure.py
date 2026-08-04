"""
Per-strike signed dealer gamma exposure.

    GEX_k = gamma_k(call) * w_k(call) * lot * F^2 * 0.01
          - gamma_k(put)  * w_k(put)  * lot * F^2 * 0.01

Calls positive, puts negative. That is the standard convention across every
published GEX product and encodes the approximation that dealers are long
calls and short puts at the index level. It is deliberately a single constant
here rather than a setting - if Indian market structure is ever shown to
warrant inverting it, DEALER_CALL_SIGN is the one place to change.

Units are currency delta change per 1% move in the underlying. The F^2 * 0.01
factor is constant across strikes, so it moves neither the walls nor the
zero-gamma level relative to an unscaled profile - it converts units only.
"""

from dataclasses import dataclass
from typing import Literal

from services.gex_levels.blackscholes import atm_iv_from, safe_gamma, safe_iv

WeightBy = Literal["oi", "volume"]

DEALER_CALL_SIGN = 1.0
DEALER_PUT_SIGN = -1.0

# Converts unit gamma into delta change per 1% move.
_ONE_PERCENT = 0.01


@dataclass(frozen=True)
class ChainRow:
    """One strike of the option chain, both legs, as fetched."""

    strike: float
    call_price: float
    put_price: float
    call_oi: float
    put_oi: float
    call_volume: float
    put_volume: float
    lot_size: int


@dataclass(frozen=True)
class StrikeExposure:
    """Signed gamma exposure at one strike, in currency per 1% move."""

    strike: float
    call_gex: float
    put_gex: float
    net_gex: float
    call_iv: float | None
    put_iv: float | None


def compute_exposures(
    black76,
    rows: list[ChainRow],
    forward: float,
    t_years: float,
    r: float,
    atm_strike: float | None,
    weight_by: WeightBy,
) -> list[StrikeExposure]:
    """
    Signed GEX for every strike, ascending.

    Two passes, because a strike whose own premium will not invert must still
    be priced - with the chain's ATM volatility - rather than dropped. Dropping
    it would move the walls by removing real open interest from the profile.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows, any order.
        forward: Per-expiry forward price (F). Never spot.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        atm_strike: ATM strike, for the IV fallback.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        One StrikeExposure per input row, sorted by strike ascending.
    """
    ordered = sorted(rows, key=lambda row: row.strike)

    per_strike_iv: dict[float, float | None] = {}
    call_ivs: dict[float, float | None] = {}
    put_ivs: dict[float, float | None] = {}
    for row in ordered:
        call_iv = safe_iv(black76, row.call_price, forward, row.strike, r, t_years, "c")
        put_iv = safe_iv(black76, row.put_price, forward, row.strike, r, t_years, "p")
        call_ivs[row.strike] = call_iv
        put_ivs[row.strike] = put_iv
        sides = [v for v in (call_iv, put_iv) if v is not None]
        per_strike_iv[row.strike] = sum(sides) / len(sides) if sides else None

    fallback_iv = atm_iv_from(per_strike_iv, atm_strike)
    notional = forward * forward * _ONE_PERCENT

    out: list[StrikeExposure] = []
    for row in ordered:
        call_iv = call_ivs[row.strike]
        put_iv = put_ivs[row.strike]
        call_weight = row.call_volume if weight_by == "volume" else row.call_oi
        put_weight = row.put_volume if weight_by == "volume" else row.put_oi

        call_gamma = safe_gamma(
            black76, "c", forward, row.strike, t_years, r, call_iv or fallback_iv
        )
        put_gamma = safe_gamma(black76, "p", forward, row.strike, t_years, r, put_iv or fallback_iv)

        call_gex = DEALER_CALL_SIGN * call_gamma * call_weight * row.lot_size * notional
        put_gex = DEALER_PUT_SIGN * put_gamma * put_weight * row.lot_size * notional

        out.append(
            StrikeExposure(
                strike=row.strike,
                call_gex=call_gex,
                put_gex=put_gex,
                net_gex=call_gex + put_gex,
                call_iv=call_iv,
                put_iv=put_iv,
            )
        )
    return out
