"""The recorder: one tick in, one snapshot plus its strike profile out.

Patched at the real IO boundaries so the WHOLE pipeline runs - the same
`get_option_chain` / `_resolve_forward_price` pair `test_gex_levels_service`
stubs, plus the recorder's own three (api key, session guard, expiry lookup).
Nothing here mocks `build_snapshot`: the point of this suite is that the
recorder's rows come out of the live path's maths, not a second copy of it.
"""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)
_TEMP_DB_URL = f"sqlite:///{_DB_PATH}"

_ORIGINAL_DB_URL = os.environ.get("GEX_DATABASE_URL")
os.environ["GEX_DATABASE_URL"] = _TEMP_DB_URL
try:
    from database import gex_history_db
finally:
    if _ORIGINAL_DB_URL is None:
        os.environ.pop("GEX_DATABASE_URL", None)
    else:
        os.environ["GEX_DATABASE_URL"] = _ORIGINAL_DB_URL

from services import gex_recorder_service  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")

# An exact cadence boundary. Must be a multiple of 60, or the floor lands in the
# previous bucket and every assertion about it is off by a minute.
MINUTE = 1_754_000_040
assert MINUTE % 60 == 0

# Derived, never hardcoded. A fixed expiry silently expires: once it is past,
# calculate_time_to_expiry returns 0 and every gamma is 0.0, so assertions that
# a column is populated start comparing zero to zero.
_EXPIRY_DT = datetime.now(IST) + timedelta(days=6)
EXPIRY = _EXPIRY_DT.strftime("%d%b%y").upper()
EXPIRY_DASHED = _EXPIRY_DT.strftime("%d-%b-%y").upper()
NEXT_EXPIRY_DT = _EXPIRY_DT + timedelta(days=7)
NEXT_EXPIRY = NEXT_EXPIRY_DT.strftime("%d%b%y").upper()
NEXT_EXPIRY_DASHED = NEXT_EXPIRY_DT.strftime("%d-%b-%y").upper()


def _chain_response():
    """Five strikes, both legs, shaped exactly as option_chain_service returns.

    Call and put volume differ from OI by more than a constant factor so the
    two weightings cannot accidentally produce the same profile.
    """
    rows = []
    for strike in (24400, 24500, 24600, 24700, 24800):
        rows.append(
            {
                "strike": float(strike),
                "ce": {
                    "symbol": f"NIFTY{EXPIRY}{strike}CE",
                    "ltp": 120.0,
                    "oi": 100000,
                    "volume": 5000,
                    "lotsize": 75,
                },
                "pe": {
                    "symbol": f"NIFTY{EXPIRY}{strike}PE",
                    "ltp": 110.0,
                    "oi": 90000,
                    "volume": 12000,
                    "lotsize": 75,
                },
            }
        )
    return {
        "status": "success",
        "chain": rows,
        "atm_strike": 24600.0,
        "underlying_ltp": 24590.0,
        "underlying": "NIFTY",
    }


@pytest.fixture
def gexdb():
    """An empty gex.db per test, bound explicitly to the temp engine."""
    engine = create_engine(
        _TEMP_DB_URL, poolclass=NullPool, connect_args={"check_same_thread": False}
    )
    original_bind = gex_history_db.db_session.get_bind()
    gex_history_db.db_session.remove()
    gex_history_db.db_session.configure(bind=engine)
    gex_history_db.Base.metadata.drop_all(engine)
    gex_history_db.Base.metadata.create_all(engine)

    yield gex_history_db

    gex_history_db.db_session.remove()
    gex_history_db.db_session.configure(bind=original_bind)
    engine.dispose()


@pytest.fixture
def recording():
    """Every IO boundary a tick crosses, stubbed. The maths is real."""
    with (
        patch(
            "services.gex_levels_service.get_option_chain",
            return_value=(True, _chain_response(), 200),
        ),
        patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0),
        patch("services.gex_recorder_service.get_first_available_api_key", return_value="key"),
        patch("services.gex_recorder_service.session_is_open", return_value=True),
        patch(
            "services.gex_recorder_service.get_expiry_dates",
            return_value=(True, {"data": [EXPIRY_DASHED, NEXT_EXPIRY_DASHED]}, 200),
        ),
    ):
        yield


def _rows(gexdb, series_id):
    return gexdb.get_snapshots_in_range(series_id, 0, 2_000_000_000)


# -------------------------------------------------------------------- one tick


def test_one_tick_writes_one_snapshot_with_both_weightings_populated(gexdb, recording):
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)

    gex_recorder_service.record_series_once(series["id"])

    rows = _rows(gexdb, series["id"])
    assert len(rows) == 1
    snap = rows[0]
    for column in (
        "call_wall_oi",
        "call_wall_vol",
        "put_wall_oi",
        "put_wall_vol",
        "net_gex_oi",
        "net_gex_vol",
        "regime_oi",
        "regime_vol",
        "quality_verdict_oi",
        "quality_verdict_vol",
        "sentiment_oi",
        "sentiment_vol",
    ):
        assert snap[column] is not None, f"{column} was not recorded"

    assert snap["expiry_date"] == EXPIRY
    assert snap["spot_price"] == 24590.0
    assert snap["forward_price"] == 24610.0
    assert snap["lot_size"] == 75
    assert snap["strikes_used"] == 5


def test_quality_is_stored_whole_so_may_draw_survives(gexdb, recording):
    """may_draw is a @property, so a verdict-plus-notes schema would drop it,
    and an absent key reads as falsy in TypeScript - every good recorded
    snapshot would render as 'do not draw'."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)
    gex_recorder_service.record_series_once(series["id"])

    snap = _rows(gexdb, series["id"])[0]
    assert isinstance(snap["quality_oi"]["may_draw"], bool)
    assert isinstance(snap["quality_vol"]["may_draw"], bool)
    assert snap["quality_oi"]["verdict"] == snap["quality_verdict_oi"]


def test_every_strike_row_carries_both_metrics_both_weightings_and_the_raw_inputs(gexdb, recording):
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)
    gex_recorder_service.record_series_once(series["id"])

    latest = gexdb.get_latest_snapshot("NIFTY", "NFO", EXPIRY)
    assert len(latest["strikes"]) == 5
    assert [s["strike"] for s in latest["strikes"]] == [24400.0, 24500.0, 24600.0, 24700.0, 24800.0]

    row = latest["strikes"][0]
    # The weighting was actually applied rather than the same profile written
    # into both column families.
    assert row["net_gex_oi"] != row["net_gex_vol"]
    assert row["net_dex_oi"] != row["net_dex_vol"]
    assert row["net_dex_oi"] != 0.0
    # The raw inputs, which are what make a maths error repairable.
    assert row["call_oi"] == 100000.0
    assert row["put_oi"] == 90000.0
    assert row["call_volume"] == 5000.0
    assert row["put_volume"] == 12000.0


def test_the_recorded_profile_matches_what_the_live_path_would_serve(gexdb, recording):
    """The drift guard, end to end: what lands in gex.db must be what the study
    would have computed for the same chain. /gex drifted from this study exactly
    that way and shipped three defects."""
    from services.gex_levels_service import get_gex_levels

    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)
    gex_recorder_service.record_series_once(series["id"])
    _, live, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    snap = _rows(gexdb, series["id"])[0]
    assert snap["call_wall_oi"] == live["call_wall"]
    assert snap["put_wall_oi"] == live["put_wall"]
    assert snap["zero_gamma_oi"] == live["zero_gamma"]
    assert snap["net_gex_oi"] == live["net_gex"]
    assert snap["regime_oi"] == live["regime"]

    recorded_strikes = gexdb.get_latest_snapshot("NIFTY", "NFO", EXPIRY)["strikes"]
    assert [s["net_gex_oi"] for s in recorded_strikes] == [s["net_gex"] for s in live["strikes"]]
    assert [s["net_dex_oi"] for s in recorded_strikes] == [s["net_dex"] for s in live["strikes"]]


def test_the_timestamp_is_floored_to_the_cadence(gexdb, recording):
    """A ragged ts turns the heatmap's x-axis into jitter and makes the unique
    constraint useless as a double-fire guard."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)

    gex_recorder_service.record_series_once(series["id"], now=MINUTE + 37)

    assert _rows(gexdb, series["id"])[0]["ts"] == MINUTE


def test_a_double_fire_in_the_same_minute_writes_once(gexdb, recording):
    """A stagger offset plus coalesce can land two fires inside one cadence
    bucket. The second must be dropped, not drawn as a second column."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)

    gex_recorder_service.record_series_once(series["id"], now=MINUTE + 10)
    gex_recorder_service.record_series_once(series["id"], now=MINUTE + 50)

    assert len(_rows(gexdb, series["id"])) == 1


# ----------------------------------------------------------------- the roll


def test_a_nearest_series_records_the_resolved_expiry_and_follows_the_roll(gexdb, recording):
    """Walls jump at a roll because the book changed, not because the market
    moved. A reader cannot tell the two apart without the resolved contract on
    every row."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")

    gex_recorder_service.record_series_once(series["id"], now=MINUTE)
    with patch(
        "services.gex_recorder_service.get_expiry_dates",
        return_value=(True, {"data": [NEXT_EXPIRY_DASHED]}, 200),
    ):
        gex_recorder_service.record_series_once(series["id"], now=MINUTE + 60)

    assert [s["expiry_date"] for s in _rows(gexdb, series["id"])] == [EXPIRY, NEXT_EXPIRY]


def test_a_pinned_series_ignores_the_expiry_lookup_entirely(gexdb, recording):
    """A pinned contract must keep recording the contract that was pinned, even
    once it is no longer the nearest one."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", NEXT_EXPIRY)

    with patch("services.gex_recorder_service.get_expiry_dates") as lookup:
        gex_recorder_service.record_series_once(series["id"])

    lookup.assert_not_called()
    assert _rows(gexdb, series["id"])[0]["expiry_date"] == NEXT_EXPIRY


def test_an_expiry_lookup_that_returns_nothing_writes_nothing(gexdb, recording):
    """Better a gap than a snapshot recorded against a guessed contract."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")

    with patch(
        "services.gex_recorder_service.get_expiry_dates",
        return_value=(True, {"data": []}, 200),
    ):
        gex_recorder_service.record_series_once(series["id"])

    assert _rows(gexdb, series["id"]) == []


# --------------------------------------------------------------- not recording


def test_a_failed_fetch_writes_nothing_and_does_not_raise(gexdb, recording):
    """An exception escaping the job would be caught by APScheduler, but a
    recorder that logs nothing and writes a partial row is worse than a gap."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)

    with patch(
        "services.gex_levels_service.get_option_chain",
        return_value=(False, {"status": "error", "message": "broker down"}, 502),
    ):
        gex_recorder_service.record_series_once(series["id"])

    assert _rows(gexdb, series["id"]) == []


def test_an_unusable_chain_writes_nothing_and_does_not_raise(gexdb, recording):
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)
    empty = {"status": "success", "chain": [], "atm_strike": None, "underlying_ltp": 0}

    with patch("services.gex_levels_service.get_option_chain", return_value=(True, empty, 200)):
        gex_recorder_service.record_series_once(series["id"])

    assert _rows(gexdb, series["id"]) == []


def test_a_closed_market_does_not_reach_the_broker(gexdb, recording):
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)

    with (
        patch("services.gex_recorder_service.session_is_open", return_value=False),
        patch("services.gex_levels_service.get_option_chain") as fetch,
    ):
        gex_recorder_service.record_series_once(series["id"])

    fetch.assert_not_called()
    assert _rows(gexdb, series["id"]) == []


def test_a_disabled_series_does_not_reach_the_broker(gexdb, recording):
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)
    gexdb.set_series_enabled(series["id"], False)

    with patch("services.gex_levels_service.get_option_chain") as fetch:
        gex_recorder_service.record_series_once(series["id"])

    fetch.assert_not_called()
    assert _rows(gexdb, series["id"]) == []


def test_a_deleted_series_is_a_no_op(gexdb, recording):
    """sync_jobs removes a series' job, but a tick already in flight can arrive
    after the delete."""
    gex_recorder_service.record_series_once(999)  # must not raise


def test_no_api_key_means_no_broker_call(gexdb, recording):
    """A fresh install has no API key. The recorder must not spin on a broker
    call it cannot authenticate."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)

    with (
        patch("services.gex_recorder_service.get_first_available_api_key", return_value=None),
        patch("services.gex_levels_service.get_option_chain") as fetch,
    ):
        gex_recorder_service.record_series_once(series["id"])

    fetch.assert_not_called()


# ------------------------------------------------------------------ scheduling


def test_series_are_staggered_across_the_cadence():
    """Rate limiting is live, not hypothetical - a single manual call during
    design hit 'Rate limit hit (805)'. Every series firing on the same second is
    the shape that triggers it."""
    offsets = [gex_recorder_service.stagger_seconds(i) for i in range(1, 6)]

    assert len(set(offsets)) == 5
    assert all(0 <= o < gex_recorder_service.CADENCE_SECONDS for o in offsets)


def test_the_stagger_wraps_without_leaving_the_cadence_window():
    assert all(
        0 <= gex_recorder_service.stagger_seconds(i) < gex_recorder_service.CADENCE_SECONDS
        for i in range(1, 200)
    )


def test_an_empty_watchlist_registers_no_jobs(gexdb):
    """The recorder ships idle. An upgrade must not start polling on a schedule
    nobody asked for."""
    scheduler = gex_recorder_service.GexRecorderScheduler()
    with patch.object(scheduler, "_scheduler") as apscheduler:
        apscheduler.get_jobs.return_value = []
        scheduler.sync_jobs()

    apscheduler.add_job.assert_not_called()


def test_sync_jobs_registers_one_job_per_enabled_series(gexdb):
    gexdb.add_series("NIFTY", "NFO", "nearest")
    _, _, disabled = gexdb.add_series("BANKNIFTY", "NFO", "nearest")
    gexdb.set_series_enabled(disabled["id"], False)

    scheduler = gex_recorder_service.GexRecorderScheduler()
    with patch.object(scheduler, "_scheduler") as apscheduler:
        apscheduler.get_jobs.return_value = []
        scheduler.sync_jobs()

    assert apscheduler.add_job.call_count == 1
    assert apscheduler.add_job.call_args.kwargs["id"] == "gex_record_1"


def test_sync_jobs_removes_the_job_of_a_series_that_is_gone(gexdb):
    class _Job:
        id = "gex_record_7"

    scheduler = gex_recorder_service.GexRecorderScheduler()
    with patch.object(scheduler, "_scheduler") as apscheduler:
        apscheduler.get_jobs.return_value = [_Job()]
        scheduler.sync_jobs()

    apscheduler.remove_job.assert_called_once_with("gex_record_7")


def test_sync_jobs_leaves_jobs_it_does_not_own_alone(gexdb):
    """The prune job lives on the same scheduler and must survive a watchlist
    change."""

    class _Job:
        id = "gex_prune"

    scheduler = gex_recorder_service.GexRecorderScheduler()
    with patch.object(scheduler, "_scheduler") as apscheduler:
        apscheduler.get_jobs.return_value = [_Job()]
        scheduler.sync_jobs()

    apscheduler.remove_job.assert_not_called()


# --------------------------------------------------------------------- prune


def test_the_prune_job_runs_and_does_not_raise_on_an_empty_database(gexdb):
    gex_recorder_service.prune_history_once()  # must not raise


def test_the_prune_job_deletes_old_snapshots(gexdb, recording):
    import time as _time

    _, _, series = gexdb.add_series("NIFTY", "NFO", EXPIRY)
    now = int(_time.time())
    gex_recorder_service.record_series_once(series["id"], now=now - 40 * 86400)
    gex_recorder_service.record_series_once(series["id"], now=now)

    gex_recorder_service.prune_history_once()

    assert len(_rows(gexdb, series["id"])) == 1
