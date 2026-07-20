"""Indicator alert engine — headless bar-close evaluation + delivery.

Owns a single APScheduler `BackgroundScheduler` (mirrors flow/historify:
one per-process singleton, its own jobstore table, started once — FD-safe).
A periodic sweep evaluates every active `IndicatorAlert`: it groups alerts by
symbol/exchange/timeframe, fetches history once per group, runs the compiled
OpenScript IR, and when a condition fires on a newly-closed bar it delivers via
Socket.IO + Telegram and advances the per-alert dedup watermark.

Constraints (CLAUDE.md): sync only under eventlet — no asyncio; reuse the shared
telegram ThreadPoolExecutor; emit via `socketio.start_background_task`; every DB
session opened in a job is closed. Only script-based alerts run server-side
(builtin.* indicators have no Python IR — deferred).
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from utils.logging import get_logger

logger = get_logger(__name__)

# How far back to pull for each timeframe (calendar days) — enough for warmup.
_LOOKBACK_DAYS = {
    "1m": 5, "3m": 10, "5m": 15, "15m": 30, "30m": 60,
    "1h": 120, "D": 400, "W": 1000, "M": 2000,
}
_SWEEP_SECONDS = int(os.getenv("INDICATOR_ALERT_SWEEP_SECONDS", "60"))

# Serialize evaluation per alert so a slow history fetch never overlaps itself.
_alert_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _alert_lock(alert_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _alert_locks.get(alert_id)
        if lock is None:
            lock = threading.Lock()
            _alert_locks[alert_id] = lock
        return lock


class IndicatorAlertScheduler:
    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._scheduler = None
                    inst._initialized = False
                    inst._socketio = None
                    cls._instance = inst
        return cls._instance

    def init(self, socketio=None) -> None:
        with self._init_lock:
            if self._initialized:
                if socketio is not None:
                    self._socketio = socketio
                return
            self._socketio = socketio
            db_url = os.getenv("DATABASE_URL", "sqlite:///db/openalgo.db")
            jobstores = {
                "default": SQLAlchemyJobStore(url=db_url, tablename="indicator_apscheduler_jobs")
            }
            self._scheduler = BackgroundScheduler(
                jobstores=jobstores,
                job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
            )
            self._scheduler.start()
            self._scheduler.add_job(
                _sweep_alerts_job,
                trigger=IntervalTrigger(seconds=_SWEEP_SECONDS),
                id="indicator_alert_sweep",
                replace_existing=True,
                name="Indicator alert sweep",
            )
            self._initialized = True
            logger.info(f"Indicator alert scheduler started (sweep every {_SWEEP_SECONDS}s)")

    @property
    def socketio(self):
        return self._socketio

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        self._initialized = False


indicator_alert_scheduler = IndicatorAlertScheduler()


def get_indicator_alert_scheduler() -> IndicatorAlertScheduler:
    return indicator_alert_scheduler


def init_indicator_alert_scheduler(socketio=None) -> IndicatorAlertScheduler:
    indicator_alert_scheduler.init(socketio=socketio)
    return indicator_alert_scheduler


# ── job (top-level so APScheduler can reference it) ──────────────────────────


def _lookback_days(timeframe: str) -> int:
    return _LOOKBACK_DAYS.get(timeframe, 30)


def _sweep_alerts_job() -> None:
    """Evaluate every active alert once; dedup + deliver newly-fired conditions."""
    from database.indicator_db import IndicatorAlert, db_session

    try:
        alerts = IndicatorAlert.query.filter_by(is_active=True).all()
        # detach the fields we need so the session can close before the (slow) work
        pending = [
            {
                "id": a.id,
                "user_id": a.user_id,
                "script_version_id": a.script_version_id,
                "builtin_id": a.builtin_id,
                "symbol": a.symbol,
                "exchange": a.exchange,
                "timeframe": a.timeframe,
                "condition_id": a.condition_id,
                "inputs": a.inputs_json or {},
                "last_evaluated_bar": a.last_evaluated_bar or 0,
            }
            for a in alerts
        ]
    except Exception:
        logger.exception("indicator alert sweep: failed to load alerts")
        return
    finally:
        db_session.remove()

    for alert in pending:
        if alert["builtin_id"] and not alert["script_version_id"]:
            continue  # builtin alerts have no server-side IR (deferred)
        lock = _alert_lock(alert["id"])
        if not lock.acquire(blocking=False):
            continue
        try:
            _evaluate_one(alert)
        except Exception as exc:
            logger.exception(f"indicator alert {alert['id']} evaluation failed: {exc}")
            _log_error(alert, "execute", str(exc))
        finally:
            lock.release()


def _evaluate_one(alert: dict) -> None:
    from datetime import timedelta

    from database.auth_db import get_api_key_for_tradingview
    from database.indicator_db import IndicatorScriptVersion, db_session
    from services.history_service import get_history

    from .execution_service import (
        bar_timestamps,
        find_alert_output,
        fired_on_last_bar,
        history_to_dataset,
    )
    from .runtime.budget import OperationBudget
    from .runtime.executor import execute_ir
    from .runtime.plancost import runtime_cost_ctx

    try:
        version = IndicatorScriptVersion.query.filter_by(id=alert["script_version_id"]).first()
        compiled_ir = version.compiled_ir if version else None
    finally:
        db_session.remove()
    if not compiled_ir:
        return  # source never compiled cleanly

    api_key = get_api_key_for_tradingview(alert["user_id"])
    if not api_key:
        return

    end = datetime.now()
    start = end - timedelta(days=_lookback_days(alert["timeframe"]))
    ok, resp, _ = get_history(
        alert["symbol"], alert["exchange"], alert["timeframe"],
        start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), api_key=api_key,
    )
    rows = (resp or {}).get("data") if ok else None
    if not rows:
        return

    dataset = history_to_dataset(rows)
    # OS4001/OS4002 weighted budget — same accounting as the TS worker (parity,
    # P1.7 + Phase 0.2 T6). Window lengths clamped to [min, max] in the runtime
    # ctx so charged <= admission estimate.
    cost_ctx = runtime_cost_ctx(compiled_ir, alert["inputs"], len(dataset["close"]))
    budget = OperationBudget(compiled_ir, cost_ctx)
    outputs = execute_ir(compiled_ir, dataset, alert["inputs"], budget=budget)
    alert_out = find_alert_output(outputs, alert["condition_id"])
    if alert_out is None:
        return

    timestamps = bar_timestamps(rows)
    last_ts = timestamps[-1] if timestamps else 0
    # Nothing new since we last evaluated this bar.
    if last_ts <= alert["last_evaluated_bar"]:
        return

    fired = fired_on_last_bar(alert_out, len(rows))
    _mark_evaluated(alert["id"], last_ts, fired)
    if fired:
        _deliver(alert, alert_out, last_ts)


def _deliver(alert: dict, alert_out: dict, bar_ts: int) -> None:
    title = alert_out.get("title") or alert["condition_id"]
    message = alert_out.get("message") or title
    text = (
        f"📢 Indicator Alert — {alert['symbol']} ({alert['exchange']} {alert['timeframe']})\n"
        f"{title}: {message}"
    )
    _emit_socket(alert, message, bar_ts)
    _send_telegram(alert, text)


def _emit_socket(alert: dict, message: str, bar_ts: int) -> None:
    try:
        from extensions import socketio

        socketio.start_background_task(
            socketio.emit,
            "indicator_alert",
            {
                "user_id": alert["user_id"],
                "symbol": alert["symbol"],
                "exchange": alert["exchange"],
                "timeframe": alert["timeframe"],
                "condition_id": alert["condition_id"],
                "message": message,
                "bar_time": bar_ts,
            },
        )
    except Exception:
        logger.exception("indicator_alert socket emit failed")


def _send_telegram(alert: dict, text: str) -> None:
    try:
        from database.telegram_db import get_telegram_user_by_username
        from services.telegram_alert_service import alert_executor, telegram_alert_service

        if not telegram_alert_service.is_bot_active():
            return
        tg = get_telegram_user_by_username(alert["user_id"])
        if not tg or not tg.get("notifications_enabled"):
            return
        alert_executor.submit(telegram_alert_service.send_alert_sync, tg["telegram_id"], text)
    except Exception:
        logger.exception("indicator_alert telegram send failed")


def _mark_evaluated(alert_id: int, bar_ts: int, fired: bool) -> None:
    from database.indicator_db import IndicatorAlert, db_session

    try:
        row = IndicatorAlert.query.filter_by(id=alert_id).first()
        if row:
            row.last_evaluated_bar = bar_ts
            if fired:
                row.last_triggered_at = datetime.now()
            db_session.commit()
    except Exception:
        db_session.rollback()
        logger.exception(f"failed to mark alert {alert_id} evaluated")
    finally:
        db_session.remove()


def _log_error(alert: dict, phase: str, message: str) -> None:
    from database.indicator_db import IndicatorExecutionError, db_session

    try:
        db_session.add(
            IndicatorExecutionError(
                user_id=alert["user_id"],
                script_version_id=alert.get("script_version_id"),
                symbol=alert.get("symbol"),
                timeframe=alert.get("timeframe"),
                phase=phase,
                message=message[:2000],
            )
        )
        db_session.commit()
    except Exception:
        db_session.rollback()
    finally:
        db_session.remove()
