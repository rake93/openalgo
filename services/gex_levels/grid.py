# services/gex_levels/grid.py
"""Pure grid assembly for the GEX Heatmap: columns through time, strikes down.

No IO, no ORM, no broker - the same split every other module in this package
makes. The caller hands in a lightweight snapshot index and, once the column
budget has decided which snapshots survive, their strike rows; this module
decides the resolution, picks the representatives, and lays the values out on a
single shared strike axis.

**The budget exists because the payload is quadratic.** One session is 375
columns x 47 strikes, about 150 KB of JSON. Thirty days is ~8,250 columns and
~3.5 MB, and every one of those columns costs a strike-row read as well. So the
resolution is chosen from the column count BEFORE any strike row is loaded,
which is why `choose_resolution` takes a count rather than the rows themselves.

**A thinned grid always says so.** `resolution` and `downsampled` ride on every
response, including the un-thinned one. A heatmap that quietly dropped four of
every five columns would look like a market that went quiet, which is the same
class of error as drawing a gap as flat gamma.
"""

from typing import Any

# Above this many raw columns the grid is bucketed. Roughly 2.7 sessions at the
# recorder's one-minute cadence, so an ordinary intraday window is never thinned.
MAX_GRID_COLUMNS = 1000

# The second step's ceiling. Past it the grid moves to the coarsest bucket
# rather than growing a third rule nobody can predict from the response.
MEDIUM_GRID_COLUMNS = 5000

# Bucket width in seconds per resolution label.
RESOLUTION_SECONDS = {"1m": 60, "5m": 300, "15m": 900}

# Which stored column a (metric, weighting) pair reads. Gamma and delta are both
# recorded for both weightings off a single chain fetch, so switching either
# costs no refetch - the whole reason `metric` is a toggle rather than a study.
_VALUE_COLUMN = {
    ("gamma", "oi"): "net_gex_oi",
    ("gamma", "volume"): "net_gex_vol",
    ("delta", "oi"): "net_dex_oi",
    ("delta", "volume"): "net_dex_vol",
}


def value_column(metric: str, weight_by: str) -> str:
    """The `gex_snapshot_strike` column one (metric, weighting) pair reads.

    Args:
        metric: 'gamma' or 'delta'.
        weight_by: 'oi' or 'volume'.

    Returns:
        The column name.

    Raises:
        ValueError: If the pair is not one of the four recorded combinations.
    """
    try:
        return _VALUE_COLUMN[(metric, weight_by)]
    except KeyError:
        raise ValueError(
            f"No recorded column for metric={metric!r} weight_by={weight_by!r}"
        ) from None


def choose_resolution(column_count: int) -> tuple[str, int]:
    """Pick the bucket width for a raw column count.

    Chosen from the COUNT rather than from the rows, so the caller can apply it
    before paying to read strike children it is about to discard.

    Args:
        column_count: How many raw snapshots the window holds.

    Returns:
        Tuple of (resolution label, bucket seconds).
    """
    if column_count <= MAX_GRID_COLUMNS:
        return "1m", RESOLUTION_SECONDS["1m"]
    if column_count <= MEDIUM_GRID_COLUMNS:
        return "5m", RESOLUTION_SECONDS["5m"]
    return "15m", RESOLUTION_SECONDS["15m"]


def select_representatives(index: list[dict], bucket_seconds: int) -> list[dict]:
    """One representative snapshot per time bucket, never an average.

    Averaging across a wall that jumped strike would invent a concentration at
    neither strike, so a bucket is REPRESENTED by a real snapshot rather than
    summarised into a synthetic one. The earliest snapshot in each bucket wins,
    which makes the choice deterministic and independent of how many rows the
    bucket happens to hold.

    Bucketed by TIME rather than by taking every Nth row. With gaps in the
    recording - and there are gaps, that is the whole point of the blank-column
    rule - every-Nth drifts off wall-clock and lands columns at uneven spacing,
    while a heatmap's x axis is read as a clock. An empty bucket stays absent
    rather than being filled, so a gap is still a gap after thinning.

    Args:
        index: Snapshot dicts with at least `ts`, ordered by `ts` ascending.
        bucket_seconds: Bucket width. 60 returns `index` unchanged.

    Returns:
        The surviving snapshots, ordered by `ts` ascending.
    """
    if bucket_seconds <= RESOLUTION_SECONDS["1m"]:
        return list(index)

    chosen: list[dict] = []
    seen: set[int] = set()
    for row in index:
        bucket = int(row["ts"]) // bucket_seconds
        if bucket in seen:
            continue
        seen.add(bucket)
        chosen.append(row)
    return chosen


def build_grid(
    columns: list[dict],
    strikes_by_snapshot: dict[int, list[dict]],
    metric: str,
    weight_by: str,
) -> dict[str, Any]:
    """Lay the selected snapshots out on one shared strike axis.

    Column-oriented on purpose: one `strikes[]` axis then `{ts, values[]}` per
    column, so a timestamp costs 47 numbers rather than 47 objects. At a
    thousand columns that is the difference between a payload a browser parses
    without noticing and one it does not.

    Args:
        columns: Selected snapshot dicts, each with `id`, `ts` and the two
            `quality_verdict_*` fields. Ordered by `ts` ascending.
        strikes_by_snapshot: Strike rows keyed by snapshot id.
        metric: 'gamma' or 'delta'.
        weight_by: 'oi' or 'volume'.

    Returns:
        Dict with `strikes`, `columns` and `max_abs_value`.

        `max_abs_value` is computed here rather than in the renderer because the
        colour scale must be normalised across the WHOLE window: normalising per
        column would paint every column's own maximum at full saturation and
        erase exactly the change through time the heatmap exists to show.
    """
    column_name = value_column(metric, weight_by)
    quality_key = "quality_verdict_oi" if weight_by == "oi" else "quality_verdict_vol"

    axis = sorted({float(row["strike"]) for rows in strikes_by_snapshot.values() for row in rows})
    position = {strike: i for i, strike in enumerate(axis)}

    out_columns: list[dict] = []
    max_abs = 0.0
    for column in columns:
        rows = strikes_by_snapshot.get(column["id"])
        # A selected snapshot whose strike rows are missing is NOT an all-zero
        # column. Dropping it leaves a blank the reader already knows how to
        # read; a row of zeros would assert gamma nobody measured.
        if not rows:
            continue

        # None, not 0.0: a strike absent from this minute's chain had no
        # reading, and the renderer leaves that cell blank.
        values: list[float | None] = [None] * len(axis)
        for row in rows:
            value = row.get(column_name)
            if value is None:
                continue
            values[position[float(row["strike"])]] = value
            magnitude = abs(value)
            if magnitude > max_abs:
                max_abs = magnitude

        out_columns.append(
            {
                "ts": int(column["ts"]),
                "values": values,
                # Carried per column so the renderer can dim a degraded one.
                # Per WEIGHTING, because a chain can be good on open interest
                # and degraded on volume - the same reason the recorder stores
                # both verdicts rather than one.
                "quality": column.get(quality_key),
            }
        )

    return {"strikes": axis, "columns": out_columns, "max_abs_value": max_abs}
