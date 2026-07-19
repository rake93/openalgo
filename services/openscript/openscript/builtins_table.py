"""OpenScript built-in surface — Python port of the TS builtins table
(openalgo-openscript/src/compiler/builtins-table.ts).

`ta.*` functions use hybrid signatures (implicit-OHLC + explicit-series
overloads disambiguated by argument count); each overload carries its
`kernelArgs` (how user args + injected sources/constants assemble into the
`openalgo.ta` kernel call) and multi-output kernels map destructure positions to
kernel output blocks via `outputMap`. Kept identical to the TS.
"""

from __future__ import annotations


def _a(i: int) -> dict:
    return {"arg": i}


def _s(src: str) -> dict:
    return {"source": src}


def _c(n: float) -> dict:
    return {"const": n}


def _src_len() -> dict:
    return {"outputs": 1, "outputMap": [0], "overloads": [{"params": 2, "kernelArgs": [_a(0), _a(1)]}]}


TA_FUNCTIONS: dict[str, dict] = {
    "sma": _src_len(),
    "ema": _src_len(),
    "wma": _src_len(),
    "hma": _src_len(),
    "dema": _src_len(),
    "tema": _src_len(),
    "zlema": _src_len(),
    "stdev": _src_len(),
    "rsi": _src_len(),
    "roc": _src_len(),
    "trix": _src_len(),
    "change": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [
            {"params": 1, "kernelArgs": [_a(0), _c(1)]},
            {"params": 2, "kernelArgs": [_a(0), _a(1)]},
        ],
    },
    "vwma": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 2, "kernelArgs": [_a(0), _s("volume"), _a(1)]}]},
    "highest": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [
            {"params": 1, "kernelArgs": [_s("high"), _a(0)]},
            {"params": 2, "kernelArgs": [_a(0), _a(1)]},
        ],
    },
    "lowest": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [
            {"params": 1, "kernelArgs": [_s("low"), _a(0)]},
            {"params": 2, "kernelArgs": [_a(0), _a(1)]},
        ],
    },
    "rising": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 2, "kernelArgs": [_a(0), _a(1)]}]},
    "falling": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 2, "kernelArgs": [_a(0), _a(1)]}]},
    "rma": _src_len(),
    "linreg": _src_len(),
    "barssince": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 1, "kernelArgs": [_a(0)]}]},
    "cum": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 1, "kernelArgs": [_a(0)]}]},
    # valuewhen(condition, source, occurrence) — occurrence 0-based like Pine.
    "valuewhen": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 3, "kernelArgs": [_a(0), _a(1), _a(2)]}]},
    "pivothigh": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [
            {"params": 2, "kernelArgs": [_s("high"), _a(0), _a(1)]},
            {"params": 3, "kernelArgs": [_a(0), _a(1), _a(2)]},
        ],
    },
    "pivotlow": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [
            {"params": 2, "kernelArgs": [_s("low"), _a(0), _a(1)]},
            {"params": 3, "kernelArgs": [_a(0), _a(1), _a(2)]},
        ],
    },
    "atr": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [
            {"params": 1, "kernelArgs": [_s("high"), _s("low"), _s("close"), _a(0)]},
            {"params": 4, "kernelArgs": [_a(0), _a(1), _a(2), _a(3)]},
        ],
    },
    "cci": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [
            {"params": 1, "kernelArgs": [_s("high"), _s("low"), _s("close"), _a(0)]},
            # source form: h=l=c=src makes the kernel's typical price equal src.
            {"params": 2, "kernelArgs": [_a(0), _a(0), _a(0), _a(1)]},
        ],
    },
    "tr": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 0, "kernelArgs": [_s("high"), _s("low"), _s("close")]}]},
    "obv": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 0, "kernelArgs": [_s("close"), _s("volume")]}]},
    "mfi": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [{"params": 1, "kernelArgs": [_s("high"), _s("low"), _s("close"), _s("volume"), _a(0)]}],
    },
    "crossover": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 2, "kernelArgs": [_a(0), _a(1)]}]},
    "crossunder": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 2, "kernelArgs": [_a(0), _a(1)]}]},
    "cross": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 2, "kernelArgs": [_a(0), _a(1)]}]},
    "macd": {"outputs": 3, "outputMap": [0, 1, 2], "overloads": [{"params": 4, "kernelArgs": [_a(0), _a(1), _a(2), _a(3)]}]},
    # wasm packs [upper, middle, lower]; Pine ta.bb returns [middle, upper, lower]
    "bb": {"outputs": 3, "outputMap": [1, 0, 2], "overloads": [{"params": 3, "kernelArgs": [_a(0), _a(1), _a(2)]}]},
    "ppo": {"outputs": 3, "outputMap": [0, 1, 2], "overloads": [{"params": 4, "kernelArgs": [_a(0), _a(1), _a(2), _a(3)]}]},
    "adx": {
        "outputs": 3,
        "outputMap": [0, 1, 2],
        "overloads": [
            {"params": 1, "kernelArgs": [_s("high"), _s("low"), _s("close"), _a(0)]},
            {"params": 4, "kernelArgs": [_a(0), _a(1), _a(2), _a(3)]},
        ],
    },
    "cpr": {"outputs": 3, "outputMap": [0, 1, 2], "overloads": [{"params": 0, "kernelArgs": [_s("high"), _s("low"), _s("close")]}]},
    "donchian": {"outputs": 3, "outputMap": [0, 1, 2], "overloads": [{"params": 1, "kernelArgs": [_s("high"), _s("low"), _a(0)]}]},
    "keltner": {
        "outputs": 3,
        "outputMap": [0, 1, 2],
        "overloads": [{"params": 3, "kernelArgs": [_s("high"), _s("low"), _s("close"), _a(0), _a(1), _a(2)]}],
    },
    "stochastic": {
        "outputs": 2,
        "outputMap": [0, 1],
        "overloads": [{"params": 3, "kernelArgs": [_s("high"), _s("low"), _s("close"), _a(0), _a(1), _a(2)]}],
    },
    "supertrend": {
        "outputs": 2,
        "outputMap": [0, 1],
        "overloads": [
            {"params": 2, "kernelArgs": [_s("high"), _s("low"), _s("close"), _a(1), _a(0)]},
            {"params": 5, "kernelArgs": [_a(0), _a(1), _a(2), _a(3), _a(4)]},
        ],
    },
    "tsi": {"outputs": 2, "outputMap": [0, 1], "overloads": [{"params": 4, "kernelArgs": [_a(0), _a(1), _a(2), _a(3)]}]},
    "ichimoku": {
        "outputs": 5,
        "outputMap": [0, 1, 2, 3, 4],
        "overloads": [{"params": 4, "kernelArgs": [_s("high"), _s("low"), _s("close"), _a(0), _a(1), _a(2), _a(3)]}],
    },
    "pivotpoints": {
        "outputs": 7,
        "outputMap": [0, 1, 2, 3, 4, 5, 6],
        "overloads": [{"params": 0, "kernelArgs": [_s("high"), _s("low"), _s("close")]}],
    },
}


# `kernels.*` — Nadaraya-Watson kernel regressions (Pine `KernelFunctions`
# library as a built-in namespace, D-LC1). Same TaSpec shape as `ta.*`; the
# executor dispatches them through the same kernel path. All reproduce the
# shipped Pine window quirk: the average covers exactly `startAtBar + 2` bars
# (see the LC plan §2.3). `periodic`/`locallyPeriodic` are deferred — LC does
# not call them.
KERNELS_FUNCTIONS: dict[str, dict] = {
    # rationalQuadratic(src, lookback, relativeWeight, startAtBar)
    "rationalQuadratic": {
        "outputs": 1,
        "outputMap": [0],
        "overloads": [{"params": 4, "kernelArgs": [_a(0), _a(1), _a(2), _a(3)]}],
    },
    # gaussian(src, lookback, startAtBar)
    "gaussian": {"outputs": 1, "outputMap": [0], "overloads": [{"params": 3, "kernelArgs": [_a(0), _a(1), _a(2)]}]},
}


def ta_arities(spec: dict) -> list[int]:
    return [o["params"] for o in spec["overloads"]]


def ta_overload(spec: dict, arg_count: int) -> dict | None:
    for o in spec["overloads"]:
        if o["params"] == arg_count:
            return o
    return None


MATH_FUNCTIONS: dict[str, dict] = {
    name: {"arities": [arity], "outputs": 1}
    for name, arity in {
        "abs": 1, "sign": 1, "sqrt": 1, "exp": 1, "log": 1, "log10": 1,
        "round": 1, "floor": 1, "ceil": 1, "pow": 2, "max": 2, "min": 2,
        # rolling window sum — windowed, dispatched to the rolling_sum kernel
        "sum": 2,
    }.items()
}

SPECIAL_FUNCTIONS = frozenset({"nz", "na"})
INPUT_FUNCTIONS = frozenset({"int", "float", "bool", "string", "source", "color", "timeframe"})
OUTPUT_FUNCTIONS = frozenset(
    {
        "plot",
        "hline",
        "fill",
        "plotshape",
        "plotchar",
        "plotcandle",
        "plotbar",
        "barcolor",
        "bgcolor",
        "alertcondition",
    }
)

CONSTANT_NAMESPACES: dict[str, frozenset[str]] = {
    "color": frozenset(
        {
            "green", "red", "blue", "orange", "purple", "teal", "gray", "grey", "yellow",
            "cyan", "pink", "white", "black", "navy", "maroon", "lime", "aqua", "fuchsia",
            "olive", "silver", "new",
        }
    ),
    "shape": frozenset(
        {
            "arrowup", "arrowdown", "circle", "square", "triangleup", "triangledown",
            "diamond", "flag", "labelup", "labeldown", "xcross", "cross",
        }
    ),
    "location": frozenset({"abovebar", "belowbar", "top", "bottom", "absolute"}),
    "size": frozenset({"tiny", "small", "normal", "large", "huge", "auto"}),
    "plot": frozenset(
        {
            "style_line",
            "style_stepline",
            "style_histogram",
            "style_cross",
            "style_area",
            "style_columns",
            "style_circles",
            "style_linebr",
        }
    ),
    "math": frozenset({"pi", "e", "phi", "rphi"}),
}

KNOWN_NAMESPACES = frozenset(
    {"ta", "math", "kernels", "input", "color", "shape", "location", "size", "plot"}
)
