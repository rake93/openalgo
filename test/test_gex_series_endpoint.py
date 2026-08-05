"""Tests for the recorder watchlist routes under /gex/api/gex-series.

Session gating: `check_session_validity` is applied as a decorator at import
time (`blueprints/gex.py`), so patching the name afterwards has no effect on the
already-decorated view. This drives the REAL blueprint through a Flask test
client and sets the exact session keys `is_session_valid()` inspects, the same
way test_gex_levels_endpoint.py does.

`gex_history_db` is patched at its import site inside `blueprints.gex`, so no
test here touches a database file.
"""

from datetime import datetime
from unittest.mock import patch

import pytest
import pytz
from flask import Flask

from blueprints.gex import MAX_SERIES, gex_bp


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


def _series(series_id=1, **overrides):
    row = {
        "id": series_id,
        "underlying": "NIFTY",
        "exchange": "NFO",
        "expiry_rule": "nearest",
        "enabled": True,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------- authentication


def test_every_route_requires_a_session(client):
    """The watchlist decides what the server polls the broker for, so it is
    session-gated exactly like the study's own endpoint.

    GET and DELETE carry no body, so `check_session_validity` cannot infer AJAX
    from the content type the way it does for POST and PATCH. They send
    `Accept: application/json` to take the 401 branch instead of a redirect to
    the login page - which is what a `fetch` caller wants, and what it must send
    to get it. That is pre-existing decorator behaviour shared by every
    body-less route in the app, not something these routes introduce.
    """
    json_header = {"Accept": "application/json"}
    assert client.get("/gex/api/gex-series", headers=json_header).status_code == 401
    assert client.post("/gex/api/gex-series", json={"underlying": "NIFTY"}).status_code == 401
    assert client.patch("/gex/api/gex-series/1", json={"enabled": False}).status_code == 401
    assert client.delete("/gex/api/gex-series/1", headers=json_header).status_code == 401


def test_an_unauthenticated_request_never_reaches_the_database(client):
    with patch("blueprints.gex.gex_history_db") as db:
        client.post("/gex/api/gex-series", json={"underlying": "NIFTY", "exchange": "NFO"})
    db.add_series.assert_not_called()


# ---------------------------------------------------------------------- listing


def test_listing_returns_the_watchlist(authed_client):
    with patch("blueprints.gex.gex_history_db.list_series", return_value=[_series()]):
        res = authed_client.get("/gex/api/gex-series")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["status"] == "success"
    assert payload["data"][0]["underlying"] == "NIFTY"


def test_an_empty_watchlist_is_a_success_not_an_error(authed_client):
    """The recorder ships empty. A fresh install must not see a failure."""
    with patch("blueprints.gex.gex_history_db.list_series", return_value=[]):
        res = authed_client.get("/gex/api/gex-series")

    assert res.status_code == 200
    assert res.get_json()["data"] == []


# ----------------------------------------------------------------------- adding


def test_adding_a_series_registers_its_recorder_job(authed_client):
    """Without the sync the row would exist and nothing would ever poll it -
    the watchlist would silently do nothing until the next restart."""
    with (
        patch("blueprints.gex.gex_history_db.list_series", return_value=[]),
        patch(
            "blueprints.gex.gex_history_db.add_series",
            return_value=(True, "Series added", _series()),
        ),
        patch("blueprints.gex.get_gex_recorder") as recorder,
    ):
        res = authed_client.post(
            "/gex/api/gex-series",
            json={"underlying": "NIFTY", "exchange": "NFO", "expiry_rule": "nearest"},
        )

    assert res.status_code == 201
    assert res.get_json()["data"]["id"] == 1
    recorder.return_value.sync_jobs.assert_called_once()


def test_the_expiry_rule_defaults_to_nearest(authed_client):
    with (
        patch("blueprints.gex.gex_history_db.list_series", return_value=[]),
        patch(
            "blueprints.gex.gex_history_db.add_series", return_value=(True, "ok", _series())
        ) as add,
        patch("blueprints.gex.get_gex_recorder"),
    ):
        authed_client.post("/gex/api/gex-series", json={"underlying": "NIFTY", "exchange": "NFO"})

    assert add.call_args.kwargs["expiry_rule"] == "nearest"


def test_a_pinned_expiry_rule_is_accepted(authed_client):
    with (
        patch("blueprints.gex.gex_history_db.list_series", return_value=[]),
        patch(
            "blueprints.gex.gex_history_db.add_series", return_value=(True, "ok", _series())
        ) as add,
        patch("blueprints.gex.get_gex_recorder"),
    ):
        res = authed_client.post(
            "/gex/api/gex-series",
            json={"underlying": "NIFTY", "exchange": "NFO", "expiry_rule": "11AUG26"},
        )

    assert res.status_code == 201
    assert add.call_args.kwargs["expiry_rule"] == "11AUG26"


@pytest.mark.parametrize(
    "body",
    [
        {"exchange": "NFO"},
        {"underlying": "NIFTY"},
        {"underlying": "nifty!", "exchange": "NFO"},
        {"underlying": "NIFTY", "exchange": "NFO-"},
        {"underlying": "NIFTY", "exchange": "NFO", "expiry_rule": "next-week"},
        {"underlying": "NIFTY", "exchange": "NFO", "expiry_rule": "2026-08-11"},
    ],
)
def test_a_malformed_series_is_rejected_before_it_can_be_scheduled(authed_client, body):
    """A bad row here becomes a broker call every minute that can never succeed."""
    with (
        patch("blueprints.gex.gex_history_db.list_series", return_value=[]),
        patch("blueprints.gex.gex_history_db.add_series") as add,
    ):
        res = authed_client.post("/gex/api/gex-series", json=body)

    assert res.status_code == 400
    add.assert_not_called()


def test_a_duplicate_series_is_reported_rather_than_silently_ignored(authed_client):
    with (
        patch("blueprints.gex.gex_history_db.list_series", return_value=[]),
        patch(
            "blueprints.gex.gex_history_db.add_series",
            return_value=(False, "NIFTY NFO nearest is already being recorded", None),
        ),
        patch("blueprints.gex.get_gex_recorder") as recorder,
    ):
        res = authed_client.post(
            "/gex/api/gex-series", json={"underlying": "NIFTY", "exchange": "NFO"}
        )

    assert res.status_code == 400
    assert "already" in res.get_json()["message"]
    recorder.return_value.sync_jobs.assert_not_called()


def test_the_watchlist_is_capped(authed_client):
    """Ten series is 940 chain symbols a minute against a broker that
    rate-limited a single manual call during design. The design rejected
    auto-follow for unbounded growth; a manual list with no ceiling reaches the
    same place, just more slowly."""
    with (
        patch(
            "blueprints.gex.gex_history_db.list_series",
            return_value=[_series(i) for i in range(MAX_SERIES)],
        ),
        patch("blueprints.gex.gex_history_db.add_series") as add,
    ):
        res = authed_client.post(
            "/gex/api/gex-series", json={"underlying": "BANKNIFTY", "exchange": "NFO"}
        )

    assert res.status_code == 400
    assert str(MAX_SERIES) in res.get_json()["message"]
    add.assert_not_called()


def test_the_cap_counts_disabled_series_too(authed_client):
    """A disabled series still occupies a slot: re-enabling it must not be able
    to push the recorder past the cap."""
    disabled = [_series(i, enabled=False) for i in range(MAX_SERIES)]
    with (
        patch("blueprints.gex.gex_history_db.list_series", return_value=disabled) as listed,
        patch("blueprints.gex.gex_history_db.add_series") as add,
    ):
        res = authed_client.post(
            "/gex/api/gex-series", json={"underlying": "BANKNIFTY", "exchange": "NFO"}
        )

    assert res.status_code == 400
    add.assert_not_called()
    # Called without enabled_only, so disabled rows are counted.
    assert listed.call_args.kwargs.get("enabled_only", False) is False


# -------------------------------------------------------------- enable / disable


def test_disabling_a_series_stops_its_job(authed_client):
    with (
        patch(
            "blueprints.gex.gex_history_db.set_series_enabled",
            return_value=(True, "Series disabled"),
        ) as toggle,
        patch("blueprints.gex.get_gex_recorder") as recorder,
    ):
        res = authed_client.patch("/gex/api/gex-series/3", json={"enabled": False})

    assert res.status_code == 200
    toggle.assert_called_once_with(3, False)
    recorder.return_value.sync_jobs.assert_called_once()


def test_patching_without_an_enabled_flag_is_rejected(authed_client):
    with patch("blueprints.gex.gex_history_db.set_series_enabled") as toggle:
        res = authed_client.patch("/gex/api/gex-series/3", json={})

    assert res.status_code == 400
    toggle.assert_not_called()


def test_patching_an_unknown_series_is_a_404(authed_client):
    with patch(
        "blueprints.gex.gex_history_db.set_series_enabled", return_value=(False, "Series not found")
    ):
        res = authed_client.patch("/gex/api/gex-series/999", json={"enabled": True})

    assert res.status_code == 404


# -------------------------------------------------------------------- removing


def test_removing_a_series_stops_its_job_and_says_history_went_with_it(authed_client):
    """The delete is destructive and there is no source to rebuild from - the
    option chain API returns only current OI and volume. The caller must not
    have to read the source to find that out."""
    with (
        patch(
            "blueprints.gex.gex_history_db.remove_series",
            return_value=(
                True,
                "Series removed along with 400 recorded snapshot(s). "
                "Recorded history cannot be rebuilt.",
            ),
        ) as remove,
        patch("blueprints.gex.get_gex_recorder") as recorder,
    ):
        res = authed_client.delete("/gex/api/gex-series/3")

    assert res.status_code == 200
    remove.assert_called_once_with(3)
    recorder.return_value.sync_jobs.assert_called_once()
    assert "cannot be rebuilt" in res.get_json()["message"]


def test_removing_an_unknown_series_is_a_404(authed_client):
    with patch(
        "blueprints.gex.gex_history_db.remove_series", return_value=(False, "Series not found")
    ):
        res = authed_client.delete("/gex/api/gex-series/999")

    assert res.status_code == 404


def test_a_recorder_sync_failure_does_not_lose_the_watchlist_change(authed_client):
    """The row is committed before the scheduler is touched. If sync raises, the
    change survives and the next restart picks it up - reporting a 500 and
    leaving a committed row would be the worst of both."""
    with (
        patch("blueprints.gex.gex_history_db.list_series", return_value=[]),
        patch("blueprints.gex.gex_history_db.add_series", return_value=(True, "ok", _series())),
        patch("blueprints.gex.get_gex_recorder", side_effect=RuntimeError("scheduler down")),
    ):
        res = authed_client.post(
            "/gex/api/gex-series", json={"underlying": "NIFTY", "exchange": "NFO"}
        )

    assert res.status_code == 201
