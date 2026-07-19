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
    return impl(*args)
