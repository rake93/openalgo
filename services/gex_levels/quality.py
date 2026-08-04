"""
The data-quality verdict for a GEX snapshot.

Follows the rule `direction.ts` set for this codebase: a missing input must
never read as a zero. A chain of stale premiums, or one whose window sits
entirely on one side of the forward, still produces numbers - and those numbers
would look exactly like a real reading unless something says otherwise.
"""

from dataclasses import dataclass, field
from typing import Literal

from services.gex_levels.exposure import StrikeExposure
from services.gex_levels.levels import Walls

Verdict = Literal["good", "degraded", "unusable"]

# At or below this share of strikes yielding an invertible IV, the profile is
# being driven by the fallback volatility rather than by the market.
_MIN_PRICED_SHARE = 0.6


@dataclass(frozen=True)
class Quality:
    """What the caller may safely conclude from this snapshot."""

    verdict: Verdict
    strikes_used: int
    strikes_priced: int
    both_sides: bool
    wall_at_edge: bool
    notes: list[str] = field(default_factory=list)


def assess_quality(
    exposures: list[StrikeExposure],
    walls: Walls,
    forward: float,
    total_weight: float,
) -> Quality:
    """
    Grade a snapshot and name every reason it is not clean.

    Args:
        exposures: Per-strike exposures, ascending.
        walls: The selected walls, carrying their at-edge flags.
        forward: The forward the chain was priced against.
        total_weight: Summed OI or volume across both legs, on the selected
            weighting. Zero means the chain carried no positions at all.

    Returns:
        Quality. 'unusable' means do not draw levels; 'degraded' means draw
        them with the caveat shown.
    """
    used = len(exposures)
    priced = sum(1 for e in exposures if e.call_iv is not None or e.put_iv is not None)
    below = any(e.strike < forward for e in exposures)
    above = any(e.strike > forward for e in exposures)
    both_sides = below and above
    wall_at_edge = walls.call_wall_at_edge or walls.put_wall_at_edge

    notes: list[str] = []

    if used == 0 or total_weight <= 0:
        return Quality(
            verdict="unusable",
            strikes_used=used,
            strikes_priced=priced,
            both_sides=both_sides,
            wall_at_edge=wall_at_edge,
            notes=["No open interest or volume in the fetched chain"],
        )

    degraded = False

    if priced / used <= _MIN_PRICED_SHARE:
        degraded = True
        notes.append(
            f"Only {priced} of {used} strikes invert to an implied volatility; "
            "the rest are priced at the chain's ATM volatility"
        )

    if not both_sides:
        degraded = True
        notes.append("The fetched strikes sit entirely on one side of the forward")

    if wall_at_edge:
        degraded = True
        notes.append("A wall sits at the edge of the fetched window and may be a window artefact")

    return Quality(
        verdict="degraded" if degraded else "good",
        strikes_used=used,
        strikes_priced=priced,
        both_sides=both_sides,
        wall_at_edge=wall_at_edge,
        notes=notes,
    )
