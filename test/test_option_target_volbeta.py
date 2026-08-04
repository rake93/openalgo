"""Vol-beta sample construction and the clamp on an implausible estimate.

The estimator itself is covered in test_option_target_ranking.py. This file
covers turning 1-minute option bars into (return, ATM IV) samples, and the
guard that stops a runaway fit from driving the projection.
"""

import math
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from opengreeks import black76

from services.option_target.models import SmileFit
from services.option_target.volbeta import (
    DEFAULT_WINDOW_MINUTES,
    MAX_ESTIMATED_BETA,
    PRESETS,
    build_beta_samples,
    estimate_vol_beta,
)
from services.option_target_service import _BETA_BARS_CACHE, _vol_beta_samples

IST = ZoneInfo("Asia/Kolkata")

STRIKE = 24600.0
EXPIRY = datetime(2026, 8, 11, 15, 30, tzinfo=IST)
OPEN = datetime(2026, 8, 4, 9, 15, tzinfo=IST)
BASE_IV = 0.12


def _bar(ts: datetime, fwd: float, iv: float, rate: float = 0.0):
    """One (timestamp, call close, put close) bar priced off a known forward."""
    t = (EXPIRY - ts).total_seconds() / (365 * 24 * 3600)
    call = black76.black("c", fwd, STRIKE, t, rate, iv)
    put = black76.black("p", fwd, STRIKE, t, rate, iv)
    return (ts, call, put)


def _session(
    minutes: int,
    beta: float,
    *,
    rate: float = 0.0,
    start: datetime = OPEN,
    base_iv: float = BASE_IV,
):
    """Bars whose ATM IV falls by exactly `beta` vol points per 1 percent rally."""
    bars = []
    for i in range(minutes):
        ret_pct = 0.01 * i
        fwd = 24600.0 * (1 + ret_pct / 100.0)
        iv = base_iv - (beta * ret_pct) / 100.0
        bars.append(_bar(start + timedelta(minutes=i), fwd, iv, rate))
    return bars


def _samples(bars, **kwargs):
    kwargs.setdefault("strike", STRIKE)
    kwargs.setdefault("expiry", EXPIRY)
    kwargs.setdefault("rate", 0.0)
    return build_beta_samples(bars, **kwargs)


def test_build_samples_recovers_a_known_beta():
    result = estimate_vol_beta(_samples(_session(60, beta=1.5)))
    assert result["source"] == "estimated"
    assert result["beta"] == pytest.approx(1.5, abs=0.01)
    assert result["r_squared"] == pytest.approx(1.0, abs=1e-3)


def test_build_samples_recovers_a_known_beta_at_a_nonzero_rate():
    # Parity must discount correctly; getting e^{rt} wrong biases every forward.
    result = estimate_vol_beta(_samples(_session(60, beta=1.5, rate=0.07), rate=0.07))
    assert result["beta"] == pytest.approx(1.5, abs=0.01)


def test_build_samples_measures_the_return_from_the_windows_first_bar():
    samples = _samples(_session(60, beta=1.5))
    assert samples[0][0] == pytest.approx(0.0, abs=1e-9)
    assert samples[-1][0] == pytest.approx(0.59, abs=0.01)


def test_build_samples_uses_only_the_trailing_window():
    bars = _session(200, beta=1.5)
    samples = _samples(bars, window_minutes=120)
    assert len(samples) == 121


def test_build_samples_uses_the_whole_session_when_younger_than_the_window():
    # Treated as a maximum lookback, not a minimum waiting period.
    bars = _session(40, beta=1.5)
    samples = _samples(bars, window_minutes=120)
    assert len(samples) == 40


def test_default_window_is_two_hours():
    assert DEFAULT_WINDOW_MINUTES == 120


def test_build_samples_returns_empty_for_no_bars():
    assert _samples([]) == []


def test_build_samples_skips_a_bar_with_no_time_value():
    bars = _session(30, beta=1.5)
    # A forward far above the strike leaves the out-of-the-money put worthless,
    # so no implied vol can be backed out of that bar.
    bars.insert(10, (OPEN + timedelta(minutes=9, seconds=30), 5000.0, 0.0))
    assert len(_samples(bars)) == 30


def test_build_samples_corrects_fixed_strike_iv_towards_the_money():
    # A skewed smile means a fixed strike's own IV drifts as the forward moves.
    # Left uncorrected that drift is read as a vol-level change.
    fit = SmileFit(
        a=BASE_IV, b=-0.15, c=0.0, x_lo=-0.5, x_hi=0.5, rms=0.0, n_points=25, degenerate=False
    )
    bars = _session(60, beta=1.5)
    uncorrected = estimate_vol_beta(_samples(bars))["beta"]
    corrected = estimate_vol_beta(_samples(bars, fit=fit))["beta"]
    assert corrected != pytest.approx(uncorrected, abs=1e-6)
    assert corrected > uncorrected


def test_build_samples_ignores_a_degenerate_smile():
    fit = SmileFit(
        a=BASE_IV, b=-0.15, c=0.0, x_lo=-0.5, x_hi=0.5, rms=0.0, n_points=2, degenerate=True
    )
    bars = _session(60, beta=1.5)
    assert _samples(bars, fit=fit) == _samples(bars)


def test_estimate_clamps_a_beta_above_the_panic_preset():
    samples = [(0.05 * i, 12.0 - 3.0 * 0.05 * i) for i in range(40)]
    result = estimate_vol_beta(samples)
    assert result["source"] == "estimated"
    assert result["beta"] == pytest.approx(MAX_ESTIMATED_BETA)
    assert result["clamped_from"] == pytest.approx(3.0, abs=0.01)


def test_estimate_clamps_a_large_negative_beta():
    samples = [(0.05 * i, 12.0 + 3.0 * 0.05 * i) for i in range(40)]
    result = estimate_vol_beta(samples)
    assert result["beta"] == pytest.approx(-MAX_ESTIMATED_BETA)
    assert result["clamped_from"] == pytest.approx(-3.0, abs=0.01)


def test_estimate_leaves_an_in_range_beta_alone():
    samples = [(0.05 * i, 12.0 - 1.5 * 0.05 * i) for i in range(40)]
    result = estimate_vol_beta(samples)
    assert result["beta"] == pytest.approx(1.5, abs=1e-6)
    assert result["clamped_from"] is None


def test_clamp_is_the_panic_preset():
    assert MAX_ESTIMATED_BETA == PRESETS["panic"]


def test_fallback_reports_no_clamp():
    assert estimate_vol_beta([])["clamped_from"] is None


def test_build_samples_tolerates_unsorted_bars():
    bars = _session(60, beta=1.5)
    shuffled = bars[30:] + bars[:30]
    assert _samples(shuffled) == _samples(bars)


def test_build_samples_needs_a_positive_forward():
    # A nonsense quote pair must be dropped, not turned into a negative forward.
    bars = _session(30, beta=1.5)
    bars.insert(5, (OPEN + timedelta(minutes=4, seconds=30), 0.0, 1e9))
    assert len(_samples(bars)) == 30


def test_recovered_beta_is_independent_of_the_iv_level():
    low = estimate_vol_beta(_samples(_session(60, beta=1.5, base_iv=0.12)))["beta"]
    high = estimate_vol_beta(_samples(_session(60, beta=1.5, base_iv=0.30)))["beta"]
    assert low == pytest.approx(high, abs=0.02)


def test_window_boundary_is_measured_from_the_last_bar_not_the_clock():
    # Bars end at 11:00; a 120-minute window must reach back to 09:00, not to
    # two hours before "now", which would empty the window outside market hours.
    bars = _session(90, beta=1.5, start=datetime(2026, 8, 4, 9, 30, tzinfo=IST))
    assert len(_samples(bars, window_minutes=120)) == 90


def test_intraday_time_decay_does_not_leak_into_beta():
    # Time to expiry shrinks through the window. If the sample builder priced
    # every bar at a single t, the resulting IV drift would be read as beta.
    flat = []
    for i in range(90):
        ts = OPEN + timedelta(minutes=i)
        flat.append(_bar(ts, 24600.0 * (1 + 0.0001 * i), BASE_IV))
    result = estimate_vol_beta(build_beta_samples(flat, strike=STRIKE, expiry=EXPIRY, rate=0.0))
    assert abs(result["beta"]) < 0.05 or result["source"] == "fallback"


def test_parity_forward_matches_the_prices_it_came_from():
    ts = OPEN + timedelta(minutes=5)
    fwd = 24712.5
    _, call, put = _bar(ts, fwd, BASE_IV, rate=0.07)
    t = (EXPIRY - ts).total_seconds() / (365 * 24 * 3600)
    assert STRIKE + (call - put) * math.exp(0.07 * t) == pytest.approx(fwd, abs=1e-6)


# --- service plumbing: fetching the bars ------------------------------------

CALL_SYMBOL = "NIFTY11AUG2624600CE"
PUT_SYMBOL = "NIFTY11AUG2624600PE"


@pytest.fixture(autouse=True)
def _clear_beta_cache():
    """The bar cache is module-level and keyed on symbols, which every test
    here shares. Without this, one test's bars answer the next test's fetch."""
    _BETA_BARS_CACHE.clear()
    yield
    _BETA_BARS_CACHE.clear()


def _fake_history(series, *, volume=1000.0):
    """Stand in for services.history_service.get_history."""

    def _call(symbol, exchange, interval, start_date, end_date, **kwargs):
        rows = series.get(symbol)
        if rows is None:
            return False, {"status": "error", "message": f"no data for {symbol}"}, 404
        data = [
            {
                "timestamp": ts,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": vol,
                "oi": 0.0,
            }
            for ts, close, vol in rows
        ]
        return True, {"status": "success", "data": data}, 200

    return _call


def _history_series(minutes=60, beta=1.5, volume=1000.0):
    bars = _session(minutes, beta=beta)
    calls = [(ts.timestamp(), call, volume) for ts, call, _ in bars]
    puts = [(ts.timestamp(), put, volume) for ts, _, put in bars]
    return {CALL_SYMBOL: calls, PUT_SYMBOL: puts}


def _fetch(series, **kwargs):
    kwargs.setdefault("strike", STRIKE)
    kwargs.setdefault("expiry", EXPIRY)
    kwargs.setdefault("rate", 0.0)
    kwargs.setdefault("fit", None)
    kwargs.setdefault("api_key", "k")
    with patch("services.option_target_service.get_history", _fake_history(series)):
        return _vol_beta_samples(CALL_SYMBOL, PUT_SYMBOL, "NFO", **kwargs)


def test_vol_beta_samples_recovers_beta_from_history():
    result = estimate_vol_beta(_fetch(_history_series()))
    assert result["source"] == "estimated"
    assert result["beta"] == pytest.approx(1.5, abs=0.01)


def test_vol_beta_samples_returns_empty_when_history_fails():
    assert _fetch({}) == []


def test_vol_beta_samples_returns_empty_when_only_one_leg_has_history():
    series = _history_series()
    del series[PUT_SYMBOL]
    assert _fetch(series) == []


def test_vol_beta_samples_never_raises():
    def _boom(*args, **kwargs):
        raise RuntimeError("broker exploded")

    with patch("services.option_target_service.get_history", _boom):
        assert (
            _vol_beta_samples(
                CALL_SYMBOL,
                PUT_SYMBOL,
                "NFO",
                strike=STRIKE,
                expiry=EXPIRY,
                rate=0.0,
                fit=None,
                api_key="k",
            )
            == []
        )


def test_vol_beta_samples_drops_zero_volume_bars():
    # Post-close padding repeats the last close at zero volume. Kept, those
    # bars pin a flat forward against a decaying IV and fabricate a beta.
    series = _history_series(minutes=60)
    padding_start = series[CALL_SYMBOL][-1][0]
    for i in range(1, 31):
        ts = padding_start + i * 60
        series[CALL_SYMBOL].append((ts, series[CALL_SYMBOL][-1][1], 0.0))
        series[PUT_SYMBOL].append((ts, series[PUT_SYMBOL][-1][1], 0.0))
    assert len(_fetch(series)) == 60


def test_vol_beta_samples_pairs_bars_by_timestamp():
    # A leg that skipped a minute must not shift the other leg's prices onto
    # the wrong bar - that would corrupt every parity forward after the gap.
    series = _history_series(minutes=60)
    del series[PUT_SYMBOL][20]
    assert len(_fetch(series)) == 59


def test_vol_beta_samples_applies_the_window():
    series = _history_series(minutes=200)
    assert len(_fetch(series, window_minutes=120)) == 121


def test_vol_beta_samples_reuses_cached_bars():
    # The page refetches on every scenario tweak; two broker history calls per
    # keystroke is what this cache exists to stop.
    series = _history_series(minutes=60)
    calls = []

    def _counting(symbol, exchange, interval, start_date, end_date, **kwargs):
        calls.append(symbol)
        return _fake_history(series)(symbol, exchange, interval, start_date, end_date, **kwargs)

    with patch("services.option_target_service.get_history", _counting):
        first = _vol_beta_samples(
            CALL_SYMBOL,
            PUT_SYMBOL,
            "NFO",
            strike=STRIKE,
            expiry=EXPIRY,
            rate=0.0,
            fit=None,
            api_key="k",
        )
        second = _vol_beta_samples(
            CALL_SYMBOL,
            PUT_SYMBOL,
            "NFO",
            strike=STRIKE,
            expiry=EXPIRY,
            rate=0.0,
            fit=None,
            api_key="k",
        )

    assert first == second
    assert len(calls) == 2


def test_vol_beta_samples_caches_a_failure_too():
    # A delisted or illiquid strike must not cost a broker round trip on every
    # request, and the second leg is never fetched when the first found nothing.
    calls = []

    def _counting(symbol, exchange, interval, start_date, end_date, **kwargs):
        calls.append(symbol)
        return False, {"status": "error", "message": "no data"}, 404

    with patch("services.option_target_service.get_history", _counting):
        for _ in range(3):
            assert (
                _vol_beta_samples(
                    CALL_SYMBOL,
                    PUT_SYMBOL,
                    "NFO",
                    strike=STRIKE,
                    expiry=EXPIRY,
                    rate=0.0,
                    fit=None,
                    api_key="k",
                )
                == []
            )

    assert calls == [CALL_SYMBOL]
