"""
The three levels GEX Levels draws: Call Wall, Put Wall and Zero-Gamma.

Walls are the signed extremes of the per-strike profile. Zero-Gamma is not a
strike at all - see `scan_zero_gamma`.
"""

from dataclasses import dataclass

from services.gex_levels.exposure import ChainRow, StrikeExposure, WeightBy, compute_exposures


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

    Args:
        exposures: Per-strike exposures, ascending by strike.

    Returns:
        Walls, with None levels when there is nothing to rank.
    """
    if not exposures:
        return Walls(call_wall=None, put_wall=None, call_wall_at_edge=False, put_wall_at_edge=False)

    call = max(exposures, key=lambda e: e.net_gex)
    put = min(exposures, key=lambda e: e.net_gex)
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
      3. Find the first sign change and interpolate linearly between the two
         bracketing samples.

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
        The interpolated price, or None when the profile does not cross zero
        anywhere in the window. None is a normal outcome - a chain can be long
        gamma or short gamma across its whole plausible range - and callers
        must render it as "no local cross", not as an error.
    """
    if not rows or forward <= 0 or t_years <= 0:
        return None

    lo = forward * (1.0 - SCAN_RANGE_PCT)
    hi = forward * (1.0 + SCAN_RANGE_PCT)
    step = (hi - lo) / (SCAN_STEPS - 1)

    previous_level: float | None = None
    previous_total: float | None = None

    for i in range(SCAN_STEPS):
        level = lo + step * i
        total = sum(
            e.net_gex
            for e in compute_exposures(
                black76,
                rows,
                forward=level,
                t_years=t_years,
                r=r,
                atm_strike=atm_strike,
                weight_by=weight_by,
            )
        )

        if previous_total is not None and _crosses_zero(previous_total, total):
            return _interpolate_zero(previous_level, previous_total, level, total)

        previous_level = level
        previous_total = total

    return None


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
