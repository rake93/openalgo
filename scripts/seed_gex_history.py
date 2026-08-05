#!/usr/bin/env python
"""
Seed `gex.db` with a fabricated session of GEX history. **Development only.**

Gamma Bands draws recorded history, and the recorder only records during market
hours - so with the market shut there is nothing on screen to check a renderer
against. This writes a plausible session so the bands can be looked at before
waiting for an open. That matters here more than usual: three defects on this
feature reached the live chart with a full green test suite, because jsdom calls
draw handlers with no chart underneath and cannot see a line in the wrong place.

**It writes fake data into a real database.** It refuses to touch a series that
already holds real snapshots, and `--clear` removes only what it wrote. Delete
the whole series from the UI, or `db/gex.db` itself, when you are done.

The shape is chosen to exercise the rules the renderer has to get right, not to
look pretty:

  * walls that HOLD then step to a new strike, so the step-line is visible;
  * a zero-gamma that wanders and then goes null for a stretch ("no local
    cross" - a real reading that must leave a hole, not drop to the bottom);
  * a ONE-MINUTE hole, which must NOT break the line (the 150s threshold
    exists so a single dropped tick does not shatter a session); and
  * a TEN-MINUTE hole, which must break it.

Usage:
    uv run python scripts/seed_gex_history.py --underlying NIFTY --exchange NFO \\
        --expiry 28JUL26 --hours 6
    uv run python scripts/seed_gex_history.py --expiry 28JUL26 --clear
"""

import argparse
import math
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import gex_history_db  # noqa: E402

# Every snapshot this script writes carries this marker in its quality notes, so
# `--clear` can delete exactly what it created and nothing else.
SEED_MARKER = "SEEDED-BY-seed_gex_history.py"

CADENCE = 60
IST = ZoneInfo("Asia/Kolkata")


def _walls(step: int, total: int, center: float) -> tuple[float, float, float | None]:
    """Levels for one minute of the fabricated session.

    Walls hold for long stretches and then jump a strike, which is what they
    actually do - a wall is the strike with the most gamma, and that changes in
    discrete steps rather than drifting.

    Derived from `center` so the fabricated levels land inside the price range
    the chart is actually showing. Bands contribute nothing to autoscale (by
    design - see the primitive), so a wall seeded outside the visible range is
    simply clipped away and the whole check silently shows nothing.
    """
    third = max(1, total // 3)
    call_wall = center + 50.0 + 50.0 * (step // third)
    put_wall = center - 100.0 - 50.0 * (step // max(1, total // 2))

    # A stretch with no local cross, so the band has to leave a hole rather
    # than draw a line through it.
    if third <= step < third + max(2, total // 12):
        return call_wall, put_wall, None

    # Otherwise it drifts between the walls. A smooth wander, NOT a sawtooth:
    # the first version used `step % 40` and produced a jagged ramp-and-reset
    # that looked like violent churn on the chart. Judging a renderer against
    # that is judging the seeder, so this uses two slow sine components whose
    # periods do not divide each other - close to how the flip level actually
    # moves as the book fills in.
    span = call_wall - put_wall
    wander = 0.5 + 0.16 * math.sin(step / 47.0) + 0.07 * math.sin(step / 13.0)
    return call_wall, put_wall, put_wall + span * wander


def _snapshot(ts: int, expiry: str, call_wall: float, put_wall: float, zero_gamma):
    net_gex = 1_500.0 - 3_000.0 * ((ts // CADENCE) % 7) / 6.0
    quality = {
        "verdict": "good",
        "strikes_used": 47,
        "strikes_priced": 47,
        "both_sides": True,
        "wall_at_edge": False,
        "may_draw": True,
        "notes": [SEED_MARKER],
    }
    return {
        "ts": ts,
        "expiry_date": expiry,
        "spot_price": 24_590.0,
        "forward_price": 24_610.0,
        "atm_strike": 24_600.0,
        "dte_days": 6.0,
        "interest_rate": 6.5,
        "lot_size": 75,
        "strikes_used": 47,
        "call_wall_oi": call_wall,
        "call_wall_vol": call_wall - 50.0,
        "put_wall_oi": put_wall,
        "put_wall_vol": put_wall + 50.0,
        "zero_gamma_oi": zero_gamma,
        "zero_gamma_vol": zero_gamma if zero_gamma is None else zero_gamma + 15.0,
        "net_gex_oi": net_gex,
        "net_gex_vol": -net_gex,
        "regime_oi": "suppressive" if net_gex >= 0 else "amplifying",
        "regime_vol": "amplifying" if net_gex >= 0 else "suppressive",
        "sentiment_oi": {"bias": "neutral", "score": 0.0, "signals": []},
        "sentiment_vol": {"bias": "neutral", "score": 0.0, "signals": []},
        "quality_verdict_oi": "good",
        "quality_verdict_vol": "good",
        "quality_oi": quality,
        "quality_vol": quality,
    }


def _is_seeded(row: dict) -> bool:
    quality = row.get("quality_oi") or {}
    return SEED_MARKER in (quality.get("notes") or [])


def _resolve_series(underlying: str, exchange: str) -> dict | None:
    for series in gex_history_db.list_series():
        if series["underlying"] == underlying and series["exchange"] == exchange:
            return series
    return None


def clear(underlying: str, exchange: str, expiry: str) -> int:
    """Delete only the rows this script wrote. Real snapshots are left alone."""
    series = _resolve_series(underlying, exchange)
    if series is None:
        print(f"No series for {underlying} {exchange}; nothing to clear.")
        return 0

    rows = gex_history_db.get_snapshots_in_range(series["id"], 0, 2_000_000_000, expiry_date=expiry)
    seeded = [row for row in rows if _is_seeded(row)]
    if not seeded:
        print("No seeded rows found.")
        return 0

    session = gex_history_db.db_session
    ids = [row["id"] for row in seeded]
    session.query(gex_history_db.GexSnapshotStrike).filter(
        gex_history_db.GexSnapshotStrike.snapshot_id.in_(ids)
    ).delete(synchronize_session=False)
    session.query(gex_history_db.GexSnapshot).filter(gex_history_db.GexSnapshot.id.in_(ids)).delete(
        synchronize_session=False
    )
    session.commit()
    print(f"Cleared {len(ids)} seeded snapshot(s) for {underlying} {exchange} {expiry}.")
    return len(ids)


def seed(
    underlying: str,
    exchange: str,
    expiry: str,
    hours: int,
    center: float,
    end_ts: int | None = None,
) -> int:
    series = _resolve_series(underlying, exchange)
    if series is None:
        ok, message, series = gex_history_db.add_series(underlying, exchange, "nearest")
        if not ok:
            print(f"Could not create the series: {message}")
            return 0
        print(f"Created series {series['id']} for {underlying} {exchange} (nearest).")

    existing = gex_history_db.get_snapshots_in_range(
        series["id"], 0, 2_000_000_000, expiry_date=expiry
    )
    real = [row for row in existing if not _is_seeded(row)]
    if real:
        # Never mix fabricated rows into a real recording: a session that was
        # half real and half invented is worse than either, and there would be
        # no way to tell them apart on the chart afterwards.
        print(
            f"Refusing to seed: {len(real)} REAL snapshot(s) already exist for "
            f"{underlying} {exchange} {expiry}. Pick another expiry, or clear them "
            "from the UI first."
        )
        return 0

    total = hours * 60
    # Anchored to the END of a charted session by default rather than to the
    # clock. Run after hours, `now` sits past the last bar, and every point
    # extrapolates off the right edge of a gapless time axis - the bands are
    # drawn perfectly and none of them are on screen.
    end = (end_ts if end_ts is not None else int(time.time())) // CADENCE * CADENCE
    start = end - total * CADENCE

    # The two deliberate holes. Positions chosen to sit inside a normal lookback
    # window rather than at an edge, so both are visible without scrolling.
    one_minute_hole = total // 4
    outage_start = total // 2
    outage_minutes = 10

    written = 0
    for step in range(total):
        if step == one_minute_hole:
            continue
        if outage_start <= step < outage_start + outage_minutes:
            continue

        call_wall, put_wall, zero_gamma = _walls(step, total, center)
        ts = start + step * CADENCE
        if gex_history_db.write_snapshot(
            series["id"], _snapshot(ts, expiry, call_wall, put_wall, zero_gamma), []
        ):
            written += 1

    print(
        f"Seeded {written} snapshot(s) over {hours}h for {underlying} {exchange} {expiry}.\n"
        f"  - one-minute hole at +{one_minute_hole}m (the band must NOT break)\n"
        f"  - {outage_minutes}-minute outage at +{outage_start}m (the band MUST break)\n"
        f"  - a null zero-gamma stretch (must leave a hole, not drop to the axis)\n"
        f"Open /charts, enable GEX levels and turn Gamma bands on."
    )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--exchange", default="NFO")
    parser.add_argument(
        "--expiry",
        required=True,
        help="Resolved DDMMMYY expiry, e.g. 28JUL26. Must match what the study resolves to.",
    )
    parser.add_argument("--hours", type=int, default=6)
    parser.add_argument(
        "--center",
        type=float,
        default=24600.0,
        help="Price the fabricated walls straddle. Set it inside the range the chart shows.",
    )
    parser.add_argument(
        "--end",
        default=None,
        help=(
            "IST end of the fabricated session, 'YYYY-MM-DD HH:MM'. Defaults to now, "
            "which is wrong after hours - anchor it to the last bar on the chart."
        ),
    )
    parser.add_argument("--clear", action="store_true", help="Remove seeded rows and exit")
    args = parser.parse_args()

    underlying = args.underlying.strip().upper()
    exchange = args.exchange.strip().upper()
    expiry = args.expiry.strip().upper()

    gex_history_db.init_gex_history_db()
    try:
        if args.clear:
            clear(underlying, exchange, expiry)
        else:
            end_ts = None
            if args.end:
                ist = datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
                end_ts = int(ist.timestamp())
            seed(underlying, exchange, expiry, args.hours, args.center, end_ts)
    finally:
        gex_history_db.db_session.remove()


if __name__ == "__main__":
    main()
