# services/gex_history_service.py
"""
The read side of recorded GEX history: stored snapshots as plottable points.

Deliberately a separate module from `gex_recorder_service` so that **a query
path can never trigger a fetch**. The recorder owns every broker call and every
write; nothing here touches the network, the chain service, or the scheduler. A
test pins that by exploding if the chain service is reached.

Two shapes off one store. `fields="levels"` backs Gamma Bands: one row per
minute, levels only. `fields="grid"` backs the GEX Heatmap: a shared strike axis
and one column per minute, carrying the full per-strike profile for the requested
metric and weighting.

The grid's column budget, its bucketing and its strike-axis layout are pure and
live in `services/gex_levels/grid.py`; this module is the IO around them. The
budget is applied to a COUNT before any strike row is read, which is why the
grid path queries a light index first and only then pays for the survivors'
children.

**One contract, never a spliced series.** The request names a resolved
`expiry_date`, matching how `blueprints/gex.py`'s recorded fast path scopes its
lookup. A `nearest` series rolls weekly, so 30 days of it is four or five
different books; joining them would draw a wall jump at every roll that is the
book changing, not the market moving - the same class of error as labelling a
synthetic forward "Futures". The honest cost is that a weekly series shows only
as much history as the current contract has existed, which is the trade the
design took.
"""

from typing import Any

from database import gex_history_db
from services.gex_levels import grid
from services.option_symbol_service import normalize_options_exchange
from utils.logging import get_logger

logger = get_logger(__name__)

# What the recorder writes today. Carried on every response so phase 5's
# downsampling arrives as a value change rather than a shape change - and so a
# thinned series can never be mistaken for a market that went quiet.
NATIVE_RESOLUTION = "1m"

_VALID_FIELDS = ("levels", "grid")


def get_gex_history(
    underlying: str,
    exchange: str,
    expiry_date: str,
    weight_by: str,
    from_ts: int,
    to_ts: int,
    fields: str = "levels",
    metric: str = "gamma",
) -> tuple[bool, dict[str, Any], int]:
    """Recorded levels for one contract over a time window.

    Args:
        underlying: Underlying symbol (e.g. NIFTY).
        exchange: Options exchange (e.g. NFO).
        expiry_date: The RESOLVED expiry in DDMMMYY - not a rule.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.
        from_ts: Inclusive lower bound, epoch seconds.
        to_ts: Inclusive upper bound, epoch seconds.
        fields: 'levels' for the band series, 'grid' for the Heatmap.
        metric: 'gamma' or 'delta'. Read only by the grid - the bands draw
            levels, which are computed from gamma whichever metric is selected.

    Returns:
        Tuple of (success, response_data, status_code). An unrecorded contract is
        a SUCCESS with no points, not a 404: a series nobody chose to record is
        an ordinary state, and the study must render exactly as it did before
        this feature existed.
    """
    if weight_by not in ("oi", "volume"):
        return (
            False,
            {
                "status": "error",
                "message": f"weight_by must be 'oi' or 'volume', got {weight_by!r}",
            },
            400,
        )

    if fields not in _VALID_FIELDS:
        return (
            False,
            {"status": "error", "message": f"fields must be 'levels' or 'grid', got {fields!r}"},
            400,
        )

    if metric not in ("gamma", "delta"):
        return (
            False,
            {"status": "error", "message": f"metric must be 'gamma' or 'delta', got {metric!r}"},
            400,
        )

    try:
        from_ts = int(from_ts)
        to_ts = int(to_ts)
    except (TypeError, ValueError):
        return (
            False,
            {"status": "error", "message": "from_ts and to_ts must be epoch seconds"},
            400,
        )

    if from_ts > to_ts:
        return (
            False,
            {"status": "error", "message": "from_ts must not be after to_ts"},
            400,
        )

    try:
        # The caller may send the CHARTED instrument's exchange (NSE_INDEX for a
        # NIFTY index chart) while the watchlist stores the options exchange
        # (NFO). Matching one against the other by string finds nothing and
        # looks exactly like a feature that is switched off - which is how this
        # went unnoticed until the bands were looked at on a real chart.
        options_exchange = normalize_options_exchange(exchange)

        empty = {
            "status": "success",
            "underlying": underlying,
            "exchange": options_exchange,
            "expiry_date": expiry_date,
            "weight_by": weight_by,
            "resolution": NATIVE_RESOLUTION,
            "downsampled": False,
            # Whether this contract is on the recorder's watchlist at all, and
            # which series it belongs to. Reported here so the UI never has to
            # re-derive the exchange mapping to answer the same question - the
            # duplication that caused the bug this normalisation fixes.
            "recorded": False,
            "series_id": None,
            "points": [],
        }

        if fields == "grid":
            # The grid carries a strike axis and columns instead of points, and
            # its own metric. An unrecorded contract is still a success with an
            # empty payload, for the same reason it is for the bands.
            empty = {k: v for k, v in empty.items() if k != "points"} | {
                "metric": metric,
                "strikes": [],
                "columns": [],
                "max_abs_value": 0.0,
            }

        series = gex_history_db.get_series_by_contract(underlying, options_exchange, expiry_date)
        if series is None:
            return True, empty, 200

        if fields == "grid":
            return _grid_response(series, empty, expiry_date, from_ts, to_ts, weight_by, metric)

        rows = gex_history_db.get_snapshots_in_range(
            series["id"], from_ts, to_ts, expiry_date=expiry_date
        )

        return (
            True,
            {
                **empty,
                "recorded": True,
                "series_id": series["id"],
                "points": [_point(row, weight_by) for row in rows],
            },
            200,
        )

    except Exception:
        logger.exception("Error reading GEX history")
        return (
            False,
            {"status": "error", "message": "Error reading GEX history"},
            500,
        )


def _grid_response(
    series: dict[str, Any],
    empty: dict[str, Any],
    expiry_date: str,
    from_ts: int,
    to_ts: int,
    weight_by: str,
    metric: str,
) -> tuple[bool, dict[str, Any], int]:
    """Assemble the Heatmap's grid for one contract.

    Two passes on purpose. The first reads a light index - four columns, no
    strike children - so the column budget can be applied to a COUNT; only the
    snapshots that survive it are then paid for. Reading 8,250 columns' worth of
    strike rows to throw away fourteen of every fifteen is precisely the cost
    the budget exists to avoid.

    The window itself is already bounded by the route's `MAX_HISTORY_POINTS`
    check, which refuses an over-wide range by name rather than truncating it.
    This bounds the RESOLUTION inside whatever range survived that.
    """
    index = gex_history_db.get_snapshot_index_in_range(
        series["id"], from_ts, to_ts, expiry_date=expiry_date
    )
    resolution, bucket_seconds = grid.choose_resolution(len(index))
    selected = grid.select_representatives(index, bucket_seconds)

    strikes_by_snapshot = gex_history_db.get_strikes_for_snapshots([row["id"] for row in selected])
    built = grid.build_grid(selected, strikes_by_snapshot, metric, weight_by)

    return (
        True,
        {
            **empty,
            "recorded": True,
            "series_id": series["id"],
            "resolution": resolution,
            # Reported rather than inferred from `resolution`: a caller should
            # never have to know that "1m" happens to be the native cadence to
            # find out whether it is looking at every snapshot or one in five.
            "downsampled": resolution != NATIVE_RESOLUTION,
            **built,
        },
        200,
    )


def _point(row: dict[str, Any], weight_by: str) -> dict[str, Any]:
    """Project one stored snapshot into a band point for the requested weighting.

    Only the levels, not the strike profile: this is what Bands draws, and a
    month of strike rows is exactly what the phase 5 grid endpoint exists to
    downsample.

    `zero_gamma` is passed through including `None`. "No local cross" is a real
    reading - substituted with 0 it would draw a band along the bottom of the
    chart, and dropped it would let the line join silently across a stretch where
    the profile genuinely had no crossing.
    """
    suffix = "oi" if weight_by == "oi" else "vol"
    return {
        "ts": row["ts"],
        "call_wall": row[f"call_wall_{suffix}"],
        "put_wall": row[f"put_wall_{suffix}"],
        "zero_gamma": row[f"zero_gamma_{suffix}"],
        "net_gex": row[f"net_gex_{suffix}"],
        "regime": row[f"regime_{suffix}"],
        # Quality travels with history, so a reader can dim or hatch a stretch
        # that was degraded when it was recorded.
        "quality_verdict": row[f"quality_verdict_{suffix}"],
    }
