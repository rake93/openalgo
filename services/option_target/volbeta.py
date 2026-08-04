"""Estimate the vol-level response (beta) from the session's own data.

Beta is how many volatility POINTS ATM implied vol moves per 1% move in the
underlying, signed so a fall raises vol. Backtesting a completed BANKNIFTY
trade on 2026-08-04 measured a realised beta near 1.4, well above the 0.8 that
"normal" intuition suggests. Since beta is the single largest error term in the
projection, it is measured rather than assumed.

Pure function: the caller supplies (percent_return, atm_iv_in_vol_points)
samples. Fetching them is the session layer's job.
"""

from typing import Any

import numpy as np

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


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "beta": PRESETS["normal"],
        "r_squared": 0.0,
        "samples": 0,
        "source": "fallback",
        "reason": reason,
    }


def estimate_vol_beta(samples: list[tuple[float, float]]) -> dict[str, Any]:
    """Regress ATM IV against underlying return; return the negated slope.

    `samples` is a list of (percent_return, atm_iv_vol_points) pairs relative to
    a common baseline. A weak or under-sampled fit falls back to the Normal
    preset and says why, rather than reporting a confident wrong number.
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

    return {
        "beta": float(-slope),
        "r_squared": r_squared,
        "samples": len(samples),
        "source": "estimated",
        "reason": "",
    }
