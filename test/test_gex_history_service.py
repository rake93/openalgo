"""The read side: recorded snapshots projected into band points.

Deliberately separate from the recorder, and these tests pin that: a query path
must never be able to trigger a broker fetch. The chain service is not stubbed
here because nothing in this module may reach it - if it ever does, the test that
asserts so will fail with a real network attempt rather than a passing mock.
"""

import os
import tempfile

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

from services.gex_history_service import get_gex_history  # noqa: E402

MINUTE = 1_754_000_040


@pytest.fixture
def gexdb():
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


def _snapshot(ts, expiry_date="11AUG26", **overrides):
    row = {
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
        "sentiment_oi": {"bias": "bullish"},
        "sentiment_vol": {"bias": "neutral"},
        "quality_verdict_oi": "good",
        "quality_verdict_vol": "degraded",
        "quality_oi": {"verdict": "good", "may_draw": True},
        "quality_vol": {"verdict": "degraded", "may_draw": True},
    }
    row.update(overrides)
    return row


def _seed(gexdb, count=3, expiry_date="11AUG26", start=MINUTE, step=60, **overrides):
    ok, _, series = gexdb.add_series("NIFTY", "NFO", "nearest")
    if not ok:
        series = gexdb.list_series()[0]
    for i in range(count):
        gexdb.write_snapshot(
            series["id"], _snapshot(start + i * step, expiry_date, **overrides), []
        )
    return series


# ------------------------------------------------------------------- happy path


def test_a_recorded_contract_returns_its_band_points(gexdb):
    _seed(gexdb, count=3)

    ok, payload, status = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", MINUTE, MINUTE + 120)

    assert ok is True
    assert status == 200
    assert payload["status"] == "success"
    assert [p["ts"] for p in payload["points"]] == [MINUTE, MINUTE + 60, MINUTE + 120]
    first = payload["points"][0]
    assert first["call_wall"] == 24800.0
    assert first["put_wall"] == 24400.0
    assert first["zero_gamma"] == 24605.0
    assert first["net_gex"] == 1234.5
    assert first["regime"] == "suppressive"
    assert first["quality_verdict"] == "good"


def test_the_weighting_selects_the_right_column_family(gexdb):
    _seed(gexdb, count=1)

    _, by_oi, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 0, 2_000_000_000)
    _, by_vol, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "volume", 0, 2_000_000_000)

    assert by_oi["points"][0]["call_wall"] == 24800.0
    assert by_vol["points"][0]["call_wall"] == 24700.0
    assert by_oi["points"][0]["regime"] == "suppressive"
    assert by_vol["points"][0]["regime"] == "amplifying"
    assert by_oi["weight_by"] == "oi"
    assert by_vol["weight_by"] == "volume"


def test_a_null_zero_gamma_stays_null(gexdb):
    """'No local cross' is a real reading. Rendered as 0 it would draw a band at
    the bottom of the chart; dropped, the line would silently join across it."""
    _seed(gexdb, count=1)

    _, payload, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "volume", 0, 2_000_000_000)

    assert payload["points"][0]["zero_gamma"] is None
    assert payload["points"][0]["call_wall"] is not None


def test_the_range_is_inclusive_and_gaps_survive(gexdb):
    """A minute the recorder missed has no row, and must arrive as an absent
    timestamp rather than an interpolated point."""
    series = _seed(gexdb, count=0)
    for ts in (MINUTE, MINUTE + 60, MINUTE + 300):
        gexdb.write_snapshot(series["id"], _snapshot(ts), [])

    _, payload, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", MINUTE, MINUTE + 300)

    assert [p["ts"] for p in payload["points"]] == [MINUTE, MINUTE + 60, MINUTE + 300]


def test_the_window_bounds_are_honoured(gexdb):
    series = _seed(gexdb, count=0)
    for ts in (MINUTE, MINUTE + 60, MINUTE + 120):
        gexdb.write_snapshot(series["id"], _snapshot(ts), [])

    _, payload, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", MINUTE + 60, MINUTE + 60)

    assert [p["ts"] for p in payload["points"]] == [MINUTE + 60]


def test_the_response_declares_its_resolution(gexdb):
    """Phase 5 downsamples above a column budget. Carrying the fields now means
    that lands as a value change, not a shape change - and a heatmap that
    silently thinned itself would look like a market that went quiet."""
    _seed(gexdb, count=1)

    _, payload, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 0, 2_000_000_000)

    assert payload["resolution"] == "1m"
    assert payload["downsampled"] is False


# ------------------------------------------------------------- ordinary absences


def test_an_unrecorded_contract_is_an_empty_success_not_an_error(gexdb):
    """A series nobody chose to record is ordinary. The study must render
    exactly as it did before Bands existed, with no error surfaced."""
    ok, payload, status = get_gex_history("BANKNIFTY", "NFO", "11AUG26", "oi", 0, 2_000_000_000)

    assert ok is True
    assert status == 200
    assert payload["points"] == []


def test_a_recorded_series_on_a_different_contract_returns_nothing(gexdb):
    """A 'nearest' series rolls, so one series holds several contracts. Bands is
    scoped to one contract: splicing them would draw wall jumps that are the book
    changing, not the market moving."""
    _seed(gexdb, count=2, expiry_date="18AUG26")

    _, payload, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 0, 2_000_000_000)

    assert payload["points"] == []


def test_a_window_with_no_rows_in_it_is_an_empty_success(gexdb):
    _seed(gexdb, count=2)

    _, payload, status = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 1, 2)

    assert status == 200
    assert payload["points"] == []


# ------------------------------------------------------------------- rejections


def test_an_unknown_weighting_is_rejected(gexdb):
    ok, payload, status = get_gex_history("NIFTY", "NFO", "11AUG26", "delta", 0, 1)

    assert ok is False
    assert status == 400
    assert "weight_by" in payload["message"]


def test_the_grid_field_is_an_explicit_400_until_phase_5(gexdb):
    """An explicit seam rather than a silent empty response - a heatmap that got
    back an empty grid would render as a market with no gamma anywhere."""
    ok, payload, status = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 0, 1, fields="grid")

    assert ok is False
    assert status == 400
    assert "grid" in payload["message"].lower()


def test_an_unknown_fields_value_is_rejected(gexdb):
    ok, _, status = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 0, 1, fields="everything")

    assert ok is False
    assert status == 400


def test_an_inverted_window_is_rejected(gexdb):
    ok, payload, status = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 500, 100)

    assert ok is False
    assert status == 400


def test_the_read_path_never_calls_the_recorder_or_the_chain(gexdb, monkeypatch):
    """The whole reason this module is separate from gex_recorder_service: a
    query must never be able to trigger a broker fetch."""
    import services.gex_levels_service as levels

    def explode(*_a, **_kw):
        raise AssertionError("the history read path reached a broker fetch")

    monkeypatch.setattr(levels, "get_option_chain", explode)
    _seed(gexdb, count=2)

    ok, _, _ = get_gex_history("NIFTY", "NFO", "11AUG26", "oi", 0, 2_000_000_000)

    assert ok is True
