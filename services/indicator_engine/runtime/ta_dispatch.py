"""Dispatch an IR `ta.*` call to `openalgo.ta` (numpy backend).

IR function names mirror the compiler; a few map to differently-named
`openalgo.ta` exports. Single-output kernels return an ndarray, multi-output
kernels return a tuple whose element order matches the IR `output` block index.
"""

from __future__ import annotations

from openalgo import ta

# IR name → openalgo.ta export name, where they differ.
FACADE_NAME = {"bb": "bbands", "tr": "true_range", "pivotpoints": "pivot_points"}


def facade_of(fn: str) -> str:
    return FACADE_NAME.get(fn, fn)


def invoke_kernel(fn: str, args: list):
    impl = getattr(ta, facade_of(fn), None)
    if impl is None or not callable(impl):
        raise ValueError(f"unknown ta kernel: {fn}")
    return impl(*args)
