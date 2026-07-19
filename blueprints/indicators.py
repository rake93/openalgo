# blueprints/indicators.py
"""Indicator engine UI APIs (session + CSRF auth): OpenScript script CRUD with
immutable versioning, and chart layout persistence (architecture doc §15/§16).

Scripts store source in immutable versions; every source change appends a new
version and moves the script's current pointer. Server-side compilation of the
stored source (the Python OpenScript port) lands next — until then `compiled_ir`
is left null and the browser compiles for preview only. Alert endpoints land
with the headless execution engine.
"""

import hashlib

from flask import Blueprint, jsonify, request, session

from database.indicator_db import (
    ChartLayout,
    IndicatorAlert,
    IndicatorScript,
    IndicatorScriptVersion,
    db_session,
)
from services.openscript.compiler_service import compile_source
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

indicators_bp = Blueprint("indicators_bp", __name__, url_prefix="/indicators/api")


def _user() -> str:
    return session.get("user")


def _layout_row(layout: ChartLayout) -> dict:
    return {
        "id": layout.id,
        "name": layout.name,
        "symbol": layout.symbol,
        "exchange": layout.exchange,
        "timeframe": layout.timeframe,
        "layout": layout.layout_json,
        "updated_at": layout.updated_at.isoformat() if layout.updated_at else None,
    }


@indicators_bp.route("/layouts", methods=["GET"])
@check_session_validity
def list_layouts():
    try:
        rows = (
            ChartLayout.query.filter_by(user_id=_user())
            .order_by(ChartLayout.updated_at.desc())
            .all()
        )
        return jsonify({"status": "success", "data": [_layout_row(r) for r in rows]})
    except Exception as e:
        logger.exception(f"Error listing chart layouts: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/layouts", methods=["POST"])
@check_session_validity
def create_layout():
    try:
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "name is required"}), 400
        layout = ChartLayout(
            user_id=_user(),
            name=name,
            symbol=data.get("symbol"),
            exchange=data.get("exchange"),
            timeframe=data.get("timeframe"),
            layout_json=data.get("layout") or {},
        )
        db_session.add(layout)
        db_session.commit()
        return jsonify({"status": "success", "data": _layout_row(layout)}), 201
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error creating chart layout: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/layouts/<int:layout_id>", methods=["PUT"])
@check_session_validity
def update_layout(layout_id: int):
    try:
        layout = ChartLayout.query.filter_by(id=layout_id, user_id=_user()).first()
        if not layout:
            return jsonify({"status": "error", "message": "layout not found"}), 404
        data = request.get_json(force=True) or {}
        if "name" in data and (data["name"] or "").strip():
            layout.name = data["name"].strip()
        for field in ("symbol", "exchange", "timeframe"):
            if field in data:
                setattr(layout, field, data[field])
        if "layout" in data:
            layout.layout_json = data["layout"] or {}
        db_session.commit()
        return jsonify({"status": "success", "data": _layout_row(layout)})
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error updating chart layout: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/layouts/<int:layout_id>", methods=["DELETE"])
@check_session_validity
def delete_layout(layout_id: int):
    try:
        layout = ChartLayout.query.filter_by(id=layout_id, user_id=_user()).first()
        if not layout:
            return jsonify({"status": "error", "message": "layout not found"}), 404
        db_session.delete(layout)
        db_session.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error deleting chart layout: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


# ── OpenScript scripts (immutable versioning) ────────────────────────────────


def _current_version(script: IndicatorScript) -> IndicatorScriptVersion | None:
    if not script.current_version_id:
        return None
    return IndicatorScriptVersion.query.filter_by(id=script.current_version_id).first()


def _script_row(
    script: IndicatorScript,
    version: IndicatorScriptVersion | None = None,
    include_source: bool = False,
) -> dict:
    row = {
        "id": script.id,
        "name": script.name,
        "description": script.description,
        "language": script.language,
        "visibility": script.visibility,
        "current_version_id": script.current_version_id,
        "updated_at": script.updated_at.isoformat() if script.updated_at else None,
        "created_at": script.created_at.isoformat() if script.created_at else None,
    }
    if version is not None:
        row["version_number"] = version.version_number
        if include_source:
            row["source"] = version.source_code
            meta = version.metadata_json or {}
            row["diagnostics"] = meta.get("diagnostics", [])
    return row


def _new_version(script_id: int, source: str, version_number: int) -> IndicatorScriptVersion:
    """Build an immutable version, compiling the source server-side (the server
    never trusts client IR — it stores its own compiled IR + diagnostics)."""
    compiled = compile_source(source)
    return IndicatorScriptVersion(
        script_id=script_id,
        version_number=version_number,
        source_code=source,
        source_hash=compiled["source_hash"],
        compiler_version=compiled["compiler_version"],
        compiled_ir=compiled["ir"],
        metadata_json={"diagnostics": compiled["diagnostics"]},
    )


@indicators_bp.route("/scripts", methods=["GET"])
@check_session_validity
def list_scripts():
    try:
        rows = (
            IndicatorScript.query.filter_by(user_id=_user())
            .order_by(IndicatorScript.updated_at.desc())
            .all()
        )
        return jsonify({"status": "success", "data": [_script_row(s) for s in rows]})
    except Exception as e:
        logger.exception(f"Error listing scripts: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/scripts/<int:script_id>", methods=["GET"])
@check_session_validity
def get_script(script_id: int):
    try:
        script = IndicatorScript.query.filter_by(id=script_id, user_id=_user()).first()
        if not script:
            return jsonify({"status": "error", "message": "script not found"}), 404
        return jsonify(
            {"status": "success", "data": _script_row(script, _current_version(script), include_source=True)}
        )
    except Exception as e:
        logger.exception(f"Error fetching script {script_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/scripts", methods=["POST"])
@check_session_validity
def create_script():
    try:
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "name is required"}), 400
        script = IndicatorScript(
            user_id=_user(),
            name=name,
            description=data.get("description"),
            language="openscript",
        )
        db_session.add(script)
        db_session.flush()  # assign script.id for the version FK
        version = _new_version(script.id, data.get("source") or "", 1)
        db_session.add(version)
        db_session.flush()  # assign version.id for the current pointer
        script.current_version_id = version.id
        db_session.commit()
        return jsonify(
            {"status": "success", "data": _script_row(script, version, include_source=True)}
        ), 201
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error creating script: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/scripts/<int:script_id>", methods=["PUT"])
@check_session_validity
def update_script(script_id: int):
    try:
        script = IndicatorScript.query.filter_by(id=script_id, user_id=_user()).first()
        if not script:
            return jsonify({"status": "error", "message": "script not found"}), 404
        data = request.get_json(force=True) or {}
        if "name" in data and (data["name"] or "").strip():
            script.name = data["name"].strip()
        if "description" in data:
            script.description = data["description"]
        version = _current_version(script)
        if "source" in data:
            source = data["source"] or ""
            new_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            # Only append a version when the source actually changed.
            if version is None or version.source_hash != new_hash:
                next_number = (version.version_number + 1) if version else 1
                version = _new_version(script.id, source, next_number)
                db_session.add(version)
                db_session.flush()
                script.current_version_id = version.id
        db_session.commit()
        return jsonify(
            {"status": "success", "data": _script_row(script, version, include_source=True)}
        )
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error updating script {script_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/scripts/<int:script_id>", methods=["DELETE"])
@check_session_validity
def delete_script(script_id: int):
    try:
        script = IndicatorScript.query.filter_by(id=script_id, user_id=_user()).first()
        if not script:
            return jsonify({"status": "error", "message": "script not found"}), 404
        db_session.delete(script)  # cascades to versions
        db_session.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error deleting script {script_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/scripts/<int:script_id>/versions", methods=["GET"])
@check_session_validity
def list_script_versions(script_id: int):
    try:
        script = IndicatorScript.query.filter_by(id=script_id, user_id=_user()).first()
        if not script:
            return jsonify({"status": "error", "message": "script not found"}), 404
        versions = (
            IndicatorScriptVersion.query.filter_by(script_id=script.id)
            .order_by(IndicatorScriptVersion.version_number.desc())
            .all()
        )
        return jsonify(
            {
                "status": "success",
                "data": [
                    {
                        "id": v.id,
                        "version_number": v.version_number,
                        "source_hash": v.source_hash,
                        "compiler_version": v.compiler_version,
                        "created_at": v.created_at.isoformat() if v.created_at else None,
                        "is_current": v.id == script.current_version_id,
                    }
                    for v in versions
                ],
            }
        )
    except Exception as e:
        logger.exception(f"Error listing versions for script {script_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/scripts/<int:script_id>/versions/<int:version_id>", methods=["GET"])
@check_session_validity
def get_script_version(script_id: int, version_id: int):
    """Fetch a single immutable version *with its source* — the list endpoint
    omits source to stay lightweight, so the version-history panel loads a
    version's code (to preview / restore into the editor) through here."""
    try:
        script = IndicatorScript.query.filter_by(id=script_id, user_id=_user()).first()
        if not script:
            return jsonify({"status": "error", "message": "script not found"}), 404
        version = IndicatorScriptVersion.query.filter_by(
            id=version_id, script_id=script.id
        ).first()
        if not version:
            return jsonify({"status": "error", "message": "version not found"}), 404
        meta = version.metadata_json or {}
        return jsonify(
            {
                "status": "success",
                "data": {
                    "id": version.id,
                    "version_number": version.version_number,
                    "source_code": version.source_code,
                    "source_hash": version.source_hash,
                    "compiler_version": version.compiler_version,
                    "created_at": version.created_at.isoformat() if version.created_at else None,
                    "is_current": version.id == script.current_version_id,
                    "diagnostics": meta.get("diagnostics", []),
                },
            }
        )
    except Exception as e:
        logger.exception(f"Error fetching version {version_id} of script {script_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


# ── Indicator alerts (headless bar-close evaluation) ─────────────────────────


def _alert_row(alert: IndicatorAlert) -> dict:
    return {
        "id": alert.id,
        "script_version_id": alert.script_version_id,
        "builtin_id": alert.builtin_id,
        "symbol": alert.symbol,
        "exchange": alert.exchange,
        "timeframe": alert.timeframe,
        "condition_id": alert.condition_id,
        "inputs": alert.inputs_json or {},
        "trigger_mode": alert.trigger_mode,
        "is_active": alert.is_active,
        "last_evaluated_bar": alert.last_evaluated_bar,
        "last_triggered_at": alert.last_triggered_at.isoformat() if alert.last_triggered_at else None,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


def _owns_version(user_id: str, version_id: int) -> bool:
    row = (
        db_session.query(IndicatorScriptVersion)
        .join(IndicatorScript, IndicatorScriptVersion.script_id == IndicatorScript.id)
        .filter(IndicatorScriptVersion.id == version_id, IndicatorScript.user_id == user_id)
        .first()
    )
    return row is not None


@indicators_bp.route("/alerts", methods=["GET"])
@check_session_validity
def list_alerts():
    try:
        rows = (
            IndicatorAlert.query.filter_by(user_id=_user())
            .order_by(IndicatorAlert.updated_at.desc())
            .all()
        )
        return jsonify({"status": "success", "data": [_alert_row(a) for a in rows]})
    except Exception as e:
        logger.exception(f"Error listing alerts: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/alerts", methods=["POST"])
@check_session_validity
def create_alert():
    try:
        data = request.get_json(force=True) or {}
        required = ("symbol", "exchange", "timeframe", "condition_id")
        missing = [f for f in required if not (data.get(f) or "").strip()]
        if missing:
            return jsonify({"status": "error", "message": f"missing: {', '.join(missing)}"}), 400
        script_version_id = data.get("script_version_id")
        builtin_id = data.get("builtin_id")
        if not script_version_id and not builtin_id:
            return jsonify({"status": "error", "message": "script_version_id or builtin_id is required"}), 400
        if script_version_id and not _owns_version(_user(), script_version_id):
            return jsonify({"status": "error", "message": "unknown script version"}), 404
        alert = IndicatorAlert(
            user_id=_user(),
            script_version_id=script_version_id,
            builtin_id=builtin_id,
            symbol=data["symbol"].strip(),
            exchange=data["exchange"].strip(),
            timeframe=data["timeframe"].strip(),
            condition_id=data["condition_id"].strip(),
            inputs_json=data.get("inputs") or {},
            trigger_mode=data.get("trigger_mode") or "bar-close",
            is_active=bool(data.get("is_active", True)),
        )
        db_session.add(alert)
        db_session.commit()
        return jsonify({"status": "success", "data": _alert_row(alert)}), 201
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error creating alert: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/alerts/<int:alert_id>", methods=["PUT"])
@check_session_validity
def update_alert(alert_id: int):
    try:
        alert = IndicatorAlert.query.filter_by(id=alert_id, user_id=_user()).first()
        if not alert:
            return jsonify({"status": "error", "message": "alert not found"}), 404
        data = request.get_json(force=True) or {}
        if "is_active" in data:
            alert.is_active = bool(data["is_active"])
        if "inputs" in data:
            alert.inputs_json = data["inputs"] or {}
        for field in ("symbol", "exchange", "timeframe", "condition_id", "trigger_mode"):
            if field in data and (data[field] or "").strip():
                setattr(alert, field, data[field].strip())
        db_session.commit()
        return jsonify({"status": "success", "data": _alert_row(alert)})
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error updating alert {alert_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()


@indicators_bp.route("/alerts/<int:alert_id>", methods=["DELETE"])
@check_session_validity
def delete_alert(alert_id: int):
    try:
        alert = IndicatorAlert.query.filter_by(id=alert_id, user_id=_user()).first()
        if not alert:
            return jsonify({"status": "error", "message": "alert not found"}), 404
        db_session.delete(alert)
        db_session.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db_session.rollback()
        logger.exception(f"Error deleting alert {alert_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db_session.remove()
