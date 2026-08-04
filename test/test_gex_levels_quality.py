"""The data-quality verdict that stops a thin chain reading as signal."""

from services.gex_levels.exposure import StrikeExposure
from services.gex_levels.levels import Walls
from services.gex_levels.quality import assess_quality


def _exposures(strikes, priced=True):
    return [
        StrikeExposure(
            strike=float(s),
            call_gex=1.0,
            put_gex=-1.0,
            net_gex=0.0,
            call_iv=0.2 if priced else None,
            put_iv=0.2 if priced else None,
        )
        for s in strikes
    ]


def _walls(call_edge=False, put_edge=False):
    return Walls(
        call_wall=24600.0, put_wall=24500.0, call_wall_at_edge=call_edge, put_wall_at_edge=put_edge
    )


def test_a_full_priced_two_sided_chain_is_good():
    q = assess_quality(
        _exposures(range(24400, 24900, 100)), _walls(), forward=24600.0, total_weight=500000.0
    )
    assert q.verdict == "good"
    assert q.notes == []


def test_a_mostly_unpriced_chain_is_degraded_and_says_why():
    rows = _exposures([24400, 24500], priced=False) + _exposures([24600, 24700, 24800])
    q = assess_quality(rows, _walls(), forward=24600.0, total_weight=500000.0)
    assert q.verdict == "degraded"
    assert any("invert" in n for n in q.notes)


def test_a_one_sided_window_is_degraded():
    """Every strike above the forward: a 'put wall' is just the window edge."""
    q = assess_quality(
        _exposures([24700, 24800, 24900]), _walls(), forward=24600.0, total_weight=500000.0
    )
    assert q.verdict == "degraded"
    assert q.both_sides is False


def test_a_wall_at_the_window_edge_is_called_out():
    q = assess_quality(
        _exposures(range(24400, 24900, 100)),
        _walls(call_edge=True),
        forward=24600.0,
        total_weight=500000.0,
    )
    assert q.wall_at_edge is True
    assert any("edge" in n for n in q.notes)


def test_a_chain_with_no_weight_at_all_is_unusable():
    q = assess_quality(
        _exposures(range(24400, 24900, 100)), _walls(), forward=24600.0, total_weight=0.0
    )
    assert q.verdict == "unusable"


def test_an_empty_chain_is_unusable():
    q = assess_quality([], Walls(None, None, False, False), forward=24600.0, total_weight=0.0)
    assert q.verdict == "unusable"
    assert q.strikes_used == 0


def test_counts_are_reported_for_the_data_status_row():
    rows = _exposures([24400], priced=False) + _exposures([24500, 24600])
    q = assess_quality(rows, _walls(), forward=24600.0, total_weight=500000.0)
    assert q.strikes_used == 3
    assert q.strikes_priced == 2
