"""OpenScript IR generation — Python port of the TS ir-gen
(openalgo-openscript/src/compiler/ir-gen.ts).

Lowers a semantically-valid AST to the JSON IR: a topologically-ordered node
DAG (content-addressed CSE), declared inputs, render outputs, and warm-up
metadata. IR nodes are plain dicts (the JSON contract). The server uses the
canonical sha-256 of the source (the browser uses a fast preview hash).
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from ..limits import SCRIPT_LIMITS
from ..runtime.plancost import estimate_plan_cost

# The compiler resolves a timeframe with the SAME parser the runtime resampler uses,
# so lowering can never produce a timeframe the executor cannot bucket (register C4).
from ..runtime.timeframe import parse_timeframe
from . import ast_nodes as ast
from .builtins_table import CONTEXT_MEMBERS, KERNELS_FUNCTIONS, TA_FUNCTIONS, ta_overload
from .diagnostics import Diagnostic, Span, make_diagnostic
from .stdlib import stdlib_function

IR_VERSION = 1
COMPILER_VERSION = "openscript-1.0"

COLOR_HEX = {
    "green": "#4caf50", "red": "#ef5350", "blue": "#2962ff", "orange": "#ff9800",
    "purple": "#9c27b0", "teal": "#26a69a", "gray": "#787b86", "grey": "#787b86",
    "yellow": "#fdd835", "cyan": "#00bcd4", "pink": "#e91e63", "white": "#ffffff",
    "black": "#000000", "navy": "#001f7f", "maroon": "#7f0000", "lime": "#00e676",
    "aqua": "#00e5ff", "fuchsia": "#e040fb", "olive": "#808000", "silver": "#b2b5be",
}
SHAPE_MAP = {
    "arrowup": "arrowUp", "arrowdown": "arrowDown", "circle": "circle", "square": "square",
    "triangleup": "triangleUp", "triangledown": "triangleDown", "diamond": "diamond",
    "flag": "flag", "labelup": "flag", "labeldown": "flag", "xcross": "square", "cross": "circle",
}
LOCATION_MAP = {
    "abovebar": "aboveBar", "belowbar": "belowBar", "top": "aboveBar",
    "bottom": "belowBar", "absolute": "atPrice",
}
# Identity, plus the one documented alias (label-size design §3.1). The map
# used to collapse six source names into four buckets, so `large` and `huge`
# were indistinguishable in the IR and no source name produced `medium`.
SIZE_MAP = {
    "tiny": "tiny", "small": "small", "normal": "normal", "medium": "medium",
    "large": "large", "huge": "huge", "auto": "normal",
}
STYLE_MAP = {
    "style_line": "line", "style_stepline": "stepline", "style_histogram": "histogram",
    "style_cross": "cross", "style_area": "area", "style_columns": "columns", "style_circles": "circles",
    "style_linebr": "linebr",
}
MATH_CONST = {"pi": math.pi, "e": math.e, "phi": 1.618033988749895, "rphi": 0.6180339887498949}
ALERT_MAP = {"bar_close": "bar.close", "tick": "tick"}
# Drawing-object enums (design 0.5 §2/§4). `line.style_*` -> IR lineStyle/borderStyle;
# `extend` -> IRDrawExtend; `terminate` -> IRDrawTerminate.
LINE_STYLE_MAP = {"style_solid": "solid", "style_dashed": "dashed", "style_dotted": "dotted"}
EXTEND_MAP = {"lastbar": "lastbar", "until": "until", "bars": "bars"}
TERMINATE_MAP = {
    "close_above": "close_above", "close_below": "close_below",
    "cross_above": "cross_above", "cross_below": "cross_below", "touch": "touch",
    "straddle": "straddle", "new_session": "new_session",
}
INPUT_TYPE = {
    "int": "integer", "float": "float", "bool": "bool", "string": "string", "source": "source",
    "color": "color", "timeframe": "timeframe",
}

_HEX_BODY_LENGTHS = {3, 4, 6, 8}


def _is_well_formed_hex(s: str) -> bool:
    if not s.startswith("#"):
        return False
    body = s[1:]
    return len(body) in _HEX_BODY_LENGTHS and all(ch in "0123456789abcdefABCDEF" for ch in body)


def _normalize_hex(hex_color: str) -> str:
    """Expand a 3/4-digit hex color to its 6/8-digit form; 6/8-digit pass through."""
    h = hex_color[1:]
    if len(h) in (3, 4):
        h = "".join(ch * 2 for ch in h)
    return f"#{h}"


def _as_bar_count(v):
    """Integral drawing counts (`offset`, `right_pad`, `bars`, `max_kept`) as INTs.

    The Python lexer produces a float for every numeric literal, so `offset=-2`
    lowered to `-2.0` while the TS compiler emitted `-2`. Two consequences, and
    the second is why this is a correctness fix rather than a cosmetic one:

      1. The serialized IR differed between the runtimes -- `"offset": -2.0` vs
         `"offset": -2` -- despite "byte-identical IR" being the contract.
      2. `x1 = spawn + offset` became a FLOAT, and indexing the numpy time column
         with it raises IndexError. So any drawing with a non-zero offset crashed
         the server-side executor while working fine in the browser.

    Invisible to the IR-conformance guard because Python compares `-2 == -2.0` as
    equal; it took the first drawing-geometry fixture to EXECUTE a negative offset
    to surface it.
    """
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return out or "x"


def _resolve_const_member(e: ast.MemberExpr):
    ns = e.object.name if getattr(e.object, "type", None) == "Identifier" else ""
    p = e.property
    table = {
        "color": COLOR_HEX, "shape": SHAPE_MAP, "location": LOCATION_MAP, "size": SIZE_MAP,
        "plot": STYLE_MAP, "math": MATH_CONST, "alert": ALERT_MAP,
        "line": LINE_STYLE_MAP, "extend": EXTEND_MAP, "terminate": TERMINATE_MAP,
    }.get(ns)
    return table.get(p) if table is not None else None


def _resolve_const(e: ast.Expr):
    kind = e.type
    if kind in ("Number", "String", "Color", "Bool"):
        return e.value
    if kind == "Na":
        return None
    if kind == "Identifier":
        return e.name if e.name in _SOURCES else None
    if kind == "Member":
        return _resolve_const_member(e)
    if kind == "Unary" and e.op == "-":
        v = _resolve_const(e.operand)
        return -v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if kind == "Call":
        # color.new(color, transp) with const args folds to #RRGGBBAA
        # (Pine transp: 0 = opaque, 100 = fully transparent).
        c = e.callee
        if (
            getattr(c, "type", None) == "Member"
            and getattr(c.object, "type", None) == "Identifier"
            and c.object.name == "color"
            and c.property == "new"
        ):
            base = _resolve_const(e.args[0].value) if len(e.args) > 0 else None
            transp = _resolve_const(e.args[1].value) if len(e.args) > 1 else 0
            if (
                isinstance(base, str)
                and base.startswith("#")
                and isinstance(transp, (int, float))
                and not isinstance(transp, bool)
            ):
                return _with_transparency(base, float(transp))
        return None
    return None


def _with_transparency(hex_color: str, transp: float) -> str:
    """Expand a 3/4/6/8-digit hex color to #RRGGBBAA with Pine transparency."""
    h = _normalize_hex(hex_color)[1:]
    base_alpha = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    clamped = min(100.0, max(0.0, transp))
    # int(x + 0.5) = JS Math.round (Python round() is banker's rounding).
    alpha = int(base_alpha * ((100.0 - clamped) / 100.0) * 255 + 0.5)
    return f"#{h[:6]}{alpha:02x}"


_GRADIENT_STEPS = 16


def _parse_rgba(hex_color: str) -> tuple[int, int, int, int]:
    """Parse a hex color to (r, g, b, a) channel ints (0-255); missing alpha -> 255."""
    h = _normalize_hex(hex_color)[1:]
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return r, g, b, a


def _interpolate_hex(a: str, b: str, f: float) -> str:
    """Interpolate two hex colors at fraction f in [0, 1] -> #RRGGBBAA. Uses
    int(x + 0.5) (round-half-up; channels are non-negative) to match TS Math.round."""
    ar, ag, ab, aa = _parse_rgba(a)
    br, bg, bb, ba = _parse_rgba(b)

    def ch(x: int, y: int) -> int:
        return int(x + (y - x) * f + 0.5)

    return f"#{ch(ar, br):02x}{ch(ag, bg):02x}{ch(ab, bb):02x}{ch(aa, ba):02x}"


# Price sources only — used to const-fold source *strings* (e.g. an
# input.source default). Context series are NOT const-foldable sources.
_SOURCES = frozenset({"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"})
# Time/context series (P-time). Bare identifiers in _SERIES lower to a `source`
# node; _CONTEXT_IDS resolve from the dataset time column + length at runtime.
_CONTEXT_IDS = frozenset(
    {"time", "bar_index", "last_bar_index", "dayofweek", "dayofmonth", "hour", "minute", "month", "year"}
)
_SERIES = _SOURCES | _CONTEXT_IDS


def _is_input_call(e: ast.Expr) -> bool:
    return (
        e.type == "Call"
        and e.callee.type == "Member"
        and getattr(e.callee.object, "type", None) == "Identifier"
        and e.callee.object.name == "input"
    )


def _is_plot_binding(e: ast.Expr) -> bool:
    return (
        e.type == "Call"
        and getattr(e.callee, "type", None) == "Identifier"
        and e.callee.name == "plot"
    )


def _tf_dict(tf) -> dict:
    """A parsed Timeframe -> the IR's plain-dict shape."""
    return {"unit": tf.unit, "multiple": tf.multiple}


def _required_features(gen) -> list[str]:
    """Header features, in the SAME order the TS compiler pushes them, because the
    shared fixtures compare the serialized header byte for byte."""
    features: list[str] = []
    if gen._uses_drawings:
        features.append("drawing-streams")
    if gen._uses_request_security:
        features.append("request-security")
    return features


class IRGenerator:
    def __init__(self, source: str) -> None:
        self._source = source
        self._nodes: list[dict] = []
        self._spans: dict[int, dict] = {}
        self._warmups: list[int] = []
        self._statics: list[float | None] = []
        self._cse: dict[str, int] = {}
        self._inputs: list[dict] = []
        self._outputs: list[dict] = []
        self._scopes: list[dict[str, int]] = [{}]
        # Lowest scope index visible to a variable lookup -- raised while inlining
        # a stdlib body so it cannot see the caller's scope chain.
        self._scope_floor = 0
        # While inlining a stdlib body, the USER call site every emitted node and
        # diagnostic is attributed to. A stdlib span would point into source the
        # author cannot open, and `meta.spans` is what maps a value back to text.
        self._span_override: Span | None = None
        self._functions: dict[str, ast.FunctionDecl] = {}
        # `p = plot(...)` bindings: name → index into _outputs (fill targets).
        self._plot_handles: dict[str, int] = {}
        # Interned colors; color-valued nodes are const palette indices.
        self._palette: list[str] = []
        # Names reassigned with `:=` (scan lanes) and their declared seeds.
        self._scan_targets: set[str] = set()
        self._scan_seeds: dict[str, float | None] = {}
        self._declaration = {"name": "Untitled", "overlay": False}
        self._diagnostics: list[Diagnostic] = []
        # Set when a `plotlevel`/`plotzone` output is lowered — drives the
        # `drawing-streams` requiredFeatures flag (design 0.5 §8).
        self._uses_drawings = False
        self._uses_request_security = False

    # ── node emission (CSE) ─────────────────────────────────────────────────────

    def _emit(self, node: dict, span: Span, warmup: int, static_val: float | None = None) -> int:
        key = json.dumps(node, sort_keys=True)
        found = self._cse.get(key)
        if found is not None:
            return found
        node_id = len(self._nodes)
        self._nodes.append({**node, "id": node_id})
        self._spans[node_id] = (self._span_override or span).to_dict()
        self._warmups.append(warmup)
        self._statics.append(static_val)
        self._cse[key] = node_id
        return node_id

    def _na_node(self, span: Span) -> int:
        return self._emit({"op": "const", "value": None}, span, 0)

    def _warn(self, code: str, span: Span, detail: str | None = None) -> None:
        self._diagnostics.append(make_diagnostic(code, "warning", self._span_override or span, detail))

    def _error(self, code: str, span: Span, detail: str | None = None) -> None:
        self._diagnostics.append(make_diagnostic(code, "error", self._span_override or span, detail))

    def _bind(self, name: str, node_id: int) -> None:
        self._scopes[-1][name] = node_id

    def _resolve_var(self, name: str):
        # `_scope_floor` seals a stdlib body off from the caller's variables.
        # Without it a body's `close` could resolve to a user variable that
        # shadowed the series -- the library would then mean different things in
        # different scripts, the one property it exists to prevent.
        for scope in reversed(self._scopes[self._scope_floor:]):
            if name in scope:
                return scope[name]
        return None

    # ── top-level statements ────────────────────────────────────────────────────

    def _lower_top_stmt(self, stmt: ast.Stmt) -> None:
        kind = stmt.type
        if kind == "VarDecl":
            if stmt.name in self._scan_targets:
                # Seed for a `:=` scan lane — recorded, bound at the Reassign.
                seed = _resolve_const(stmt.value)
                if isinstance(seed, bool):
                    seed = 1.0 if seed else 0.0
                self._scan_seeds[stmt.name] = float(seed) if isinstance(seed, (int, float)) else None
            elif _is_input_call(stmt.value):
                self._bind(stmt.name, self._lower_input(stmt.value, stmt.name))
            elif _is_plot_binding(stmt.value):
                # p = plot(...) — emit the plot output and record the handle.
                self._outputs.append(self._plot_output(stmt.value))
                self._plot_handles[stmt.name] = len(self._outputs) - 1
            else:
                self._bind(stmt.name, self._lower_expr(stmt.value))
        elif kind == "Reassign":
            self._bind(stmt.name, self._lower_scan(stmt))
        elif kind == "TupleDecl":
            call = stmt.value
            callee = call.callee
            # `[a, b] = request.security(sym, tf, [S1, S2])` -> one htf node per
            # element, all sharing the resolved timeframe (=> ONE resample).
            if (
                getattr(callee, "type", None) == "Member"
                and getattr(callee.object, "type", None) == "Identifier"
                and callee.object.name == "request"
                and callee.property == "security"
            ):
                self._uses_request_security = True
                positional = [a for a in call.args if getattr(a, "name", None) is None]
                tf = self._resolve_htf_timeframe(
                    positional[1].value if len(positional) > 1 else None
                )
                arr = positional[2].value if len(positional) > 2 else None
                elements = arr.elements if arr is not None and arr.type == "ArrayLiteral" else []
                for i, n in enumerate(stmt.names):
                    el = elements[i] if i < len(elements) else None
                    self._bind(n.name, self._htf_node_for(el, tf, call.span))
                return
            fn = call.callee.property
            spec = TA_FUNCTIONS.get(fn, {"outputMap": []})
            for i, n in enumerate(stmt.names):
                out_map = spec["outputMap"]
                output = out_map[i] if i < len(out_map) else 0
                self._bind(n.name, self._lower_ta_call(fn, call, output))
        elif kind == "FunctionDecl":
            self._functions[stmt.name] = stmt
        elif kind == "ExprStmt":
            e = stmt.expr
            if e.type == "Call" and getattr(e.callee, "type", None) == "Identifier":
                if e.callee.name == "indicator":
                    self._set_declaration(e)
                    return
                self._emit_output(e.callee.name, e)
                return
            self._lower_expr(e)

    def _set_declaration(self, call: ast.CallExpr) -> None:
        name = self._const_arg(call, 0, "title")
        short_name = self._const_arg(call, None, "shorttitle")
        overlay = self._const_arg(call, None, "overlay")
        self._declaration = {
            "name": name if isinstance(name, str) else "Untitled",
            "overlay": overlay is True,
        }
        if isinstance(short_name, str):
            self._declaration["shortName"] = short_name

    # ── expressions ─────────────────────────────────────────────────────────────

    def _lower_expr(self, e: ast.Expr) -> int:
        kind = e.type
        if kind == "Number":
            return self._emit({"op": "const", "value": e.value}, e.span, 0, e.value)
        if kind == "Color":
            # Colors are DAG values as palette indices (dynamic-color support).
            return self._palette_const(e.value, e.span)
        if kind == "String":
            return self._emit({"op": "const", "value": e.value}, e.span, 0)
        if kind == "Bool":
            return self._emit({"op": "const", "value": e.value}, e.span, 0)
        if kind == "Na":
            return self._na_node(e.span)
        if kind == "Identifier":
            if e.name in _SERIES:
                return self._emit({"op": "source", "source": e.name}, e.span, 0)
            bound = self._resolve_var(e.name)
            return bound if bound is not None else self._na_node(e.span)
        if kind == "Member":
            # An execution-resolved context property lowers to a `source` node,
            # NOT a const: the chart interval must be read from the dataset every
            # run, or stored IR would keep reporting the authoring interval.
            if getattr(e.object, "type", None) == "Identifier":
                context_id = CONTEXT_MEMBERS.get(e.object.name, {}).get(e.property)
                if context_id is not None:
                    return self._emit({"op": "source", "source": context_id}, e.span, 0)
            if getattr(e.object, "type", None) == "Identifier" and e.object.name == "color":
                hex_color = COLOR_HEX.get(e.property)
                return self._palette_const(hex_color, e.span) if hex_color else self._na_node(e.span)
            v = _resolve_const_member(e)
            static = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
            return self._emit({"op": "const", "value": v}, e.span, 0, static)
        if kind == "Call":
            return self._lower_call(e)
        if kind == "Index":
            return self._lower_hist(e)
        if kind == "Unary":
            arg = self._lower_expr(e.operand)
            return self._emit({"op": "unop", "operator": e.op, "arg": arg}, e.span, self._warmups[arg])
        if kind == "Binary":
            left = self._lower_expr(e.left)
            right = self._lower_expr(e.right)
            w = max(self._warmups[left], self._warmups[right])
            return self._emit({"op": "binop", "operator": e.op, "args": [left, right]}, e.span, w)
        if kind == "Ternary":
            c = self._lower_expr(e.cond)
            t = self._lower_expr(e.then)
            el = self._lower_expr(e.else_)
            w = max(self._warmups[c], self._warmups[t], self._warmups[el])
            return self._emit({"op": "select", "cond": c, "then": t, "else": el}, e.span, w)
        if kind == "ArrayLiteral":
            # Only ever reachable as input.string's `options=` value, which
            # `_lower_input` extracts directly — never lowered as a generic expr.
            return self._na_node(e.span)
        # If
        c = self._lower_expr(e.cond)
        t = self._lower_block_value(e.then.statements, e.span)
        el = self._lower_block_value(e.else_.statements, e.span) if e.else_ else self._na_node(e.span)
        w = max(self._warmups[c], self._warmups[t], self._warmups[el])
        return self._emit({"op": "select", "cond": c, "then": t, "else": el}, e.span, w)

    def _lower_block_value(self, statements: list[ast.Stmt], span: Span) -> int:
        self._scopes.append({})
        last = self._na_node(span)
        for stmt in statements:
            if stmt.type == "VarDecl":
                self._bind(stmt.name, self._lower_expr(stmt.value))
            elif stmt.type == "ExprStmt":
                last = self._lower_expr(stmt.expr)
        self._scopes.pop()
        return last

    def _lower_call(self, call: ast.CallExpr) -> int:
        callee = call.callee
        if callee.type == "Member" and getattr(callee.object, "type", None) == "Identifier":
            ns = callee.object.name
            fn = callee.property
            if ns == "ta":
                return self._lower_ta_call(fn, call, 0)
            if ns == "kernels":
                return self._lower_ta_call(fn, call, 0, "kernels")
            if ns == "math":
                return self._lower_math_call(fn, call)
            if ns == "input":
                return self._lower_input(call, None)
            if ns == "color" and fn == "from_gradient":
                return self._lower_from_gradient(call)
            if ns == "request" and fn == "security":
                return self._lower_request_security(call)
            # Bundled standard library. MUST come before the const-fold
            # fallthrough below: that path answers an unrecognised `ns.fn(...)`
            # with `const null`, so a missing branch here would not fail -- it
            # would silently produce an all-`na` series, the exact
            # silent-degradation shape the request.security port nearly shipped.
            std_fn = stdlib_function(ns, fn)
            if std_fn is not None:
                return self._inline_stdlib_function(std_fn, call)
            # constant-namespace call (e.g. color.new) — fold to a palette color
            folded = _resolve_const(call)
            if isinstance(folded, str) and folded.startswith("#"):
                return self._palette_const(folded, call.span)
            v = _resolve_const_member(callee)
            if isinstance(v, str) and v.startswith("#"):
                return self._palette_const(v, call.span)
            return self._emit({"op": "const", "value": v}, call.span, 0)
        if callee.type == "Identifier":
            if callee.name == "nz":
                return self._lower_nz(call)
            if callee.name == "na":
                arg = self._lower_expr(call.args[0].value)
                return self._emit(
                    {"op": "unop", "operator": "isna", "arg": arg}, call.span, self._warmups[arg]
                )
            fn = self._functions.get(callee.name)
            if fn is not None:
                return self._inline_function(fn, call)
        return self._na_node(call.span)

    def _lower_ta_call(self, fn: str, call: ast.CallExpr, output: int, ns: str = "ta") -> int:
        spec = (TA_FUNCTIONS if ns == "ta" else KERNELS_FUNCTIONS).get(fn)
        overload = ta_overload(spec, len(call.args)) if spec else None
        if spec is None or overload is None:
            return self._na_node(call.span)
        user_args = [self._lower_expr(a.value) for a in call.args]
        arg_ids = []
        for ka in overload["kernelArgs"]:
            if "source" in ka:
                arg_ids.append(self._emit({"op": "source", "source": ka["source"]}, call.span, 0))
            elif "const" in ka:
                arg_ids.append(self._emit({"op": "const", "value": ka["const"]}, call.span, 0, ka["const"]))
            else:
                arg_ids.append(user_args[ka["arg"]])
        child_warmup = max((self._warmups[i] for i in arg_ids), default=0)
        period = max((self._statics[i] or 0 for i in user_args), default=0)
        node = {"op": "call", "namespace": ns, "function": fn, "args": arg_ids}
        if spec["outputs"] > 1:
            node["output"] = output
        return self._emit(node, call.span, int(child_warmup + period))

    def _lower_math_call(self, fn: str, call: ast.CallExpr) -> int:
        args = [self._lower_expr(a.value) for a in call.args]
        w = max((self._warmups[i] for i in args), default=0)
        return self._emit({"op": "call", "namespace": "math", "function": fn, "args": args}, call.span, w)

    def _lower_nz(self, call: ast.CallExpr) -> int:
        arg = self._lower_expr(call.args[0].value)
        node = {"op": "nz", "arg": arg}
        if len(call.args) > 1:
            replacement = _resolve_const(call.args[1].value)
            if isinstance(replacement, (int, float)) and not isinstance(replacement, bool):
                node["replacement"] = replacement
        return self._emit(node, call.span, self._warmups[arg])

    def _inline_stdlib_function(self, fn: ast.FunctionDecl, call: ast.CallExpr) -> int:
        """Inline a stdlib primitive. Same mechanism as a user function -- the body
        lowers into the caller's DAG and no IR node records a library was involved
        -- with two seals the user-function path does not need: arguments are
        lowered BEFORE the span override so they keep the caller's spans, and the
        scope floor hides the caller's variables from the body."""
        arg_ids = [self._lower_expr(a.value) for a in call.args]
        scope = {}
        for i, p in enumerate(fn.params):
            scope[p.name] = arg_ids[i] if i < len(arg_ids) else self._na_node(call.span)
        # A nested stdlib call keeps the OUTERMOST user call site: an inner one
        # would still be a span the author cannot open, just a different one.
        outer_span = self._span_override
        outer_floor = self._scope_floor
        self._span_override = outer_span or call.span
        self._scopes.append(scope)
        self._scope_floor = len(self._scopes) - 1
        result = self._lower_expr(fn.body)
        self._scopes.pop()
        self._scope_floor = outer_floor
        self._span_override = outer_span
        return result

    def _inline_function(self, fn: ast.FunctionDecl, call: ast.CallExpr) -> int:
        arg_ids = [self._lower_expr(a.value) for a in call.args]
        scope = {}
        for i, p in enumerate(fn.params):
            scope[p.name] = arg_ids[i] if i < len(arg_ids) else self._na_node(call.span)
        self._scopes.append(scope)
        result = self._lower_expr(fn.body)
        self._scopes.pop()
        return result

    def _lower_hist(self, index: ast.IndexExpr) -> int:
        arg = self._lower_expr(index.object)
        offset = _resolve_const(index.index)
        is_int = isinstance(offset, (int, float)) and not isinstance(offset, bool) and float(offset).is_integer()
        if not is_int or offset < 0:
            self._diagnostics.append(
                make_diagnostic("OS2006", "error", index.span, "historical offset must be a non-negative integer literal")
            )
            return arg
        offset = int(offset)
        if offset > SCRIPT_LIMITS["maximumLookback"]:
            self._diagnostics.append(
                make_diagnostic("OS3007", "error", index.span, f"{offset} > {SCRIPT_LIMITS['maximumLookback']}")
            )
            return arg
        return self._emit({"op": "hist", "arg": arg, "offset": offset}, index.span, self._warmups[arg] + offset)

    # ── inputs ──────────────────────────────────────────────────────────────────

    def _lower_input(self, call: ast.CallExpr, preferred_id: str | None) -> int:
        fn = call.callee.property
        type_ = INPUT_TYPE.get(fn, "float")
        title_val = self._const_arg(call, 1, "title")
        title = title_val if isinstance(title_val, str) else None
        input_id = preferred_id or (_slug(title) if title else f"input_{len(self._inputs)}")
        label = title if title is not None else input_id
        default = _resolve_const(call.args[0].value) if call.args else None

        decl: dict = {"id": input_id, "type": type_, "label": label}
        static_val = None
        if type_ in ("integer", "float"):
            decl["defaultValue"] = default if isinstance(default, (int, float)) and not isinstance(default, bool) else 0
            for key, field in (("minval", "min"), ("maxval", "max"), ("step", "step")):
                v = self._const_arg(call, None, key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    decl[field] = v
            static_val = decl["defaultValue"]
        elif type_ == "bool":
            decl["defaultValue"] = default is True
        elif type_ == "string":
            decl["defaultValue"] = default if isinstance(default, str) else ""
            options_arg = next((a for a in call.args if a.name == "options"), None)
            if options_arg is not None and options_arg.value.type == "ArrayLiteral":
                # Semantic (OS2004) already guaranteed every element is a string literal.
                options = [el.value for el in options_arg.value.elements if el.type == "String"]
                if options:
                    decl["options"] = options
        elif type_ == "color":
            decl["defaultValue"] = (
                _normalize_hex(default) if isinstance(default, str) and _is_well_formed_hex(default) else "#000000"
            )
        elif type_ == "timeframe":
            decl["defaultValue"] = default if isinstance(default, str) and default else "1"
        else:  # source
            decl["defaultValue"] = default if isinstance(default, str) else "close"

        for key, field in (("group", "group"), ("tooltip", "tooltip"), ("inline", "inline")):
            v = self._const_arg(call, None, key)
            if isinstance(v, str):
                decl[field] = v

        if not any(d["id"] == input_id for d in self._inputs):
            self._inputs.append(decl)
        return self._emit({"op": "input", "inputId": input_id}, call.span, 0, static_val)

    # ── outputs ─────────────────────────────────────────────────────────────────

    def _emit_output(self, fn: str, call: ast.CallExpr) -> None:
        if fn == "plot":
            self._outputs.append(self._plot_output(call))
        elif fn == "hline":
            self._outputs.append(self._hline_output(call))
        elif fn in ("plotshape", "plotchar"):
            self._outputs.append(self._marker_output(fn, call))
        elif fn in ("plotcandle", "plotbar"):
            out = self._candle_output(fn, call)
            if out is not None:
                self._outputs.append(out)
        elif fn in ("barcolor", "bgcolor"):
            self._outputs.append(self._tint_output(fn, call))
        elif fn == "alertcondition":
            self._outputs.append(self._alert_output(call))
        elif fn == "plotlevel":
            self._outputs.append(self._level_output(call))
        elif fn == "plotzone":
            self._outputs.append(self._zone_output(call))
        elif fn == "fill":
            out = self._fill_output(call)
            if out is not None:
                self._outputs.append(out)

    def _fill_output(self, call: ast.CallExpr) -> dict | None:
        """fill(p1, p2[, color=...][, title=...]) — semantic has validated
        (OS2012) that the first two positional args are plot handles."""
        positionals = [a for a in call.args if a.name is None]
        top = self._handle_index(positionals[0]) if len(positionals) > 0 else None
        bottom = self._handle_index(positionals[1]) if len(positionals) > 1 else None
        if top is None or bottom is None:
            return None  # invalid handles — diagnostics already emitted
        color, color_input_id = self._color_with_input(call, "#2962ff33")
        out: dict = {
            "kind": "fill",
            "topPlotIndex": top,
            "bottomPlotIndex": bottom,
            "color": color,
            "title": self._title(call, None),
        }
        if color_input_id is not None:
            out["colorInputId"] = color_input_id
        return out

    def _handle_index(self, arg) -> int | None:
        if getattr(arg.value, "type", None) != "Identifier":
            return None
        return self._plot_handles.get(arg.value.name)

    def _plot_output(self, call: ast.CallExpr) -> dict:
        node_id = self._lower_expr(call.args[0].value)
        color, color_node, color_input_id = self._color_spec(call, "#2962ff")
        style: dict = {"color": color}
        if color_node is not None:
            style["colorNodeId"] = color_node
        if color_input_id is not None:
            style["colorInputId"] = color_input_id
        lw = self._const_arg(call, None, "linewidth")
        if isinstance(lw, (int, float)) and not isinstance(lw, bool):
            style["lineWidth"] = lw
        variant = self._const_arg(call, None, "style")
        if isinstance(variant, str):
            style["variant"] = variant
        return {"kind": "plot", "nodeId": node_id, "title": self._title(call, 1), "style": style}

    def _hline_output(self, call: ast.CallExpr) -> dict:
        price = _resolve_const(call.args[0].value) if call.args else None
        color, color_input_id = self._color_with_input(call, "#787b86")
        style: dict = {"color": color}
        if color_input_id is not None:
            style["colorInputId"] = color_input_id
        return {
            "kind": "hline",
            "price": price if isinstance(price, (int, float)) and not isinstance(price, bool) else 0,
            "title": self._title(call, 1),
            "style": style,
        }

    def _marker_output(self, fn: str, call: ast.CallExpr) -> dict:
        cond_node = self._lower_expr(call.args[0].value)
        location = self._const_arg(call, None, "location")
        color, color_node, color_input_id = self._color_spec(call, "#2962ff")
        out: dict = {
            "kind": fn,
            "condNodeId": cond_node,
            "title": self._title(call, None),
            "location": location if isinstance(location, str) else "aboveBar",
            "color": color,
        }
        if color_node is not None:
            out["colorNodeId"] = color_node
        if color_input_id is not None:
            out["colorInputId"] = color_input_id
        text = self._const_arg(call, None, "text")
        if fn == "plotchar":
            ch = self._const_arg(call, None, "char")
            out["char"] = ch if isinstance(ch, str) else "★"
            if isinstance(text, str):
                out["text"] = text
        else:
            shape = self._const_arg(call, None, "shape")
            out["shape"] = shape if isinstance(shape, str) else "circle"
            if isinstance(text, str):
                out["text"] = text
            size = self._const_arg(call, None, "size")
            if isinstance(size, str):
                out["size"] = size
        return out

    def _candle_output(self, fn: str, call: ast.CallExpr) -> dict | None:
        """plotcandle/plotbar(open, high, low, close[, title][, color=...])."""
        positionals = [a for a in call.args if a.name is None]
        if len(positionals) < 4:
            return None
        color_from_input, color_input_id = self._color_with_input(call, "")
        color = color_from_input if color_input_id is not None else self._const_arg(call, None, "color")
        up_color = color if isinstance(color, str) and color else "#26a69a"
        down_color = color if isinstance(color, str) and color else "#ef5350"
        out: dict = {
            "kind": "plotcandle",
            "openNodeId": self._lower_expr(positionals[0].value),
            "highNodeId": self._lower_expr(positionals[1].value),
            "lowNodeId": self._lower_expr(positionals[2].value),
            "closeNodeId": self._lower_expr(positionals[3].value),
            "title": self._title(call, 4),
            "upColor": up_color,
            "downColor": down_color,
        }
        if fn == "plotbar":
            out["bar"] = True
        if color_input_id is not None:
            out["colorInputId"] = color_input_id
        return out

    def _tint_output(self, fn: str, call: ast.CallExpr) -> dict:
        color, color_node, color_input_id = self._color_spec(call, "#ff9800")
        out: dict = {
            "kind": fn,
            "condNodeId": self._lower_expr(call.args[0].value),
            "color": color,
            "title": self._title(call, None),
        }
        if color_node is not None:
            out["colorNodeId"] = color_node
        if color_input_id is not None:
            out["colorInputId"] = color_input_id
        return out

    def _alert_output(self, call: ast.CallExpr) -> dict:
        cond_node = self._lower_expr(call.args[0].value)
        title = self._title(call, 1)
        message = self._const_arg(call, 2, "message")
        # `on` is a namespace constant (alert.bar_close | alert.tick), read the
        # same way as location=/shape=/style= (_resolve_const_member -> ALERT_MAP).
        # Default, and any non-`tick` value, is bar.close.
        on = "tick" if self._const_arg(call, None, "on") == "tick" else "bar.close"
        return {
            "kind": "alertcondition",
            "condNodeId": cond_node,
            "conditionId": _slug(title or f"cond_{len(self._outputs)}"),
            "title": title,
            "message": message if isinstance(message, str) else title,
            "on": on,
        }

    # ── drawing outputs (design 0.5 §2/§3/§6) ───────────────────────────────────
    #
    # `plotlevel`/`plotzone` lower to the frozen `level`/`zone` IR shapes.
    # condNodeId spawns an object on a confirmed true bar; the price/top/bottom
    # node ids are sampled at the spawn bar by the Phase-1 materializer. Styling
    # is const-or-input value only (design §2) — the frozen IR carries no
    # colorNodeId/colorInputId slot, so an input.color arg bakes its default hex.

    def _level_output(self, call: ast.CallExpr) -> dict:
        self._uses_drawings = True
        cond_expr = self._arg_expr(call, 0, None)
        price_expr = self._arg_expr(call, 1, None)
        level_color = self._draw_color_binding(call, "color")
        style: dict = {
            "color": level_color[0] if level_color is not None else "#2962ff",
            "lineStyle": self._draw_line_style(call, "style"),
        }
        if level_color is not None and level_color[1] is not None:
            style["colorInputId"] = level_color[1]
        width = self._const_arg(call, None, "width")
        if isinstance(width, (int, float)) and not isinstance(width, bool):
            style["lineWidth"] = width
        extend = self._draw_extend(call)
        out: dict = {
            "kind": "level",
            "condNodeId": self._lower_expr(cond_expr) if cond_expr is not None else self._na_node(call.span),
            "priceNodeId": self._lower_expr(price_expr) if price_expr is not None else self._na_node(call.span),
            "title": self._title(call, 2),
            "style": style,
            "offset": self._draw_num(call, "offset", 0),
            "rightPad": self._draw_num(call, "right_pad", 0),
            "extend": extend,
            "maxKept": self._draw_max_kept(call, 20),
            "labelLatestOnly": self._const_arg(call, None, "label_latest_only") is True,
        }
        self._apply_extend_args(out, call, extend)
        label = self._draw_text(call, "label", {"price": out["priceNodeId"]})
        if label is not None:
            out["label"] = label
        label_size = self._draw_size(call, "label_size")
        if label_size is not None:
            out["labelSize"] = label_size
        return out

    def _zone_output(self, call: ast.CallExpr) -> dict:
        self._uses_drawings = True
        cond_expr = self._arg_expr(call, 0, None)
        top_expr = self._arg_expr(call, 1, None)
        bottom_expr = self._arg_expr(call, 2, None)
        fill_color = self._draw_color_binding(call, "color")
        style: dict = {
            "color": fill_color[0] if fill_color is not None else "#2962ff33",
            "borderStyle": self._draw_line_style(call, "border_style"),
        }
        if fill_color is not None and fill_color[1] is not None:
            style["colorInputId"] = fill_color[1]
        border = self._draw_color_binding(call, "border_color")
        if border is not None:
            style["borderColor"] = border[0]
            if border[1] is not None:
                style["borderColorInputId"] = border[1]
        extend = self._draw_extend(call)
        out: dict = {
            "kind": "zone",
            "condNodeId": self._lower_expr(cond_expr) if cond_expr is not None else self._na_node(call.span),
            "topNodeId": self._lower_expr(top_expr) if top_expr is not None else self._na_node(call.span),
            "bottomNodeId": self._lower_expr(bottom_expr) if bottom_expr is not None else self._na_node(call.span),
            "title": self._title(call, 3),
            "style": style,
            "offset": self._draw_num(call, "offset", 0),
            "rightPad": self._draw_num(call, "right_pad", 0),
            "extend": extend,
            "maxKept": self._draw_max_kept(call, 10),
        }
        self._apply_extend_args(out, call, extend)
        # mitigated_color styles a zone closed via terminate.touch or the strict
        # terminate.straddle (design §4); semantic OS2022 already rejects it on a
        # level or on any other terminate.
        if out.get("terminate") in ("touch", "straddle"):
            mc = self._draw_color_binding(call, "mitigated_color")
            if mc is not None:
                out["mitigatedColor"] = mc[0]
                if mc[1] is not None:
                    out["mitigatedColorInputId"] = mc[1]
        text = self._draw_text(call, "text", {"top": out["topNodeId"], "bottom": out["bottomNodeId"]})
        if text is not None:
            out["text"] = text
        text_size = self._draw_size(call, "text_size")
        if text_size is not None:
            out["textSize"] = text_size
        return out

    def _draw_size(self, call: ast.CallExpr, arg: str) -> str | None:
        """A `label_size=` / `text_size=` argument, or None when unspecified.

        Absent stays absent: keeping the field off the IR is what makes it
        additive over already-stored IR (design §3.2).
        """
        v = self._const_arg(call, None, arg)
        return v if isinstance(v, str) else None

    def _apply_extend_args(self, out: dict, call: ast.CallExpr, extend: str) -> None:
        """`terminate=` present iff extend=='until'; `bars=` present iff
        extend=='bars' (design §3/§6). Semantic OS2018-2021 enforces consistency."""
        if extend == "until":
            t = self._const_arg(call, None, "terminate")
            if isinstance(t, str):
                out["terminate"] = t
        elif extend == "bars":
            b = self._const_arg(call, None, "bars")
            if isinstance(b, (int, float)) and not isinstance(b, bool):
                out["bars"] = b

    def _draw_color(self, call: ast.CallExpr, name: str, fallback: str) -> str:
        """A drawing color arg by name -> static hex: const color, folded
        color.new, or an input.color's baked default."""
        b = self._draw_color_binding(call, name)
        return b[0] if b is not None else fallback

    def _draw_color_opt(self, call: ast.CallExpr, name: str) -> str | None:
        b = self._draw_color_binding(call, name)
        return b[0] if b is not None else None

    def _draw_color_binding(self, call: ast.CallExpr, name: str) -> tuple[str, str | None] | None:
        """A draw-color argument as (baked_hex, colorInputId or None) -- G8.

        This used to keep only the hex and drop the binding id, so a drawing
        colour wired to an `input.color` compiled clean, showed a swatch in the
        settings dialog, and ignored it. Every non-drawing kind already threads
        the id through; this is the same shape, mirrored from the TS ir-gen.

        Callers omit `colorInputId` entirely when it is None rather than writing
        a null, so IR for an unchanged script stays byte-identical to both the
        stored artifact and the TS golden.
        """
        expr = self._arg_expr(call, None, name)
        ci = self._color_input_id_of(expr)
        if ci is not None:
            return ci[1], ci[0]
        v = _resolve_const(expr) if expr is not None else None
        return (v, None) if isinstance(v, str) else None

    def _draw_line_style(self, call: ast.CallExpr, name: str) -> str:
        """`style=`/`border_style=` enum -> IR lineStyle/borderStyle; default 'solid'."""
        v = self._const_arg(call, None, name)
        return v if v in ("dashed", "dotted") else "solid"

    def _draw_num(self, call: ast.CallExpr, name: str, fallback: float):
        v = self._const_arg(call, None, name)
        raw = v if isinstance(v, (int, float)) and not isinstance(v, bool) else fallback
        return _as_bar_count(raw)

    def _draw_max_kept(self, call: ast.CallExpr, default: int):
        """`max_kept` -> clamped <= maximumObjectsPerOutput; a source value over
        the cap warns OS5001 (advisory; runtime clamps in Phase 1)."""
        v = self._const_arg(call, None, "max_kept")
        raw = v if isinstance(v, (int, float)) and not isinstance(v, bool) else default
        cap = SCRIPT_LIMITS["maximumObjectsPerOutput"]
        if raw > cap:
            arg = self._arg_expr(call, None, "max_kept")
            self._warn("OS5001", arg.span if arg is not None else call.span)
            return cap
        return _as_bar_count(raw)

    def _draw_text(
        self, call: ast.CallExpr, name: str, slots: dict[str, int] | None = None
    ) -> dict | None:
        """`label=`/`text=` -> IRDrawText (design §11). Mirror of the TS `drawText`.

        A plain string stays `const`. A string containing a known NAMED
        placeholder lowers to the frozen `template` variant: the name resolves
        against the output's OWN geometry (`{price}` on a level, `{top}` /
        `{bottom}` on a zone) and is rewritten into the positional `{0}`/`{1}`
        form the materializer renders, with `args` as node ids sampled once at
        the spawn bar.

        An UNKNOWN placeholder is deliberately left in the text: the materializer
        substitutes only the indices it has args for, so a typo shows up on the
        chart rather than vanishing.

        Insertion order of `slots` is the argument order, and must match the TS
        object-literal order — the two IRs are compared node for node.
        """
        v = self._const_arg(call, None, name)
        if not isinstance(v, str):
            return None
        if slots is None:
            return {"kind": "const", "value": v}
        args: list[int] = []
        fmt = v
        for placeholder, node_id in slots.items():
            token = "{" + placeholder + "}"
            if token not in fmt:
                continue
            fmt = fmt.replace(token, "{" + str(len(args)) + "}")
            args.append(node_id)
        if args:
            return {"kind": "template", "fmt": fmt, "args": args}
        return {"kind": "const", "value": v}

    def _draw_extend(self, call: ast.CallExpr) -> str:
        v = self._const_arg(call, None, "extend")
        return v if v in ("until", "bars") else "lastbar"

    # ── argument helpers ────────────────────────────────────────────────────────

    def _arg_expr(self, call: ast.CallExpr, positional: int | None, name: str | None):
        if name is not None:
            for a in call.args:
                if a.name == name:
                    return a.value
        if positional is not None:
            pos = [a for a in call.args if a.name is None]
            if positional < len(pos):
                return pos[positional].value
        return None

    def _const_arg(self, call: ast.CallExpr, positional: int | None, name: str | None):
        e = self._arg_expr(call, positional, name)
        return _resolve_const(e) if e is not None else None

    def _title(self, call: ast.CallExpr, positional: int | None) -> str:
        v = self._const_arg(call, positional, "title")
        return v if isinstance(v, str) else ""

    def _lower_scan(self, stmt) -> int:
        """Lower `x := expr` into a single-lane scan node. Self-references stay
        in the body (`x` -> prev, `x[1]` -> prevh); every self-free subtree
        lowers to an ordinary DAG node and becomes an input series."""
        target = stmt.name
        inputs: list[int] = []
        input_slot: dict[int, int] = {}

        def as_input(e) -> dict:
            node_id = self._lower_expr(e)
            slot = input_slot.get(node_id)
            if slot is None:
                slot = len(inputs)
                inputs.append(node_id)
                input_slot[node_id] = slot
            return {"k": "input", "i": slot}

        def self_ref(e) -> bool:
            kind = e.type
            if kind == "Identifier":
                return e.name == target
            if kind == "Index":
                return self_ref(e.object) or self_ref(e.index)
            if kind == "Unary":
                return self_ref(e.operand)
            if kind == "Binary":
                return self_ref(e.left) or self_ref(e.right)
            if kind == "Ternary":
                return self_ref(e.cond) or self_ref(e.then) or self_ref(e.else_)
            if kind == "Call":
                return any(self_ref(a.value) for a in e.args)
            return False

        def body(e) -> dict:
            if not self_ref(e):
                if e.type == "Number":
                    return {"k": "const", "v": e.value}
                if e.type == "Na":
                    return {"k": "const", "v": None}
                if e.type == "Bool":
                    return {"k": "const", "v": 1 if e.value else 0}
                return as_input(e)
            kind = e.type
            if kind == "Identifier":
                return {"k": "prev"}
            if kind == "Index":
                return {"k": "prevh"}
            if kind == "Unary":
                return {"k": "un", "op": "-" if e.op == "-" else "not", "a": body(e.operand)}
            if kind == "Binary":
                return {"k": "bin", "op": e.op, "a": body(e.left), "b": body(e.right)}
            if kind == "Ternary":
                return {"k": "select", "c": body(e.cond), "t": body(e.then), "e": body(e.else_)}
            if kind == "Call":
                callee = e.callee
                if getattr(callee, "type", None) == "Identifier" and callee.name == "na":
                    return {"k": "un", "op": "isna", "a": body(e.args[0].value)}
                if getattr(callee, "type", None) == "Identifier" and callee.name == "nz":
                    out = {"k": "nz", "a": body(e.args[0].value)}
                    if len(e.args) > 1:
                        out["b"] = body(e.args[1].value)
                    return out
                if (
                    getattr(callee, "type", None) == "Member"
                    and getattr(callee.object, "type", None) == "Identifier"
                    and callee.object.name == "math"
                ):
                    return {"k": "math", "fn": callee.property, "args": [body(a.value) for a in e.args]}
            # unsupported (semantic already reported) — degrade to na
            return {"k": "const", "v": None}

        expr = body(stmt.value)
        init = self._scan_seeds.get(target)
        warmup = max((self._warmups[i] for i in inputs), default=0)
        return self._emit({"op": "scan", "init": init, "expr": expr, "inputs": inputs}, stmt.span, warmup)

    def _palette_const(self, hex_color: str, span: Span) -> int:
        """Intern a color and emit its palette-index const node."""
        try:
            idx = self._palette.index(hex_color)
        except ValueError:
            idx = len(self._palette)
            self._palette.append(hex_color)
        return self._emit({"op": "const", "value": idx}, span, 0, idx)

    def _const_num(self, value: float, span: Span) -> int:
        """Emit a numeric const node (palette index or literal)."""
        return self._emit({"op": "const", "value": value}, span, 0, value)

    def _palette_block(self, hexes: list[str]) -> int:
        """Append hexes to the palette as a contiguous block (no dedup) so the
        bucket->index arithmetic base+bucket is valid. Returns the base index."""
        base = len(self._palette)
        self._palette.extend(hexes)
        return base

    def _gradient_color(self, call, positional: int, name: str) -> str | None:
        """Resolve a gradient endpoint color arg to a hex: const color / folded
        color.new, or an input.color's baked default; None when unresolved."""
        expr = self._arg_expr(call, positional, name)
        v = _resolve_const(expr) if expr is not None else None
        if isinstance(v, str) and v.startswith("#"):
            return v
        ci = self._color_input_id_of(expr)
        return ci[1] if ci is not None else None

    def _lower_request_security(self, call: ast.CallExpr) -> int:
        """`request.security(syminfo.tickerid, tf, source[n])` -> one `htf` IR node.

        Single-series form; the tuple/array form is lowered in the TupleDecl branch
        so its elements can share one resolved timeframe (and therefore one
        resample).
        """
        self._uses_request_security = True
        positional = [a for a in call.args if getattr(a, "name", None) is None]
        tf = self._resolve_htf_timeframe(positional[1].value if len(positional) > 1 else None)
        return self._htf_node_for(
            positional[2].value if len(positional) > 2 else None, tf, call.span
        )

    def _resolve_htf_timeframe(self, tf_expr) -> dict:
        """Resolve the timeframe argument to `{timeframe, timeframeInputId?}`.

        A const string parses directly. An identifier bound to an
        input.timeframe/input.string declaration carries `timeframeInputId`, and the
        node's own `timeframe` becomes that input's parsed DEFAULT -- the executor
        prefers the runtime input value and falls back to this, so a settings change
        re-resamples without a recompile.

        The `D/1` fallback matches TS exactly. It is unreachable through a compiling
        script (semantic emits OS2025/OS2026 first), but both sides must agree on it
        or a hand-authored IR would differ.
        """
        fallback = {"unit": "D", "multiple": 1}
        if tf_expr is not None and tf_expr.type == "String":
            parsed = parse_timeframe(tf_expr.value)
            return {"timeframe": _tf_dict(parsed) if parsed else fallback}
        if tf_expr is not None and tf_expr.type == "Identifier":
            for decl in self._inputs:
                if decl.get("id") == tf_expr.name and decl.get("type") in ("timeframe", "string"):
                    default = decl.get("defaultValue")
                    parsed = parse_timeframe(default) if isinstance(default, str) else None
                    return {
                        "timeframe": _tf_dict(parsed) if parsed else fallback,
                        "timeframeInputId": tf_expr.name,
                    }
        return {"timeframe": fallback}

    def _htf_node_for(self, source_expr, tf: dict, span) -> int:
        """Emit one `htf` node for `source` or `source[n]`.

        The `close` / offset-0 defaults are unreachable from a compiling script
        (OS2027 fires first) but are kept identical to TS so hand-authored IR cannot
        diverge between the runtimes.
        """
        src = source_expr
        offset = 0
        if src is not None and src.type == "Index":
            idx = getattr(src, "index", None)
            offset = int(idx.value) if idx is not None and idx.type == "Number" else 0
            src = src.object
        source = src.name if src is not None and src.type == "Identifier" else "close"
        node = {"op": "htf", "timeframe": tf["timeframe"], "source": source, "offset": offset}
        if "timeframeInputId" in tf:
            # Key order matches the TS object literal so the serialized IR is
            # byte-identical: timeframe, timeframeInputId, source, offset.
            node = {
                "op": "htf",
                "timeframe": tf["timeframe"],
                "timeframeInputId": tf["timeframeInputId"],
                "source": source,
                "offset": offset,
            }
        return self._emit(node, span, 0)

    def _lower_from_gradient(self, call) -> int:
        """color.from_gradient(value, bottom_value, top_value, bottom_color, top_color)."""
        lo_expr = self._arg_expr(call, 1, "bottom_value")
        hi_expr = self._arg_expr(call, 2, "top_value")
        lo = self._const_arg(call, 1, "bottom_value")
        hi = self._const_arg(call, 2, "top_value")
        lo_hex = self._gradient_color(call, 3, "bottom_color")
        hi_hex = self._gradient_color(call, 4, "top_color")
        # v1 requires CONST numeric bounds (design 0.5 §2). A PRESENT-but-non-const
        # bound is OS2006 ("argument must be a compile-time constant"), not a silent
        # no-color fallback. (Colors stay unvalidated — color.* is best-effort.)
        if lo_expr is not None and not isinstance(lo, (int, float)):
            self._error("OS2006", lo_expr.span, "from_gradient bottom_value must be a constant")
        if hi_expr is not None and not isinstance(hi, (int, float)):
            self._error("OS2006", hi_expr.span, "from_gradient top_value must be a constant")
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or not lo_hex or not hi_hex:
            return self._na_node(call.span)
        k = _GRADIENT_STEPS
        colors = [_interpolate_hex(lo_hex, hi_hex, 0 if k == 1 else j / (k - 1)) for j in range(k)]

        def bucket_of(v: float) -> int:
            if hi == lo:
                return k - 1 if v >= lo else 0
            c = min(max((v - lo) / (hi - lo), 0.0), 1.0)
            return int(math.floor(c * (k - 1) + 0.5))

        v_arg = self._arg_expr(call, 0, "value")
        v_const = _resolve_const(v_arg) if v_arg is not None else None
        if isinstance(v_const, (int, float)) and not isinstance(v_const, bool):
            return self._palette_const(colors[bucket_of(v_const)], call.span)

        base = self._palette_block(colors)
        value_node = self._lower_expr(v_arg) if v_arg is not None else self._na_node(call.span)
        w = self._warmups[value_node]
        if hi == lo:
            ge = self._emit({"op": "binop", "operator": ">=", "args": [value_node, self._const_num(lo, call.span)]}, call.span, w)
            pick = self._emit(
                {"op": "select", "cond": ge, "then": self._const_num(base + k - 1, call.span), "else": self._const_num(base, call.span)},
                call.span,
                w,
            )
            # A NaN value must map to the na (no-color) index — matching the hi≠lo
            # path (NaN propagates through the arithmetic to a NaN index, → ''). Without
            # this guard `NaN >= lo` is falsy → the bottom bucket, a wrong tint on warmup
            # NaN bars (Pine `from_gradient(na, …)` → na).
            is_na = self._emit({"op": "unop", "operator": "isna", "arg": value_node}, call.span, w)
            return self._emit(
                {"op": "select", "cond": is_na, "then": self._na_node(call.span), "else": pick},
                call.span,
                w,
            )
        sub = self._emit({"op": "binop", "operator": "-", "args": [value_node, self._const_num(lo, call.span)]}, call.span, w)
        norm = self._emit({"op": "binop", "operator": "/", "args": [sub, self._const_num(hi - lo, call.span)]}, call.span, w)
        mx = self._emit({"op": "call", "namespace": "math", "function": "max", "args": [norm, self._const_num(0, call.span)]}, call.span, w)
        mn = self._emit({"op": "call", "namespace": "math", "function": "min", "args": [mx, self._const_num(1, call.span)]}, call.span, w)
        scaled = self._emit({"op": "binop", "operator": "*", "args": [mn, self._const_num(k - 1, call.span)]}, call.span, w)
        shifted = self._emit({"op": "binop", "operator": "+", "args": [scaled, self._const_num(0.5, call.span)]}, call.span, w)
        fl = self._emit({"op": "call", "namespace": "math", "function": "floor", "args": [shifted]}, call.span, w)
        return self._emit({"op": "binop", "operator": "+", "args": [fl, self._const_num(base, call.span)]}, call.span, w)

    def _color_input_id_of(self, expr) -> tuple[str, str] | None:
        """Detects a `color=` argument that is a bare identifier bound to an
        `input.color(...)` declaration (P4.4 v1 usage rule — semantic OS2017
        already rejects every other usage). Returns (input_id, default_hex),
        or None when the argument isn't a color input."""
        if expr is None or getattr(expr, "type", None) != "Identifier":
            return None
        node_id = self._resolve_var(expr.name)
        if node_id is None:
            return None
        node = self._nodes[node_id]
        if node.get("op") != "input":
            return None
        decl = next((d for d in self._inputs if d["id"] == node["inputId"]), None)
        if decl is None or decl["type"] != "color":
            return None
        return decl["id"], decl["defaultValue"]

    def _color_with_input(self, call: ast.CallExpr, fallback: str) -> tuple[str, str | None]:
        """color() with input.color detection — used by outputs whose IR
        shape has no colorNodeId slot (hline, fill, plotcandle): static
        color (with no colorInputId), or the input's default hex plus its id."""
        ci = self._color_input_id_of(self._arg_expr(call, None, "color"))
        if ci is not None:
            return ci[1], ci[0]
        return self._color(call, fallback), None

    def _color_spec(self, call: ast.CallExpr, fallback: str) -> tuple[str, int | None, str | None]:
        """Resolve a color= argument: (input_hex, None, colorInputId) for an
        input.color binding, (static_color, None, None) via the const fast
        path, or (fallback, colorNodeId, None) for a dynamic palette
        expression."""
        expr = self._arg_expr(call, None, "color")
        ci = self._color_input_id_of(expr)
        if ci is not None:
            return ci[1], None, ci[0]
        v = self._const_arg(call, None, "color")
        if isinstance(v, str):
            return v, None, None
        if expr is None:
            return fallback, None, None
        node_id = self._lower_expr(expr)
        node = self._nodes[node_id]
        if node.get("op") == "const" and isinstance(node.get("value"), (int, float)):
            idx = int(node["value"])
            if 0 <= idx < len(self._palette):
                return self._palette[idx], None, None
        return fallback, node_id, None

    def _color(self, call: ast.CallExpr, fallback: str) -> str:
        v = self._const_arg(call, None, "color")
        return v if isinstance(v, str) else fallback


def generate_ir(source: str, program: ast.Program) -> tuple[dict | None, list[Diagnostic]]:
    gen = IRGenerator(source)
    for stmt in program.body:
        if stmt.type == "Reassign":
            gen._scan_targets.add(stmt.name)
    for stmt in program.body:
        gen._lower_top_stmt(stmt)
    # Errors halt lowering (no IR); warnings (e.g. OS5001 max_kept clamp) are
    # advisory and ride through with a built IR — mirroring analyze_finality's
    # OS5002/OS5003 warnings that compile() appends to a non-null IR.
    if any(d.severity == "error" for d in gen._diagnostics):
        return None, gen._diagnostics
    warmup_bars = max(gen._warmups) if gen._warmups else 0
    ir = {
        "version": IR_VERSION,
        "compilerVersion": COMPILER_VERSION,
        "sourceHash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "header": {
            "major": 1,
            "minor": 0,
            "compilerVersion": COMPILER_VERSION,
            "requiredFeatures": _required_features(gen),
            "numericMode": "f64-strict",
        },
        "declaration": gen._declaration,
        "inputs": gen._inputs,
        "nodes": gen._nodes,
        "outputs": gen._outputs,
        "meta": {"warmupBars": warmup_bars, "spans": gen._spans},
    }
    if gen._palette:
        ir["palette"] = gen._palette
    # TELEMETRY/EXPLAIN hint only — admission RECOMPUTES the authoritative
    # cost from the IR nodes and NEVER trusts this field.
    ir["meta"]["planCost"] = estimate_plan_cost(ir)
    return ir, gen._diagnostics
