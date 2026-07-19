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


# Kernels the engine defines but `openalgo.ta` does not export (yet). The SDK
# facade wins when both exist; the value-parity tests pin the formulas so a
# future SDK addition that diverges from the wasm kernel fails loudly.
LOCAL_KERNELS = {"cpr": _cpr}


def facade_of(fn: str) -> str:
    return FACADE_NAME.get(fn, fn)


def invoke_kernel(fn: str, args: list):
    impl = getattr(ta, facade_of(fn), None)
    if impl is None or not callable(impl):
        impl = LOCAL_KERNELS.get(fn)
    if impl is None or not callable(impl):
        raise ValueError(f"unknown ta kernel: {fn}")
    return impl(*args)
