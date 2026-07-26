"""Task 9 (G7): the alert sweep resolves the instrument's calendar before executing.

`_evaluate_one` (services/openscript/alert_service.py) already fetches history for
`alert['symbol']`/`alert['exchange']` before this task, but its `execute_ir` call
passed no `calendar` kwarg -- silently taking `executor.execute_ir`'s IST default,
which the executor itself documents as forbidden for any production path. That
means a CRYPTO alert (Delta Exchange, 24/7 trading) evaluated on an IST midnight
day boundary that matches no market event on that instrument.

This test proves the fix: `_evaluate_one` now resolves `calendar_for_instrument`
from the alert's own symbol/exchange and passes the result into `execute_ir`, so a
CRYPTO alert runs under `UTC_CALENDAR` and an NSE alert runs under `IST_CALENDAR`.

`_evaluate_one` imports `execute_ir`, `get_history`, the DB session and the
compiler-support modules LOCALLY inside the function body (see the module-level
docstring note in alert_service.py about the plancost<->ir_gen circular import).
Every patch target below is therefore the ORIGIN module the local `from X import Y`
pulls from at call time -- patching `services.openscript.alert_service.execute_ir`
would do nothing, since that name is never bound at module scope.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

# Compiler package first, exactly like every sibling openscript test --
# `runtime.plancost` and `openscript.ir_gen` import each other, and entering the
# import graph from `runtime.*` first raises a partially-initialized-module
# ImportError. Pre-existing fragility, not introduced here.
from services.openscript import openscript  # noqa: F401
from services.openscript.alert_service import _evaluate_one
from services.openscript.runtime.calendar import IST_CALENDAR, UTC_CALENDAR

# Eagerly imported (module level, at collection time) rather than left for
# `unittest.mock.patch`'s dotted-path lookup to import lazily inside the test:
# `runtime.executor` pulls in `ta_dispatch`, which does `from openalgo import
# ta`. This repo's own editable install and the PyPI `openalgo` SDK dependency
# share a top-level import name, and pytest mutates sys.path between collection
# and test execution such that a FIRST import of `openalgo` attempted lazily
# during a test body resolves to this repo's own root package instead of the
# SDK, raising `ImportError: cannot import name 'ta' from 'openalgo'`.
# Importing `execute_ir` here, at collection time (before that sys.path
# mutation happens), caches the correct module in `sys.modules` first, exactly
# as `test_openscript_calendar.py` already does. Pre-existing environment
# fragility, not introduced by this test.
from services.openscript.runtime.executor import execute_ir  # noqa: F401

_ROWS = [
    {"timestamp": 1_700_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
    {"timestamp": 1_700_003_600, "open": 1.5, "high": 2.5, "low": 1, "close": 2, "volume": 20},
]


def _alert(exchange: str) -> dict:
    """A minimal pending-alert dict shaped like `_sweep_alerts_job`'s detached rows."""
    return {
        "id": 1,
        "user_id": "u1",
        "script_version_id": 7,
        "builtin_id": None,
        "symbol": "SOME-SYMBOL",
        "exchange": exchange,
        "timeframe": "5m",
        "condition_id": "cond-1",
        "inputs": {},
        "last_evaluated_bar": 0,
    }


def _run_and_capture_calendar(exchange: str):
    """Drive `_evaluate_one` with every DB/history/compiler-support call stubbed at
    its origin module, and return the `calendar` kwarg `execute_ir` was actually
    called with (or None if `execute_ir` was never reached).
    """
    alert = _alert(exchange)

    fake_version = MagicMock()
    fake_version.compiled_ir = {"nodes": [], "inputs": [], "outputs": []}

    fake_script_version_cls = MagicMock()
    fake_script_version_cls.query.filter_by.return_value.first.return_value = fake_version

    fake_db_session = MagicMock()

    with (
        patch("database.indicator_db.IndicatorScriptVersion", fake_script_version_cls),
        patch("database.indicator_db.db_session", fake_db_session),
        patch("database.auth_db.get_api_key_for_tradingview", return_value="fake-api-key"),
        patch(
            "services.history_service.get_history",
            return_value=(True, {"data": _ROWS}, 200),
        ),
        patch("services.openscript.runtime.plancost.runtime_cost_ctx", return_value={}),
        patch("services.openscript.runtime.budget.OperationBudget", return_value=MagicMock()),
        patch(
            "services.openscript.runtime.executor.execute_ir", return_value=[]
        ) as execute_ir_mock,
    ):
        _evaluate_one(alert)

    assert execute_ir_mock.called, (
        "execute_ir was never reached -- an earlier guard clause in _evaluate_one "
        "returned before the executor call, so this test is not exercising the "
        "calendar-resolution path at all"
    )
    return execute_ir_mock.call_args.kwargs.get("calendar")


def test_crypto_alert_evaluates_under_utc_calendar():
    """A CRYPTO instrument (Delta Exchange, 24/7) must resolve to UTC_CALENDAR,
    not the executor's IST default -- this is G7's actual behaviour change.
    """
    calendar = _run_and_capture_calendar("CRYPTO")
    assert calendar is UTC_CALENDAR


def test_nse_alert_evaluates_under_ist_calendar():
    """An NSE alert must still resolve to IST_CALENDAR -- proving the value
    reaching `execute_ir` is genuinely *resolved* per-instrument rather than a
    constant that happens to equal the IST default for every alert.
    """
    calendar = _run_and_capture_calendar("NSE")
    assert calendar is IST_CALENDAR


def test_unmapped_exchange_logs_a_warning_naming_the_fallback(caplog):
    """A fallback resolution must not be silent server-side: the alert sweep has
    no UI to surface `CalendarResolution.warning_code` the way the chart host
    does, so `_evaluate_one` logs it instead. The alert still evaluates (under
    the IST fallback) -- logging is additive, not a new failure mode.
    """
    with caplog.at_level(logging.WARNING, logger="services.openscript.alert_service"):
        calendar = _run_and_capture_calendar("SOME_MADE_UP_EXCHANGE")

    assert calendar is IST_CALENDAR
    assert any("CALENDAR_FALLBACK_UNKNOWN_EXCHANGE" in r.message for r in caplog.records)
