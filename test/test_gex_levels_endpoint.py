"""Tests for POST /gex/api/gex-levels.

Session gating: `check_session_validity` is applied as a decorator at import
time (`blueprints/gex.py`), so patching the name after the module has already
been imported has no effect on the already-decorated view -- this was tried
and does not work. Instead this drives the REAL blueprint through a Flask
test client and uses `session_transaction` to set the exact session keys
`is_session_valid()` (utils/session.py) inspects: `logged_in`, `login_time`,
and `user`. This mirrors the pattern already used in
test/test_indicator_script_endpoints.py.

The service itself (`services.gex_levels_service.get_gex_levels`) is patched
at its import site inside `blueprints.gex` so no broker call happens.
"""

import time
from datetime import datetime
from unittest.mock import patch

import pytest
import pytz
from flask import Flask

from blueprints.gex import gex_bp


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = "test-secret-key"
    app.register_blueprint(gex_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def authed_client(client):
    """A client with a session that satisfies `is_session_valid()`."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "tester"
        sess["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    return client


@pytest.fixture(autouse=True)
def _no_recorded_snapshot():
    """Default every test to "nothing recorded", so the live path is exercised.

    The view consults `gex_history_db` before falling back to a live fetch. Left
    unpatched every test in this file would open the real `db/gex.db`; the tests
    that are specifically about the fast path patch over this.
    """
    with patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=None):
        yield


def body(**overrides):
    out = {
        "underlying": "NIFTY",
        "exchange": "NFO",
        "expiry_date": "11AUG26",
        "weight_by": "oi",
    }
    out.update(overrides)
    return out


# --------------------------------------------------------------- authentication


def test_unauthenticated_request_is_rejected(client):
    """No session at all: `check_session_validity`'s own gate must reject the
    request before the view -- and therefore the service -- ever runs.

    A JSON body is sent so the decorator takes its AJAX branch (401 JSON)
    rather than a redirect; this proves the outer session gate, not the
    view's own `session.get("user")` check further inside.
    """
    with patch("blueprints.gex.get_gex_levels") as service:
        response = client.post("/gex/api/gex-levels", json=body())

    assert response.status_code == 401
    service.assert_not_called()


def test_missing_api_key_is_rejected(authed_client):
    """A logged-in user with no OpenAlgo API key configured must not reach
    the service either."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value=None),
        patch("blueprints.gex.get_gex_levels") as service,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    assert response.status_code == 401
    assert "API key" in response.get_json()["message"]
    service.assert_not_called()


# ------------------------------------------------------------------- validation


@pytest.mark.parametrize("field", ["underlying", "exchange", "expiry_date"])
def test_missing_required_field_is_400(authed_client, field):
    payload = body()
    payload[field] = ""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.get_gex_levels") as service,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=payload)

    assert response.status_code == 400
    service.assert_not_called()


@pytest.mark.parametrize("field", ["underlying", "exchange", "expiry_date"])
def test_absent_required_field_is_400(authed_client, field):
    """Field omitted from the JSON body entirely, not just blank."""
    payload = body()
    del payload[field]
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.get_gex_levels") as service,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=payload)

    assert response.status_code == 400
    service.assert_not_called()


def test_malformed_underlying_is_400(authed_client):
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.get_gex_levels") as service,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body(underlying="nifty-50"))

    assert response.status_code == 400
    assert response.get_json()["message"] == "Invalid input format"
    service.assert_not_called()


def test_malformed_exchange_is_400(authed_client):
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.get_gex_levels") as service,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body(exchange="nfo!"))

    assert response.status_code == 400
    assert response.get_json()["message"] == "Invalid input format"
    service.assert_not_called()


def test_malformed_expiry_is_400(authed_client):
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.get_gex_levels") as service,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body(expiry_date="2026-08-11"))

    assert response.status_code == 400
    assert "DDMMMYY" in response.get_json()["message"]
    service.assert_not_called()


def test_invalid_weight_by_is_400(authed_client):
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.get_gex_levels") as service,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body(weight_by="delta"))

    assert response.status_code == 400
    assert response.get_json()["message"] == "weight_by must be 'oi' or 'volume'"
    service.assert_not_called()


def test_weight_by_omitted_defaults_to_oi(authed_client):
    """Asserts on what the service actually received, not just the HTTP status."""
    captured = {}

    def fake_get_gex_levels(**kwargs):
        captured.update(kwargs)
        return True, {"status": "success"}, 200

    payload = body()
    del payload["weight_by"]
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.get_gex_levels", side_effect=fake_get_gex_levels),
    ):
        response = authed_client.post("/gex/api/gex-levels", json=payload)

    assert response.status_code == 200
    assert captured["weight_by"] == "oi"


# ---------------------------------------------------------------- pass-through


def test_successful_call_passes_service_payload_and_status_through(authed_client):
    service_payload = {
        "status": "success",
        "call_wall": 24700.0,
        "put_wall": 24400.0,
        "zero_gamma": 24580.0,
    }
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.get_gex_levels",
            return_value=(True, service_payload, 200),
        ),
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    assert response.status_code == 200
    assert response.get_json() == service_payload


def test_service_failure_status_passes_through(authed_client):
    """e.g. the option chain came back empty -- a 404, not a 500."""
    failure_payload = {"status": "error", "message": "Spot price or option chain unavailable"}
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.get_gex_levels",
            return_value=(False, failure_payload, 404),
        ),
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    assert response.status_code == 404
    assert response.get_json() == failure_payload


# ----------------------------------------------------------- the recorded path


def _recorded_snapshot(age_seconds=30):
    """One stored snapshot as `gex_history_db.get_latest_snapshot` returns it."""
    return {
        "id": 1,
        "series_id": 1,
        "ts": int(time.time()) - age_seconds,
        # Folded in from the owning series by get_latest_snapshot: a snapshot
        # row alone cannot say what instrument it belongs to.
        "underlying": "NIFTY",
        "exchange": "NFO",
        "expiry_date": "11AUG26",
        "spot_price": 24590.0,
        "forward_price": 24610.0,
        "atm_strike": 24600.0,
        "dte_days": 6.2,
        "interest_rate": 6.5,
        "lot_size": 75,
        "strikes_used": 2,
        "call_wall_oi": 24800.0,
        "call_wall_vol": 24700.0,
        "put_wall_oi": 24400.0,
        "put_wall_vol": 24500.0,
        "zero_gamma_oi": 24605.0,
        "zero_gamma_vol": None,
        "net_gex_oi": 1234.5,
        "net_gex_vol": -678.9,
        "regime_oi": "suppressive",
        "regime_vol": "amplifying",
        "sentiment_oi": {"bias": "bullish", "score": 0.4, "signals": []},
        "sentiment_vol": {"bias": "neutral", "score": 0.0, "signals": []},
        "quality_verdict_oi": "good",
        "quality_verdict_vol": "degraded",
        "quality_oi": {"verdict": "good", "strikes_used": 2, "notes": [], "may_draw": True},
        "quality_vol": {
            "verdict": "degraded",
            "strikes_used": 2,
            "notes": ["thin volume"],
            "may_draw": True,
        },
        "strikes": [
            {
                "snapshot_id": 1,
                "strike": 24400.0,
                "call_gex_oi": 10.0,
                "put_gex_oi": -4.0,
                "net_gex_oi": 6.0,
                "call_gex_vol": 1.0,
                "put_gex_vol": -0.4,
                "net_gex_vol": 0.6,
                "call_dex_oi": 20.0,
                "put_dex_oi": -8.0,
                "net_dex_oi": 12.0,
                "call_dex_vol": 2.0,
                "put_dex_vol": -0.8,
                "net_dex_vol": 1.2,
                "call_oi": 100000.0,
                "put_oi": 90000.0,
                "call_volume": 5000.0,
                "put_volume": 4000.0,
            },
            {
                "snapshot_id": 1,
                "strike": 24800.0,
                "call_gex_oi": 12.0,
                "put_gex_oi": -6.0,
                "net_gex_oi": 6.0,
                "call_gex_vol": 1.2,
                "put_gex_vol": -0.6,
                "net_gex_vol": 0.6,
                "call_dex_oi": 24.0,
                "put_dex_oi": -12.0,
                "net_dex_oi": 12.0,
                "call_dex_vol": 2.4,
                "put_dex_vol": -1.2,
                "net_dex_vol": 1.2,
                "call_oi": 80000.0,
                "put_oi": 70000.0,
                "call_volume": 3000.0,
                "put_volume": 2000.0,
            },
        ],
    }


def test_a_fresh_recorded_snapshot_is_served_without_a_broker_call(authed_client):
    """N open tabs cost one poll instead of N. That is the recorder paying for
    itself, not an optimisation bolted on afterwards."""
    recorded = _recorded_snapshot(age_seconds=30)
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=recorded),
        patch("blueprints.gex.get_gex_levels") as live,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_not_called()
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["source"] == "recorded"
    assert payload["as_of"] == recorded["ts"]
    assert len(payload["strikes"]) == 2


def test_the_recorded_payload_carries_may_draw(authed_client):
    """may_draw absent reads as undefined -> falsy in TypeScript, which would
    suppress every recorded snapshot the study was handed."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.gex_history_db.get_latest_snapshot",
            return_value=_recorded_snapshot(),
        ),
    ):
        payload = authed_client.post("/gex/api/gex-levels", json=body()).get_json()

    assert payload["quality"]["may_draw"] is True
    assert payload["quality"]["verdict"] == "good"


def test_the_recorded_payload_serves_the_requested_weighting(authed_client):
    """One stored row holds both weightings; the toggle must pick the right
    column family, at the levels AND at every strike."""
    recorded = _recorded_snapshot()
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=recorded),
    ):
        by_oi = authed_client.post("/gex/api/gex-levels", json=body(weight_by="oi")).get_json()
        by_vol = authed_client.post("/gex/api/gex-levels", json=body(weight_by="volume")).get_json()

    assert by_oi["call_wall"] == 24800.0
    assert by_vol["call_wall"] == 24700.0
    assert by_oi["zero_gamma"] == 24605.0
    assert by_vol["zero_gamma"] is None
    assert by_oi["regime"] == "suppressive"
    assert by_vol["regime"] == "amplifying"
    assert by_oi["quality"]["verdict"] == "good"
    assert by_vol["quality"]["verdict"] == "degraded"
    assert by_oi["strikes"][0]["net_gex"] == 6.0
    assert by_vol["strikes"][0]["net_gex"] == 0.6
    assert by_oi["strikes"][0]["net_dex"] == 12.0
    assert by_vol["strikes"][0]["net_dex"] == 1.2


def test_the_recorded_payload_has_the_same_shape_as_the_live_one(authed_client):
    """The frontend must have exactly one payload shape to handle. A key the
    recorded path omits reads as undefined rather than failing - which is how
    the first implementation of this study shipped a chart with no bar column."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.gex_history_db.get_latest_snapshot",
            return_value=_recorded_snapshot(),
        ),
    ):
        payload = authed_client.post("/gex/api/gex-levels", json=body()).get_json()

    for key in (
        "status",
        "underlying",
        "exchange",
        "expiry_date",
        "weight_by",
        "spot_price",
        "forward_price",
        "atm_strike",
        "lot_size",
        "dte_days",
        "interest_rate",
        "strikes",
        "call_wall",
        "put_wall",
        "zero_gamma",
        "total_call_gex",
        "total_put_gex",
        "net_gex",
        "regime",
        "quality",
        "sentiment",
        "source",
        "as_of",
    ):
        assert key in payload, f"recorded payload is missing {key!r}"

    assert set(payload["strikes"][0]) == {
        "strike",
        "call_gex",
        "put_gex",
        "net_gex",
        "call_dex",
        "put_dex",
        "net_dex",
    }


def test_the_recorded_totals_are_summed_from_the_profile(authed_client):
    """Derived rather than stored: a stored total that disagreed with its own
    strikes would be unfixable, and the dashboard and the bars would tell the
    reader two different stories."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.gex_history_db.get_latest_snapshot",
            return_value=_recorded_snapshot(),
        ),
    ):
        payload = authed_client.post("/gex/api/gex-levels", json=body()).get_json()

    assert payload["total_call_gex"] == 22.0
    assert payload["total_put_gex"] == -10.0
    assert payload["net_gex"] == 1234.5  # the stored total, not a re-sum


def test_a_stale_recorded_snapshot_falls_back_to_a_live_fetch(authed_client):
    """Two cadence intervals, so one missed tick does not force a broker round
    trip - but a recorder that is down must not freeze the study on old
    numbers."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.gex_history_db.get_latest_snapshot",
            return_value=_recorded_snapshot(age_seconds=180),
        ),
        patch(
            "blueprints.gex.get_gex_levels", return_value=(True, {"status": "success"}, 200)
        ) as live,
    ):
        authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_called_once()


def test_a_snapshot_just_inside_the_window_is_still_served(authed_client):
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.gex_history_db.get_latest_snapshot",
            return_value=_recorded_snapshot(age_seconds=110),
        ),
        patch("blueprints.gex.get_gex_levels") as live,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_not_called()
    assert response.get_json()["source"] == "recorded"


def test_a_series_nobody_recorded_still_renders(authed_client):
    """Unifying the fetch must not make the study fail closed on instruments
    nobody chose to record."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=None),
        patch(
            "blueprints.gex.get_gex_levels", return_value=(True, {"status": "success"}, 200)
        ) as live,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_called_once()
    assert response.status_code == 200


def test_a_history_lookup_failure_falls_back_rather_than_500s(authed_client):
    """The recorded path is an optimisation. A broken gex.db must degrade the
    study to exactly the behaviour it had before the recorder existed, not take
    the study down with it."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch(
            "blueprints.gex.gex_history_db.get_latest_snapshot",
            side_effect=RuntimeError("db gone"),
        ),
        patch(
            "blueprints.gex.get_gex_levels", return_value=(True, {"status": "success"}, 200)
        ) as live,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_called_once()
    assert response.status_code == 200


def test_a_malformed_recorded_row_falls_back_rather_than_500s(authed_client):
    """A row written by an older schema is missing keys the reshape reads. That
    must cost a live fetch, not a broken study."""
    broken = {"ts": int(time.time()), "expiry_date": "11AUG26"}
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=broken),
        patch(
            "blueprints.gex.get_gex_levels", return_value=(True, {"status": "success"}, 200)
        ) as live,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_called_once()
    assert response.status_code == 200


def test_the_live_path_is_still_consulted_before_validation_passes(authed_client):
    """The fast path must sit AFTER validation: a malformed request must not be
    answered from history any more than from the broker."""
    with (
        patch("blueprints.gex.get_api_key_for_tradingview", return_value="key"),
        patch("blueprints.gex.gex_history_db.get_latest_snapshot") as lookup,
    ):
        response = authed_client.post("/gex/api/gex-levels", json=body(expiry_date="bad"))

    assert response.status_code == 400
    lookup.assert_not_called()
