"""
GEX Blueprint

Serves Gamma Exposure and OI Walls data.
Endpoints:
    POST   /gex/api/gex-data          - GEX data for all strikes (/gex Tools page)
    POST   /gex/api/gex-levels        - GEX Levels for the /charts study
    POST   /gex/api/gex-history       - Recorded levels through time (Gamma Bands)
    GET    /gex/api/gex-series        - The snapshot recorder's watchlist
    POST   /gex/api/gex-series        - Start recording a series
    PATCH  /gex/api/gex-series/<id>   - Enable or disable recording
    DELETE /gex/api/gex-series/<id>   - Stop recording AND drop its history

The watchlist routes are session-gated rather than API-key-gated: they decide
what the server polls the broker for on a schedule, which is an operator
decision, not something an external platform should be able to set.
"""

import re
import time

from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database import gex_history_db
from database.auth_db import get_api_key_for_tradingview
from services.gex_history_service import get_gex_history
from services.gex_levels_service import get_gex_levels
from services.gex_recorder_service import (
    CADENCE_SECONDS as RECORDER_CADENCE_SECONDS,
)
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

# Two cadence intervals. One missed tick must not force a broker round trip -
# that would undo the point of the recorder - while a recorder that is down must
# not freeze the study on stale numbers.
FAST_PATH_MAX_AGE_SECONDS = 120

# `get_snapshots_in_range` has no row limit of its own, so the ceiling lives
# here. At the recorder's one-per-minute cadence 20,000 points is about 53
# sessions - far more than any band a reader can resolve on screen, and still
# small enough to serialise. A wider window is refused by name rather than
# streamed: a year-wide request would otherwise build ~140,000 rows in memory
# and ship them to a browser that will draw them 3 pixels apart.
MAX_HISTORY_POINTS = 20_000

# The recorder's cadence, used only to turn a requested window into an upper
# bound on the point count. Imported rather than restated so the two cannot
# drift if the cadence is ever retuned.
_CADENCE_SECONDS = max(1, RECORDER_CADENCE_SECONDS)


def _recorded_payload(snapshot: dict, weight_by: str) -> dict:
    """Reshape one stored snapshot into the payload the study already renders.

    The frontend must have exactly ONE payload shape to handle, so `strikes`
    is rebuilt with the same keys the live path emits (`net_gex`, `net_dex`,
    ...) rather than the storage names, and every key the live payload carries
    is present here. A key this path omitted would read as `undefined` rather
    than failing - which is how the first implementation of this study shipped
    a chart with no bar column and a dashboard with two blank rows.

    The two GEX totals are summed from the strike rows rather than stored. They
    are derivable, and a stored total that disagreed with its own profile would
    be unfixable - the dashboard and the bars would tell the reader two
    different stories. `net_gex` is the stored figure, because that is what the
    levels and the regime were derived from.

    Args:
        snapshot: A row from `gex_history_db.get_latest_snapshot`, including its
            `strikes` list.
        weight_by: 'oi' or 'volume' - which stored column family to serve.

    Returns:
        The same payload shape `services.gex_levels_service.build_snapshot`
        produces, with `source: "recorded"`.

    Raises:
        KeyError: If the row is missing a column - e.g. written by an older
            schema. The caller treats that as "fall back to a live fetch".
    """
    # "oi" -> the _oi columns, "volume" -> the _vol columns.
    suffix = "oi" if weight_by == "oi" else "vol"

    strikes = [
        {
            "strike": row["strike"],
            "call_gex": row[f"call_gex_{suffix}"],
            "put_gex": row[f"put_gex_{suffix}"],
            "net_gex": row[f"net_gex_{suffix}"],
            "call_dex": row[f"call_dex_{suffix}"],
            "put_dex": row[f"put_dex_{suffix}"],
            "net_dex": row[f"net_dex_{suffix}"],
        }
        for row in snapshot["strikes"]
    ]

    return {
        "status": "success",
        "underlying": snapshot["underlying"],
        "exchange": snapshot["exchange"],
        "expiry_date": snapshot["expiry_date"],
        "weight_by": weight_by,
        "spot_price": snapshot["spot_price"],
        "forward_price": snapshot["forward_price"],
        "atm_strike": snapshot["atm_strike"],
        "lot_size": snapshot["lot_size"],
        # Recorded at write time, so it is up to two minutes stale. Over a
        # 120-second window that is immaterial to a figure quoted in days.
        "dte_days": snapshot["dte_days"],
        "interest_rate": snapshot["interest_rate"],
        "strikes": strikes,
        "total_call_gex": round(sum(s["call_gex"] for s in strikes), 2),
        "total_put_gex": round(sum(s["put_gex"] for s in strikes), 2),
        "call_wall": snapshot[f"call_wall_{suffix}"],
        "put_wall": snapshot[f"put_wall_{suffix}"],
        # Null is a real reading - "no local cross" - not missing data.
        "zero_gamma": snapshot[f"zero_gamma_{suffix}"],
        "net_gex": snapshot[f"net_gex_{suffix}"],
        "regime": snapshot[f"regime_{suffix}"],
        # The whole stored quality dict, `may_draw` included. Anything less and
        # the study would treat every recorded snapshot as undrawable.
        "quality": snapshot[f"quality_{suffix}"],
        "sentiment": snapshot[f"sentiment_{suffix}"],
        "source": "recorded",
        "as_of": snapshot["ts"],
    }


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

        # The recorded fast path, deliberately AFTER validation: a malformed
        # request must not be answered from history any more than from the
        # broker. Because a watchlisted series is already being polled once a
        # minute, N open tabs on the study now cost one broker call rather than
        # N - this deployment shares one broker session across up to five
        # devices.
        #
        # Wrapped so it can only ever SKIP the fast path. The recorded path is
        # an optimisation; a broken or absent gex.db, or a row written by an
        # older schema, must degrade the study to exactly the behaviour it had
        # before the recorder existed rather than take the study down. A series
        # nobody chose to record has no row at all and must still render.
        try:
            snapshot = gex_history_db.get_latest_snapshot(underlying, exchange, expiry_date)
            if snapshot and (int(time.time()) - snapshot["ts"]) < FAST_PATH_MAX_AGE_SECONDS:
                return jsonify(_recorded_payload(snapshot, weight_by)), 200
        except Exception:
            logger.exception("Recorded GEX fast path failed; falling back to a live broker fetch")

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


@gex_bp.route("/gex/api/gex-history", methods=["POST"])
@cross_origin()
@check_session_validity
def gex_history():
    """Recorded GEX levels for one contract over a time window.

    Backs Gamma Bands. Reads only what the recorder already wrote - this route
    can never cause a broker call, which is why the read service is a separate
    module from the recorder.
    """
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        data = request.get_json(silent=True) or {}
        underlying = str(data.get("underlying") or "").strip().upper()[:20]
        exchange = str(data.get("exchange") or "").strip().upper()[:20]
        expiry_date = str(data.get("expiry_date") or "").strip().upper()[:10]
        weight_by = str(data.get("weight_by") or "oi").strip().lower()[:10]
        fields = str(data.get("fields") or "levels").strip().lower()[:10]

        if not underlying or not exchange or not expiry_date:
            return jsonify(
                {
                    "status": "error",
                    "message": "underlying, exchange, and expiry_date are required",
                }
            ), 400

        if not re.match(r"^[A-Z0-9]+$", underlying) or not re.match(r"^[A-Z0-9_]+$", exchange):
            return jsonify({"status": "error", "message": "Invalid input format"}), 400

        # A RESOLVED contract, never a rule: "nearest" identifies no single book,
        # and history spliced across a roll would show wall jumps that are the
        # book changing rather than the market moving.
        if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_date):
            return jsonify(
                {
                    "status": "error",
                    "message": "Invalid expiry_date format. Expected a resolved DDMMMYY expiry",
                }
            ), 400

        try:
            from_ts = int(data.get("from_ts"))
            to_ts = int(data.get("to_ts"))
        except (TypeError, ValueError):
            return jsonify(
                {"status": "error", "message": "from_ts and to_ts must be epoch seconds"}
            ), 400

        if from_ts < 0 or to_ts < 0:
            return jsonify(
                {"status": "error", "message": "from_ts and to_ts must be epoch seconds"}
            ), 400

        if from_ts > to_ts:
            return jsonify({"status": "error", "message": "from_ts must not be after to_ts"}), 400

        # Bounded here rather than in the query, and by the WINDOW rather than by
        # truncating the result: silently returning the first N points of a wider
        # window would draw a band that simply stops, which reads as the market
        # going quiet. Refusing by name tells the caller to narrow it instead.
        if (to_ts - from_ts) // _CADENCE_SECONDS > MAX_HISTORY_POINTS:
            return jsonify(
                {
                    "status": "error",
                    "message": (
                        f"Requested window is too wide; it could hold more than "
                        f"{MAX_HISTORY_POINTS} recorded points. Narrow the range."
                    ),
                }
            ), 400

        success, response, status_code = get_gex_history(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            weight_by=weight_by,
            from_ts=from_ts,
            to_ts=to_ts,
            fields=fields,
        )

        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error in GEX History API: {e}")
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
