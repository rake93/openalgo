"""Orchestration tests. The broker layer is stubbed; the math is real."""

from unittest.mock import patch

import pytest

from services.option_target_service import (
    build_ladder,
    get_option_target,
    parse_chain_quotes,
    resolve_hold,
    strike_step_of,
)

CHAIN_ROWS = [
    {
        "strike": 24450.0,
        "ce": {
            "symbol": "NIFTY11AUG2624450CE",
            "ltp": 186.0,
            "bid": 185.0,
            "ask": 187.0,
            "oi": 50_000,
            "volume": 10_000,
            "lotsize": 65,
        },
        "pe": {
            "symbol": "NIFTY11AUG2624450PE",
            "ltp": 121.0,
            "bid": 120.0,
            "ask": 122.0,
            "oi": 60_000,
            "volume": 12_000,
            "lotsize": 65,
        },
    },
    {
        "strike": 24500.0,
        "ce": {
            "symbol": "NIFTY11AUG2624500CE",
            "ltp": 158.0,
            "bid": 157.0,
            "ask": 159.0,
            "oi": 70_000,
            "volume": 20_000,
            "lotsize": 65,
        },
        "pe": {
            "symbol": "NIFTY11AUG2624500PE",
            "ltp": 143.0,
            "bid": 142.0,
            "ask": 144.0,
            "oi": 80_000,
            "volume": 22_000,
            "lotsize": 65,
        },
    },
]


def test_parse_chain_quotes_indexes_by_strike_and_type():
    quotes = parse_chain_quotes(CHAIN_ROWS)
    assert set(quotes) == {
        (24450.0, "CE"),
        (24450.0, "PE"),
        (24500.0, "CE"),
        (24500.0, "PE"),
    }
    assert quotes[(24500.0, "CE")].ask == 159.0
    assert quotes[(24500.0, "CE")].lot_size == 65


def test_parse_chain_quotes_skips_legs_with_no_symbol():
    rows = [{"strike": 24500.0, "ce": {}, "pe": CHAIN_ROWS[1]["pe"]}]
    quotes = parse_chain_quotes(rows)
    assert (24500.0, "CE") not in quotes
    assert (24500.0, "PE") in quotes


def test_strike_step_is_the_modal_gap():
    assert strike_step_of([24400.0, 24450.0, 24500.0, 24550.0]) == 50.0


def test_strike_step_handles_a_single_strike():
    assert strike_step_of([24500.0]) == 0.0


def test_resolve_hold_prefers_days_when_given():
    minutes = resolve_hold(hold_minutes=45, hold_days=2.0)
    assert minutes == pytest.approx(2.0 * 24 * 60)


def test_resolve_hold_uses_minutes_by_default():
    assert resolve_hold(hold_minutes=45, hold_days=None) == 45.0


def test_resolve_hold_rejects_negative():
    with pytest.raises(ValueError, match="must not be negative"):
        resolve_hold(hold_minutes=-1, hold_days=None)


def test_build_ladder_brackets_the_target():
    ladder = build_ladder(
        reference_now=24500.0,
        reference_target=24700.0,
        steps=5,
        project=lambda ref: ref - 24500.0,
    )
    levels = [row["reference_level"] for row in ladder]
    assert min(levels) < 24500.0
    assert max(levels) > 24700.0
    assert len(ladder) == 5


def test_build_ladder_calls_the_projector_per_level():
    calls = []
    build_ladder(
        reference_now=100.0,
        reference_target=110.0,
        steps=3,
        project=lambda ref: calls.append(ref) or 0.0,
    )
    assert len(calls) == 3


def _fake_chain(success=True):
    def _call(underlying, exchange, expiry_date, strike_count, api_key):
        if not success:
            return False, {"status": "error", "message": "boom"}, 500
        return (
            True,
            {
                "status": "success",
                "underlying": underlying,
                "underlying_ltp": 24507.10,
                "expiry_date": expiry_date,
                "atm_strike": 24500.0,
                "chain": CHAIN_ROWS,
            },
            200,
        )

    return _call


def _patches(chain_ok=True):
    return (
        patch("services.option_target_service.get_option_chain", _fake_chain(chain_ok)),
        patch("services.option_target_service._matched_future_symbol", return_value=None),
        patch("services.option_target_service._vol_beta_samples", return_value=[]),
    )


def _run(**overrides):
    kwargs = {
        "underlying": "NIFTY",
        "exchange": "NFO",
        "expiry_date": "11AUG26",
        "reference": "SPOT",
        "target_price": 24700.0,
        "api_key": "k",
    }
    kwargs.update(overrides)
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        return get_option_target(**kwargs)


def test_get_option_target_returns_a_full_envelope():
    ok, resp, code = _run()
    assert ok is True
    assert code == 200
    for key in ("snapshot", "smile", "scenario", "candidates", "ladder", "warnings"):
        assert key in resp


def test_get_option_target_reports_basis_modelled_without_a_matched_future():
    _, resp, _ = _run()
    assert resp["scenario"]["forward_mode"] == "basis_modelled"


def test_get_option_target_picks_calls_for_an_upside_target():
    _, resp, _ = _run(target_price=24700.0)
    assert {c["option_type"] for c in resp["candidates"]} == {"CE"}


def test_get_option_target_picks_puts_for_a_downside_target():
    _, resp, _ = _run(target_price=24300.0)
    assert {c["option_type"] for c in resp["candidates"]} == {"PE"}


def test_get_option_target_propagates_a_chain_failure():
    p1, p2, p3 = _patches(chain_ok=False)
    with p1, p2, p3:
        ok, resp, code = get_option_target(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            reference="SPOT",
            target_price=24700.0,
            api_key="k",
        )
    assert ok is False
    assert code == 500


def test_get_option_target_rejects_a_non_positive_target():
    ok, resp, code = get_option_target(
        underlying="NIFTY",
        exchange="NFO",
        expiry_date="11AUG26",
        reference="SPOT",
        target_price=0.0,
        api_key="k",
    )
    assert ok is False
    assert code == 400


def test_get_option_target_warns_when_the_hold_runs_past_expiry():
    _, resp, _ = _run(hold_days=400)
    assert any("expir" in w.lower() for w in resp["warnings"])


def test_get_option_target_echoes_the_vol_beta_actually_used():
    _, resp, _ = _run()
    beta = resp["scenario"]["vol_beta"]
    assert "beta" in beta and "source" in beta
