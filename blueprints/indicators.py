# blueprints/indicators.py
"""Indicator engine UI APIs (session + CSRF auth): chart layout persistence.

Script CRUD / compile / alert endpoints land with the OpenScript server
runtime (architecture doc §15); layouts ship first so /charts and /trading
state survives reloads across devices.
"""

from flask import Blueprint, jsonify, request, session

from database.indicator_db import ChartLayout, db_session
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
