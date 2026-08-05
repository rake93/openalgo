"""
Per-strike signed delta exposure (DEX).

    DEX_k = (delta_k(call) * w_k(call) + delta_k(put) * w_k(put)) * F

WHOSE delta this is: the open-interest book's, not the dealer's. A positive
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

As in `exposure.py` there is no lot-size factor: this broker's chain reports
open interest and volume already multiplied by the lot size, so multiplying
again would double-count it.

Units are currency notional - delta per unit, times the weight in units, times
the forward. Only ONE factor of F, not the F^2 * 0.01 `exposure.py` uses:
gamma has to be converted from a per-unit-move sensitivity into a per-1%-move
one before it means anything as currency, which costs the second factor of F
(and the 0.01 scale). Delta is already expressed per unit of the underlying, so
a single multiplication by F is enough to turn it into notional. The `* F`
factor is constant across strikes either way, so it scales the profile without
moving where its extremes sit.

Purity: no network, database, logging or clock. Plain inputs, plain values.
"""

import math
from dataclasses import dataclass

from services.gex_levels.blackscholes import safe_delta
from services.gex_levels.exposure import WeightedLeg


@dataclass(frozen=True)
class StrikeDeltaExposure:
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
    legs: list[WeightedLeg],
    forward: float,
    t_years: float,
    r: float,
) -> list[StrikeDeltaExposure]:
    """
    Signed DEX at `forward`, pricing PRE-BUILT per-strike legs.

    Takes `legs` - built once by `weighted_legs` in `exposure.py` - rather
    than `rows` and `ivs` directly. `weighted_legs`' work (sorting, weighting,
    IV substitution) does not depend on `forward`, so a caller that reprices
    at many hypothetical forwards builds `legs` once and calls a pricer many
    times, the way `price_exposures`' zero-gamma-scan caller does. It also
    means a caller pricing both GEX and DEX from the same chain can pass the
    identical `legs` list to both pricers, so the two outputs are aligned by
    identity - one list, one order - rather than by two independently built
    lists that merely happen to agree; see `weighted_legs`'s docstring.

    Args:
        black76: The opengreeks.black76 module.
        legs: Per-strike pricing inputs from `weighted_legs`, sorted by strike
            ascending. This function trusts that order and does not re-sort
            or re-validate against a chain.
        forward: The price to evaluate delta at.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.

    Returns:
        One StrikeDeltaExposure per input leg, in the same order, matching
        `price_exposures` so the two can be zipped by position.
    """
    # A non-finite forward yields no exposure rather than a profile of NaN,
    # matching price_exposures' handling of the same case.
    scale = forward if math.isfinite(forward) else 0.0

    out: list[StrikeDeltaExposure] = []
    for leg in legs:
        call_delta = safe_delta(black76, "c", forward, leg.strike, t_years, r, leg.call_sigma)
        put_delta = safe_delta(black76, "p", forward, leg.strike, t_years, r, leg.put_sigma)

        # No dealer sign flip and no lot_size - see the module docstring for
        # why both would be wrong here.
        call_dex = call_delta * leg.call_weight * scale
        put_dex = put_delta * leg.put_weight * scale

        out.append(
            StrikeDeltaExposure(
                strike=leg.strike,
                call_dex=call_dex,
                put_dex=put_dex,
                net_dex=call_dex + put_dex,
                call_delta=call_delta,
                put_delta=put_delta,
            )
        )
    return out
