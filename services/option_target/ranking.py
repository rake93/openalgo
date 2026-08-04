"""Per-strike metrics, liquidity filters and objective-driven ranking.

Deep ITM maximises rupees, far OTM maximises percentage return, and they are
never the same strike. Rather than pick one and pretend it is "best", every
metric is computed and the user chooses the objective.
"""

from typing import Any

from opengreeks import black76

from services.option_target.models import SmileFit, StrikeQuote
from services.option_target.projection import (
    attribute_pnl,
    project_strike,
    target_iv,
)
from utils.logging import get_logger

logger = get_logger(__name__)

MIN_OI = 500
MIN_VOLUME = 100
MAX_SPREAD_PCT = 25.0
MAX_REWARD_RISK = 999.0

OBJECTIVES = ("max_pnl", "max_return", "max_rr", "balanced")


def _label(strike: float, atm_strike: float, strike_step: float, option_type: str) -> str:
    """Moneyness label relative to the forward-derived ATM strike."""
    if strike_step <= 0:
        return ""
    steps = round((strike - atm_strike) / strike_step)
    if steps == 0:
        return "ATM"
    in_the_money = (option_type.upper() == "CE" and steps < 0) or (
        option_type.upper() == "PE" and steps > 0
    )
    return f"{'ITM' if in_the_money else 'OTM'}{abs(steps)}"


def _exclusion(quote: StrikeQuote) -> str:
    if quote.bid <= 0:
        return "Zero bid - cannot exit at market"
    if quote.oi < MIN_OI and quote.volume < MIN_VOLUME:
        return f"Low liquidity - OI {quote.oi}, volume {quote.volume}"
    if quote.spread_pct > MAX_SPREAD_PCT:
        return f"Spread {quote.spread_pct:.1f}% exceeds {MAX_SPREAD_PCT:.0f}%"
    return ""


def build_candidate(
    quote: StrikeQuote,
    forward_now: float,
    forward_target: float,
    forward_adverse: float,
    t_now: float,
    t_target: float,
    rate: float,
    fit: SmileFit,
    iv_model: str,
    vol_beta: float,
    move_pct: float,
    vol_shift: float,
    lots: int,
    atm_strike: float,
    strike_step: float,
) -> dict[str, Any]:
    """Full metric set for one strike."""
    strike = quote.strike
    opt_type = quote.option_type
    flag = "c" if opt_type.upper() == "CE" else "p"
    mid_now = quote.mid
    entry_cost = quote.ask if quote.ask > 0 else mid_now

    try:
        iv_now = black76.implied_volatility(mid_now, forward_now, strike, rate, t_now, flag)
    except Exception:  # noqa: BLE001 - deep ITM legs have no recoverable IV
        iv_now = fit.a

    common = {
        "strike": strike,
        "option_type": opt_type,
        "t_target": t_target,
        "rate": rate,
        "iv_now": iv_now,
        "fit": fit,
        "iv_model": iv_model,
        "vol_beta": vol_beta,
        "vol_shift": vol_shift,
    }
    projected = project_strike(forward_target=forward_target, move_pct=move_pct, **common)
    adverse = project_strike(forward_target=forward_adverse, move_pct=-move_pct, **common)

    iv_target = target_iv(
        strike=strike,
        forward_target=forward_target,
        iv_now=iv_now,
        fit=fit,
        iv_model=iv_model,
        vol_beta=vol_beta,
        move_pct=move_pct,
        vol_shift=vol_shift,
    )

    exit_value = max(projected - quote.half_spread, 0.0)
    adverse_exit = max(adverse - quote.half_spread, 0.0)

    pnl_per_lot = (exit_value - entry_cost) * quote.lot_size
    adverse_pnl_per_lot = (adverse_exit - entry_cost) * quote.lot_size

    # Pure decay: same forward, less time. Isolates what waiting costs.
    if t_target > 0:
        static = black76.black(flag, forward_now, strike, t_target, rate, iv_now)
    else:
        static = max(forward_now - strike, 0.0) if flag == "c" else max(strike - forward_now, 0.0)
    theta_cost_per_lot = (static - mid_now) * quote.lot_size

    d_forward = forward_target - forward_now
    effective_delta = (projected - mid_now) / d_forward if d_forward else 0.0

    attribution = attribute_pnl(
        strike=strike,
        option_type=opt_type,
        forward_now=forward_now,
        forward_target=forward_target,
        t_now=t_now,
        t_target=t_target,
        rate=rate,
        iv_now=iv_now,
        iv_target=iv_target,
        premium_now=mid_now,
        premium_target=projected,
        entry_cost=entry_cost,
        exit_value=exit_value,
    )

    if adverse_pnl_per_lot < 0:
        reward_risk = min(pnl_per_lot / abs(adverse_pnl_per_lot), MAX_REWARD_RISK)
    elif pnl_per_lot > 0:
        # Adverse case is also profitable: no downside in this scenario pair.
        # Capped rather than infinite so the value stays JSON-serialisable and
        # sortable alongside ordinary ratios.
        reward_risk = MAX_REWARD_RISK
    else:
        reward_risk = 0.0

    reason = _exclusion(quote)
    return {
        "strike": strike,
        "option_type": opt_type,
        "symbol": quote.symbol,
        "label": _label(strike, atm_strike, strike_step, opt_type),
        "lot_size": quote.lot_size,
        "bid": quote.bid,
        "ask": quote.ask,
        "mid_now": mid_now,
        "spread_pct": quote.spread_pct,
        "entry_cost": entry_cost,
        "iv_now_pct": iv_now * 100,
        "iv_target_pct": iv_target * 100,
        "greeks_now": {
            "delta": black76.delta(flag, forward_now, strike, t_now, rate, iv_now),
            "gamma": black76.gamma(flag, forward_now, strike, t_now, rate, iv_now),
            "theta": black76.theta(flag, forward_now, strike, t_now, rate, iv_now),
            "vega": black76.vega(flag, forward_now, strike, t_now, rate, iv_now),
        },
        "projected_premium": projected,
        "exit_value": exit_value,
        "pnl_per_lot": pnl_per_lot,
        "pnl_total": pnl_per_lot * lots,
        "return_pct": (exit_value / entry_cost - 1) * 100 if entry_cost > 0 else 0.0,
        "effective_delta": effective_delta,
        "theta_cost_per_lot": theta_cost_per_lot,
        "adverse_premium": adverse,
        "adverse_pnl_per_lot": adverse_pnl_per_lot,
        "reward_risk": reward_risk,
        "attribution": {
            "delta": attribution.delta,
            "gamma": attribution.gamma,
            "theta": attribution.theta,
            "vega": attribution.vega,
            "spread": attribution.spread,
            "residual": attribution.residual,
            "total": attribution.total,
        },
        "oi": quote.oi,
        "volume": quote.volume,
        "excluded": bool(reason),
        "exclude_reason": reason,
    }


def _normalise(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def rank_candidates(candidates: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    """Sort by objective, flag one Recommended, push exclusions to the bottom.

    Excluded strikes are RETAINED, not dropped. A hidden exclusion is
    indistinguishable from a strike that does not exist, which is exactly the
    confusion this tool is meant to remove.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"Unknown objective: {objective!r}. Use one of {OBJECTIVES}.")

    for c in candidates:
        c["recommended"] = False
        c["recommend_reason"] = ""

    eligible = [c for c in candidates if not c["excluded"]]
    excluded = [c for c in candidates if c["excluded"]]

    if eligible:
        if objective == "balanced":
            returns = _normalise([c["return_pct"] for c in eligible])
            rr = _normalise([c["reward_risk"] for c in eligible])
            eff = _normalise([abs(c["effective_delta"]) for c in eligible])
            for c, r, k, e in zip(eligible, returns, rr, eff, strict=True):
                penalty = min(c["spread_pct"], MAX_SPREAD_PCT) / MAX_SPREAD_PCT * 0.15
                c["score"] = 0.4 * r + 0.4 * k + 0.2 * e - penalty
            key = "score"
        else:
            key = {
                "max_pnl": "pnl_per_lot",
                "max_return": "return_pct",
                "max_rr": "reward_risk",
            }[objective]
            for c in eligible:
                c["score"] = c[key]

        # Secondary sort on strike keeps ordering deterministic when scores tie.
        eligible.sort(key=lambda c: (-c[key], c["strike"]))
        best = eligible[0]
        best["recommended"] = True
        best["recommend_reason"] = {
            "max_pnl": f"Highest rupee P&L per lot at {best['return_pct']:.1f}% return",
            "max_return": f"Highest return at {best['return_pct']:.1f}% on premium",
            "max_rr": f"Best reward-to-risk at {best['reward_risk']:.2f}x",
            "balanced": (
                f"Best blend of {best['return_pct']:.1f}% return, "
                f"{best['reward_risk']:.2f}x reward-to-risk and "
                f"{abs(best['effective_delta']):.2f} effective delta"
            ),
        }[objective]

    for c in excluded:
        c["score"] = float("-inf")
    excluded.sort(key=lambda c: c["strike"])

    return eligible + excluded
