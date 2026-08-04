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
