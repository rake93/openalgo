"""
Per-strike signed delta exposure (DEX).

    DEX_k = (delta_k(call) * w_k(call) + delta_k(put) * w_k(put)) * F

WHOSE delta this is: the **open-interest book's**, not the dealer's. A positive
DEX means calls dominate that strike and the book is net long delta; negative
means puts dominate. Dealers stand on the other side, so dealer delta is the
negation of this number.

Why this does NOT mirror `exposure.py`'s dealer-sign convention, which is the
trap this module exists to avoid. Gamma is positive for both legs, so
DEALER_CALL_SIGN / DEALER_PUT_SIGN are GEX's only source of sign. Delta already
carries its own: a call's delta is positive and a put's is negative. Applying
the dealer constants on top gives

    +1 * delta_call * w   ->  positive
    -1 * delta_put  * w   ->  negative x negative -> ALSO positive

so every strike contributes positively and the total is always positive,
carrying no direction at all. That is why no published DEX is defined that way.
Each leg here keeps its natural delta sign and there is no dealer flip.

As in `exposure.py` there is **no lot-size factor**: this broker's chain reports
open interest and volume already multiplied by the lot size, so multiplying
again would double-count it.

Units are currency notional - delta per unit, times the weight in units, times
the forward. The `* F` factor is constant across strikes, so it scales the
profile without moving where its extremes sit.

Purity: no network, database, logging or clock. Plain inputs, plain values.
"""

import math
from dataclasses import dataclass

from services.gex_levels.blackscholes import safe_delta
from services.gex_levels.exposure import ChainRow, ResolvedIVs, WeightBy, finite_weight


@dataclass(frozen=True)
class StrikeDelta:
    """
    Signed delta exposure at one strike, in currency notional.

    Attributes:
        strike: The strike.
        call_dex: Call-leg notional delta. Positive.
        put_dex: Put-leg notional delta. Negative.
        net_dex: call_dex + put_dex. Positive where calls dominate.
        call_delta: Raw Black-76 call delta, carried for display.
        put_delta: Raw Black-76 put delta, carried for display. Negative.
    """

    strike: float
    call_dex: float
    put_dex: float
    net_dex: float
    call_delta: float
    put_delta: float


def price_delta_exposures(
    black76,
    rows: list[ChainRow],
    ivs: ResolvedIVs,
    forward: float,
    t_years: float,
    r: float,
    weight_by: WeightBy,
) -> list[StrikeDelta]:
    """
    Signed DEX at `forward`, using PRE-RESOLVED volatilities.

    Takes `ivs` rather than resolving its own so that a caller computing both
    gamma and delta pays for the Black-76 inversion once. That solve is the
    expensive half of this pipeline - two solver calls per strike - and it is
    identical for both metrics, since `resolve_ivs` does not depend on the Greek
    being priced.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows, any order.
        ivs: Volatilities from `resolve_ivs`, inverted at the real forward, and
            resolved from this exact same `rows` list.
        forward: The price to evaluate delta at.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        One StrikeDelta per input row, sorted by strike ascending, matching
        `price_exposures` so the two can be zipped by position.

    Raises:
        ValueError: If `weight_by` is neither 'oi' nor 'volume'. An unrecognised
            weighting must never quietly read as open interest.
        ValueError: If a row's strike is absent from `ivs`, which means `rows`
            does not match what `resolve_ivs` was given. Distinct from a strike
            present but None, which is a leg that did not invert and legitimately
            takes the fallback.
    """
    if weight_by not in ("oi", "volume"):
        raise ValueError(f"weight_by must be 'oi' or 'volume', got {weight_by!r}")

    ordered = sorted(rows, key=lambda row: row.strike)
    use_volume = weight_by == "volume"

    # A non-finite forward yields no exposure rather than a profile of NaN,
    # matching price_exposures' handling of the same case.
    scale = forward if math.isfinite(forward) else 0.0

    out: list[StrikeDelta] = []
    for row in ordered:
        if row.strike not in ivs.call or row.strike not in ivs.put:
            raise ValueError(
                f"ivs was not resolved for strike {row.strike}; resolve_ivs and "
                "price_delta_exposures must be given the same rows"
            )
        call_iv = ivs.call.get(row.strike)
        put_iv = ivs.put.get(row.strike)
        call_weight = finite_weight(row.call_volume if use_volume else row.call_oi)
        put_weight = finite_weight(row.put_volume if use_volume else row.put_oi)

        call_sigma = call_iv if call_iv is not None else ivs.fallback
        put_sigma = put_iv if put_iv is not None else ivs.fallback

        call_delta = safe_delta(black76, "c", forward, row.strike, t_years, r, call_sigma)
        put_delta = safe_delta(black76, "p", forward, row.strike, t_years, r, put_sigma)

        # No dealer sign flip and no lot_size - see the module docstring for
        # why both would be wrong here.
        call_dex = call_delta * call_weight * scale
        put_dex = put_delta * put_weight * scale

        out.append(
            StrikeDelta(
                strike=row.strike,
                call_dex=call_dex,
                put_dex=put_dex,
                net_dex=call_dex + put_dex,
                call_delta=call_delta,
                put_delta=put_delta,
            )
        )
    return out
