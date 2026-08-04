"""Forward anchoring and target mapping.

Indian F&O options are priced off the forward for their OWN expiry, not the
index spot and not the near-month future. Measured on 2026-08-04:

    NIFTY    11AUG26 (7 DTE weekly)   basis  +7.5 pts
    BANKNIFTY 25AUG26 (21 DTE monthly) basis +138.9 pts

At a +139 point basis, a 57800 strike that looks at-the-money against spot
57795 is 134 points out-of-the-money against the forward 57934. Those are
different options. Pricing off spot is not an approximation; it is wrong.
"""

from services.option_target.models import ForwardAnchor, ForwardTarget, StrikeQuote
from utils.logging import get_logger

logger = get_logger(__name__)


def compute_forward(
    quotes: dict[tuple[float, str], StrikeQuote],
    atm_strike: float,
    spot: float,
) -> ForwardAnchor:
    """Synthetic forward from put-call parity at the ATM strike.

    F = K_atm + CE_atm_mid - PE_atm_mid

    Falls back to spot when either ATM leg has no usable quote. The caller is
    expected to surface a warning in that case: the projection stays usable but
    is anchored on the wrong instrument by the size of the basis.
    """
    ce = quotes.get((atm_strike, "CE"))
    pe = quotes.get((atm_strike, "PE"))

    if ce is None or pe is None or ce.mid <= 0 or pe.mid <= 0:
        logger.warning(
            "ATM parity unavailable at strike %s; falling back to spot %s", atm_strike, spot
        )
        return ForwardAnchor(forward=spot, spot=spot, atm_strike=atm_strike, source="spot_fallback")

    return ForwardAnchor(
        forward=atm_strike + ce.mid - pe.mid,
        spot=spot,
        atm_strike=atm_strike,
        source="parity",
    )


def project_forward(
    anchor: ForwardAnchor,
    reference: str,
    reference_now: float,
    reference_target: float,
    matched_future: bool,
) -> ForwardTarget:
    """Map the user's target on `reference` onto the option-expiry forward.

    Exact mode (`matched_future=True` and reference is FUT): the future and the
    synthetic forward are the same economic instrument, so the shift is 1:1 and
    carries NO basis assumption. Measured on BANKNIFTY 2026-08-04, this removes
    a 17.3 point error that basis modelling could not avoid.

    Basis-modelled mode: shift proportionally, because basis is a cost-of-carry
    ratio. Proportional and parallel shifts were measured 0.7 points apart, so
    the choice is immaterial next to the basis drift itself.
    """
    if reference_now <= 0 or reference_target <= 0:
        raise ValueError("Reference prices must be positive")

    if matched_future and reference == "FUT":
        forward = anchor.forward + (reference_target - reference_now)
        mode = "exact"
    else:
        forward = anchor.forward * (reference_target / reference_now)
        mode = "basis_modelled"

    return ForwardTarget(
        forward=forward,
        mode=mode,
        reference=reference,
        reference_now=reference_now,
        reference_target=reference_target,
    )
