"""
The three levels GEX Levels draws: Call Wall, Put Wall and Zero-Gamma.

Walls are the signed extremes of the per-strike profile. Zero-Gamma is not a
strike at all - see `scan_zero_gamma`.
"""

from dataclasses import dataclass

from services.gex_levels.exposure import StrikeExposure


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
