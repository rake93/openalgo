"""Persistence for the GEX Levels snapshot recorder: watchlist and history.

Binds to a throwaway SQLite file rather than `db/gex.db`. `gex_history_db`
builds its engine at IMPORT time from GEX_DATABASE_URL, so the variable has to
be set before the import below - and restored immediately after, because a value
left in os.environ leaks into every later test module in the same pytest session
(the pattern, and the reason for it, are from test_indicator_script_endpoints.py).
"""

import os
import tempfile
import time

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


@pytest.fixture
def gexdb():
    """An empty gex.db per test, bound explicitly to the temp engine.

    Bound here rather than trusting what the module picked up at import: if an
    earlier test module already imported it, the module-level engine points at
    whatever GEX_DATABASE_URL was then, and this suite must never write into a
    real database. The engine is disposed at teardown - a leaked engine holds
    its SQLite file descriptor for the life of the process.
    """
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


def _snapshot(ts, expiry_date="11AUG26"):
    """One snapshot row as the recorder hands it over, both weightings filled."""
    return {
        "ts": ts,
        "expiry_date": expiry_date,
        "spot_price": 24590.0,
        "forward_price": 24610.0,
        "atm_strike": 24600.0,
        "dte_days": 6.2,
        "interest_rate": 6.5,
        "lot_size": 75,
        "strikes_used": 2,
        "call_wall_oi": 24800.0,
        "call_wall_vol": 24700.0,
        "put_wall_oi": 24400.0,
        "put_wall_vol": 24500.0,
        "zero_gamma_oi": 24605.0,
        "zero_gamma_vol": None,
        "net_gex_oi": 1234.5,
        "net_gex_vol": -678.9,
        "regime_oi": "suppressive",
        "regime_vol": "amplifying",
        "sentiment_oi": {"bias": "bullish", "score": 0.4},
        "sentiment_vol": {"bias": "neutral", "score": 0.0},
        "quality_verdict_oi": "good",
        "quality_verdict_vol": "degraded",
        "quality_oi": {"verdict": "good", "strikes_used": 2, "notes": [], "may_draw": True},
        "quality_vol": {
            "verdict": "degraded",
            "strikes_used": 2,
            "notes": ["thin volume"],
            "may_draw": True,
        },
    }


def _strikes():
    """Two strike rows, both metrics, both weightings, plus the raw inputs."""
    return [
        {
            "strike": 24400.0,
            "call_gex_oi": 10.0,
            "put_gex_oi": -4.0,
            "net_gex_oi": 6.0,
            "call_gex_vol": 1.0,
            "put_gex_vol": -0.4,
            "net_gex_vol": 0.6,
            "call_dex_oi": 20.0,
            "put_dex_oi": -8.0,
            "net_dex_oi": 12.0,
            "call_dex_vol": 2.0,
            "put_dex_vol": -0.8,
            "net_dex_vol": 1.2,
            "call_oi": 100000.0,
            "put_oi": 90000.0,
            "call_volume": 5000.0,
            "put_volume": 4000.0,
        },
        {
            "strike": 24800.0,
            "call_gex_oi": 12.0,
            "put_gex_oi": -6.0,
            "net_gex_oi": 6.0,
            "call_gex_vol": 1.2,
            "put_gex_vol": -0.6,
            "net_gex_vol": 0.6,
            "call_dex_oi": 24.0,
            "put_dex_oi": -12.0,
            "net_dex_oi": 12.0,
            "call_dex_vol": 2.4,
            "put_dex_vol": -1.2,
            "net_dex_vol": 1.2,
            "call_oi": 80000.0,
            "put_oi": 70000.0,
            "call_volume": 3000.0,
            "put_volume": 2000.0,
        },
    ]


# ------------------------------------------------------------------- watchlist


def test_the_watchlist_ships_empty(gexdb):
    """An upgrade must not silently start making broker calls on a schedule
    nobody asked for. The recorder stays idle until a series is added."""
    assert gexdb.list_series() == []


def test_a_series_round_trips(gexdb):
    ok, _, row = gexdb.add_series("NIFTY", "NFO", "nearest")

    assert ok is True
    assert row["underlying"] == "NIFTY"
    assert row["exchange"] == "NFO"
    assert row["expiry_rule"] == "nearest"
    assert row["enabled"] is True
    assert [s["id"] for s in gexdb.list_series()] == [row["id"]]
    assert gexdb.get_series(row["id"])["underlying"] == "NIFTY"


def test_the_same_series_cannot_be_added_twice(gexdb):
    gexdb.add_series("NIFTY", "NFO", "nearest")
    ok, msg, row = gexdb.add_series("NIFTY", "NFO", "nearest")

    assert ok is False
    assert row is None
    assert "already" in msg.lower()


def test_the_same_underlying_may_be_watched_on_two_expiry_rules(gexdb):
    """A pinned contract and the rolling nearest are different series, not a
    duplicate - watching both is how you keep history across a roll."""
    assert gexdb.add_series("NIFTY", "NFO", "nearest")[0] is True
    assert gexdb.add_series("NIFTY", "NFO", "11AUG26")[0] is True
    assert len(gexdb.list_series()) == 2


def test_a_series_is_stored_upper_cased(gexdb):
    """The chain service and the fast-path lookup both work in upper case; a
    lower-case row would record fine and then never be found again."""
    _, _, row = gexdb.add_series("nifty", "nfo", "11aug26")

    assert row["underlying"] == "NIFTY"
    assert row["exchange"] == "NFO"
    assert row["expiry_rule"] == "11AUG26"


def test_disabling_a_series_keeps_it_and_its_history(gexdb):
    _, _, row = gexdb.add_series("NIFTY", "NFO", "nearest")
    gexdb.write_snapshot(row["id"], _snapshot(ts=100), _strikes())

    ok, _ = gexdb.set_series_enabled(row["id"], False)

    assert ok is True
    assert gexdb.list_series(enabled_only=True) == []
    assert len(gexdb.list_series()) == 1
    assert len(gexdb.get_snapshots_in_range(row["id"], 0, 200)) == 1


def test_enabling_a_series_again_brings_it_back(gexdb):
    _, _, row = gexdb.add_series("NIFTY", "NFO", "nearest")
    gexdb.set_series_enabled(row["id"], False)
    gexdb.set_series_enabled(row["id"], True)

    assert [s["id"] for s in gexdb.list_series(enabled_only=True)] == [row["id"]]


def test_operations_on_an_unknown_series_fail_without_raising(gexdb):
    assert gexdb.get_series(999) is None
    assert gexdb.set_series_enabled(999, False)[0] is False
    assert gexdb.remove_series(999)[0] is False


# -------------------------------------------------------------------- snapshots


def test_a_snapshot_and_its_strikes_round_trip(gexdb):
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")

    snapshot_id = gexdb.write_snapshot(series["id"], _snapshot(ts=1_754_000_000), _strikes())
    assert snapshot_id is not None

    latest = gexdb.get_latest_snapshot("NIFTY", "NFO", "11AUG26")
    assert latest["ts"] == 1_754_000_000
    assert latest["call_wall_oi"] == 24800.0
    assert latest["call_wall_vol"] == 24700.0
    assert latest["regime_vol"] == "amplifying"
    assert latest["sentiment_oi"]["bias"] == "bullish"
    assert len(latest["strikes"]) == 2
    assert latest["strikes"][0]["strike"] == 24400.0
    assert latest["strikes"][0]["call_oi"] == 100000.0


def test_may_draw_survives_the_round_trip(gexdb):
    """`may_draw` is a @property, not a dataclass field. An absent key reads as
    undefined -> falsy in TypeScript, which would render every good recorded
    snapshot as 'do not draw' - the same trap _quality_payload guards on the
    live path. Storing the whole quality dict is what keeps it."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    gexdb.write_snapshot(series["id"], _snapshot(ts=100), _strikes())

    latest = gexdb.get_latest_snapshot("NIFTY", "NFO", "11AUG26")
    assert latest["quality_oi"]["may_draw"] is True
    assert latest["quality_vol"]["may_draw"] is True


def test_a_null_zero_gamma_stays_null_rather_than_becoming_zero(gexdb):
    """'No local cross' is a real reading. Stored as 0.0 it would draw a
    Zero-Gamma band at the bottom of the chart."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    gexdb.write_snapshot(series["id"], _snapshot(ts=100), _strikes())

    assert gexdb.get_latest_snapshot("NIFTY", "NFO", "11AUG26")["zero_gamma_vol"] is None


def test_a_second_write_in_the_same_minute_is_dropped_not_duplicated(gexdb):
    """coalesce plus a retry can fire the same minute twice. The unique
    constraint is what makes that a no-op instead of two heatmap columns at the
    same timestamp."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")

    first = gexdb.write_snapshot(series["id"], _snapshot(ts=1_754_000_000), _strikes())
    second = gexdb.write_snapshot(series["id"], _snapshot(ts=1_754_000_000), _strikes())

    assert first is not None
    assert second is None
    assert len(gexdb.get_snapshots_in_range(series["id"], 0, 2_000_000_000)) == 1
    assert gexdb.db_session.query(gexdb.GexSnapshotStrike).count() == 2


def test_the_latest_snapshot_is_the_newest_one(gexdb):
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    for ts in (100, 220, 160):
        gexdb.write_snapshot(series["id"], _snapshot(ts=ts), _strikes())

    assert gexdb.get_latest_snapshot("NIFTY", "NFO", "11AUG26")["ts"] == 220


def test_the_latest_snapshot_is_scoped_to_the_resolved_expiry(gexdb):
    """A 'nearest' series rolls, so one series holds several contracts. Serving
    the newest row regardless of expiry would hand the study last week's book
    the moment the roll happened."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    gexdb.write_snapshot(series["id"], _snapshot(ts=100, expiry_date="11AUG26"), _strikes())
    gexdb.write_snapshot(series["id"], _snapshot(ts=220, expiry_date="18AUG26"), _strikes())

    assert gexdb.get_latest_snapshot("NIFTY", "NFO", "11AUG26")["ts"] == 100
    assert gexdb.get_latest_snapshot("NIFTY", "NFO", "18AUG26")["ts"] == 220
    assert gexdb.get_latest_snapshot("NIFTY", "NFO", "25AUG26") is None


def test_an_unrecorded_series_has_no_latest_snapshot(gexdb):
    assert gexdb.get_latest_snapshot("BANKNIFTY", "NFO", "11AUG26") is None


def test_the_range_query_is_inclusive_at_both_ends(gexdb):
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    for ts in (100, 160, 220):
        gexdb.write_snapshot(series["id"], _snapshot(ts=ts), _strikes())

    got = [s["ts"] for s in gexdb.get_snapshots_in_range(series["id"], 100, 220)]
    assert got == [100, 160, 220]

    assert [s["ts"] for s in gexdb.get_snapshots_in_range(series["id"], 101, 219)] == [160]


def test_a_gap_stays_a_gap(gexdb):
    """A failed tick has no row. The reader must see the hole, not an
    interpolated value - flat gamma where there was NO READING is the same error
    quality.py and direction.ts already forbid."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    gexdb.write_snapshot(series["id"], _snapshot(ts=100), _strikes())
    gexdb.write_snapshot(series["id"], _snapshot(ts=220), _strikes())

    assert [s["ts"] for s in gexdb.get_snapshots_in_range(series["id"], 0, 400)] == [100, 220]


# ------------------------------------------------------------------- retention


def test_the_prune_deletes_strike_children_explicitly(gexdb):
    """SQLite does not enforce foreign keys unless PRAGMA foreign_keys=ON is set
    PER CONNECTION, and NullPool hands out a fresh connection every operation -
    so that pragma cannot be assumed armed and a cascade cannot be relied on.
    Orphaned strike rows are silent disk growth."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    now = int(time.time())
    gexdb.write_snapshot(series["id"], _snapshot(ts=now - 40 * 86400), _strikes())
    gexdb.write_snapshot(series["id"], _snapshot(ts=now), _strikes())

    result = gexdb.prune_snapshots(retention_days=30)

    assert result["snapshots_deleted"] == 1
    assert result["strikes_deleted"] == 2
    assert result["snapshots_remaining"] == 1
    assert gexdb.db_session.query(gexdb.GexSnapshotStrike).count() == 2


def test_the_prune_keeps_everything_inside_the_window(gexdb):
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    now = int(time.time())
    gexdb.write_snapshot(series["id"], _snapshot(ts=now - 29 * 86400), _strikes())

    result = gexdb.prune_snapshots(retention_days=30)

    assert result["snapshots_deleted"] == 0
    assert result["snapshots_remaining"] == 1


def test_the_prune_reports_what_it_did_on_an_empty_database(gexdb):
    """Prune failure is silent disk growth, so the counts are the observable.
    Zero rows must still return the shape, not None."""
    result = gexdb.prune_snapshots(retention_days=30)

    assert result == {"snapshots_deleted": 0, "strikes_deleted": 0, "snapshots_remaining": 0}


def test_removing_a_series_takes_its_history_with_it(gexdb):
    """No cascade to rely on, and a series left behind with orphaned snapshots
    is history nothing can ever read."""
    _, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    gexdb.write_snapshot(series["id"], _snapshot(ts=100), _strikes())

    ok, _ = gexdb.remove_series(series["id"])

    assert ok is True
    assert gexdb.list_series() == []
    assert gexdb.db_session.query(gexdb.GexSnapshot).count() == 0
    assert gexdb.db_session.query(gexdb.GexSnapshotStrike).count() == 0


def test_removing_one_series_leaves_another_series_history_alone(gexdb):
    _, _, keep = gexdb.add_series("NIFTY", "NFO", "nearest")
    _, _, drop = gexdb.add_series("BANKNIFTY", "NFO", "nearest")
    gexdb.write_snapshot(keep["id"], _snapshot(ts=100), _strikes())
    gexdb.write_snapshot(drop["id"], _snapshot(ts=100), _strikes())

    gexdb.remove_series(drop["id"])

    assert len(gexdb.get_snapshots_in_range(keep["id"], 0, 400)) == 1
    assert gexdb.db_session.query(gexdb.GexSnapshotStrike).count() == 2
