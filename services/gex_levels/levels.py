"""
The three levels GEX Levels draws: Call Wall, Put Wall and Zero-Gamma.

Walls are the signed extremes of the per-strike profile. Zero-Gamma is not a
strike at all - see `scan_zero_gamma`.
"""

import math
from dataclasses import dataclass

from services.gex_levels.exposure import (
    ChainRow,
    StrikeExposure,
    WeightBy,
    price_exposures,
    resolve_ivs,
)


@dataclass(frozen=True)
class Walls:
    """The two gamma concentrations, and whether either is a window artefact."""

    call_wall: float | None
    put_wall: float | None
    call_wall_at_edge: bool
    put_wall_at_edge: bool


def find_walls(exposures: list[StrikeExposure]) -> Walls:
    """
    Call Wall is the strike with the greatest net GEX, Put Wall the least.

    Both may be the same strike - a single dominant expiry-day strike routinely
    holds the largest call gamma and the largest put gamma at once, so no caller
    may assume they differ.

    A wall landing on the first or last strike of the fetched window is flagged:
    it may be a real concentration, or it may simply be where the window stopped.
    The quality gate turns that flag into a user-visible caveat.

    Strikes with a non-finite net GEX are excluded from the ranking before
    max/min ever see them. NaN does not merely lose a comparison, it corrupts
    the result order-dependently: a NaN in first position wins BOTH walls,
    because every later `x > nan` is False, while a NaN anywhere else is
    silently ignored. The edge flags still measure against the full window.

    Args:
        exposures: Per-strike exposures, ascending by strike.

    Returns:
        Walls, with None levels when there is nothing finite to rank - which
        includes the empty case.
    """
    rankable = [e for e in exposures if math.isfinite(e.net_gex)]
    if not rankable:
        return Walls(call_wall=None, put_wall=None, call_wall_at_edge=False, put_wall_at_edge=False)

    call = max(rankable, key=lambda e: e.net_gex)
    put = min(rankable, key=lambda e: e.net_gex)
    edges = {exposures[0].strike, exposures[-1].strike}

    return Walls(
        call_wall=call.strike,
        put_wall=put.strike,
        call_wall_at_edge=call.strike in edges,
        put_wall_at_edge=put.strike in edges,
    )


# Scan window around the forward, and how finely it is sampled. Twenty percent
# is the span the published methodologies use; sixty steps puts a sample every
# ~0.67% of the forward, which is finer than any real strike ladder.
SCAN_RANGE_PCT = 0.20
SCAN_STEPS = 60


def scan_zero_gamma(
    black76,
    rows: list[ChainRow],
    forward: float,
    t_years: float,
    r: float,
    atm_strike: float | None,
    weight_by: WeightBy,
) -> float | None:
    """
    The price at which aggregate dealer gamma changes sign.

    NOT the strike where a running total of per-strike GEX crosses zero - that
    is a different quantity and can only ever land on a strike. Gamma itself
    depends on where the underlying is, so the profile has to be rebuilt at each
    hypothetical price:

      1. Sample SCAN_STEPS forward levels across +/- SCAN_RANGE_PCT of `forward`.
      2. At each, recompute EVERY contract's gamma with Black-76 - `t` and
         `sigma` held fixed, only F varies - and sum the signed exposure.
      3. Collect EVERY sign change, interpolating linearly between the two
         bracketing samples, and return the one nearest `forward`.

    Nearest, not first. A real chain can flip sign more than once across a 20%
    window - short gamma far downside, long gamma near spot, short gamma far
    upside - and returning the first crossing found while walking up from the
    bottom of the window reports the far-downside flip. What a trader means by
    "the gamma flip" is the boundary of the CURRENT regime: the crossing that
    brackets today's price, above which dealers stabilise and below which they
    amplify. That framing only makes sense for the adjacent crossing, which is
    also why the empty case reads "no LOCAL cross".

    Volatility is held at each strike's own IV, inverted once at the real
    forward. Re-inverting at every scan level would be both far more expensive
    and wrong: the premiums observed are the ones at today's forward.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows.
        forward: Per-expiry forward price the scan centres on.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        atm_strike: ATM strike, for the IV fallback.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        The interpolated price of the crossing nearest `forward`, or None when
        the profile does not cross zero anywhere in the window. None is a normal
        outcome - a chain can be long gamma or short gamma across its whole
        plausible range - and callers must render it as "no local cross", not as
        an error. Two crossings exactly equidistant from `forward` resolve to
        the lower one, deterministically.
    """
    if not rows or forward <= 0 or t_years <= 0:
        return None

    # Resolved ONCE, at the real forward, and then held fixed for every sample
    # below. Only F may vary across the scan.
    ivs = resolve_ivs(
        black76,
        rows,
        forward=forward,
        t_years=t_years,
        r=r,
        atm_strike=atm_strike,
    )

    lo = forward * (1.0 - SCAN_RANGE_PCT)
    hi = forward * (1.0 + SCAN_RANGE_PCT)
    step = (hi - lo) / (SCAN_STEPS - 1)

    # Every sample is visited: the nearest crossing cannot be known until the
    # whole window has been walked, so there is no early exit here.
    crossings: list[float] = []
    previous_level: float | None = None
    previous_total: float | None = None

    for i in range(SCAN_STEPS):
        level = lo + step * i
        total = sum(
            e.net_gex
            for e in price_exposures(
                black76,
                rows,
                ivs,
                forward=level,
                t_years=t_years,
                r=r,
                weight_by=weight_by,
            )
        )

        if previous_total is not None and _crosses_zero(previous_total, total):
            crossings.append(_interpolate_zero(previous_level, previous_total, level, total))

        previous_level = level
        previous_total = total

    if not crossings:
        return None
    return min(crossings, key=lambda crossing: abs(crossing - forward))


def _crosses_zero(before: float, after: float) -> bool:
    """True when the sign changes between two consecutive samples."""
    if before == 0.0:
        return True
    return (before < 0.0) != (after < 0.0)


def _interpolate_zero(x0: float, y0: float, x1: float, y1: float) -> float:
    """
    Linear interpolation to the zero of the segment (x0, y0) -> (x1, y1).

    This is what lets the level land between strikes, which is the whole point
    of the scan. Falls back to the left endpoint when the segment is flat, which
    can only happen when y0 is already zero.
    """
    if y1 == y0:
        return x0
    return x0 + (x1 - x0) * (-y0) / (y1 - y0)
