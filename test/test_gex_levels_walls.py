"""Call Wall and Put Wall selection."""

from services.gex_levels.exposure import StrikeExposure
from services.gex_levels.levels import find_walls


def _exposure(strike, net, call=None, put=None):
    call = net if call is None else call
    put = 0.0 if put is None else put
    return StrikeExposure(
        strike=strike, call_gex=call, put_gex=put, net_gex=net, call_iv=0.2, put_iv=0.2
    )


def test_call_wall_is_the_largest_positive_net():
    rows = [_exposure(24500, -50.0), _exposure(24600, 10.0), _exposure(24800, 90.0)]
    assert find_walls(rows).call_wall == 24800


def test_put_wall_is_the_most_negative_net():
    rows = [_exposure(24500, -50.0), _exposure(24600, 10.0), _exposure(24800, 90.0)]
    assert find_walls(rows).put_wall == 24500


def test_both_walls_may_be_the_same_strike():
    """One dominant strike can hold both extremes. Nothing may assume they differ."""
    rows = [_exposure(29500, 0.0, call=500.0, put=-900.0)]
    walls = find_walls(rows)
    assert walls.call_wall == 29500
    assert walls.put_wall == 29500


def test_an_all_positive_profile_still_reports_a_put_wall():
    """The least positive strike is the put wall; None would read as 'no support'."""
    rows = [_exposure(24500, 10.0), _exposure(24600, 90.0)]
    walls = find_walls(rows)
    assert walls.call_wall == 24600
    assert walls.put_wall == 24500


def test_empty_input_yields_no_walls():
    walls = find_walls([])
    assert walls.call_wall is None
    assert walls.put_wall is None


def test_walls_report_whether_they_sit_at_the_window_edge():
    """A wall on the first or last strike may be an artefact of the fetch window."""
    rows = [_exposure(24500, -50.0), _exposure(24600, 10.0), _exposure(24800, 90.0)]
    walls = find_walls(rows)
    assert walls.call_wall_at_edge is True
    assert walls.put_wall_at_edge is True

    interior = [
        _exposure(24400, 0.0),
        _exposure(24500, -50.0),
        _exposure(24800, 90.0),
        _exposure(24900, 0.0),
    ]
    assert find_walls(interior).call_wall_at_edge is False
