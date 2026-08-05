"""
GEX Blueprint

Serves Gamma Exposure and OI Walls data.
Endpoints:
    POST   /gex/api/gex-data          - GEX data for all strikes (/gex Tools page)
    POST   /gex/api/gex-levels        - GEX Levels for the /charts study
    GET    /gex/api/gex-series        - The snapshot recorder's watchlist
    POST   /gex/api/gex-series        - Start recording a series
    PATCH  /gex/api/gex-series/<id>   - Enable or disable recording
    DELETE /gex/api/gex-series/<id>   - Stop recording AND drop its history

The watchlist routes are session-gated rather than API-key-gated: they decide
what the server polls the broker for on a schedule, which is an operator
decision, not something an external platform should be able to set.
"""

import re

from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database import gex_history_db
from database.auth_db import get_api_key_for_tradingview
from services.gex_levels_service import get_gex_levels
from services.gex_recorder_service import get_gex_recorder
from services.gex_service import get_gex_data
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

gex_bp = Blueprint("gex_bp", __name__, url_prefix="/")

# Not in the original design, and deliberately added: the design rejected
# auto-follow because it grows without bound, and a manually curated list with
# no ceiling reaches the same place, just more slowly. Ten series is 940 chain
# symbols a minute against a broker that rate-limited a single manual call
# during design. Raise it only alongside a look at the rate limit.
MAX_SERIES = 10

# `nearest` resolves per tick and rolls weekly; anything else must be a pinned
# DDMMMYY, the same shape the chain service takes.
_EXPIRY_RULE_RE = re.compile(r"^\d{2}[A-Z]{3}\d{2}$")


def _sync_recorder_jobs() -> None:
    """Re-register the recorder's jobs after a watchlist change.

    Never raises. The watchlist row is already committed by the time this runs,
    so a scheduler that is down must not turn a successful change into a 500 -
    the change survives and the next restart picks it up. Reporting failure
    while leaving the row committed would be the worst of both.
    """
    try:
        get_gex_recorder().sync_jobs()
    except Exception:
        logger.exception(
            "GEX recorder: could not sync jobs after a watchlist change. "
            "The change is saved and will take effect on the next restart."
        )


@gex_bp.route("/gex/api/gex-data", methods=["POST"])
@cross_origin()
@check_session_validity
def gex_data():
    """Get GEX data for all strikes."""
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify(
                {
                    "status": "error",
                    "message": "API key not configured. Please generate an API key in /apikey",
                }
            ), 401

        data = request.get_json(silent=True) or {}
        underlying = data.get("underlying", "").strip()[:20]
        exchange = data.get("exchange", "").strip()[:20]
        expiry_date = data.get("expiry_date", "").strip()[:10]

        if not underlying or not exchange or not expiry_date:
            return jsonify(
                {
                    "status": "error",
                    "message": "underlying, exchange, and expiry_date are required",
                }
            ), 400

        if not re.match(r"^[A-Z0-9]+$", underlying) or not re.match(r"^[A-Z0-9_]+$", exchange):
            return jsonify({"status": "error", "message": "Invalid input format"}), 400

        if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_date):
            return jsonify(
                {"status": "error", "message": "Invalid expiry_date format. Expected DDMMMYY"}
            ), 400

        success, response, status_code = get_gex_data(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            api_key=api_key,
        )

        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error in GEX data API: {e}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )


@gex_bp.route("/gex/api/gex-levels", methods=["POST"])
@cross_origin()
@check_session_validity
def gex_levels():
    """Get GEX Levels (call wall, put wall, zero gamma) for the /charts study."""
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify(
                {
                    "status": "error",
                    "message": "API key not configured. Please generate an API key in /apikey",
                }
            ), 401

        data = request.get_json(silent=True) or {}
        underlying = data.get("underlying", "").strip()[:20]
        exchange = data.get("exchange", "").strip()[:20]
        expiry_date = data.get("expiry_date", "").strip()[:10]
        weight_by = data.get("weight_by", "oi").strip().lower()[:10]

        if not underlying or not exchange or not expiry_date:
            return jsonify(
                {
                    "status": "error",
                    "message": "underlying, exchange, and expiry_date are required",
                }
            ), 400

        if not re.match(r"^[A-Z0-9]+$", underlying) or not re.match(r"^[A-Z0-9_]+$", exchange):
            return jsonify({"status": "error", "message": "Invalid input format"}), 400

        if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_date):
            return jsonify(
                {"status": "error", "message": "Invalid expiry_date format. Expected DDMMMYY"}
            ), 400

        if weight_by not in ("oi", "volume"):
            return jsonify(
                {"status": "error", "message": "weight_by must be 'oi' or 'volume'"}
            ), 400

        success, response, status_code = get_gex_levels(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            api_key=api_key,
            weight_by=weight_by,
        )

        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error in GEX Levels API: {e}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )


# ------------------------------------------------ the snapshot recorder watchlist


@gex_bp.route("/gex/api/gex-series", methods=["GET"])
@cross_origin()
@check_session_validity
def gex_series_list():
    """Every series the snapshot recorder is configured to poll.

    Ships empty, and an empty list is a success rather than an error - the
    recorder stays idle until something is added.
    """
    try:
        return jsonify({"status": "success", "data": gex_history_db.list_series()}), 200
    except Exception as e:
        logger.exception(f"Error listing GEX series: {e}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )


@gex_bp.route("/gex/api/gex-series", methods=["POST"])
@cross_origin()
@check_session_validity
def gex_series_add():
    """Start recording a series, and register its job immediately.

    Validated before it can be scheduled: a malformed row here becomes a broker
    call every minute that can never succeed.
    """
    try:
        data = request.get_json(silent=True) or {}
        underlying = data.get("underlying", "").strip().upper()[:20]
        exchange = data.get("exchange", "").strip().upper()[:20]
        expiry_rule = (data.get("expiry_rule") or "nearest").strip()[:10]

        if not underlying or not exchange:
            return jsonify(
                {"status": "error", "message": "underlying and exchange are required"}
            ), 400

        if not re.match(r"^[A-Z0-9]+$", underlying) or not re.match(r"^[A-Z0-9_]+$", exchange):
            return jsonify({"status": "error", "message": "Invalid input format"}), 400

        if expiry_rule.lower() == "nearest":
            expiry_rule = "nearest"
        elif not _EXPIRY_RULE_RE.match(expiry_rule.upper()):
            return jsonify(
                {
                    "status": "error",
                    "message": "expiry_rule must be 'nearest' or a DDMMMYY expiry (e.g. 11AUG26)",
                }
            ), 400
        else:
            expiry_rule = expiry_rule.upper()

        # Counted across the whole watchlist, disabled rows included: a disabled
        # series still occupies a slot, so re-enabling it must not be able to
        # push the recorder past the cap.
        if len(gex_history_db.list_series()) >= MAX_SERIES:
            return jsonify(
                {
                    "status": "error",
                    "message": (
                        f"The recorder watchlist is capped at {MAX_SERIES} series. "
                        "Remove one before adding another."
                    ),
                }
            ), 400

        success, message, series = gex_history_db.add_series(
            underlying=underlying,
            exchange=exchange,
            expiry_rule=expiry_rule,
        )
        if not success:
            return jsonify({"status": "error", "message": message}), 400

        _sync_recorder_jobs()
        return jsonify({"status": "success", "message": message, "data": series}), 201

    except Exception as e:
        logger.exception(f"Error adding a GEX series: {e}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )


@gex_bp.route("/gex/api/gex-series/<int:series_id>", methods=["PATCH"])
@cross_origin()
@check_session_validity
def gex_series_toggle(series_id):
    """Start or stop recording a series WITHOUT touching its history.

    This is the non-destructive counterpart to DELETE, and the one to reach for
    when a series should stop polling but its recorded history should stay
    readable.
    """
    try:
        data = request.get_json(silent=True) or {}
        if "enabled" not in data or not isinstance(data["enabled"], bool):
            return jsonify(
                {"status": "error", "message": "enabled must be provided as true or false"}
            ), 400

        success, message = gex_history_db.set_series_enabled(series_id, data["enabled"])
        if not success:
            return jsonify({"status": "error", "message": message}), 404

        _sync_recorder_jobs()
        return jsonify({"status": "success", "message": message}), 200

    except Exception as e:
        logger.exception(f"Error updating GEX series {series_id}: {e}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )


@gex_bp.route("/gex/api/gex-series/<int:series_id>", methods=["DELETE"])
@cross_origin()
@check_session_validity
def gex_series_remove(series_id):
    """Stop recording a series AND delete every snapshot recorded for it.

    Destructive, and irreversible: the option chain API returns only current OI
    and volume, so there is no source to backfill from. The success message says
    so explicitly rather than leaving the caller to discover it. Use PATCH
    `{"enabled": false}` to stop recording and keep the history.
    """
    try:
        success, message = gex_history_db.remove_series(series_id)
        if not success:
            return jsonify({"status": "error", "message": message}), 404

        _sync_recorder_jobs()
        return jsonify({"status": "success", "message": message}), 200

    except Exception as e:
        logger.exception(f"Error removing GEX series {series_id}: {e}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )
