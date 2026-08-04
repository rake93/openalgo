"""Replay regression harness for the Option Target Calculator.

Replays a completed BANKNIFTY trade recorded on 2026-08-04 (entry spot 57793
around 10:25, exit spot 57505 around 12:01, expiry 25AUG26FUT/25AUG26 options,
a 96-minute hold) across every captured strike series. The forward and smile
are reconstructed ONCE, at entry, exactly as the live service would see them;
every strike is then projected to the exit spot and compared against what it
actually traded at on exit. That measured mean absolute percentage error is
what the headline claim in `services/option_target/projection.py` rests on:

    delta only                    6.84%
    slide + vol-beta 1.5          1.26%

This module turns that measurement into a regression gate - any future change
to the projection math must not make the full model materially worse, and
must keep beating the delta-only baseline by a wide margin.

Skipped entirely when the fixture has not been captured. Recapture with:

    uv run python scripts/capture_option_target_fixture.py \\
        --underlying BANKNIFTY --expiry 25AUG26 --date 2026-08-04 \\
        --low 56800 --high 58800 --step 100 \\
        --out test/fixtures/option_target/banknifty_2026-08-04.json
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from opengreeks import black76

from services.option_target.forward import compute_forward, project_forward
from services.option_target.models import StrikeQuote
from services.option_target.projection import project_strike
from services.option_target.smile import calibrate_ivs, fit_smile

FIXTURE = Path(__file__).parent / "fixtures" / "option_target" / "banknifty_2026-08-04.json"
pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="replay fixture not captured")

IST = ZoneInfo("Asia/Kolkata")

ENTRY_SPOT, EXIT_SPOT = 57793.0, 57505.0
# (lo_minute, hi_minute) since IST midnight, bracketing when entry/exit actually happened.
ENTRY_WINDOW = (10 * 60 + 0, 11 * 60 + 0)  # 10:00 - 11:00
EXIT_WINDOW = (11 * 60 + 45, 12 * 60 + 20)  # 11:45 - 12:20
EXPIRY = datetime(2026, 8, 25, 15, 30, tzinfo=IST)

LOT_SIZE = 35
MIN_LEG_PRICE = 5.0  # legs priced under this are noise, not signal
VOL_BETA = 1.5
RATE = 0.0


def _load() -> dict:
    return json.loads(FIXTURE.read_text())


def _minute_of_day(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, IST)
    return dt.hour * 60 + dt.minute


def _bar_nearest(spot: dict[str, float], level: float, lo_min: int, hi_min: int) -> int:
    """Timestamp (epoch seconds) whose spot close is nearest `level`.

    Restricted to bars whose IST minute-of-day falls in [lo_min, hi_min], so a
    coincidentally similar price at an unrelated time of day cannot be picked.
    """
    candidates = [
        (int(ts), close)
        for ts, close in spot.items()
        if lo_min <= _minute_of_day(int(ts)) <= hi_min
    ]
    if not candidates:
        raise AssertionError(f"No spot bars with minute-of-day in [{lo_min}, {hi_min}]")
    best_ts, _ = min(candidates, key=lambda pair: abs(pair[1] - level))
    return best_ts


def _quotes_at(data: dict, ts: int) -> dict[tuple[float, str], StrikeQuote]:
    """Build StrikeQuote objects from the fixture at one timestamp.

    History has no book, so ltp = bid = ask = close, matching the fixture
    format. A strike/side with no bar at exactly this timestamp is omitted.
    """
    quotes: dict[tuple[float, str], StrikeQuote] = {}
    key = str(ts)
    for label, series in data["options"].items():
        close = series.get(key)
        if close is None:
            continue
        option_type = label[-2:]
        strike = float(label[:-2])
        quotes[(strike, option_type)] = StrikeQuote(
            strike=strike,
            option_type=option_type,
            symbol=f"{data['underlying']}{data['expiry']}{label}",
            ltp=close,
            bid=close,
            ask=close,
            oi=1,
            volume=1,
            lot_size=LOT_SIZE,
        )
    return quotes


def _atm_strike(strikes: list[float], spot: float) -> float:
    return min(strikes, key=lambda s: abs(s - spot))


def _mae(data: dict, use_full_model: bool) -> float:
    """Mean absolute percentage error projecting every strike from entry to exit.

    Forward and smile are reconstructed ONCE at entry. Every strike is then
    projected to the exit spot and compared against what it actually traded
    at on exit - either with the full model (smile slide + vol-beta 1.5) or
    the delta-only baseline `mid + delta * (forward_target - forward)`.
    """
    entry_ts = _bar_nearest(data["spot"], ENTRY_SPOT, *ENTRY_WINDOW)
    exit_ts = _bar_nearest(data["spot"], EXIT_SPOT, *EXIT_WINDOW)

    entry_quotes = _quotes_at(data, entry_ts)
    exit_quotes = _quotes_at(data, exit_ts)

    strikes = sorted({strike for strike, _ in entry_quotes})
    atm_strike = _atm_strike(strikes, ENTRY_SPOT)

    anchor = compute_forward(entry_quotes, atm_strike=atm_strike, spot=ENTRY_SPOT)

    entry_dt = datetime.fromtimestamp(entry_ts, IST)
    exit_dt = datetime.fromtimestamp(exit_ts, IST)
    t_now = (EXPIRY - entry_dt).total_seconds() / (365 * 86400)
    t_target = (EXPIRY - exit_dt).total_seconds() / (365 * 86400)

    points, _rejects = calibrate_ivs(entry_quotes, anchor.forward, t_now, RATE)
    atm_fallback = next((p.iv for p in points if abs(p.strike - atm_strike) < 1e-6), 0.12)
    fit = fit_smile(points, atm_iv_fallback=atm_fallback)

    fwd_target = project_forward(
        anchor,
        reference="SPOT",
        reference_now=ENTRY_SPOT,
        reference_target=EXIT_SPOT,
        matched_future=False,
    )

    errors: list[float] = []
    for (strike, option_type), quote in entry_quotes.items():
        actual_quote = exit_quotes.get((strike, option_type))
        if actual_quote is None:
            continue

        mid_now = quote.mid
        actual = actual_quote.mid
        if mid_now < MIN_LEG_PRICE or actual < MIN_LEG_PRICE:
            continue

        flag = "c" if option_type == "CE" else "p"
        try:
            iv_now = black76.implied_volatility(mid_now, anchor.forward, strike, RATE, t_now, flag)
        except Exception:  # noqa: BLE001 - solver failure on this leg must not kill the run
            continue

        if use_full_model:
            projected = project_strike(
                strike=strike,
                option_type=option_type,
                forward_target=fwd_target.forward,
                t_target=t_target,
                rate=RATE,
                iv_now=iv_now,
                fit=fit,
                iv_model="smile_slide",
                vol_beta=VOL_BETA,
                move_pct=fwd_target.move_pct,
                vol_shift=0.0,
            )
        else:
            delta = black76.delta(flag, anchor.forward, strike, t_now, RATE, iv_now)
            projected = mid_now + delta * (fwd_target.forward - anchor.forward)

        errors.append(abs(projected - actual) / actual * 100)

    if not errors:
        raise AssertionError("No usable legs to compute MAE - fixture may be malformed")
    return sum(errors) / len(errors)


def test_full_model_beats_delta_only_by_a_wide_margin():
    data = _load()
    assert _mae(data, use_full_model=True) < _mae(data, use_full_model=False) / 3


def test_full_model_mae_stays_within_the_recorded_band():
    # Recorded 1.26 percent. A 2.5 percent ceiling catches regressions
    # without being brittle to a fixture recapture.
    assert _mae(_load(), use_full_model=True) < 2.5


def test_delta_only_is_as_bad_as_recorded():
    # Confirms the fixture and harness still reproduce the baseline.
    assert _mae(_load(), use_full_model=False) > 4.0
