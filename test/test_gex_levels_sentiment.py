"""
Directional Sentiment - deliberately NOT the sign of net GEX.

Regime is about volatility (suppressive/amplifying); Sentiment is a separate,
genuinely directional read built from wall position, put-call ratio and IV
skew. The one test that matters most here is
`test_sentiment_is_independent_of_the_sign_of_net_gex` - the whole point of
this feature is that a gamma-driven squeeze upward must not print bearish.
"""

from services.gex_levels.exposure import ChainRow, StrikeExposure
from services.gex_levels.levels import Walls
from services.gex_levels.sentiment import COMPOSITE_BAND, read_sentiment


def _row(strike, call_oi=0.0, put_oi=0.0, call_volume=0.0, put_volume=0.0, lot_size=75):
    return ChainRow(
        strike=float(strike),
        call_price=1.0,
        put_price=1.0,
        call_oi=call_oi,
        put_oi=put_oi,
        call_volume=call_volume,
        put_volume=put_volume,
        lot_size=lot_size,
    )


def _exposure(strike, net=0.0, call_iv=0.15, put_iv=0.15):
    return StrikeExposure(
        strike=float(strike),
        call_gex=net,
        put_gex=0.0,
        net_gex=net,
        call_iv=call_iv,
        put_iv=put_iv,
    )


def _walls(call_wall=24700.0, put_wall=24500.0, call_edge=False, put_edge=False):
    return Walls(
        call_wall=call_wall,
        put_wall=put_wall,
        call_wall_at_edge=call_edge,
        put_wall_at_edge=put_edge,
    )


def _neutral_rows_and_exposures(forward=24600.0):
    """A chain shaped so both PCR and skew read neutral, leaving only the
    wall signal free to vary in the wall-position tests below."""
    rows = [
        _row(24400, call_oi=1000, put_oi=1000, call_volume=500, put_volume=500),
        _row(24600, call_oi=1000, put_oi=1000, call_volume=500, put_volume=500),
        _row(24800, call_oi=1000, put_oi=1000, call_volume=500, put_volume=500),
    ]
    exposures = [
        _exposure(24400, put_iv=0.15, call_iv=0.15),
        _exposure(24600, put_iv=0.15, call_iv=0.15),
        _exposure(24800, put_iv=0.15, call_iv=0.15),
    ]
    return rows, exposures


# ── wall position ────────────────────────────────────────────────────────────


def test_spot_above_the_call_wall_reads_bullish():
    rows, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(
        exposures,
        _walls(call_wall=24700, put_wall=24500),
        rows,
        spot=24750,
        forward=24600,
        weight_by="oi",
    )
    wall = next(x for x in s.signals if x.key == "walls")
    assert wall.bias == "bullish"
    assert "24700" in wall.detail


def test_spot_below_the_put_wall_reads_bearish():
    rows, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(
        exposures,
        _walls(call_wall=24700, put_wall=24500),
        rows,
        spot=24450,
        forward=24600,
        weight_by="oi",
    )
    wall = next(x for x in s.signals if x.key == "walls")
    assert wall.bias == "bearish"
    assert "24500" in wall.detail


def test_spot_between_the_walls_reads_neutral_and_names_the_range():
    rows, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(
        exposures,
        _walls(call_wall=24700, put_wall=24500),
        rows,
        spot=24600,
        forward=24600,
        weight_by="oi",
    )
    wall = next(x for x in s.signals if x.key == "walls")
    assert wall.bias == "neutral"
    assert "24500" in wall.detail and "24700" in wall.detail


def test_both_walls_on_one_strike_is_neutral_not_a_crash():
    rows, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(
        exposures,
        _walls(call_wall=24600, put_wall=24600),
        rows,
        spot=24600,
        forward=24600,
        weight_by="oi",
    )
    wall = next(x for x in s.signals if x.key == "walls")
    assert wall.bias == "neutral"
    assert "24600" in wall.detail


def test_a_missing_wall_is_unavailable_and_excluded_from_the_composite():
    """PCR and skew still read (both neutral here), so participating must be
    exactly 2 - the wall signal alone drops out."""
    rows, exposures = _neutral_rows_and_exposures()
    walls = Walls(call_wall=None, put_wall=24500.0, call_wall_at_edge=False, put_wall_at_edge=False)
    s = read_sentiment(exposures, walls, rows, spot=24600, forward=24600, weight_by="oi")
    wall = next(x for x in s.signals if x.key == "walls")
    assert wall.bias == "unavailable"
    assert s.participating == 2


def test_missing_spot_makes_the_wall_signal_unavailable():
    rows, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(exposures, _walls(), rows, spot=None, forward=24600, weight_by="oi")
    wall = next(x for x in s.signals if x.key == "walls")
    assert wall.bias == "unavailable"


# ── put-call ratio ───────────────────────────────────────────────────────────


def test_pcr_above_1_2_is_bullish():
    rows = [_row(24600, call_oi=1000, put_oi=1300)]
    _, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    pcr = next(x for x in s.signals if x.key == "pcr")
    assert pcr.bias == "bullish"
    assert "PCR" in pcr.detail


def test_pcr_below_0_8_is_bearish():
    rows = [_row(24600, call_oi=1300, put_oi=1000)]
    _, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    pcr = next(x for x in s.signals if x.key == "pcr")
    assert pcr.bias == "bearish"


def test_pcr_between_0_8_and_1_2_is_neutral():
    rows = [_row(24600, call_oi=1000, put_oi=1000)]
    _, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    pcr = next(x for x in s.signals if x.key == "pcr")
    assert pcr.bias == "neutral"


def test_zero_call_weight_is_unavailable():
    rows = [_row(24600, call_oi=0, put_oi=1000)]
    _, exposures = _neutral_rows_and_exposures()
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    pcr = next(x for x in s.signals if x.key == "pcr")
    assert pcr.bias == "unavailable"


def test_pcr_uses_volume_when_selected_and_oi_otherwise():
    """OI says bullish (put OI dominates), volume says bearish (call volume
    dominates) - the two disagree, so this genuinely discriminates."""
    rows = [_row(24600, call_oi=1000, put_oi=1500, call_volume=1500, put_volume=1000)]
    _, exposures = _neutral_rows_and_exposures()

    by_oi = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    by_volume = read_sentiment(
        exposures, _walls(), rows, spot=24600, forward=24600, weight_by="volume"
    )

    pcr_oi = next(x for x in by_oi.signals if x.key == "pcr")
    pcr_volume = next(x for x in by_volume.signals if x.key == "pcr")
    assert pcr_oi.bias == "bullish"
    assert pcr_volume.bias == "bearish"
    assert "open interest" in pcr_oi.detail
    assert "volume" in pcr_volume.detail


# ── IV skew ──────────────────────────────────────────────────────────────────


def test_puts_richer_than_calls_by_more_than_1_5_points_is_bearish():
    rows, _ = _neutral_rows_and_exposures()
    exposures = [
        _exposure(24400, put_iv=0.16, call_iv=0.16),  # below forward: put side
        _exposure(24800, put_iv=0.12, call_iv=0.12),  # above forward: call side
    ]
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    skew = next(x for x in s.signals if x.key == "skew")
    assert skew.bias == "bearish"
    assert "puts" in skew.detail and "calls" in skew.detail


def test_calls_richer_than_puts_by_more_than_1_5_points_is_bullish():
    rows, _ = _neutral_rows_and_exposures()
    exposures = [
        _exposure(24400, put_iv=0.12, call_iv=0.12),
        _exposure(24800, put_iv=0.16, call_iv=0.16),
    ]
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    skew = next(x for x in s.signals if x.key == "skew")
    assert skew.bias == "bullish"


def test_skew_within_the_band_is_neutral():
    rows, _ = _neutral_rows_and_exposures()
    exposures = [
        _exposure(24400, put_iv=0.150, call_iv=0.150),
        _exposure(24800, put_iv=0.151, call_iv=0.151),
    ]
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    skew = next(x for x in s.signals if x.key == "skew")
    assert skew.bias == "neutral"


def test_no_invertible_iv_on_one_side_is_unavailable():
    rows, _ = _neutral_rows_and_exposures()
    exposures = [
        _exposure(24400, put_iv=None, call_iv=None),
        _exposure(24800, put_iv=0.16, call_iv=0.16),
    ]
    s = read_sentiment(exposures, _walls(), rows, spot=24600, forward=24600, weight_by="oi")
    skew = next(x for x in s.signals if x.key == "skew")
    assert skew.bias == "unavailable"


# ── composite ────────────────────────────────────────────────────────────────


def test_all_unavailable_yields_zero_score_and_neutral_without_dividing_by_zero():
    rows = [_row(24600, call_oi=0, put_oi=0)]
    exposures = [_exposure(24600, put_iv=None, call_iv=None)]
    walls = Walls(call_wall=None, put_wall=None, call_wall_at_edge=False, put_wall_at_edge=False)
    s = read_sentiment(exposures, walls, rows, spot=None, forward=None, weight_by="oi")

    assert s.participating == 0
    assert s.score == 0.0
    assert s.bias == "neutral"


def test_one_signal_alone_can_still_produce_a_verdict():
    """Only PCR is available (walls both None, skew has no invertible IV) and
    it is strongly bullish - the composite must still resolve, with
    participating == 1 so the UI can show a one-signal verdict honestly."""
    rows = [_row(24600, call_oi=1000, put_oi=2000)]
    exposures = [_exposure(24600, put_iv=None, call_iv=None)]
    walls = Walls(call_wall=None, put_wall=None, call_wall_at_edge=False, put_wall_at_edge=False)
    s = read_sentiment(exposures, walls, rows, spot=None, forward=None, weight_by="oi")

    assert s.participating == 1
    assert s.bias == "bullish"
    assert s.score >= COMPOSITE_BAND


def test_sentiment_is_independent_of_the_sign_of_net_gex():
    """The whole point of this feature: two chains with opposite net-GEX sign
    (one suppressive, one amplifying) but identical wall/PCR/skew inputs must
    read the same Sentiment. Regime and Sentiment answer different questions."""
    rows = [_row(24600, call_oi=1500, put_oi=1000)]  # PCR bearish-leaning (0.67)
    walls = _walls(call_wall=24700, put_wall=24500)

    positive_net = [
        _exposure(24400, net=500.0, put_iv=0.12, call_iv=0.12),
        _exposure(24800, net=500.0, put_iv=0.12, call_iv=0.12),
    ]
    negative_net = [
        _exposure(24400, net=-500.0, put_iv=0.12, call_iv=0.12),
        _exposure(24800, net=-500.0, put_iv=0.12, call_iv=0.12),
    ]
    assert sum(e.net_gex for e in positive_net) > 0
    assert sum(e.net_gex for e in negative_net) < 0

    spot = 24450  # below the put wall -> bearish wall signal in both cases
    s_positive = read_sentiment(positive_net, walls, rows, spot=spot, forward=24600, weight_by="oi")
    s_negative = read_sentiment(negative_net, walls, rows, spot=spot, forward=24600, weight_by="oi")

    assert s_positive.bias == s_negative.bias
    assert s_positive.score == s_negative.score
