"""OpenScript IR generation — Python port of the TS ir-gen
(openalgo-indicator-engine/src/compiler/ir-gen.ts).

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
from . import ast_nodes as ast
from .builtins_table import TA_FUNCTIONS, ta_overload
from .diagnostics import Diagnostic, Span, make_diagnostic

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
SIZE_MAP = {"tiny": "tiny", "small": "small", "normal": "medium", "large": "big", "huge": "big", "auto": "medium"}
STYLE_MAP = {
    "style_line": "line", "style_stepline": "stepline", "style_histogram": "histogram",
    "style_cross": "cross", "style_area": "area", "style_columns": "columns", "style_circles": "circles",
}
MATH_CONST = {"pi": math.pi, "e": math.e, "phi": 1.618033988749895, "rphi": 0.6180339887498949}
INPUT_TYPE = {"int": "integer", "float": "float", "bool": "bool", "string": "string", "source": "source"}


def _slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return out or "x"


def _resolve_const_member(e: ast.MemberExpr):
    ns = e.object.name if getattr(e.object, "type", None) == "Identifier" else ""
    p = e.property
    table = {"color": COLOR_HEX, "shape": SHAPE_MAP, "location": LOCATION_MAP, "size": SIZE_MAP, "plot": STYLE_MAP, "math": MATH_CONST}.get(ns)
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
    return None


_SOURCES = frozenset({"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"})


def _is_input_call(e: ast.Expr) -> bool:
    return (
        e.type == "Call"
        and e.callee.type == "Member"
        and getattr(e.callee.object, "type", None) == "Identifier"
        and e.callee.object.name == "input"
    )


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
        self._functions: dict[str, ast.FunctionDecl] = {}
        self._declaration = {"name": "Untitled", "overlay": False}
        self._diagnostics: list[Diagnostic] = []

    # ── node emission (CSE) ─────────────────────────────────────────────────────

    def _emit(self, node: dict, span: Span, warmup: int, static_val: float | None = None) -> int:
        key = json.dumps(node, sort_keys=True)
        found = self._cse.get(key)
        if found is not None:
            return found
        node_id = len(self._nodes)
        self._nodes.append({**node, "id": node_id})
        self._spans[node_id] = span.to_dict()
        self._warmups.append(warmup)
        self._statics.append(static_val)
        self._cse[key] = node_id
        return node_id

    def _na_node(self, span: Span) -> int:
        return self._emit({"op": "const", "value": None}, span, 0)

    def _bind(self, name: str, node_id: int) -> None:
        self._scopes[-1][name] = node_id

    def _resolve_var(self, name: str):
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    # ── top-level statements ────────────────────────────────────────────────────

    def _lower_top_stmt(self, stmt: ast.Stmt) -> None:
        kind = stmt.type
        if kind == "VarDecl":
            if _is_input_call(stmt.value):
                self._bind(stmt.name, self._lower_input(stmt.value, stmt.name))
            else:
                self._bind(stmt.name, self._lower_expr(stmt.value))
        elif kind == "TupleDecl":
            call = stmt.value
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
        if kind in ("String", "Color"):
            return self._emit({"op": "const", "value": e.value}, e.span, 0)
        if kind == "Bool":
            return self._emit({"op": "const", "value": e.value}, e.span, 0)
        if kind == "Na":
            return self._na_node(e.span)
        if kind == "Identifier":
            if e.name in _SOURCES:
                return self._emit({"op": "source", "source": e.name}, e.span, 0)
            bound = self._resolve_var(e.name)
            return bound if bound is not None else self._na_node(e.span)
        if kind == "Member":
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
            if ns == "math":
                return self._lower_math_call(fn, call)
            if ns == "input":
                return self._lower_input(call, None)
            v = _resolve_const_member(callee)
            return self._emit({"op": "const", "value": v if v is not None else "#000000"}, call.span, 0)
        if callee.type == "Identifier":
            if callee.name == "nz":
                return self._lower_nz(call)
            fn = self._functions.get(callee.name)
            if fn is not None:
                return self._inline_function(fn, call)
        return self._na_node(call.span)

    def _lower_ta_call(self, fn: str, call: ast.CallExpr, output: int) -> int:
        spec = TA_FUNCTIONS.get(fn)
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
        node = {"op": "call", "namespace": "ta", "function": fn, "args": arg_ids}
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
        else:  # source
            decl["defaultValue"] = default if isinstance(default, str) else "close"

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
        elif fn in ("barcolor", "bgcolor"):
            self._outputs.append(self._tint_output(fn, call))
        elif fn == "alertcondition":
            self._outputs.append(self._alert_output(call))
        # 'fill' references plot handles — deferred; no-op for v1.

    def _plot_output(self, call: ast.CallExpr) -> dict:
        node_id = self._lower_expr(call.args[0].value)
        style: dict = {"color": self._color(call, "#2962ff")}
        lw = self._const_arg(call, None, "linewidth")
        if isinstance(lw, (int, float)) and not isinstance(lw, bool):
            style["lineWidth"] = lw
        variant = self._const_arg(call, None, "style")
        if isinstance(variant, str):
            style["variant"] = variant
        return {"kind": "plot", "nodeId": node_id, "title": self._title(call, 1), "style": style}

    def _hline_output(self, call: ast.CallExpr) -> dict:
        price = _resolve_const(call.args[0].value) if call.args else None
        return {
            "kind": "hline",
            "price": price if isinstance(price, (int, float)) and not isinstance(price, bool) else 0,
            "title": self._title(call, 1),
            "style": {"color": self._color(call, "#787b86")},
        }

    def _marker_output(self, fn: str, call: ast.CallExpr) -> dict:
        cond_node = self._lower_expr(call.args[0].value)
        location = self._const_arg(call, None, "location")
        out: dict = {
            "kind": fn,
            "condNodeId": cond_node,
            "title": self._title(call, None),
            "location": location if isinstance(location, str) else "aboveBar",
            "color": self._color(call, "#2962ff"),
        }
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

    def _tint_output(self, fn: str, call: ast.CallExpr) -> dict:
        return {
            "kind": fn,
            "condNodeId": self._lower_expr(call.args[0].value),
            "color": self._color(call, "#ff9800"),
            "title": self._title(call, None),
        }

    def _alert_output(self, call: ast.CallExpr) -> dict:
        cond_node = self._lower_expr(call.args[0].value)
        title = self._title(call, 1)
        message = self._const_arg(call, 2, "message")
        return {
            "kind": "alertcondition",
            "condNodeId": cond_node,
            "conditionId": _slug(title or f"cond_{len(self._outputs)}"),
            "title": title,
            "message": message if isinstance(message, str) else title,
        }

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

    def _color(self, call: ast.CallExpr, fallback: str) -> str:
        v = self._const_arg(call, None, "color")
        return v if isinstance(v, str) else fallback


def generate_ir(source: str, program: ast.Program) -> tuple[dict | None, list[Diagnostic]]:
    gen = IRGenerator(source)
    for stmt in program.body:
        gen._lower_top_stmt(stmt)
    if gen._diagnostics:
        return None, gen._diagnostics
    warmup_bars = max(gen._warmups) if gen._warmups else 0
    ir = {
        "version": IR_VERSION,
        "compilerVersion": COMPILER_VERSION,
        "sourceHash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "declaration": gen._declaration,
        "inputs": gen._inputs,
        "nodes": gen._nodes,
        "outputs": gen._outputs,
        "meta": {"warmupBars": warmup_bars, "spans": gen._spans},
    }
    return ir, []
