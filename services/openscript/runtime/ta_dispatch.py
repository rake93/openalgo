"""Dispatch an IR `ta.*` call to `openalgo.ta` (numpy backend).

IR function names mirror the compiler; a few map to differently-named
`openalgo.ta` exports. Single-output kernels return an ndarray, multi-output
kernels return a tuple whose element order matches the IR `output` block index.
"""

from __future__ import annotations

import numpy as np
from openalgo import ta

# IR name → openalgo.ta export name, where they differ.
FACADE_NAME = {"bb": "bbands", "tr": "true_range", "pivotpoints": "pivot_points"}


def _cpr(high, low, close):
    """Central Pivot Range, elementwise per bar — mirrors oa_composites::cpr
    (the wasm kernel): pivot=(h+l+c)/3, bc=(h+l)/2, tc=2*pivot-bc."""
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    pivot = (h + lo + c) / 3.0
    bc = (h + lo) / 2.0
    tc = 2.0 * pivot - bc
    return pivot, bc, tc


def _rma(data, period):
    """Wilder-smoothed MA — mirrors oa_core::ema_wilder: SMA seed at the first
    `period` finite values, then r[i] = (r[i-1]*(p-1) + x[i]) / p; NaN input
    carries the previous value."""
    x = np.asarray(data, dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    p = int(period)
    if p == 0 or n == 0:
        return out
    first_valid = 0
    while first_valid < n and np.isnan(x[first_valid]):
        first_valid += 1
    if first_valid + p > n:
        return out
    seed = x[first_valid : first_valid + p]
    if np.isnan(seed).any():
        return out
    start = first_valid + p - 1
    out[start] = seed.sum() / p
    for i in range(start + 1, n):
        out[i] = out[i - 1] if np.isnan(x[i]) else (out[i - 1] * (p - 1) + x[i]) / p
    return out


def _valuewhen(cond, source, occurrence):
    """Pine ta.valuewhen — occurrence is 0-based; the SDK kernel's n is 1-based."""
    return ta.valuewhen(cond, source, int(occurrence) + 1)


def _pivot(data, left, right, high):
    """Mirrors oa_composites::pivot: strict local extreme of [p-left, p+right]
    emitted at the confirmation bar p+right; equal neighbours or NaN disqualify."""
    x = np.asarray(data, dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    left, right = int(left), int(right)
    for i in range(left + right, n):
        p = i - right
        v = x[p]
        if np.isnan(v):
            continue
        window = np.concatenate([x[p - left : p], x[p + 1 : p + right + 1]])
        if np.isnan(window).any():
            continue
        if (high and (window < v).all()) or (not high and (window > v).all()):
            out[i] = v
    return out


def _pivothigh(data, left, right):
    return _pivot(data, left, right, True)


def _pivotlow(data, left, right):
    return _pivot(data, left, right, False)


def _barssince(cond):
    """Mirrors oa_composites::bars_since: bars since cond was last nonzero;
    0 on a true bar, NaN until the first true."""
    x = np.asarray(cond, dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    last = -1
    for i in range(n):
        if x[i] != 0 and not np.isnan(x[i]):
            last = i
        if last >= 0:
            out[i] = i - last
    return out


def _cum(data):
    """Mirrors oa_composites::cum: running sum from bar 0; NaN inputs
    contribute nothing so the output stays finite."""
    x = np.asarray(data, dtype=float)
    return np.cumsum(np.where(np.isnan(x), 0.0, x))


def _rolling_sum(data, period):
    """Mirrors oa_core::rolling_sum exactly, including its sequential NaN
    propagation (a NaN entering the window poisons all later values)."""
    x = np.asarray(data, dtype=float)
    n = len(x)
    p = int(period)
    out = np.full(n, np.nan)
    if p == 0 or n < p:
        return out
    rolling = float(x[:p].sum())
    out[p - 1] = rolling
    for i in range(p, n):
        rolling = rolling + x[i] - x[i - p]
        out[i] = rolling
    return out


def _nw_fir(data, start_at_bar, weight):
    """Shared Nadaraya-Watson FIR average — mirrors oa_composites::nw_fir
    (`fn nw_fir(data, start_at_bar, weight)` in the Rust kernel). The average
    window is always exactly `start_at_bar + 2` bars, regardless of the
    caller's `lookback` — `lookback` only reshapes the weight curve via
    `weight(i)`. This is a deliberate Pine `KernelFunctions` quirk (LC-2) that
    must be reproduced verbatim, not "fixed". Any NaN inside the window
    poisons that bar's output; bars before the window fills are NaN.

    `weight(i)` is fed a numpy scalar (not a Python float) so a zero
    `lookback` divides IEEE-754 style (0/0 -> nan, x/0 -> inf) instead of
    raising `ZeroDivisionError` — matching the Rust/wasm kernel, which never
    crashes on a degenerate lookback."""
    x = np.asarray(data, dtype=float)
    n = len(x)
    window = start_at_bar + 2
    with np.errstate(divide="ignore", invalid="ignore"):
        weights = np.array([weight(np.float64(i)) for i in range(window)], dtype=float)
    cumulative = weights.sum()
    out = np.full(n, np.nan)
    for t in range(window - 1, n):
        # weights[i] pairs with x[t - i] (ascending i, most-recent bar first);
        # reversing the ascending-time slice restores that alignment for the dot.
        segment = x[t - window + 1 : t + 1][::-1]
        out[t] = float(np.dot(segment, weights) / cumulative)
    return out


def _nw_rational_quadratic(data, lookback, relative_weight, start_at_bar):
    """kernels.rationalQuadratic — mirrors oa_composites::nw_rational_quadratic.
    weight(i) = (1 + i^2/(lookback^2 * 2 * relative_weight))^-relative_weight;
    window is start_at_bar + 2 (the shared Pine quirk, see `_nw_fir`)."""
    lb = float(lookback)
    rw = float(relative_weight)
    sab = int(start_at_bar)
    return _nw_fir(data, sab, lambda i: (1.0 + (i * i) / (lb * lb * 2.0 * rw)) ** -rw)


def _nw_gaussian(data, lookback, start_at_bar):
    """kernels.gaussian — mirrors oa_composites::nw_gaussian.
    weight(i) = exp(-i^2 / (2 * lookback^2)); window is start_at_bar + 2
    (the shared Pine quirk, see `_nw_fir`)."""
    lb = float(lookback)
    sab = int(start_at_bar)
    return _nw_fir(data, sab, lambda i: np.exp(-(i * i) / (2.0 * lb * lb)))


# Kernels the engine defines but `openalgo.ta` does not export (yet), plus
# semantic adapters (valuewhen's occurrence mapping). The SDK facade wins when
# both exist EXCEPT for names listed in FORCE_LOCAL; the value-parity tests pin
# the formulas so a diverging future SDK addition fails loudly.
LOCAL_KERNELS = {
    "cpr": _cpr,
    "rma": _rma,
    "valuewhen": _valuewhen,
    "pivothigh": _pivothigh,
    "pivotlow": _pivotlow,
    "barssince": _barssince,
    "cum": _cum,
    "sum": _rolling_sum,
    "rationalQuadratic": _nw_rational_quadratic,
    "gaussian": _nw_gaussian,
}

# IR names whose local adapter must win even though `openalgo.ta` exports the
# name (different argument semantics than the raw SDK kernel).
FORCE_LOCAL = {"valuewhen"}


def facade_of(fn: str) -> str:
    return FACADE_NAME.get(fn, fn)


def invoke_kernel(fn: str, args: list):
    if fn in FORCE_LOCAL:
        return LOCAL_KERNELS[fn](*args)
    impl = getattr(ta, facade_of(fn), None)
    if impl is None or not callable(impl):
        impl = LOCAL_KERNELS.get(fn)
    if impl is None or not callable(impl):
        raise ValueError(f"unknown ta kernel: {fn}")
    try:
        return impl(*args)
    except ValueError as exc:
        # `openalgo.ta` REJECTS a window longer than the data ("Period (10)
        # cannot be greater than data length (5)"), where Pine and the TS runtime
        # both return `na` for bars the window has not warmed up on. That is a
        # cross-language divergence with a crash on one side: a chart holding
        # fewer bars than an indicator's longest window would 500 the server-side
        # execute instead of drawing nothing, and it is reachable from any script
        # whose window comes from an input.
        #
        # Narrow on purpose -- only this message is absorbed, and only into the
        # all-`na` series the other runtime already produces. Every other
        # ValueError still propagates.
        if "cannot be greater than data length" not in str(exc):
            raise
        base = next((a for a in args if isinstance(a, np.ndarray)), None)
        if base is None:
            raise
        return np.full(len(base), np.nan, dtype=float)
