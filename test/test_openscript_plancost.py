"""Phase 0.2 PlanCost mode config — mirrors
openalgo-openscript/tests/plancost-config.test.ts.
"""

import math

import pytest

from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.openscript.builtins_table import (
    KERNELS_FUNCTIONS,
    MATH_FUNCTIONS,
    TA_FUNCTIONS,
)
from services.openscript.runtime.cost_expr import CostCtx, CostExprError, eval_cost_expr
from services.openscript.runtime.operator_cost import (
    COST_MODEL_VERSION,
    COVERED_FUNCTIONS,
    cost_family_of,
    cost_of,
    has_cost,
    scan_expr_size,
)
from services.openscript.runtime.plancost import FIELD_COUNTS, estimate_plan_cost
from services.openscript.runtime.plancost_config import plancost_mode


def test_plancost_mode_defaults_to_observe():
    assert plancost_mode() == "observe"


# --- CostExpr DSL + evaluator — mirrors openalgo-openscript/tests/cost-expr.test.ts ---

_CTX = CostCtx(
    bar_count=2000,
    input_bound=lambda i: 50 if i == "len" else math.nan,
    arg_const=lambda n: 20 if n == 3 else math.nan,
)


def test_cost_expr_evaluates_every_node_kind_deterministically():
    assert eval_cost_expr({"k": "lit", "v": 7}, _CTX) == 7
    assert eval_cost_expr({"k": "barCount"}, _CTX) == 2000
    assert eval_cost_expr({"k": "inputBound", "id": "len"}, _CTX) == 50
    assert eval_cost_expr({"k": "argConst", "nodeId": 3}, _CTX) == 20
    assert (
        eval_cost_expr({"k": "mul", "a": {"k": "barCount"}, "b": {"k": "lit", "v": 8}}, _CTX)
        == 16000
    )
    assert (
        eval_cost_expr({"k": "add", "a": {"k": "lit", "v": 1}, "b": {"k": "barCount"}}, _CTX)
        == 2001
    )
    assert eval_cost_expr({"k": "pow", "a": {"k": "barCount"}, "b": 2}, _CTX) == 4_000_000
    assert (
        eval_cost_expr({"k": "max", "a": {"k": "lit", "v": 1}, "b": {"k": "lit", "v": 5}}, _CTX)
        == 5
    )


def test_cost_expr_nested_composition_superlinear_integer_exponent():
    # mul(pow(barCount, 2), inputBound('len')) — quadratic kernel shape
    e = {
        "k": "mul",
        "a": {"k": "pow", "a": {"k": "barCount"}, "b": 2},
        "b": {"k": "inputBound", "id": "len"},
    }
    assert eval_cost_expr(e, _CTX) == 200_000_000


def test_cost_expr_pow_is_repeated_multiplication_bit_identical():
    # Past 2^53 libm pow and a multiply-fold can differ by 1 ulp; the DSL
    # semantics ARE the fold — assert the implementation matches it exactly.
    p457 = 1.0
    for _ in range(6):
        p457 *= 457.0
    assert eval_cost_expr({"k": "pow", "a": {"k": "lit", "v": 457}, "b": 6}, _CTX) == p457
    big = 1.0 * 123456789.0 * 123456789.0
    assert eval_cost_expr({"k": "pow", "a": {"k": "lit", "v": 123456789}, "b": 2}, _CTX) == big
    assert eval_cost_expr({"k": "pow", "a": {"k": "lit", "v": 5}, "b": 0}, _CTX) == 1
    assert eval_cost_expr({"k": "pow", "a": {"k": "lit", "v": 5}, "b": 1}, _CTX) == 5
    # JSON may deliver an integral float exponent — accepted
    assert eval_cost_expr({"k": "pow", "a": {"k": "lit", "v": 5}, "b": 2.0}, _CTX) == 25


def test_cost_expr_non_integer_pow_exponent_raises():
    with pytest.raises(CostExprError, match=r"invalid pow exponent: 1\.5"):
        eval_cost_expr({"k": "pow", "a": {"k": "barCount"}, "b": 1.5}, _CTX)
    with pytest.raises(CostExprError, match="invalid pow exponent"):
        eval_cost_expr({"k": "pow", "a": {"k": "barCount"}, "b": -1}, _CTX)
    with pytest.raises(CostExprError, match="invalid pow exponent"):
        eval_cost_expr({"k": "pow", "a": {"k": "barCount"}, "b": 65}, _CTX)
    with pytest.raises(CostExprError, match="invalid pow exponent"):
        eval_cost_expr({"k": "pow", "a": {"k": "barCount"}, "b": math.nan}, _CTX)


def test_cost_expr_unresolvable_ref_raises():
    with pytest.raises(ValueError, match="unresolved input bound: missing"):
        eval_cost_expr({"k": "inputBound", "id": "missing"}, _CTX)
    with pytest.raises(ValueError, match="unresolved arg const: 99"):
        eval_cost_expr({"k": "argConst", "nodeId": 99}, _CTX)


def test_cost_expr_overflow_to_infinity_is_allowed():
    huge = {"k": "pow", "a": {"k": "lit", "v": 1e308}, "b": 2}
    assert eval_cost_expr(huge, _CTX) == math.inf


def test_cost_expr_arithmetic_nan_raises():
    # inf * 0 = NaN in IEEE754 — must not slip through as "cost unknown"
    e = {
        "k": "mul",
        "a": {"k": "pow", "a": {"k": "lit", "v": 1e308}, "b": 2},
        "b": {"k": "lit", "v": 0},
    }
    with pytest.raises(ValueError, match="NaN"):
        eval_cost_expr(e, _CTX)


def test_cost_expr_nan_literal_raises():
    with pytest.raises(ValueError, match="NaN"):
        eval_cost_expr({"k": "lit", "v": math.nan}, _CTX)


def test_cost_expr_negative_zero_leaves_normalize_to_plus_zero():
    r = eval_cost_expr({"k": "lit", "v": -0.0}, _CTX)
    assert r == 0.0 and math.copysign(1.0, r) == 1.0
    r = eval_cost_expr({"k": "max", "a": {"k": "lit", "v": -0.0}, "b": {"k": "lit", "v": 0}}, _CTX)
    assert r == 0.0 and math.copysign(1.0, r) == 1.0


def test_cost_expr_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown CostExpr kind"):
        eval_cost_expr({"k": "div", "a": 1}, _CTX)


def test_cost_expr_missing_subkey_raises_dsl_error_not_key_error():
    with pytest.raises(CostExprError, match="malformed CostExpr node"):
        eval_cost_expr({"k": "add", "a": {"k": "lit", "v": 1}}, _CTX)  # missing "b"
    with pytest.raises(
        CostExprError, match="malformed CostExpr node: inputBound requires string id"
    ):
        eval_cost_expr({"k": "inputBound"}, _CTX)
    with pytest.raises(
        CostExprError, match="malformed CostExpr node: argConst requires numeric nodeId"
    ):
        eval_cost_expr({"k": "argConst"}, _CTX)
    with pytest.raises(CostExprError, match="malformed CostExpr node"):
        eval_cost_expr(None, _CTX)  # type: ignore[arg-type]


def test_cost_expr_bigint_lit_raises_dsl_error_not_overflow_error():
    # Python json parses arbitrary-precision ints (JS JSON.parse would give
    # Infinity); math.isfinite raises OverflowError on them — must surface as
    # the single DSL error type, never a leaked OverflowError.
    with pytest.raises(CostExprError, match="non-finite CostExpr lit"):
        eval_cost_expr({"k": "lit", "v": 10**400}, _CTX)


def test_cost_expr_error_is_a_value_error():
    # Task 7 admission catches one exception type; keep it a ValueError subclass.
    assert issubclass(CostExprError, ValueError)


# --- Operator-cost registry — mirrors openalgo-openscript/tests/operator-cost.test.ts ---

_LIT1 = {"k": "lit", "v": 1}
_BARS = {"k": "barCount"}
_SERIES_BYTES = {"k": "mul", "a": {"k": "lit", "v": 8}, "b": {"k": "barCount"}}


def _prog(nodes: list[dict], inputs: list[dict] | None = None) -> dict:
    return {
        "version": 1,
        "compilerVersion": "openscript-1.0",
        "sourceHash": "test",
        "declaration": {"name": "Cost", "overlay": False},
        "inputs": inputs or [],
        "nodes": nodes,
        "outputs": [],
        "meta": {"warmupBars": 0, "spans": {}},
    }


def test_operator_cost_model_version_is_1():
    assert COST_MODEL_VERSION == 1


def test_operator_cost_source_is_element_class():
    ir = _prog([{"id": 0, "op": "source", "source": "close"}])
    assert cost_of(ir["nodes"][0], ir) == {
        "perBarCost": _LIT1,
        "totalCost": _BARS,
        "bytesCost": _SERIES_BYTES,
    }


def test_operator_cost_element_ops():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "source", "source": "open"},
            {"id": 2, "op": "binop", "operator": "+", "args": [0, 1]},
            {"id": 3, "op": "unop", "operator": "-", "arg": 2},
            {"id": 4, "op": "select", "cond": 2, "then": 0, "else": 1},
            {"id": 5, "op": "hist", "arg": 0, "offset": 3},
            {"id": 6, "op": "nz", "arg": 5},
        ]
    )
    for node_id in (2, 3, 4, 5, 6):
        assert cost_of(ir["nodes"][node_id], ir) == {
            "perBarCost": _LIT1,
            "totalCost": _BARS,
            "bytesCost": _SERIES_BYTES,
        }


def test_operator_cost_const_scalar_is_fixed():
    ir = _prog([{"id": 0, "op": "const", "value": 20}])
    assert cost_of(ir["nodes"][0], ir) == {
        "perBarCost": {"k": "lit", "v": 0},
        "totalCost": _LIT1,
        "bytesCost": {"k": "lit", "v": 8},
    }


def test_operator_cost_input_scalar_vs_source_series():
    ir = _prog(
        [
            {"id": 0, "op": "input", "inputId": "len"},
            {"id": 1, "op": "input", "inputId": "src"},
        ],
        inputs=[
            {"id": "len", "type": "integer", "label": "Length", "defaultValue": 14, "max": 500},
            {"id": "src", "type": "source", "label": "Source", "defaultValue": "close"},
        ],
    )
    assert cost_of(ir["nodes"][0], ir)["totalCost"] == _LIT1
    assert cost_of(ir["nodes"][1], ir) == {
        "perBarCost": _LIT1,
        "totalCost": _BARS,
        "bytesCost": _SERIES_BYTES,
    }


def test_operator_cost_windowed_ema_literal_period_uses_arg_const():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 20},
            {"id": 2, "op": "call", "namespace": "ta", "function": "ema", "args": [0, 1]},
        ]
    )
    c = cost_of(ir["nodes"][2], ir)
    length = {"k": "argConst", "nodeId": 1}
    assert c["perBarCost"] == length
    assert c["totalCost"] == {"k": "mul", "a": length, "b": _BARS}
    assert c["bytesCost"] == _SERIES_BYTES
    ctx = CostCtx(
        bar_count=1000,
        input_bound=lambda _i: math.nan,
        arg_const=lambda n: 20 if n == 1 else math.nan,
    )
    assert eval_cost_expr(c["perBarCost"], ctx) == 20
    assert eval_cost_expr(c["totalCost"], ctx) == 20_000
    assert eval_cost_expr(c["bytesCost"], ctx) == 8000


def test_operator_cost_windowed_sma_input_period_uses_input_bound():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "input", "inputId": "len"},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
        ],
        inputs=[
            {"id": "len", "type": "integer", "label": "Length", "defaultValue": 14, "max": 500}
        ],
    )
    assert cost_of(ir["nodes"][2], ir)["perBarCost"] == {"k": "inputBound", "id": "len"}


def test_operator_cost_computed_period_falls_back_to_conservative_lit():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 10},
            {"id": 2, "op": "binop", "operator": "+", "args": [1, 1]},
            {"id": 3, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 2]},
        ]
    )
    assert cost_of(ir["nodes"][3], ir)["perBarCost"] == {
        "k": "lit",
        "v": SCRIPT_LIMITS["maximumLookback"],
    }


def test_operator_cost_macd_sums_window_terms():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 12},
            {"id": 2, "op": "const", "value": 26},
            {"id": 3, "op": "const", "value": 9},
            {
                "id": 4,
                "op": "call",
                "namespace": "ta",
                "function": "macd",
                "args": [0, 1, 2, 3],
                "output": 0,
            },
        ]
    )
    c = cost_of(ir["nodes"][4], ir)
    fast = {"k": "argConst", "nodeId": 1}
    slow = {"k": "argConst", "nodeId": 2}
    signal = {"k": "argConst", "nodeId": 3}
    assert c["perBarCost"] == {"k": "add", "a": {"k": "add", "a": fast, "b": slow}, "b": signal}
    ctx = CostCtx(
        bar_count=100,
        input_bound=lambda _i: math.nan,
        arg_const=lambda n: {1: 12, 2: 26, 3: 9}.get(n, math.nan),
    )
    assert eval_cost_expr(c["perBarCost"], ctx) == 47


def test_operator_cost_pivothigh_charges_left_right_plus_one():
    # real emitted shape: (HIGH source, left, right) — kernel arity 3
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "high"},
            {"id": 1, "op": "const", "value": 4},
            {"id": 2, "op": "const", "value": 2},
            {"id": 3, "op": "call", "namespace": "ta", "function": "pivothigh", "args": [0, 1, 2]},
        ]
    )
    left = {"k": "argConst", "nodeId": 1}
    right = {"k": "argConst", "nodeId": 2}
    assert cost_of(ir["nodes"][3], ir)["perBarCost"] == {
        "k": "add",
        "a": {"k": "add", "a": left, "b": right},
        "b": _LIT1,
    }


def test_operator_cost_stream_kernels_charge_small_constants():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "call", "namespace": "ta", "function": "cum", "args": [0]},
            {"id": 2, "op": "call", "namespace": "ta", "function": "tr", "args": []},
        ]
    )
    assert cost_of(ir["nodes"][1], ir)["perBarCost"] == {"k": "lit", "v": 2}
    assert cost_of(ir["nodes"][2], ir)["perBarCost"] == {"k": "lit", "v": 4}


def test_operator_cost_math_elementwise_is_2_per_bar():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "call", "namespace": "math", "function": "abs", "args": [0]},
        ]
    )
    assert cost_of(ir["nodes"][1], ir) == {
        "perBarCost": {"k": "lit", "v": 2},
        "totalCost": {"k": "mul", "a": {"k": "lit", "v": 2}, "b": _BARS},
        "bytesCost": _SERIES_BYTES,
    }


def test_operator_cost_math_sum_is_windowed():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 14},
            {"id": 2, "op": "call", "namespace": "math", "function": "sum", "args": [0, 1]},
        ]
    )
    assert cost_of(ir["nodes"][2], ir)["perBarCost"] == {"k": "argConst", "nodeId": 1}


def test_operator_cost_kernels_charge_quirk_window():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 8},
            {"id": 2, "op": "const", "value": 1},
            {"id": 3, "op": "const", "value": 25},
            {
                "id": 4,
                "op": "call",
                "namespace": "kernels",
                "function": "rationalQuadratic",
                "args": [0, 1, 2, 3],
            },
        ]
    )
    # 4 units per window element over startAtBar elements + 8 fixed (the +2 quirk bars)
    assert cost_of(ir["nodes"][4], ir)["perBarCost"] == {
        "k": "add",
        "a": {"k": "mul", "a": {"k": "lit", "v": 4}, "b": {"k": "argConst", "nodeId": 3}},
        "b": {"k": "lit", "v": 8},
    }


def test_operator_cost_scan_per_bar_is_expr_tree_size():
    expr = {"k": "bin", "op": "+", "a": {"k": "prev"}, "b": {"k": "input", "i": 0}}
    assert scan_expr_size(expr) == 3
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "scan", "init": None, "expr": expr, "inputs": [0]},
        ]
    )
    assert cost_of(ir["nodes"][1], ir) == {
        "perBarCost": {"k": "lit", "v": 3},
        "totalCost": {"k": "mul", "a": {"k": "lit", "v": 3}, "b": _BARS},
        "bytesCost": _SERIES_BYTES,
    }


def test_operator_cost_scan_expr_size_counts_every_kind():
    expr = {
        "k": "select",
        "c": {"k": "un", "op": "isna", "a": {"k": "prevh"}},
        "t": {"k": "const", "v": 0},
        "e": {
            "k": "nz",
            "a": {"k": "math", "fn": "max", "args": [{"k": "prev"}, {"k": "input", "i": 0}]},
            "b": {"k": "const", "v": None},
        },
    }
    assert scan_expr_size(expr) == 9


def test_operator_cost_coverage_every_builtin_is_priced():
    expected = set()
    expected.update(f"ta.{fn}" for fn in TA_FUNCTIONS)
    expected.update(f"math.{fn}" for fn in MATH_FUNCTIONS)
    expected.update(f"kernels.{fn}" for fn in KERNELS_FUNCTIONS)
    for key in expected:
        ns, fn = key.split(".")
        assert has_cost(ns, fn), f"unpriced builtin: {key}"
    # ...and the registry has no stray entries beyond the builtins surface
    assert set(COVERED_FUNCTIONS) == expected


def test_operator_cost_unregistered_function_raises():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "call", "namespace": "ta", "function": "nope", "args": [0]},
        ]
    )
    assert not has_cost("ta", "nope")
    with pytest.raises(ValueError, match=r"unpriced operator: ta\.nope"):
        cost_of(ir["nodes"][1], ir)


def test_operator_cost_unknown_node_op_raises():
    ir = _prog([{"id": 0, "op": "source", "source": "close"}])
    with pytest.raises(ValueError, match="unpriced operator: teleport"):
        cost_of({"id": 1, "op": "teleport"}, ir)


def test_operator_cost_no_fractional_pow_in_v1_registry():
    """Walk every covered function at every accepted arity and assert no pow
    node carries a non-integer exponent (v1 has no superlinear kernel)."""

    def assert_no_fractional_pow(e: dict) -> None:
        k = e["k"]
        if k == "pow":
            assert float(e["b"]).is_integer(), f"fractional pow exponent {e['b']}"
            assert_no_fractional_pow(e["a"])
        elif k in ("add", "mul", "max"):
            assert_no_fractional_pow(e["a"])
            assert_no_fractional_pow(e["b"])

    tables = [("ta", TA_FUNCTIONS), ("math", MATH_FUNCTIONS), ("kernels", KERNELS_FUNCTIONS)]
    for ns, table in tables:
        for fn, spec in table.items():
            # KERNEL arities — the shape ir_gen actually emits (post source/const
            # injection); math.* passes user args through (kernel == user arity).
            if "overloads" in spec:
                arities = sorted({len(o["kernelArgs"]) for o in spec["overloads"]})
            else:
                arities = list(spec["arities"])
            for arity in arities:
                nodes = [{"id": 0, "op": "source", "source": "close"}]
                args = []
                for i in range(arity):
                    nodes.append({"id": i + 1, "op": "const", "value": 10})
                    args.append(i + 1)
                call_id = arity + 1
                nodes.append(
                    {"id": call_id, "op": "call", "namespace": ns, "function": fn, "args": args}
                )
                ir = _prog(nodes)
                c = cost_of(ir["nodes"][call_id], ir)
                assert_no_fractional_pow(c["perBarCost"])
                assert_no_fractional_pow(c["totalCost"])
                assert_no_fractional_pow(c["bytesCost"])


# ir_gen assembles the matched overload's kernelArgs, injecting implicit source
# series (high/low/close/volume) and constants into call.args — so the emitted
# IR arity is the KERNEL arity, not the user-param count. Every source below
# compiles for real; the expected number pins BOTH the arity key and the
# period-arg kernel index (resolved from the compiled IR's own const nodes — a
# wrong index hits a source node and falls back to lit(20000), failing the
# exact-value assertion). Mirrors the TS real-compile test.
_REAL_KERNEL_CASES = [
    # the 7 kernels whose IR arity != user arity via injected sources:
    ("vwma", "plot(ta.vwma(close, 20))", 40),  # 2*20
    ("cci", "plot(ta.cci(20))", 40),  # 2*20 (implicit-OHLC form)
    ("cci", "plot(ta.cci(close, 20))", 40),  # 2*20 (source form)
    ("mfi", "plot(ta.mfi(14))", 30),  # 2*14 + 2
    ("donchian", "[du, dm, dl] = ta.donchian(20)\nplot(du)\nplot(dm)\nplot(dl)", 40),
    ("keltner", "[ku, km, kl] = ta.keltner(20, 10, 3)\nplot(ku)\nplot(km)\nplot(kl)", 32),
    ("stochastic", "[sk, sd] = ta.stochastic(14, 3, 3)\nplot(sk)\nplot(sd)", 34),
    (
        "ichimoku",
        "[ic, ib, isa, isb, il] = ta.ichimoku(9, 26, 52, 26)\n"
        "plot(ic)\nplot(ib)\nplot(isa)\nplot(isb)\nplot(il)",
        174,  # 2*9 + 2*26 + 2*52 (displacement uncharged)
    ),
    # injected-source kernels whose kernel arity was already priced — pinned:
    ("atr", "plot(ta.atr(14))", 16),  # 14 + 2
    ("adx", "[ap, am, ax] = ta.adx(14)\nplot(ap)\nplot(am)\nplot(ax)", 44),  # 3*14 + 2
    ("supertrend", "[sv, sr] = ta.supertrend(3, 10)\nplot(sv)\nplot(sr)", 14),  # 10 + 4
    ("pivothigh", "plot(ta.pivothigh(4, 2))", 7),  # 4 + 2 + 1
    ("pivotlow", "plot(ta.pivotlow(4, 2))", 7),
    ("highest", "plot(ta.highest(20))", 20),
    ("lowest", "plot(ta.lowest(20))", 20),
    ("change", "plot(ta.change(close))", 2),  # stream; injected const lag
    ("tr", "plot(ta.tr())", 4),
    ("obv", "plot(ta.obv())", 4),
    ("cpr", "[cp, cb, ct] = ta.cpr()\nplot(cp)\nplot(cb)\nplot(ct)", 6),
    (
        "pivotpoints",
        "[pp, pr1, ps1, pr2, ps2, pr3, ps3] = ta.pivotpoints()\n"
        "plot(pp)\nplot(pr1)\nplot(ps1)\nplot(pr2)\nplot(ps2)\nplot(pr3)\nplot(ps3)",
        14,
    ),
]


def test_operator_cost_prices_real_compiler_ir_injected_source_kernels():
    from services.openscript import openscript

    for fn, source, expected_per_bar in _REAL_KERNEL_CASES:
        result = openscript.compile(source)
        assert result.diagnostics == [], f"{fn}: {result.diagnostics}"
        assert result.ir is not None
        ir = result.ir
        # EVERY node of the real IR must price — no unpriced arity/operator.
        for node in ir["nodes"]:
            cost_of(node, ir)  # raises on any unpriced shape
        call = next(n for n in ir["nodes"] if n["op"] == "call" and n["function"] == fn)
        ctx = CostCtx(
            bar_count=100,
            input_bound=lambda _i: math.nan,
            arg_const=lambda node_id, _ir=ir: (
                float(_ir["nodes"][node_id]["value"])
                if _ir["nodes"][node_id]["op"] == "const"
                and isinstance(_ir["nodes"][node_id].get("value"), (int, float))
                and not isinstance(_ir["nodes"][node_id].get("value"), bool)
                else math.nan
            ),
        )
        assert eval_cost_expr(cost_of(call, ir)["perBarCost"], ctx) == expected_per_bar, fn


# --- Symbolic PlanCost estimator — mirrors openalgo-openscript/tests/plancost.test.ts ---


def _compile_ir(source: str) -> dict:
    from services.openscript import openscript

    result = openscript.compile(source)
    assert result.diagnostics == [], f"{source}: {result.diagnostics}"
    assert result.ir is not None
    return result.ir


def _ctx_of(ir: dict, bars: int) -> CostCtx:
    """Admission-shaped ctx resolving argConst from the IR's own const nodes."""

    def arg_const(node_id: int) -> float:
        nodes = ir["nodes"]
        node = nodes[node_id] if 0 <= node_id < len(nodes) else None
        if node is not None and node["op"] == "const":
            v = node.get("value")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return math.nan

    return CostCtx(bar_count=bars, input_bound=lambda _i: math.nan, arg_const=arg_const)


def test_plancost_cost_model_version_mirrors_registry():
    ir = _compile_ir("plot(close)")
    assert estimate_plan_cost(ir)["costModelVersion"] == COST_MODEL_VERSION


def test_plancost_macd_charges_kernel_once_plus_three_projections():
    # Real compiled IR: source close(0), const 12(1)/26(2)/9(3), and THREE
    # call nodes (4,5,6) sharing args [0,1,2,3] with output 0/1/2 — the exact
    # shape the runtime kernel cache computes once and slices.
    ir = _compile_ir("[m, s, h] = ta.macd(close, 12, 26, 9)\nplot(m)\nplot(s)\nplot(h)")
    assert sum(1 for n in ir["nodes"] if n["op"] == "call") == 3
    cost = estimate_plan_cost(ir)
    ctx = _ctx_of(ir, 1000)

    # kernel perBar = 12 + 26 + 9 = 47; grouped total =
    #   1000 (source) + 3 (consts) + 47_000 (compute ONCE) + 3*1000 (projections)
    assert eval_cost_expr(cost["totalOperations"], ctx) == 51_003
    # perBar = 1 (source) + 0*3 (consts) + 47 (compute) + 3*1 (projections)
    assert eval_cost_expr(cost["perBarOperations"], ctx) == 51
    # bytes = 8_000 (source) + 24 (consts) + 3 blocks * 8_000 (group, ALL
    # output blocks once) + 4096 fixed base
    assert eval_cost_expr(cost["estimatedPeakBytes"], ctx) == 36_120

    # ...and it is STRICTLY below the naive per-node sum, which triple-charges
    # the kernel: 1000 + 3 + 3*47_000 = 142_003.
    naive = sum(eval_cost_expr(cost_of(n, ir)["totalCost"], ctx) for n in ir["nodes"])
    assert naive == 142_003
    assert eval_cost_expr(cost["totalOperations"], ctx) < naive

    # breakdown: window carries compute + projections; element the rest.
    assert eval_cost_expr(cost["breakdown"]["element"], ctx) == 1003
    assert eval_cost_expr(cost["breakdown"]["window"], ctx) == 50_000
    assert eval_cost_expr(cost["breakdown"]["scan"], ctx) == 0
    assert eval_cost_expr(cost["breakdown"]["call"], ctx) == 0


def test_plancost_emits_exact_deterministic_trees_right_fold_sum():
    # source close(0), const 20(1), call sma(2) — contributions in id order.
    ir = _compile_ir("plot(ta.sma(close, 20))")
    cost = estimate_plan_cost(ir)
    length = {"k": "argConst", "nodeId": 1}
    lit1 = {"k": "lit", "v": 1}
    # total: [barCount, lit(1), mul(len, barCount), barCount] right-folded
    assert cost["totalOperations"] == {
        "k": "add",
        "a": _BARS,
        "b": {
            "k": "add",
            "a": lit1,
            "b": {"k": "add", "a": {"k": "mul", "a": length, "b": _BARS}, "b": _BARS},
        },
    }
    # perBar: [lit(1), lit(0), len, lit(1)]
    assert cost["perBarOperations"] == {
        "k": "add",
        "a": lit1,
        "b": {
            "k": "add",
            "a": {"k": "lit", "v": 0},
            "b": {"k": "add", "a": length, "b": lit1},
        },
    }
    # bytes: [8·bars (source), lit(8) (const), 8·bars (1 block), lit(4096) base]
    assert cost["estimatedPeakBytes"] == {
        "k": "add",
        "a": _SERIES_BYTES,
        "b": {
            "k": "add",
            "a": {"k": "lit", "v": 8},
            "b": {"k": "add", "a": _SERIES_BYTES, "b": {"k": "lit", "v": 4096}},
        },
    }
    assert cost["breakdown"]["element"] == {"k": "add", "a": _BARS, "b": lit1}
    assert cost["breakdown"]["window"] == {
        "k": "add",
        "a": {"k": "mul", "a": length, "b": _BARS},
        "b": _BARS,
    }
    assert cost["breakdown"]["scan"] == {"k": "lit", "v": 0}
    assert cost["breakdown"]["call"] == {"k": "lit", "v": 0}


def test_plancost_elementwise_math_buckets_under_call():
    # source close(0), call math.abs(1): perBar = 1 + (2 + 1) = 4
    ir = _compile_ir("plot(math.abs(close))")
    cost = estimate_plan_cost(ir)
    ctx = _ctx_of(ir, 100)
    assert eval_cost_expr(cost["perBarOperations"], ctx) == 4
    assert eval_cost_expr(cost["totalOperations"], ctx) == 400
    assert eval_cost_expr(cost["breakdown"]["call"], ctx) == 300  # 2*100 + 100
    assert eval_cost_expr(cost["breakdown"]["window"], ctx) == 0
    # single-output kernel: 1 block — 8*100 (source) + 8*100 (block) + 4096
    assert eval_cost_expr(cost["estimatedPeakBytes"], ctx) == 5696


def test_plancost_scan_buckets_under_scan():
    # bin(prev, input) = 3 ScanExpr nodes -> 3 units/bar
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {
                "id": 1,
                "op": "scan",
                "init": None,
                "expr": {"k": "bin", "op": "+", "a": {"k": "prev"}, "b": {"k": "input", "i": 0}},
                "inputs": [0],
            },
        ]
    )
    cost = estimate_plan_cost(ir)
    ctx = _ctx_of(ir, 100)
    assert eval_cost_expr(cost["breakdown"]["scan"], ctx) == 300
    assert eval_cost_expr(cost["breakdown"]["element"], ctx) == 100
    assert eval_cost_expr(cost["perBarOperations"], ctx) == 4


def test_plancost_different_args_are_different_groups():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 10},
            {"id": 2, "op": "const", "value": 20},
            {"id": 3, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
            {"id": 4, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 2]},
        ]
    )
    cost = estimate_plan_cost(ir)
    ctx = _ctx_of(ir, 100)
    # perBar = 1 (source) + 0 + 0 + (10 + 1) + (20 + 1) = 33
    assert eval_cost_expr(cost["perBarOperations"], ctx) == 33
    # bytes: source 800 + consts 16 + two 1-block groups 1600 + 4096
    assert eval_cost_expr(cost["estimatedPeakBytes"], ctx) == 6512


def test_plancost_duplicate_identical_single_output_calls_charge_both_computes():
    # The TS executor caches ONLY multi-output kernels (the TA_FIELDS surface);
    # a single-output kernel recomputes at every call node. Unreachable via
    # compiler IR (CSE merges these), but admission accepts hand IR — the
    # estimate must stay an upper bound for BOTH runtimes, so identical
    # single-output nodes never share a group.
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 20},
            {"id": 2, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
            {"id": 3, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 1]},
        ]
    )
    cost = estimate_plan_cost(ir)
    ctx = _ctx_of(ir, 100)
    # perBar = 1 (source) + 0 (const) + (20 + 1) + (20 + 1) = 43 — NOT 23
    assert eval_cost_expr(cost["perBarOperations"], ctx) == 43
    # total = 100 + 1 + (2000 + 100) + (2000 + 100) = 4301
    assert eval_cost_expr(cost["totalOperations"], ctx) == 4301
    # bytes: source 800 + const 8 + two 1-block singletons 1600 + 4096
    assert eval_cost_expr(cost["estimatedPeakBytes"], ctx) == 6504
    assert eval_cost_expr(cost["breakdown"]["window"], ctx) == 4200


def test_plancost_duplicate_identical_multi_output_calls_still_group():
    # Two macd nodes with the same args — the runtime kernel cache computes
    # the kernel once and slices per output in both languages.
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "const", "value": 12},
            {"id": 2, "op": "const", "value": 26},
            {"id": 3, "op": "const", "value": 9},
            {
                "id": 4,
                "op": "call",
                "namespace": "ta",
                "function": "macd",
                "args": [0, 1, 2, 3],
                "output": 0,
            },
            {
                "id": 5,
                "op": "call",
                "namespace": "ta",
                "function": "macd",
                "args": [0, 1, 2, 3],
                "output": 1,
            },
        ]
    )
    cost = estimate_plan_cost(ir)
    ctx = _ctx_of(ir, 100)
    # perBar = 1 + 0*3 (consts) + 47 (compute ONCE) + 2 projections = 50
    assert eval_cost_expr(cost["perBarOperations"], ctx) == 50
    # bytes: source 800 + consts 24 + 3 blocks * 800 (once) + 4096 = 7320
    assert eval_cost_expr(cost["estimatedPeakBytes"], ctx) == 7320


def test_plancost_malformed_call_node_raises_the_same_unpriced_shape():
    # A call node missing namespace/function must flow into the uniform
    # unpriced-operator ValueError (Task 7 admission catches one shape), never
    # a bare KeyError. Message tail is "None.None" (Python str formatting)
    # where TS says "undefined.undefined" — same prefix, same semantics,
    # matching the pre-existing cost_of convention for malformed op kinds.
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "call", "args": [0]},
        ]
    )
    with pytest.raises(ValueError, match=r"unpriced operator: None\.None"):
        estimate_plan_cost(ir)


def test_plancost_dims_are_the_literal_string_na_never_zero():
    cost = estimate_plan_cost(_compile_ir("plot(close)"))
    assert cost["dims"]["eventChecks"] == "n/a"
    assert cost["dims"]["objectLifecycleChecks"] == "n/a"
    assert cost["dims"]["requestedDataPoints"] == "n/a"
    assert cost["dims"]["eventChecks"] != 0
    assert cost["dims"]["objectLifecycleChecks"] != 0
    assert cost["dims"]["requestedDataPoints"] != 0


def test_plancost_unpriced_operator_raises():
    ir = _prog(
        [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "call", "namespace": "ta", "function": "nope", "args": [0]},
        ]
    )
    with pytest.raises(ValueError, match=r"unpriced operator: ta\.nope"):
        estimate_plan_cost(ir)


def test_cost_family_of_classifies_via_registry_and_raises_for_unpriced():
    assert cost_family_of("ta", "sma") == "window"
    assert cost_family_of("math", "sum") == "window"
    assert cost_family_of("math", "abs") == "elementwise"
    assert cost_family_of("ta", "tr") == "stream"
    assert cost_family_of("kernels", "gaussian") == "window"
    with pytest.raises(ValueError, match=r"unpriced operator: ta\.nope"):
        cost_family_of("ta", "nope")


def test_plancost_field_counts_matches_ts_table():
    # Mirror of the TS FIELD_COUNTS <-> TA_FIELDS conformance test (the Python
    # runtime slices tuples by index and has no field table, so this pins the
    # cross-language constant table directly).
    assert FIELD_COUNTS == {
        "macd": 3,
        "bb": 3,
        "keltner": 3,
        "donchian": 3,
        "ppo": 3,
        "adx": 3,
        "cpr": 3,
        "stochastic": 2,
        "supertrend": 2,
        "tsi": 2,
        "ichimoku": 5,
        "pivotpoints": 7,
    }
