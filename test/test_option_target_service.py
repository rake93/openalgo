"""Orchestration tests. The broker layer is stubbed; the math is real."""

from unittest.mock import MagicMock, patch

import pytest

from services.option_target_service import (
    _SNAPSHOT_CACHE,
    build_ladder,
    get_option_target,
    parse_chain_quotes,
    resolve_hold,
    strike_step_of,
)


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    """Bounded TTLCache is module-level state; a stale entry from a previous
    test must never leak into the next one and mask a fresh fetch."""
    _SNAPSHOT_CACHE.clear()
    yield
    _SNAPSHOT_CACHE.clear()


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


def test_strike_step_breaks_ties_deterministically():
    widening = [24400.0, 24450.0, 24500.0, 24600.0, 24700.0]
    narrowing = [24400.0, 24500.0, 24600.0, 24650.0, 24700.0]
    assert strike_step_of(widening) == 50.0
    assert strike_step_of(narrowing) == 50.0


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


def _fake_chain(success=True, underlying_ref=None):
    def _call(underlying, exchange, expiry_date, strike_count, api_key):
        if not success:
            return False, {"status": "error", "message": "boom"}, 500
        resp = {
            "status": "success",
            "underlying": underlying,
            "underlying_ltp": 24507.10,
            "expiry_date": expiry_date,
            "atm_strike": 24500.0,
            "chain": CHAIN_ROWS,
        }
        if underlying_ref is not None:
            resp["underlying_ref"] = underlying_ref
        return True, resp, 200

    return _call


# A commodity underlying_ref as services.pricing_underlying.resolve_pricing_underlying
# would build it for CRUDEOIL/MCX - the linked future is the pricing instrument,
# there is no spot leg at all.
COMMODITY_FUTURE_REF = {
    "symbol": "CRUDEOIL19AUG26FUT",
    "exchange": "MCX",
    "kind": "FUTURE",
    "option_expiry": "17AUG26",
    "underlying_expiry": "19-AUG-26",
    "method": "linked_future_nearest_on_or_after_option_expiry",
}


def _run_commodity(**overrides):
    kwargs = {
        "underlying": "CRUDEOIL",
        "exchange": "MCX",
        "expiry_date": "17AUG26",
        "reference": "FUT",
        "target_price": 24700.0,
        "api_key": "k",
    }
    kwargs.update(overrides)
    with (
        patch(
            "services.option_target_service.get_option_chain",
            _fake_chain(underlying_ref=COMMODITY_FUTURE_REF),
        ),
        patch("services.option_target_service._matched_future_symbol", return_value=None),
        patch("services.option_target_service._vol_beta_samples", return_value=[]),
    ):
        return get_option_target(**kwargs)


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


def test_get_option_target_rejects_a_malformed_vol_beta_with_400():
    # A JSON array/object for vol_beta raises TypeError inside float(), not
    # ValueError - it must still surface as a 400, not fall through to a 500.
    ok, resp, code = _run(vol_beta=[1, 2])
    assert ok is False
    assert code == 400


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


def test_chain_snapshot_is_cached_between_calls():
    _SNAPSHOT_CACHE.clear()
    calls = []

    def _counting_chain(underlying, exchange, expiry_date, strike_count, api_key):
        calls.append(underlying)
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

    with (
        patch("services.option_target_service.get_option_chain", _counting_chain),
        patch("services.option_target_service._matched_future_symbol", return_value=None),
        patch("services.option_target_service._vol_beta_samples", return_value=[]),
    ):
        get_option_target(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            reference="SPOT",
            target_price=24700.0,
            api_key="k",
        )
        get_option_target(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            reference="SPOT",
            target_price=24650.0,
            api_key="k",
        )
    assert len(calls) == 1
    _SNAPSHOT_CACHE.clear()


def test_time_floor_does_not_distort_the_final_hour_of_expiry_day():
    from services.option_target_service import MIN_TIME_YEARS

    # 0.0001 years is 52.6 minutes and would clamp most of expiry-day trading.
    # The Rust Black-76 core is stable to ~30 seconds, so the guard must be
    # far smaller than a typical 0DTE holding period.
    assert MIN_TIME_YEARS < 30 / (365 * 24 * 60)


def test_compact_expiry_strips_dashes():
    from services.option_target_service import _compact_expiry

    assert _compact_expiry("04-AUG-26") == "04AUG26"


def test_expiry_defaults_to_the_nearest_live_expiry():
    expiries = (True, {"status": "success", "data": ["04-AUG-26", "11-AUG-26"]}, 200)
    with (
        patch("services.option_target_service.get_option_chain", _fake_chain()),
        patch("services.option_target_service.get_expiry_dates", return_value=expiries),
        patch("services.option_target_service._matched_future_symbol", return_value=None),
        patch("services.option_target_service._vol_beta_samples", return_value=[]),
    ):
        ok, resp, _ = get_option_target(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date=None,
            reference="SPOT",
            target_price=24700.0,
            api_key="k",
        )
    assert ok is True
    assert resp["snapshot"]["expiry_date"] in ("04AUG26", "11AUG26")
    assert any("defaulted" in w.lower() for w in resp["warnings"])


def test_no_live_expiry_returns_404():
    with patch(
        "services.option_target_service.get_expiry_dates",
        return_value=(True, {"status": "success", "data": []}, 200),
    ):
        ok, resp, code = get_option_target(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date=None,
            reference="SPOT",
            target_price=24700.0,
            api_key="k",
        )
    assert ok is False
    assert code == 404


def test_response_is_strict_json_with_no_non_finite_values():
    """The payload must survive a strict JSON round-trip.

    Python's json module happily emits the bare tokens Infinity, -Infinity and
    NaN, none of which are valid JSON. JSON.parse in the browser throws a
    SyntaxError on them, so a single non-finite float silently destroys the
    entire response even though every number in it was computed correctly.
    `allow_nan=False` applies the same strictness the browser does.
    """
    import json

    _, resp, _ = _run()
    json.dumps(resp, allow_nan=False)


def test_excluded_candidates_carry_a_json_safe_score():
    _, resp, _ = _run()
    for candidate in resp["candidates"]:
        if candidate["excluded"]:
            assert candidate["score"] is None


def test_poor_smile_fit_falls_back_to_sticky_strike():
    from services.option_target.models import SmileFit

    bad_fit = SmileFit(
        a=1.25, b=3.46, c=1120.0, x_lo=-0.026, x_hi=0.023, rms=0.1436, n_points=25, degenerate=False
    )
    with (
        patch("services.option_target_service.get_option_chain", _fake_chain()),
        patch("services.option_target_service._matched_future_symbol", return_value=None),
        patch("services.option_target_service._vol_beta_samples", return_value=[]),
        patch("services.option_target_service.fit_smile", return_value=bad_fit),
    ):
        _, resp, _ = get_option_target(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            reference="SPOT",
            target_price=24700.0,
            api_key="k",
            iv_model="smile_slide",
        )
    assert resp["scenario"]["iv_model"] == "sticky_strike"
    assert resp["scenario"]["iv_model_requested"] == "smile_slide"
    assert resp["scenario"]["iv_model_overridden"] is True
    assert any("too poor to slide" in w.lower() for w in resp["warnings"])


def test_good_smile_fit_is_not_overridden():
    _, resp, _ = _run(iv_model="smile_slide")
    assert resp["scenario"]["iv_model_overridden"] is False
    assert resp["scenario"]["iv_model"] == "smile_slide"


def test_scenario_always_reports_the_requested_model():
    _, resp, _ = _run(iv_model="sticky_strike")
    assert resp["scenario"]["iv_model_requested"] == "sticky_strike"


def test_snapshot_reports_basis_plausibility():
    _, resp, _ = _run()
    assert isinstance(resp["snapshot"]["basis_plausible"], bool)


def test_basis_bound_accepts_real_carry_at_seven_days():
    from services.option_target_service import (
        BASIS_QUOTE_TOLERANCE_PCT,
        MAX_PLAUSIBLE_CARRY_RATE,
    )

    spot, t_now, basis = 24463.0, 0.0192, 22.4
    bound = spot * MAX_PLAUSIBLE_CARRY_RATE * t_now + spot * BASIS_QUOTE_TOLERANCE_PCT / 100
    assert abs(basis) <= bound


def test_basis_bound_rejects_stale_closing_quotes_at_seven_days():
    from services.option_target_service import (
        BASIS_QUOTE_TOLERANCE_PCT,
        MAX_PLAUSIBLE_CARRY_RATE,
    )

    # Measured on a closed market: -112.3 is about -24 percent annualised.
    spot, t_now, basis = 24463.0, 0.0192, -112.3
    bound = spot * MAX_PLAUSIBLE_CARRY_RATE * t_now + spot * BASIS_QUOTE_TOLERANCE_PCT / 100
    assert abs(basis) > bound


def test_basis_bound_rejects_a_large_basis_minutes_before_expiry():
    from services.option_target_service import (
        BASIS_QUOTE_TOLERANCE_PCT,
        MAX_PLAUSIBLE_CARRY_RATE,
    )

    spot, t_now, basis = 24463.0, 1.29e-05, 59.1
    bound = spot * MAX_PLAUSIBLE_CARRY_RATE * t_now + spot * BASIS_QUOTE_TOLERANCE_PCT / 100
    assert abs(basis) > bound


def test_basis_bound_accepts_a_monthly_index_basis():
    from services.option_target_service import (
        BASIS_QUOTE_TOLERANCE_PCT,
        MAX_PLAUSIBLE_CARRY_RATE,
    )

    # BANKNIFTY 21 DTE, measured +138.9.
    spot, t_now, basis = 57794.0, 0.0575, 138.9
    bound = spot * MAX_PLAUSIBLE_CARRY_RATE * t_now + spot * BASIS_QUOTE_TOLERANCE_PCT / 100
    assert abs(basis) <= bound


def test_snapshot_reports_market_open_state():
    _, resp, _ = _run()
    assert isinstance(resp["snapshot"]["market_open"], bool)


def test_market_closed_adds_a_stale_quote_warning():
    with (
        patch("services.option_target_service.session_is_open", return_value=False),
        patch("services.option_target_service.get_option_chain", _fake_chain()),
        patch("services.option_target_service._matched_future_symbol", return_value=None),
        patch("services.option_target_service._vol_beta_samples", return_value=[]),
    ):
        _, resp, _ = get_option_target(
            underlying="NIFTY",
            exchange="NFO",
            expiry_date="11AUG26",
            reference="SPOT",
            target_price=24700.0,
            api_key="k",
        )
    assert any("market is closed" in w.lower() for w in resp["warnings"])


def test_spot_reference_is_rejected_for_a_commodity():
    # MCX has no spot instrument at all - its options are written on a future.
    # This must be rejected before the chain is even fetched, so no broker
    # patches are needed here.
    ok, resp, code = get_option_target(
        underlying="CRUDEOIL",
        exchange="MCX",
        expiry_date="17AUG26",
        reference="SPOT",
        target_price=7550.0,
        api_key="k",
    )
    assert ok is False
    assert code == 400
    assert "spot instrument" in resp["message"].lower()


def test_commodity_uses_exact_forward_mode_via_the_linked_future():
    mock_matched = MagicMock(return_value=None)
    with (
        patch(
            "services.option_target_service.get_option_chain",
            _fake_chain(underlying_ref=COMMODITY_FUTURE_REF),
        ),
        patch("services.option_target_service._matched_future_symbol", mock_matched),
        patch("services.option_target_service._vol_beta_samples", return_value=[]),
    ):
        ok, resp, code = get_option_target(
            underlying="CRUDEOIL",
            exchange="MCX",
            expiry_date="17AUG26",
            reference="FUT",
            target_price=24700.0,
            api_key="k",
        )
    assert ok is True
    assert code == 200
    assert resp["scenario"]["forward_mode"] == "exact"
    # The resolver already identified the linked future (underlying_ref); a
    # same-expiry DB lookup must never be re-issued for a commodity exchange.
    mock_matched.assert_not_called()


def test_commodity_snapshot_reports_no_spot_basis():
    _, resp, _ = _run_commodity()
    assert resp["snapshot"]["basis"] is None
    assert resp["snapshot"]["basis_plausible"] is None


def test_commodity_snapshot_reports_parity_versus_the_linked_future():
    _, resp, _ = _run_commodity()
    snapshot = resp["snapshot"]
    assert snapshot["parity_vs_underlying"] is not None
    assert snapshot["parity_vs_underlying"] == pytest.approx(snapshot["forward"] - snapshot["spot"])
    assert snapshot["underlying_ref"]["kind"] == "FUTURE"
    assert snapshot["underlying_ref"]["symbol"] == "CRUDEOIL19AUG26FUT"


def test_equity_snapshot_still_reports_a_spot_basis():
    # Regression guard: NFO/BFO (no underlying_ref block simulated, same as
    # every pre-existing test in this file) must keep today's spot-basis
    # behaviour unchanged.
    _, resp, _ = _run()
    snapshot = resp["snapshot"]
    assert snapshot["basis"] is not None
    assert snapshot["basis"] == pytest.approx(snapshot["forward"] - snapshot["spot"])
    assert isinstance(snapshot["basis_plausible"], bool)
    assert snapshot.get("parity_vs_underlying") is None


# --- vol-beta wiring --------------------------------------------------------


def _run_with_beta_samples(samples, **overrides):
    kwargs = {
        "underlying": "NIFTY",
        "exchange": "NFO",
        "expiry_date": "11AUG26",
        "reference": "SPOT",
        "target_price": 24700.0,
        "api_key": "k",
    }
    kwargs.update(overrides)
    with (
        patch("services.option_target_service.get_option_chain", _fake_chain()),
        patch("services.option_target_service._matched_future_symbol", return_value=None),
        patch(
            "services.option_target_service._vol_beta_samples", return_value=samples
        ) as mock_samples,
    ):
        ok, resp, code = get_option_target(**kwargs)
    return resp, mock_samples


def _samples_for(beta):
    return [(0.05 * i, 12.0 - beta * 0.05 * i) for i in range(40)]


def test_auto_beta_samples_the_atm_straddle():
    _, mock_samples = _run_with_beta_samples(_samples_for(1.5))
    call_symbol, put_symbol, exchange = mock_samples.call_args.args
    assert call_symbol.endswith("CE")
    assert put_symbol.endswith("PE")
    assert call_symbol[:-2] == put_symbol[:-2]
    assert exchange == "NFO"


def test_auto_beta_samples_at_the_atm_strike():
    resp, mock_samples = _run_with_beta_samples(_samples_for(1.5))
    assert mock_samples.call_args.kwargs["strike"] == resp["snapshot"]["atm_strike"]


def test_auto_beta_reports_the_estimate():
    resp, _ = _run_with_beta_samples(_samples_for(1.5))
    beta = resp["scenario"]["vol_beta"]
    assert beta["source"] == "estimated"
    assert beta["beta"] == pytest.approx(1.5, abs=1e-6)
    assert beta["clamped_from"] is None
    assert not any("vol-beta" in w.lower() for w in resp["warnings"])


def test_auto_beta_warns_and_clamps_an_implausible_estimate():
    resp, _ = _run_with_beta_samples(_samples_for(3.0))
    beta = resp["scenario"]["vol_beta"]
    assert beta["beta"] == pytest.approx(2.0)
    assert beta["clamped_from"] == pytest.approx(3.0, abs=0.01)
    assert any("clamped" in w.lower() for w in resp["warnings"])


def test_auto_beta_falls_back_and_warns_without_samples():
    resp, _ = _run_with_beta_samples([])
    beta = resp["scenario"]["vol_beta"]
    assert beta["source"] == "fallback"
    assert beta["beta"] == 0.8
    assert any("vol-beta estimate unavailable" in w.lower() for w in resp["warnings"])


def test_a_manual_beta_is_never_clamped():
    # The user typed it; only an estimate can be wrong about itself.
    resp, mock_samples = _run_with_beta_samples(_samples_for(3.0), vol_beta=5.0)
    beta = resp["scenario"]["vol_beta"]
    assert beta["source"] == "manual"
    assert beta["beta"] == pytest.approx(5.0)
    assert beta["clamped_from"] is None
    mock_samples.assert_not_called()


def test_a_preset_beta_skips_sampling():
    resp, mock_samples = _run_with_beta_samples(_samples_for(1.5), vol_beta="panic")
    assert resp["scenario"]["vol_beta"]["source"] == "preset"
    assert resp["scenario"]["vol_beta"]["beta"] == pytest.approx(2.0)
    mock_samples.assert_not_called()


def test_auto_beta_passes_the_fitted_smile_for_the_moneyness_correction():
    _, mock_samples = _run_with_beta_samples(_samples_for(1.5))
    assert mock_samples.call_args.kwargs["fit"] is not None
