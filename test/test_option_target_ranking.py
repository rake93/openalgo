"""Ranking and filtering tests for the Option Target Calculator."""

import pytest

from services.option_target.models import SmileFit, StrikeQuote
from services.option_target.ranking import build_candidate, rank_candidates
from services.option_target.volbeta import PRESETS, estimate_vol_beta

FLAT_FIT = SmileFit(
    a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5, rms=0.0, n_points=25, degenerate=False
)


def _quote(strike, opt_type="CE", bid=100.0, ask=101.0, oi=50_000, volume=10_000):
    return StrikeQuote(
        strike=strike,
        option_type=opt_type,
        symbol=f"NIFTY11AUG26{int(strike)}{opt_type}",
        ltp=(bid + ask) / 2,
        bid=bid,
        ask=ask,
        oi=oi,
        volume=volume,
        lot_size=65,
    )


def _candidate(strike, **overrides):
    kwargs = {
        "quote": _quote(strike),
        "forward_now": 24500.0,
        "forward_target": 24700.0,
        "forward_adverse": 24300.0,
        "t_now": 0.02,
        "t_target": 0.019,
        "rate": 0.0,
        "fit": FLAT_FIT,
        "iv_model": "sticky_strike",
        "vol_beta": 0.0,
        "move_pct": 0.816,
        "vol_shift": 0.0,
        "lots": 1,
        "atm_strike": 24500.0,
        "strike_step": 50.0,
    }
    kwargs.update(overrides)
    return build_candidate(**kwargs)


def test_candidate_computes_entry_at_ask():
    c = _candidate(24500.0, quote=_quote(24500.0, bid=155.0, ask=160.0))
    assert c["entry_cost"] == 160.0


def test_candidate_exit_is_net_of_half_spread():
    c = _candidate(24500.0, quote=_quote(24500.0, bid=155.0, ask=160.0))
    assert c["exit_value"] == pytest.approx(c["projected_premium"] - 2.5)


def test_candidate_pnl_uses_lot_size_and_lots():
    c = _candidate(24500.0, lots=3)
    assert c["pnl_total"] == pytest.approx(c["pnl_per_lot"] * 3)
    assert c["pnl_per_lot"] == pytest.approx((c["exit_value"] - c["entry_cost"]) * 65)


def test_candidate_labels_moneyness_against_the_forward():
    # Forward 24500, step 50: a 24400 call is two steps in the money.
    c = _candidate(24400.0)
    assert c["label"] == "ITM2"


def test_candidate_labels_atm():
    assert _candidate(24500.0)["label"] == "ATM"


def test_candidate_effective_delta_is_realised_not_instantaneous():
    c = _candidate(24500.0)
    expected = (c["projected_premium"] - c["mid_now"]) / (24700.0 - 24500.0)
    assert c["effective_delta"] == pytest.approx(expected)


def test_candidate_theta_cost_is_negative_for_a_long_option():
    c = _candidate(24500.0)
    assert c["theta_cost_per_lot"] < 0


def test_candidate_reward_risk_is_positive_for_a_winning_direction():
    c = _candidate(24500.0)
    assert c["reward_risk"] > 0


def test_zero_bid_strike_is_excluded_with_a_reason():
    c = _candidate(25500.0, quote=_quote(25500.0, bid=0.0, ask=0.5))
    assert c["excluded"] is True
    assert "bid" in c["exclude_reason"].lower()


def test_illiquid_strike_is_excluded_with_a_reason():
    c = _candidate(25500.0, quote=_quote(25500.0, oi=10, volume=0))
    assert c["excluded"] is True
    assert "liquidity" in c["exclude_reason"].lower() or "oi" in c["exclude_reason"].lower()


def test_wide_spread_strike_is_excluded_with_a_reason():
    c = _candidate(25500.0, quote=_quote(25500.0, bid=10.0, ask=30.0))
    assert c["excluded"] is True
    assert "spread" in c["exclude_reason"].lower()


def test_rank_by_max_return_puts_highest_percentage_first():
    cands = [
        {
            "strike": 1.0,
            "return_pct": 10.0,
            "pnl_per_lot": 900.0,
            "reward_risk": 1.0,
            "effective_delta": 0.9,
            "spread_pct": 1.0,
            "excluded": False,
            "exclude_reason": "",
        },
        {
            "strike": 2.0,
            "return_pct": 30.0,
            "pnl_per_lot": 300.0,
            "reward_risk": 1.2,
            "effective_delta": 0.3,
            "spread_pct": 1.0,
            "excluded": False,
            "exclude_reason": "",
        },
    ]
    ranked = rank_candidates(cands, objective="max_return")
    assert ranked[0]["strike"] == 2.0
    assert ranked[0]["recommended"] is True
    assert ranked[1]["recommended"] is False


def test_rank_by_max_pnl_puts_highest_rupees_first():
    cands = [
        {
            "strike": 1.0,
            "return_pct": 10.0,
            "pnl_per_lot": 900.0,
            "reward_risk": 1.0,
            "effective_delta": 0.9,
            "spread_pct": 1.0,
            "excluded": False,
            "exclude_reason": "",
        },
        {
            "strike": 2.0,
            "return_pct": 30.0,
            "pnl_per_lot": 300.0,
            "reward_risk": 1.2,
            "effective_delta": 0.3,
            "spread_pct": 1.0,
            "excluded": False,
            "exclude_reason": "",
        },
    ]
    ranked = rank_candidates(cands, objective="max_pnl")
    assert ranked[0]["strike"] == 1.0


def test_excluded_candidates_sort_last_and_are_never_recommended():
    cands = [
        {
            "strike": 1.0,
            "return_pct": 99.0,
            "pnl_per_lot": 9999.0,
            "reward_risk": 9.0,
            "effective_delta": 0.9,
            "spread_pct": 1.0,
            "excluded": True,
            "exclude_reason": "zero bid",
        },
        {
            "strike": 2.0,
            "return_pct": 10.0,
            "pnl_per_lot": 100.0,
            "reward_risk": 1.0,
            "effective_delta": 0.3,
            "spread_pct": 1.0,
            "excluded": False,
            "exclude_reason": "",
        },
    ]
    ranked = rank_candidates(cands, objective="max_return")
    assert ranked[0]["strike"] == 2.0
    assert ranked[0]["recommended"] is True
    assert ranked[-1]["strike"] == 1.0
    assert ranked[-1]["recommended"] is False


def test_ranking_is_stable_under_input_permutation():
    cands = [
        {
            "strike": float(i),
            "return_pct": float(i),
            "pnl_per_lot": float(i),
            "reward_risk": 1.0,
            "effective_delta": 0.5,
            "spread_pct": 1.0,
            "excluded": False,
            "exclude_reason": "",
        }
        for i in range(1, 6)
    ]
    forward = [c["strike"] for c in rank_candidates(list(cands), "balanced")]
    backward = [c["strike"] for c in rank_candidates(list(reversed(cands)), "balanced")]
    assert forward == backward


def test_recommended_carries_a_reason():
    cands = [
        {
            "strike": 1.0,
            "return_pct": 30.0,
            "pnl_per_lot": 300.0,
            "reward_risk": 1.2,
            "effective_delta": 0.3,
            "spread_pct": 1.0,
            "excluded": False,
            "exclude_reason": "",
        },
    ]
    ranked = rank_candidates(cands, objective="balanced")
    assert ranked[0]["recommend_reason"]


def test_all_excluded_yields_no_recommendation():
    cands = [
        {
            "strike": 1.0,
            "return_pct": 30.0,
            "pnl_per_lot": 300.0,
            "reward_risk": 1.2,
            "effective_delta": 0.3,
            "spread_pct": 1.0,
            "excluded": True,
            "exclude_reason": "zero bid",
        },
    ]
    ranked = rank_candidates(cands, objective="balanced")
    assert all(not c["recommended"] for c in ranked)


def test_unknown_objective_raises():
    with pytest.raises(ValueError, match="Unknown objective"):
        rank_candidates([], objective="banana")


def test_estimate_recovers_a_known_beta():
    # Construct samples where IV rises 1.5 vol pts per 1% fall, exactly.
    samples = []
    for i in range(40):
        ret_pct = -0.05 * i
        samples.append((ret_pct, 12.0 - 1.5 * ret_pct))
    result = estimate_vol_beta(samples)
    assert result["beta"] == pytest.approx(1.5, abs=1e-6)
    assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert result["source"] == "estimated"


def test_estimate_falls_back_when_too_few_samples():
    result = estimate_vol_beta([(0.1, 12.0), (0.2, 12.1)])
    assert result["source"] == "fallback"
    assert result["beta"] == PRESETS["normal"]
    assert "samples" in result["reason"].lower()


def test_estimate_falls_back_on_a_weak_fit():
    # Pure noise: no relationship between return and IV.
    samples = [(0.1 * i, 12.0 + (1.0 if i % 2 else -1.0)) for i in range(40)]
    result = estimate_vol_beta(samples)
    assert result["source"] == "fallback"
    assert result["beta"] == PRESETS["normal"]
    assert "fit" in result["reason"].lower()


def test_estimate_falls_back_on_degenerate_returns():
    samples = [(0.0, 12.0 + 0.01 * i) for i in range(40)]
    result = estimate_vol_beta(samples)
    assert result["source"] == "fallback"


def test_presets_are_ordered():
    assert PRESETS["off"] < PRESETS["calm"] < PRESETS["normal"] < PRESETS["panic"]
