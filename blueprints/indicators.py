# blueprints/indicators.py
"""Indicator engine UI APIs (session + CSRF auth): OpenScript script CRUD with
immutable versioning, and chart layout persistence (architecture doc §15/§16).

Scripts store source in immutable versions; every source change appends a new
version and moves the script's current pointer. Every version is compiled
server-side by the Python OpenScript port and stored with its own `compiled_ir`,
`source_hash` and diagnostics — client-submitted IR is never trusted.

That stored IR is the artifact a saved indicator is rebuilt from: the fetch
routes return it verbatim so a reopened chart never recompiles in the browser.
It can legitimately be null (a source the server could not compile, e.g. one
using `request.security`, which the TS front end supports and the Python port
does not), so every consumer must handle that rather than assume an IR is
present. Alert endpoints land with the headless execution engine.
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
from services.openscript.compiler_service import COMPILER_FINGERPRINT, compile_source
from services.openscript.freshness import freshness
from services.openscript.runtime.admit import admit_ir
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

indicators_bp = Blueprint("indicators_bp", __name__, url_prefix="/indicators/api")


def _user() -> str:
    return session.get("user")


@indicators_bp.route("/openscript/freshness", methods=["GET"])
@check_session_validity
def openscript_freshness():
    """Is this process running the OpenScript code currently on disk? (trap T2)

    `FLASK_DEBUG=False` has no reloader, so a change to the Python service does
    not reach the server until `app.py` restarts, and the symptom is not an error
    but results that quietly disagree with the source being read. Each subtree's
    fingerprint is bound once at import; this recomputes from disk and compares,
    so the answer is exact rather than inferred.

    Always 200, including when stale: "the process is out of date" is a
    successful answer to the question, not a server error, and a non-200 would
    make callers treat a working diagnostic as a broken endpoint.
    """
    return jsonify(freshness())


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
    """Serialize a script, optionally with its full authoritative artifact.

    `include_source` controls the heavy half. Without it the caller gets
    identity only, which is what the picker needs — it lists every saved script,
    and attaching each one's source and IR would grow that response without
    bound. With it the caller gets everything needed to rebuild the indicator
    without compiling anything: the immutable `version_id`, the canonical
    `source_hash`, and the server's own `compiled_ir`.

    `compiled_ir` is read straight out of storage and is never recomputed here.
    That is the load-bearing property of the reopen contract: an artifact the
    runtime can run comes back exactly as it was stored. The one exception lives
    in `_ensure_runnable_ir`, which the fetch routes call BEFORE serializing —
    it repairs IR the runtime would REFUSE, and leaves everything else alone. A
    version whose source the server cannot compile still reads back as null, so
    the client reports it rather than having it papered over.
    """
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
        row["version_id"] = version.id
        row["version_number"] = version.version_number
        row["source_hash"] = version.source_hash
        row["compiler_version"] = version.compiler_version
        if include_source:
            row["source"] = version.source_code
            row["compiled_ir"] = version.compiled_ir
            meta = version.metadata_json or {}
            row["diagnostics"] = meta.get("diagnostics", [])
    return row


def _staleness_reason(version: IndicatorScriptVersion) -> str | None:
    """Why this version's stored IR needs rebuilding, or None to leave it alone.

    Two independent triggers, and the distinction matters:

    - ``inadmissible`` — the RUNTIME WOULD REFUSE it (P1). IR compiled before the
      negotiation header existed has no ``header`` key, so admission fails with
      IR_MAJOR_MISMATCH and the indicator can be neither added nor restored.
      ``compiled_ir`` NULL is the same problem in its extreme form.
    - ``compiler-changed`` — it still admits, but a DIFFERENT compiler build
      produced it (P2). This is the case the old repair could not see: a version
      whose IR is merely stale had no user-facing route to be refreshed at all,
      because re-saving unchanged source is a no-op. A lowering fix therefore
      never reached anything already saved.

    The P2 gate is a fingerprint EQUALITY check, not a diff against a fresh
    compile: comparing IR would mean compiling on every fetch of every version.
    `compiler_version` cannot serve — it is frozen per language revision
    ("openscript-1.0") and does not move when a lowering changes.

    A version carrying no fingerprint is the pre-feature corpus, and is stale by
    definition: it was built by an unknown compiler.

    **Contract note.** `test_a_usable_stored_ir_is_never_recompiled` still holds
    in the form that matters — an admissible artifact from THE CURRENT COMPILER
    is returned untouched, so hand-stored IR is not silently replaced and a
    layout cannot drift under the user while the compiler stands still. What
    changed is that "never" is now scoped to one compiler build instead of
    forever.
    """
    # `admit_ir` returns the REASONS the runtime would refuse; empty = admitted.
    if version.compiled_ir is None or admit_ir(version.compiled_ir):
        return "inadmissible"
    stored = (version.metadata_json or {}).get("compiler_fingerprint")
    if stored != COMPILER_FINGERPRINT:
        return "compiler-changed"
    return None


def _ensure_runnable_ir(version: IndicatorScriptVersion | None) -> None:
    """Repair a stored IR the runtime would refuse, from the version's OWN source.

    Versions are immutable in the sense that matters — the SOURCE never changes,
    so `source_hash`, `version_id` and `version_number` are untouched here and a
    layout pinned to this version keeps resolving to it. What can go stale is the
    derived artifact: IR compiled before the negotiation header existed (Python
    ir-gen gained `header` in 34977a88c) has no `header` key, so admission
    rejects it with IR_MAJOR_MISMATCH and the indicator cannot be added OR
    restored. `compiled_ir` NULL — stored before the server compiled at all — is
    the same problem in its extreme form.

    The staleness test is the admission gate itself, so the criterion is exactly
    "the runtime would refuse this" rather than a hand-maintained list of
    compiler generations. A recompile that does not produce an admissible IR is
    discarded: a source the server genuinely cannot compile must keep reading
    back as null so the client reports it, rather than being papered over.
    """
    if version is None:
        return
    reason = _staleness_reason(version)
    if reason is None:
        return
    compiled = compile_source(version.source_code)
    if compiled["ir"] is None or admit_ir(compiled["ir"]):
        # The recompile is unusable. NEVER make a saved indicator worse than it
        # was: whatever is stored stays, and the fingerprint is deliberately NOT
        # stamped, so this retries on a later compiler rather than recording a
        # lie about which build produced the artifact. Reachable in the ordinary
        # course — a compiler that tightens (an accepted construct becoming an
        # error) turns a previously-saved source into one that no longer builds.
        logger.warning(
            f"Stale IR for version {version.id} (script {version.script_id}, {reason}) "
            f"could not be refreshed: its source no longer compiles. Keeping the stored IR."
        )
        return
    logger.info(
        f"Recompiled stale IR for version {version.id} "
        f"(script {version.script_id}, compiler {version.compiler_version})"
    )
    version.compiled_ir = compiled["ir"]
    version.compiler_version = compiled["compiler_version"]
    meta = dict(version.metadata_json or {})
    meta["diagnostics"] = compiled["diagnostics"]
    # Without this the version would be refreshed on EVERY fetch forever.
    meta["compiler_fingerprint"] = compiled["compiler_fingerprint"]
    version.metadata_json = meta
    db_session.commit()


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
        metadata_json={
            "diagnostics": compiled["diagnostics"],
            # Which compiler BUILD produced this artifact (finding P2). Recorded
            # in metadata rather than a column so the existing corpus needs no
            # migration: a row without the key is simply treated as stale.
            "compiler_fingerprint": compiled["compiler_fingerprint"],
        },
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
        version = _current_version(script)
        _ensure_runnable_ir(version)
        return jsonify(
            {"status": "success", "data": _script_row(script, version, include_source=True)}
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
        _ensure_runnable_ir(version)
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
                    # A layout pinned to a specific version restores through
                    # this route, so it serves the authoritative IR too — from
                    # storage, never a recompile.
                    "compiled_ir": version.compiled_ir,
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
