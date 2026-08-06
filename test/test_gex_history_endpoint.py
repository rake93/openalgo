"""Tests for POST /gex/api/gex-history.

Same shape as test_gex_levels_endpoint.py: the real blueprint through a Flask
test client, with `session_transaction` setting the keys `is_session_valid()`
inspects. The service is patched at its import site inside `blueprints.gex`, so
nothing here touches a database.
"""

from datetime import datetime
from unittest.mock import patch

import pytest
import pytz
from flask import Flask

from blueprints.gex import MAX_HISTORY_POINTS, gex_bp


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
        "from_ts": 1_754_000_040,
        "to_ts": 1_754_003_640,
        "fields": "levels",
    }
    out.update(overrides)
    return out


_OK = (True, {"status": "success", "points": []}, 200)


# --------------------------------------------------------------- authentication


def test_an_unauthenticated_request_never_reaches_the_service(client):
    with patch("blueprints.gex.get_gex_history") as service:
        response = client.post("/gex/api/gex-history", json=body())

    assert response.status_code == 401
    service.assert_not_called()


# ------------------------------------------------------------------- validation


@pytest.mark.parametrize("field", ["underlying", "exchange", "expiry_date"])
def test_a_missing_required_field_is_400(authed_client, field):
    payload = body()
    del payload[field]
    with patch("blueprints.gex.get_gex_history") as service:
        response = authed_client.post("/gex/api/gex-history", json=payload)

    assert response.status_code == 400
    service.assert_not_called()


def test_a_malformed_expiry_is_400(authed_client):
    """Bands is scoped to a RESOLVED contract, so an expiry rule is not
    acceptable here the way it is on the watchlist route."""
    with patch("blueprints.gex.get_gex_history") as service:
        response = authed_client.post("/gex/api/gex-history", json=body(expiry_date="nearest"))

    assert response.status_code == 400
    assert "DDMMMYY" in response.get_json()["message"]
    service.assert_not_called()


def test_a_malformed_underlying_is_400(authed_client):
    with patch("blueprints.gex.get_gex_history") as service:
        response = authed_client.post("/gex/api/gex-history", json=body(underlying="nifty-50"))

    assert response.status_code == 400
    service.assert_not_called()


@pytest.mark.parametrize("window", [{"from_ts": "soon"}, {"to_ts": None}, {"from_ts": -5}])
def test_a_malformed_window_is_400(authed_client, window):
    with patch("blueprints.gex.get_gex_history") as service:
        response = authed_client.post("/gex/api/gex-history", json=body(**window))

    assert response.status_code == 400
    service.assert_not_called()


def test_an_inverted_window_is_400(authed_client):
    with patch("blueprints.gex.get_gex_history") as service:
        response = authed_client.post("/gex/api/gex-history", json=body(from_ts=2000, to_ts=1000))

    assert response.status_code == 400
    service.assert_not_called()


def test_a_window_wider_than_the_point_ceiling_is_rejected_by_name(authed_client):
    """get_snapshots_in_range has no row limit of its own, so a month-wide
    request would stream ~11,000 rows to a browser and a year-wide one far
    more. Rejected here with the ceiling named, rather than served."""
    from_ts = 1_754_000_040
    too_wide = from_ts + (MAX_HISTORY_POINTS + 10) * 60

    with patch("blueprints.gex.get_gex_history") as service:
        response = authed_client.post(
            "/gex/api/gex-history", json=body(from_ts=from_ts, to_ts=too_wide)
        )

    assert response.status_code == 400
    assert str(MAX_HISTORY_POINTS) in response.get_json()["message"]
    service.assert_not_called()


def test_a_window_just_inside_the_ceiling_is_served(authed_client):
    from_ts = 1_754_000_040
    just_inside = from_ts + (MAX_HISTORY_POINTS - 10) * 60

    with patch("blueprints.gex.get_gex_history", return_value=_OK) as service:
        response = authed_client.post(
            "/gex/api/gex-history", json=body(from_ts=from_ts, to_ts=just_inside)
        )

    assert response.status_code == 200
    service.assert_called_once()


# ----------------------------------------------------------------- pass-through


def test_the_request_reaches_the_service_intact(authed_client):
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return _OK

    with patch("blueprints.gex.get_gex_history", side_effect=fake):
        authed_client.post("/gex/api/gex-history", json=body())

    assert captured["underlying"] == "NIFTY"
    assert captured["exchange"] == "NFO"
    assert captured["expiry_date"] == "11AUG26"
    assert captured["weight_by"] == "oi"
    assert captured["from_ts"] == 1_754_000_040
    assert captured["to_ts"] == 1_754_003_640
    assert captured["fields"] == "levels"


def test_fields_defaults_to_levels(authed_client):
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return _OK

    payload = body()
    del payload["fields"]
    with patch("blueprints.gex.get_gex_history", side_effect=fake):
        authed_client.post("/gex/api/gex-history", json=payload)

    assert captured["fields"] == "levels"


def test_a_service_rejection_passes_its_status_through(authed_client):
    """The service owns the fields/metric/weighting vocabulary; the route must
    not re-word or re-status its refusals."""
    refusal = (False, {"status": "error", "message": "metric must be 'gamma' or 'delta'"}, 400)
    with patch("blueprints.gex.get_gex_history", return_value=refusal):
        response = authed_client.post("/gex/api/gex-history", json=body(metric="vanna"))

    assert response.status_code == 400
    assert "metric" in response.get_json()["message"]


def test_the_metric_reaches_the_service_and_defaults_to_gamma(authed_client):
    """The grid reads gamma or delta off one recorded chain, so the metric is a
    request parameter rather than a second endpoint."""
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return _OK

    with patch("blueprints.gex.get_gex_history", side_effect=fake):
        authed_client.post("/gex/api/gex-history", json=body(fields="grid", metric="delta"))
    assert captured["metric"] == "delta"

    payload = body()
    with patch("blueprints.gex.get_gex_history", side_effect=fake):
        authed_client.post("/gex/api/gex-history", json=payload)
    assert captured["metric"] == "gamma"


def test_an_empty_history_is_a_200(authed_client):
    """A contract nobody recorded must not read as a failure to the study."""
    with patch("blueprints.gex.get_gex_history", return_value=_OK):
        response = authed_client.post("/gex/api/gex-history", json=body())

    assert response.status_code == 200
    assert response.get_json()["points"] == []
