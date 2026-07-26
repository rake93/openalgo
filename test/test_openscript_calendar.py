"""G7 calendar parity: the Python resolver and context fields must match TypeScript.

The exchange table is replayed from a copy of the SAME fixture the TS suite reads, so
the two resolvers cannot drift. The context assertions cover both boundaries: IST
midnight falls at 18:30 UTC and UTC midnight falls mid-IST-day, so a test that checked
only one would pass against an offset applied in the wrong direction.
"""

import dataclasses
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

# The compiler package must be imported before `runtime.executor`, exactly as every
# sibling openscript test does. `runtime.plancost` and `openscript.ir_gen` import each
# other, so whichever side is entered first wins; entering from `runtime` raises a
# partially-initialized-module ImportError. Pre-existing fragility, not introduced here.
from services.openscript import openscript  # noqa: F401
from services.openscript.runtime.calendar import (
    IST_CALENDAR,
    UTC_CALENDAR,
    calendar_for_instrument,
    local_day_key,
)
from services.openscript.runtime.executor import execute_ir

# The platform carries its OWN committed copy so the parity guard cannot vanish on a
# machine where the engine repo is not checked out beside it (platform CI, Docker, a
# client box). The engine copy stays authoritative: when the sibling IS present, the
# drift test below FAILS if the two differ, which is what keeps the copy honest.
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "calendar-exchange-resolution.json"
ENGINE_FIXTURE = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "calendar"
    / "exchange-resolution.json"
)


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def test_the_local_fixture_copy_matches_the_engine_original():
    """The platform's committed copy must not drift from the engine's authoritative one.

    Skips only when the engine repo is genuinely absent, and the replay below still
    runs off the local copy, so no coverage is lost when it skips.
    """
    if not ENGINE_FIXTURE.exists():
        pytest.skip("engine repo not beside the platform; the local copy is replayed anyway")
    # Parsed equality catches any semantic drift; the normalized-text comparison also
    # catches comment/ordering churn, since this file is meant to be a byte-for-byte
    # copy (line endings normalized so a git autocrlf checkout cannot fail it).
    assert json.loads(FIXTURE.read_text()) == json.loads(ENGINE_FIXTURE.read_text())
    local_text = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
    engine_text = ENGINE_FIXTURE.read_bytes().replace(b"\r\n", b"\n")
    assert local_text == engine_text


def test_the_fixture_case_count_is_exact():
    """A count assertion, so silently deleting rows cannot quietly drop assertions."""
    assert len(json.loads(FIXTURE.read_text())["cases"]) == 20


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text())["cases"])
def test_exchange_resolution_matches_the_shared_fixture(case):
    r = calendar_for_instrument(exchange=case["exchange"], symbol=case["symbol"])
    assert r.calendar.utc_offset_seconds == case["utcOffsetSeconds"]
    assert r.calendar.semantic_key == case["semanticKey"]
    assert r.semantic_key == case["semanticKey"]
    assert r.provenance == case["provenance"]
    assert r.normalized_exchange == case["normalizedExchange"]
    assert r.warning_code == case["warningCode"]


def test_global_index_is_deferred_not_unknown():
    """GLOBAL_INDEX mixes zones and needs a tz database: deferred, never unknown."""
    r = calendar_for_instrument(exchange="GLOBAL_INDEX", symbol="US30")
    assert r.provenance == "deferred-per-symbol"
    assert r.warning_code == "CALENDAR_DEFERRED_PER_SYMBOL"
    assert r.calendar.utc_offset_seconds == 19800


def test_resolver_never_raises_on_an_unknown_exchange():
    assert calendar_for_instrument(exchange="MOONBASE", symbol="X").provenance == "fallback-unknown"


def test_a_mapped_resolution_carries_no_warning_code():
    assert calendar_for_instrument(exchange="NSE", symbol="SBIN").warning_code is None


def test_a_non_string_exchange_classifies_as_missing_rather_than_raising():
    """The TS twin takes this value from a JavaScript host; a non-str must not raise."""
    r = calendar_for_instrument(exchange=5, symbol="X")
    assert r.provenance == "fallback-missing"
    assert r.warning_code == "CALENDAR_FALLBACK_MISSING_EXCHANGE"
    assert r.normalized_exchange == ""


def test_a_bom_prefixed_exchange_still_maps():
    """A UTF-8 BOM surviving a CSV master-contract import must not read as unknown.

    ES `trim()` counts U+FEFF as whitespace and Python's `str.strip()` does not, so
    without an explicit strip TypeScript would map this and Python would classify it
    `fallback-unknown` -- a divergence no fixture row can catch.
    """
    r = calendar_for_instrument(exchange="\ufeffNSE", symbol="SBIN")
    assert r.provenance == "mapped"
    assert r.normalized_exchange == "NSE"
    assert r.warning_code is None
    # Combined with ordinary whitespace, and on the trailing edge.
    assert calendar_for_instrument(exchange=" \ufeffcrypto ", symbol="BTCUSDT").provenance == (
        "mapped"
    )
    assert calendar_for_instrument(exchange="NSE\ufeff", symbol="SBIN").normalized_exchange == "NSE"


def test_a_calendar_cannot_be_mutated():
    """A calendar whose offset drifted from its semantic_key would alias a cache entry."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        IST_CALENDAR.utc_offset_seconds = 0


# -- local_day_key -----------------------------------------------------------------


def test_local_day_key_at_both_boundaries():
    before_ist = _epoch("2026-07-26T18:29:59Z")
    after_ist = _epoch("2026-07-26T18:30:00Z")
    assert local_day_key(after_ist, IST_CALENDAR) == local_day_key(before_ist, IST_CALENDAR) + 1
    assert local_day_key(after_ist, UTC_CALENDAR) == local_day_key(before_ist, UTC_CALENDAR)

    before_utc = _epoch("2026-07-26T23:59:59Z")
    after_utc = _epoch("2026-07-27T00:00:00Z")
    assert local_day_key(after_utc, UTC_CALENDAR) == local_day_key(before_utc, UTC_CALENDAR) + 1
    assert local_day_key(after_utc, IST_CALENDAR) == local_day_key(before_utc, IST_CALENDAR)


def test_local_day_key_floors_for_pre_epoch():
    assert local_day_key(-1, UTC_CALENDAR) == -1
    assert local_day_key(0, UTC_CALENDAR) == 0


def test_local_day_key_floors_fractional_negatives_like_typescript():
    """int() would truncate toward zero here and disagree with Math.floor."""
    assert local_day_key(-1.5, UTC_CALENDAR) == -1
    assert local_day_key(-86400.5, UTC_CALENDAR) == -2


def test_local_day_key_on_non_finite_input_pins_the_typescript_divergence():
    """NaN propagates identically; infinity does NOT, and neither side raises.

    Unreachable today (`_resolve_context` casts to int64 first) and deliberately not
    engineered around -- pinned so a future change to it is a decision, not an
    accident. TypeScript's `Math.floor(Infinity / 86400)` is `Infinity`; Python's
    floor division collapses it to nan. It would matter to a future Python
    `sessionStarts`: nan makes every bar a session start, `Infinity` makes none.
    """
    assert math.isnan(local_day_key(float("nan"), IST_CALENDAR))
    assert math.isnan(local_day_key(float("inf"), IST_CALENDAR))
    assert math.isnan(local_day_key(float("-inf"), IST_CALENDAR))


def test_local_day_key_vectorizes_over_numpy_and_keeps_the_shape():
    """_resolve_context relies on this: one helper, not a second inlined division."""
    t = np.array([_epoch("2026-07-26T18:29:59Z"), _epoch("2026-07-26T18:30:00Z")], dtype=np.int64)
    days = local_day_key(t, IST_CALENDAR)
    assert isinstance(days, np.ndarray)
    assert days.shape == t.shape
    assert int(days[1]) == int(days[0]) + 1


# -- context fields under a calendar -----------------------------------------------


def _context_ir(source: str) -> dict:
    """A minimal accepted IR: one context source plotted as a line.

    The header/meta shape is the one `test_openscript_admission._valid_ir` uses, so
    this passes admission for the same reasons that corpus does.
    """
    return {
        "version": 1,
        "compilerVersion": "openscript-1.0",
        "sourceHash": "deadbeef",
        "header": {
            "major": 1,
            "minor": 0,
            "compilerVersion": "openscript-1.0",
            "requiredFeatures": [],
            "numericMode": "f64-strict",
        },
        "declaration": {"name": "CAL", "overlay": False},
        "inputs": [],
        "nodes": [{"id": 0, "op": "source", "source": source}],
        "outputs": [{"kind": "plot", "nodeId": 0, "title": "V", "style": {"color": "#fff"}}],
        "meta": {"warmupBars": 0, "spans": {}},
    }


def _dataset(isos):
    t = np.array([_epoch(s) for s in isos], dtype=np.int64)
    ones = np.ones(len(isos), dtype=float)
    return {
        "time": t,
        "open": ones,
        "high": ones,
        "low": ones,
        "close": ones,
        "volume": ones.copy(),
    }


def _values(outputs):
    return [float(v) for v in outputs[0]["values"]]


# 18:29:59Z is IST 23:59; 18:30:00Z is IST 00:00 the NEXT day.
_IST_MIDNIGHT = ["2026-07-26T18:29:59Z", "2026-07-26T18:30:00Z"]
_UTC_MIDNIGHT = ["2026-07-26T23:59:59Z", "2026-07-27T00:00:00Z"]


def test_hour_is_ist_by_default():
    ds = _dataset(_IST_MIDNIGHT)
    assert _values(execute_ir(_context_ir("hour"), ds, {})) == [23.0, 0.0]


def test_hour_is_utc_under_the_utc_calendar():
    ds = _dataset(_IST_MIDNIGHT)
    assert _values(execute_ir(_context_ir("hour"), ds, {}, calendar=UTC_CALENDAR)) == [18.0, 18.0]


def test_dayofmonth_diverges_across_the_ist_midnight_boundary():
    ds = _dataset(_IST_MIDNIGHT)
    ir = _context_ir("dayofmonth")
    assert _values(execute_ir(ir, ds, {}, calendar=IST_CALENDAR)) == [26.0, 27.0]
    assert _values(execute_ir(ir, ds, {}, calendar=UTC_CALENDAR)) == [26.0, 26.0]


def test_dayofmonth_diverges_across_the_utc_midnight_boundary():
    """Both instants are IST 2026-07-27 (05:29 and 05:30), so IST must NOT move."""
    ds = _dataset(_UTC_MIDNIGHT)
    ir = _context_ir("dayofmonth")
    assert _values(execute_ir(ir, ds, {}, calendar=UTC_CALENDAR)) == [26.0, 27.0]
    assert _values(execute_ir(ir, ds, {}, calendar=IST_CALENDAR)) == [27.0, 27.0]


def test_time_and_bar_index_are_calendar_independent():
    ds = _dataset(_IST_MIDNIGHT)
    for source in ("time", "bar_index"):
        ir = _context_ir(source)
        ist = _values(execute_ir(ir, ds, {}, calendar=IST_CALENDAR))
        utc = _values(execute_ir(ir, ds, {}, calendar=UTC_CALENDAR))
        assert ist == utc
