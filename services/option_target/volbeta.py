"""Estimate the vol-level response (beta) from the session's own data.

Beta is how many volatility POINTS ATM implied vol moves per 1% move in the
underlying, signed so a fall raises vol. Backtesting a completed BANKNIFTY
trade on 2026-08-04 measured a realised beta near 1.4, well above the 0.8 that
"normal" intuition suggests. Since beta is the single largest error term in the
projection, it is measured rather than assumed.

Pure functions throughout. `build_beta_samples` turns 1-minute ATM straddle
bars into (percent_return, atm_iv_in_vol_points) pairs; `estimate_vol_beta`
regresses them. Fetching the bars is the service layer's job.
"""

import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from opengreeks import black76

from services.option_target.models import SmileFit
from services.option_target.smile import (
    IV_LOWER_BOUND,
    IV_UPPER_BOUND,
    MIN_TIME_VALUE,
    smile_iv,
)
from utils.logging import get_logger

logger = get_logger(__name__)

PRESETS: dict[str, float] = {
    "off": 0.0,
    "calm": 0.3,
    "normal": 0.8,
    "panic": 2.0,
}

MIN_SAMPLES = 20
MIN_R_SQUARED = 0.3

# An estimate beyond the Panic preset is treated as a bad fit rather than a
# real regime. Measured on a live NIFTY weekly, short windows returned 3.0 -
# applied to a 1% target that adds 3 vol points to an 10.8% ATM IV, a 28%
# relative jump that would dominate the projection on the strength of a
# 0.18%-wide sample range. The raw value is reported, never silently dropped.
MAX_ESTIMATED_BETA = PRESETS["panic"]

# Trailing window, in minutes, of same-session bars. A MAXIMUM lookback, not a
# minimum wait: a session younger than this uses everything it has since the
# open, and the sample and fit gates decide whether that is enough. Prior
# sessions are never appended - an overnight gap is not a 1-minute return.
DEFAULT_WINDOW_MINUTES = 120.0

SECONDS_PER_YEAR = 365 * 24 * 3600


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "beta": PRESETS["normal"],
        "r_squared": 0.0,
        "samples": 0,
        "source": "fallback",
        "reason": reason,
        "clamped_from": None,
    }


def build_beta_samples(
    bars: list[tuple[datetime, float, float]],
    *,
    strike: float,
    expiry: datetime,
    rate: float,
    fit: SmileFit | None = None,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
) -> list[tuple[float, float]]:
    """Turn ATM straddle bars into (percent_return, ATM IV in vol points).

    `bars` is (timestamp, call close, put close) for one strike. Both legs of
    the same strike are used because put-call parity then gives the forward
    that bar's options were actually priced off - the same anchor the live
    snapshot uses - without a second history call for the future, and without
    assuming a spot instrument exists at all.

    Time to expiry is recomputed per bar. Holding it fixed would let the
    session's own theta decay masquerade as a change in the vol level.

    The window is measured back from the LAST bar, not from the wall clock, so
    it still yields samples when called after the close.
    """
    if not bars:
        return []

    ordered = sorted(bars, key=lambda bar: bar[0])
    cutoff = ordered[-1][0] - timedelta(minutes=window_minutes)

    rows: list[tuple[float, float]] = []
    for timestamp, call, put in ordered:
        if timestamp < cutoff:
            continue
        t_years = (expiry - timestamp).total_seconds() / SECONDS_PER_YEAR
        if t_years <= 0:
            continue

        forward = strike + (call - put) * math.exp(rate * t_years)
        if forward <= 0:
            continue

        # The out-of-the-money leg carries the time value; the in-the-money one
        # is nearly all intrinsic and inverts badly.
        flag = "p" if strike < forward else "c"
        price = put if flag == "p" else call
        intrinsic = max(strike - forward, 0.0) if flag == "p" else max(forward - strike, 0.0)
        if price <= intrinsic + MIN_TIME_VALUE:
            continue

        try:
            iv = black76.implied_volatility(price, forward, strike, rate, t_years, flag)
        except Exception:  # noqa: BLE001 - one unsolvable bar must not lose the window
            continue
        if not IV_LOWER_BOUND < iv < IV_UPPER_BOUND:
            continue

        iv_points = iv * 100.0
        if fit is not None and not fit.degenerate:
            # A fixed strike slides along the smile as the forward moves, so its
            # own IV changes even at a constant vol level. Subtracting what the
            # smile alone accounts for leaves the LEVEL change, which is what
            # beta means and what the projection applies it as.
            iv_points -= (smile_iv(fit, math.log(strike / forward)) - smile_iv(fit, 0.0)) * 100.0

        rows.append((forward, iv_points))

    if not rows:
        return []

    base_forward = rows[0][0]
    return [((forward / base_forward - 1.0) * 100.0, iv) for forward, iv in rows]


def estimate_vol_beta(samples: list[tuple[float, float]]) -> dict[str, Any]:
    """Regress ATM IV against underlying return; return the negated slope.

    `samples` is a list of (percent_return, atm_iv_vol_points) pairs relative to
    a common baseline. A weak or under-sampled fit falls back to the Normal
    preset and says why, rather than reporting a confident wrong number. An
    implausibly large estimate is clamped to `MAX_ESTIMATED_BETA`, with the raw
    value reported in `clamped_from`.
    """
    if len(samples) < MIN_SAMPLES:
        return _fallback(f"Only {len(samples)} samples, need {MIN_SAMPLES}")

    x = np.array([s[0] for s in samples], dtype=float)
    y = np.array([s[1] for s in samples], dtype=float)

    if float(np.std(x)) < 1e-9:
        return _fallback("Underlying did not move enough to estimate beta")

    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    if r_squared < MIN_R_SQUARED:
        return _fallback(f"Weak fit, R-squared {r_squared:.2f} below {MIN_R_SQUARED}")

    beta = float(-slope)
    clamped_from: float | None = None
    if abs(beta) > MAX_ESTIMATED_BETA:
        clamped_from = beta
        beta = math.copysign(MAX_ESTIMATED_BETA, beta)

    return {
        "beta": beta,
        "r_squared": r_squared,
        "samples": len(samples),
        "source": "estimated",
        "reason": "",
        "clamped_from": clamped_from,
    }
