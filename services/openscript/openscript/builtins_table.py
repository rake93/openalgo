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

# Compiler-expanded session predicates (openscript-session-surface-design.md
# §4.2). NOT stdlib functions: their argument is parsed at compile time and the
# expansion depends on literal-vs-input, which the stdlib mechanism cannot
# express. Lowered by ir_gen like color.from_gradient. Mirror of the TS
# SESSION_FUNCTIONS in builtins-table.ts.
SESSION_FUNCTIONS: dict[str, dict] = {
    "contains": {"arities": [1], "outputs": 1},
    "first_bar": {"arities": [1], "outputs": 1},
    "bars_in": {"arities": [1], "outputs": 1},
}

SPECIAL_FUNCTIONS = frozenset({"nz", "na"})
INPUT_FUNCTIONS = frozenset(
    {"int", "float", "bool", "string", "source", "color", "timeframe", "session"}
)
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
        # Drawing-object streams (design 0.5 §2). Lower to `level`/`zone` IR;
        # gated behind the `drawing-streams` feature, so they compile but reject
        # at admission until the Phase-1 materializer flips the feature on.
        "plotlevel",
        "plotzone",
    }
)

# Named arguments each builtin actually READS, keyed by function name.
#
# The allowlist is "what the lowering consumes", not "what Pine documents".
# Anything outside it is an argument this compiler SILENTLY DROPS -- which is what
# `label_size` did while being advertised, with zero diagnostics, until
# 2026-07-29. A warning here is therefore never a false positive.
#
# Emitted as OS2010 at WARNING severity: erroring is the correct end state but
# breaks any script currently passing an ignored argument, so this is the first
# half of a warning-then-error migration (label-size design section 9, FU-1).
#
# KEEP IN SYNC with the TypeScript `NAMED_ARGS` in `builtins-table.ts`.
# Namespaced CONTEXT properties: `namespace.property` -> ContextId (G2).
#
# Distinct from CONSTANT_NAMESPACES, whose members fold to compile-time
# constants. These resolve at EXECUTION from the dataset, so they lower to a
# `source` node -- baking the chart interval in as a constant would freeze it
# into stored IR, and a saved indicator would keep reporting the interval it was
# authored on after the user switched timeframe.
#
# One table, read by BOTH the semantic member check and the ir_gen lowering.
# Mirrors the TypeScript CONTEXT_MEMBERS in builtins-table.ts.
CONTEXT_MEMBERS: dict[str, dict[str, str]] = {
    "timeframe": {"in_seconds": "timeframe_in_seconds"},
}

NAMED_ARGS: dict[str, frozenset[str]] = {
    "indicator": frozenset({"title", "shorttitle", "overlay"}),
    "plot": frozenset({"title", "color", "linewidth", "style"}),
    "hline": frozenset({"title", "color"}),
    "fill": frozenset({"title", "color"}),
    # `price` pairs with location.absolute (OS2029/OS2030): the glyph sits AT a
    # value instead of above or below the bar.
    "plotshape": frozenset({"title", "color", "location", "shape", "size", "text", "price"}),
    "plotchar": frozenset({"title", "color", "location", "char", "text", "price"}),
    "plotcandle": frozenset({"title", "color"}),
    "plotbar": frozenset({"title", "color"}),
    "barcolor": frozenset({"title", "color"}),
    "bgcolor": frozenset({"title", "color"}),
    "alertcondition": frozenset({"title", "message", "on"}),
    "plotlevel": frozenset({
        "title", "color", "width", "style", "offset", "right_pad", "extend", "bars",
        "terminate", "max_kept", "label", "label_size", "label_latest_only",
        # `mitigated_color` IS dropped on a level, but OS2022 reports it precisely.
        # Listing it keeps ONE diagnostic per mistake.
        "mitigated_color",
        # Singular, not a list: OS1011 bans collection literals in v1 and the
        # spawn-sampled-values design declines arrays outright. Every label in
        # scope needs one value; a second would be a named sibling, never a list.
        "label_value",
        # G6: a bool literal or an `input.bool`, gating ONLY the label — gating
        # the spawn condition would hide the line the label belongs to.
        "label_visible",
    }),
    "plotzone": frozenset({
        "title", "color", "border_color", "border_style", "offset", "right_pad",
        "extend", "bars", "terminate", "mitigated_color", "max_kept", "text", "text_size",
        "text_value",
        "text_visible",
    }),
}

# Named arguments accepted by every `input.*` constructor.
INPUT_NAMED_ARGS: frozenset[str] = frozenset({
    "title", "defval", "group", "inline", "tooltip", "minval", "maxval", "step", "options",
})

CONSTANT_NAMESPACES: dict[str, frozenset[str]] = {
    "color": frozenset(
        {
            "green", "red", "blue", "orange", "purple", "teal", "gray", "grey", "yellow",
            "cyan", "pink", "white", "black", "navy", "maroon", "lime", "aqua", "fuchsia",
            "olive", "silver", "new", "rgb", "from_gradient",
        }
    ),
    "shape": frozenset(
        {
            "arrowup", "arrowdown", "circle", "square", "triangleup", "triangledown",
            "diamond", "flag", "labelup", "labeldown", "xcross", "cross",
        }
    ),
    "location": frozenset({"abovebar", "belowbar", "top", "bottom", "absolute"}),
    # `medium` is new (label-size design §3.1) — it was previously only an IR
    # bucket name that `normal` lowered to, so `size.medium` errored OS2001.
    "size": frozenset({"tiny", "small", "normal", "medium", "large", "huge", "auto"}),
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
    "alert": frozenset({"bar_close", "tick"}),
    # Drawing-object enums (design 0.5 §2/§4). `line.style_*` styles level lines
    # and zone borders (solid/dashed/dotted); `extend` is the right-edge growth
    # mode; `terminate` is the termination predicate for extend.until -- the first
    # six are directional PRICE predicates, `new_session` (G1) is the calendar one.
    # `touch` is INCLUSIVE and `straddle` is its STRICT counterpart (register P4).
    "line": frozenset({"style_solid", "style_dashed", "style_dotted"}),
    "extend": frozenset({"lastbar", "until", "bars"}),
    "terminate": frozenset(
        {
            "close_above",
            "close_below",
            "cross_above",
            "cross_below",
            "touch",
            "straddle",
            "new_session",
        }
    ),
    # request.security, same-symbol HTF (Phase 3): `syminfo.tickerid` is the
    # same-symbol marker; `barmerge.lookahead_*` selects the merge policy.
    #
    # `lookahead_on` and `gaps_on` are listed DELIBERATELY even though only
    # `lookahead_off` is supported. Both sides must KNOW the identifier so
    # `barmerge.lookahead_on` resolves and then fails the semantic check with
    # OS2028 ("must be barmerge.lookahead_off"). Omitting it would produce OS2001
    # "unknown identifier" instead, which says nothing about lookahead being
    # unsupported on purpose.
    "syminfo": frozenset({"tickerid"}),
    "barmerge": frozenset({"lookahead_off", "lookahead_on", "gaps_off", "gaps_on"}),
}

#: Value-returning namespaced calls that are neither ta/math/kernels kernels nor
#: constant-namespace members. `request.security` lowers to `htf` IR nodes.
REQUEST_FUNCTIONS = frozenset({"security"})

#: Source series a same-symbol HTF `request.security` may sample (design §1). A
#: kind missing here means a script that compiles in the browser is rejected on the
#: server — the G3 failure mode this port exists to close.
HTF_SOURCE_KINDS = frozenset(
    {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "time"}
)

KNOWN_NAMESPACES = frozenset(
    {
        "ta", "math", "kernels", "input", "color", "shape", "location", "size", "plot", "alert",
        "line", "extend", "terminate",
        # Phase 3 / C4. NOTE: the TS set is a SUPERSET — it also carries
        # editor-only namespaces (`timeframe`) that drive completion and hover but
        # never reach compilation. The shared fixture therefore checks membership of
        # the namespaces THIS feature adds, not set equality.
        "request", "syminfo", "barmerge",
        # Bundled standard-library namespaces (openscript-stdlib-design.md §4).
        # They resolve exactly like `ta.`/`math.` -- a table entry, no parser
        # change. Authoritative membership is the registry in `stdlib.py`; this
        # list is pinned against it by test_openscript_stdlib.py.
        "candle", "fvg", "ob", "rjb", "pivot", "bos",
    }
)
