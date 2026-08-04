"""Repricing at the target and P&L attribution.

The headline number is a FULL Black-76 reprice, not a delta or delta-gamma
approximation. Backtested against a completed BANKNIFTY trade across 37 strike
series (2026-08-04), mean absolute error:

    delta only                    6.84%
    smile slide alone             6.77%
    sticky strike, full reprice   5.55%
    slide + vol-beta 1.5          1.26%

Note that slide ALONE is worse than sticky strike. Sliding a fixed smile shape
models the strike's change in moneyness but cannot represent a change in the
vol LEVEL, which is what "the index dropped and volatility spiked" actually is.
That is why `vol_beta` exists and why it is the largest single correction in
this module.
"""

import math

from opengreeks import black76

from services.option_target.models import Attribution, SmileFit
from services.option_target.smile import smile_iv
from utils.logging import get_logger

logger = get_logger(__name__)

MIN_IV = 1e-3


def _flag(option_type: str) -> str:
    return "c" if option_type.upper() == "CE" else "p"


def intrinsic(option_type: str, forward: float, strike: float) -> float:
    if option_type.upper() == "CE":
        return max(forward - strike, 0.0)
    return max(strike - forward, 0.0)


def target_iv(
    strike: float,
    forward_target: float,
    iv_now: float,
    fit: SmileFit,
    iv_model: str,
    vol_beta: float,
    move_pct: float,
    vol_shift: float,
) -> float:
    """Implied vol for `strike` once the forward reaches `forward_target`.

    Units, because they are easy to get wrong:
      iv_now, return value   decimal (0.11 = 11%)
      vol_beta               vol POINTS per 1% move
      move_pct               percent, negative on a fall
      vol_shift               vol POINTS

    The beta term is subtracted so that a fall (negative move_pct) RAISES vol.
    """
    if iv_model == "smile_slide":
        base = smile_iv(fit, math.log(strike / forward_target))
    elif iv_model == "sticky_strike":
        base = iv_now
    else:
        raise ValueError(f"Unknown iv_model: {iv_model!r}")

    return max(base - (vol_beta * move_pct) / 100.0 + vol_shift / 100.0, MIN_IV)


def project_strike(
    strike: float,
    option_type: str,
    forward_target: float,
    t_target: float,
    rate: float,
    iv_now: float,
    fit: SmileFit,
    iv_model: str,
    vol_beta: float,
    move_pct: float,
    vol_shift: float,
) -> float:
    """Projected premium at the target. Full reprice, never a Taylor estimate."""
    if t_target <= 0:
        return intrinsic(option_type, forward_target, strike)

    sigma = target_iv(
        strike=strike,
        forward_target=forward_target,
        iv_now=iv_now,
        fit=fit,
        iv_model=iv_model,
        vol_beta=vol_beta,
        move_pct=move_pct,
        vol_shift=vol_shift,
    )
    return black76.black(_flag(option_type), forward_target, strike, t_target, rate, sigma)


def attribute_pnl(
    strike: float,
    option_type: str,
    forward_now: float,
    forward_target: float,
    t_now: float,
    t_target: float,
    rate: float,
    iv_now: float,
    iv_target: float,
    premium_now: float,
    premium_target: float,
    entry_cost: float,
    exit_value: float,
) -> Attribution:
    """Split the projected change into Greek contributions.

    Theta and vega are computed by RE-PRICING rather than from the Greek, because
    over a 300-point move and a 90-minute hold the linear approximations drift
    badly. Delta and gamma stay as Taylor terms; whatever they miss lands in
    `residual`, which is displayed rather than hidden. A large residual is a
    genuine signal that the move is big enough that attribution is only
    indicative.
    """
    flag = _flag(option_type)
    d_forward = forward_target - forward_now

    if t_now <= 0:
        return Attribution(
            delta=0.0,
            gamma=0.0,
            theta=0.0,
            vega=0.0,
            spread=exit_value - premium_target - (entry_cost - premium_now),
            residual=0.0,
            total=exit_value - entry_cost,
        )

    delta = black76.delta(flag, forward_now, strike, t_now, rate, iv_now)
    gamma = black76.gamma(flag, forward_now, strike, t_now, rate, iv_now)

    delta_term = delta * d_forward
    gamma_term = 0.5 * gamma * d_forward * d_forward

    if t_target > 0:
        theta_term = black76.black(flag, forward_now, strike, t_target, rate, iv_now) - premium_now
        vega_term = premium_target - black76.black(
            flag, forward_target, strike, t_target, rate, iv_now
        )
    else:
        theta_term = intrinsic(option_type, forward_now, strike) - premium_now
        vega_term = 0.0

    spread_term = (exit_value - premium_target) - (entry_cost - premium_now)
    total = exit_value - entry_cost
    residual = total - (delta_term + gamma_term + theta_term + vega_term + spread_term)

    return Attribution(
        delta=delta_term,
        gamma=gamma_term,
        theta=theta_term,
        vega=vega_term,
        spread=spread_term,
        residual=residual,
        total=total,
    )
