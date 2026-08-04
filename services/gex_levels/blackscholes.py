"""
Black-76 helpers, hardened against the numerical failures a live option chain
produces every session: unpriced strikes, stale premiums that will not invert,
and deep-ITM legs whose solver diverges.

Every function fails to a stated value rather than raising, because one bad
strike must not destroy a whole chain's worth of levels.
"""

import math

# Used only when not one strike in the chain yields an invertible IV, e.g. a
# fully stale chain with no usable premiums. Keeps the levels drawable and is
# reported through the quality gate so it never passes as a market reading.
FALLBACK_IV = 0.15

# Above this, an "IV" is a solver artefact rather than a market volatility.
_MAX_PLAUSIBLE_IV = 5.0


def safe_iv(
    black76, price: float, F: float, K: float, r: float, t: float, flag: str
) -> float | None:
    """
    Black-76 implied volatility as a decimal, or None if it cannot be inverted.

    Args:
        black76: The opengreeks.black76 module (injected so this stays pure).
        price: Option premium.
        F: Forward price of the underlying.
        K: Strike.
        r: Risk-free rate as a decimal (0.065, not 6.5).
        t: Time to expiry in years.
        flag: 'c' for a call, 'p' for a put.

    Returns:
        The IV as a decimal, or None when the inputs are non-positive, the
        solver raises, or the result is non-finite or implausible.
    """
    if not price or price <= 0 or F <= 0 or K <= 0 or t <= 0:
        return None
    try:
        iv = black76.implied_volatility(price, F, K, r, t, flag)
    except Exception:
        return None
    if iv is None or not math.isfinite(iv) or iv <= 0 or iv > _MAX_PLAUSIBLE_IV:
        return None
    return iv


def safe_gamma(black76, flag: str, F: float, K: float, t: float, r: float, sigma: float) -> float:
    """
    Black-76 gamma, or 0.0 on any numerical failure.

    Zero is the correct failure value here: a strike whose gamma cannot be
    computed contributes no hedging pressure to the profile.

    Args:
        black76: The opengreeks.black76 module (injected so this stays pure).
        flag: 'c' for a call, 'p' for a put.
        F: Forward price of the underlying.
        K: Strike.
        t: Time to expiry in years.
        r: Risk-free rate as a decimal (0.065, not 6.5).
        sigma: Volatility as a decimal.

    Returns:
        The gamma, or 0.0 when the inputs are non-positive, the calculation
        raises, or the result is non-finite or negative.
    """
    if not sigma or sigma <= 0 or F <= 0 or K <= 0 or t <= 0:
        return 0.0
    try:
        g = black76.gamma(flag, F, K, t, r, sigma)
    except Exception:
        return 0.0
    if g is None or not math.isfinite(g) or g < 0:
        return 0.0
    return g


def atm_iv_from(per_strike_iv: dict[float, float | None], atm_strike: float | None) -> float:
    """
    The volatility to price the whole chain with when a strike has none of its own.

    Prefers the ATM strike's own IV. Falls back to the upper-middle of the
    sorted invertible strike IVs (a true median for odd counts; for even
    counts, the higher of the two middle values), which is robust to the
    handful of far-OTM strikes whose premiums are a tick and whose inverted
    IV is nonsense. Falls back finally to FALLBACK_IV.

    Args:
        per_strike_iv: Strike to its IV (decimal), or None where it did not invert.
        atm_strike: The at-the-money strike, or None if unknown.

    Returns:
        An IV as a decimal. Never None.
    """
    if atm_strike is not None:
        at_the_money = per_strike_iv.get(atm_strike)
        if at_the_money is not None:
            return at_the_money

    valid = sorted(v for v in per_strike_iv.values() if v is not None)
    if valid:
        return valid[len(valid) // 2]
    return FALLBACK_IV
