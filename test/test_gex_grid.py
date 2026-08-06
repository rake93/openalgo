"""Pure-unit tests for the GEX Heatmap's grid assembly.

No database, no Flask, no broker: `services/gex_levels/grid.py` is pure, and
these run against it directly. The endpoint's own wiring is covered in
`test_gex_history_endpoint.py`.
"""

import pytest

from services.gex_levels.grid import (
    MAX_GRID_COLUMNS,
    MEDIUM_GRID_COLUMNS,
    build_grid,
    choose_resolution,
    select_representatives,
    value_column,
)

M = 60
T0 = 1_785_000_000


def snapshot(snapshot_id: int, ts: int, quality_oi="good", quality_vol="good") -> dict:
    return {
        "id": snapshot_id,
        "ts": ts,
        "quality_verdict_oi": quality_oi,
        "quality_verdict_vol": quality_vol,
    }


def strike_row(strike: float, gex_oi=0.0, gex_vol=0.0, dex_oi=0.0, dex_vol=0.0) -> dict:
    return {
        "strike": strike,
        "net_gex_oi": gex_oi,
        "net_gex_vol": gex_vol,
        "net_dex_oi": dex_oi,
        "net_dex_vol": dex_vol,
    }


class TestValueColumn:
    def test_maps_all_four_recorded_combinations(self):
        assert value_column("gamma", "oi") == "net_gex_oi"
        assert value_column("gamma", "volume") == "net_gex_vol"
        assert value_column("delta", "oi") == "net_dex_oi"
        assert value_column("delta", "volume") == "net_dex_vol"

    def test_rejects_a_pair_that_was_never_recorded(self):
        with pytest.raises(ValueError):
            value_column("vanna", "oi")


class TestChooseResolution:
    def test_an_ordinary_intraday_window_is_never_thinned(self):
        # 375 columns is one session at the recorder's one-minute cadence.
        assert choose_resolution(375) == ("1m", 60)

    def test_holds_native_resolution_right_up_to_the_budget(self):
        assert choose_resolution(MAX_GRID_COLUMNS) == ("1m", 60)

    def test_steps_to_five_minutes_one_column_past_the_budget(self):
        assert choose_resolution(MAX_GRID_COLUMNS + 1) == ("5m", 300)

    def test_steps_to_fifteen_minutes_past_the_second_ceiling(self):
        assert choose_resolution(MEDIUM_GRID_COLUMNS) == ("5m", 300)
        assert choose_resolution(MEDIUM_GRID_COLUMNS + 1) == ("15m", 900)

    def test_a_month_lands_on_the_coarsest_bucket(self):
        # ~8,250 columns for 30 days of sessions - the case the budget exists for.
        assert choose_resolution(8_250) == ("15m", 900)


class TestSelectRepresentatives:
    def test_returns_every_snapshot_at_native_resolution(self):
        index = [snapshot(i, T0 + i * M) for i in range(5)]
        assert select_representatives(index, 60) == index

    def test_keeps_one_snapshot_per_bucket(self):
        index = [snapshot(i, T0 + i * M) for i in range(10)]
        chosen = select_representatives(index, 300)

        assert len(chosen) == 2
        assert [c["ts"] for c in chosen] == [T0, T0 + 5 * M]

    def test_keeps_a_real_snapshot_rather_than_averaging(self):
        # Averaging across a wall that jumped strike would invent a
        # concentration at neither strike, so the representative is a row that
        # actually happened.
        index = [snapshot(7, T0), snapshot(8, T0 + M), snapshot(9, T0 + 2 * M)]
        chosen = select_representatives(index, 300)

        assert chosen == [snapshot(7, T0)]

    def test_an_empty_bucket_stays_absent_rather_than_being_filled(self):
        # The recorder wrote nothing for 5 buckets. Thinning must not invent
        # columns to bridge them - a gap is still a gap after downsampling.
        index = [snapshot(0, T0), snapshot(1, T0 + 30 * M)]
        chosen = select_representatives(index, 300)

        assert [c["ts"] for c in chosen] == [T0, T0 + 30 * M]

    def test_buckets_by_clock_time_not_by_every_nth_row(self):
        # With a gap in the middle, every-Nth drifts off wall-clock. These
        # timestamps sit in buckets 0, 0, 3 and 3 relative to T0.
        index = [
            snapshot(0, T0),
            snapshot(1, T0 + 2 * M),
            snapshot(2, T0 + 15 * M),
            snapshot(3, T0 + 17 * M),
        ]
        chosen = select_representatives(index, 300)

        assert [c["ts"] for c in chosen] == [T0, T0 + 15 * M]

    def test_handles_an_empty_index(self):
        assert select_representatives([], 300) == []


class TestBuildGrid:
    def test_lays_values_out_on_one_shared_strike_axis(self):
        columns = [snapshot(1, T0), snapshot(2, T0 + M)]
        strikes = {
            1: [strike_row(24_000, gex_oi=10.0), strike_row(24_100, gex_oi=-5.0)],
            2: [strike_row(24_000, gex_oi=20.0), strike_row(24_100, gex_oi=-8.0)],
        }

        grid = build_grid(columns, strikes, "gamma", "oi")

        assert grid["strikes"] == [24_000.0, 24_100.0]
        assert [c["ts"] for c in grid["columns"]] == [T0, T0 + M]
        assert grid["columns"][0]["values"] == [10.0, -5.0]
        assert grid["columns"][1]["values"] == [20.0, -8.0]

    def test_reads_the_column_the_metric_and_weighting_select(self):
        columns = [snapshot(1, T0)]
        strikes = {1: [strike_row(24_000, gex_oi=1.0, gex_vol=2.0, dex_oi=3.0, dex_vol=4.0)]}

        assert build_grid(columns, strikes, "gamma", "oi")["columns"][0]["values"] == [1.0]
        assert build_grid(columns, strikes, "gamma", "volume")["columns"][0]["values"] == [2.0]
        assert build_grid(columns, strikes, "delta", "oi")["columns"][0]["values"] == [3.0]
        assert build_grid(columns, strikes, "delta", "volume")["columns"][0]["values"] == [4.0]

    def test_a_strike_absent_from_a_column_is_null_not_zero(self):
        # The chain moved its strike window. That cell had no reading, and a
        # zero would assert gamma nobody measured.
        columns = [snapshot(1, T0), snapshot(2, T0 + M)]
        strikes = {
            1: [strike_row(24_000, gex_oi=10.0)],
            2: [strike_row(24_000, gex_oi=10.0), strike_row(24_500, gex_oi=7.0)],
        }

        grid = build_grid(columns, strikes, "gamma", "oi")

        assert grid["strikes"] == [24_000.0, 24_500.0]
        assert grid["columns"][0]["values"] == [10.0, None]
        assert grid["columns"][1]["values"] == [10.0, 7.0]

    def test_a_snapshot_with_no_strike_rows_is_dropped_not_zeroed(self):
        columns = [snapshot(1, T0), snapshot(2, T0 + M)]
        strikes = {1: [strike_row(24_000, gex_oi=10.0)]}

        grid = build_grid(columns, strikes, "gamma", "oi")

        assert [c["ts"] for c in grid["columns"]] == [T0]

    def test_normalises_across_the_whole_window_not_per_column(self):
        # Per-column normalisation would paint every column's own maximum at
        # full saturation and erase the change through time the heatmap exists
        # to show.
        columns = [snapshot(1, T0), snapshot(2, T0 + M)]
        strikes = {
            1: [strike_row(24_000, gex_oi=10.0)],
            2: [strike_row(24_000, gex_oi=-90.0)],
        }

        grid = build_grid(columns, strikes, "gamma", "oi")

        assert grid["max_abs_value"] == 90.0

    def test_carries_the_quality_verdict_for_the_requested_weighting(self):
        # A chain can be good on open interest and degraded on volume, so the
        # column a volume-weighted heatmap dims is the volume verdict.
        columns = [snapshot(1, T0, quality_oi="good", quality_vol="degraded")]
        strikes = {1: [strike_row(24_000, gex_oi=1.0, gex_vol=1.0)]}

        assert build_grid(columns, strikes, "gamma", "oi")["columns"][0]["quality"] == "good"
        assert (
            build_grid(columns, strikes, "gamma", "volume")["columns"][0]["quality"] == "degraded"
        )

    def test_handles_an_empty_selection(self):
        grid = build_grid([], {}, "gamma", "oi")

        assert grid == {"strikes": [], "columns": [], "max_abs_value": 0.0}
