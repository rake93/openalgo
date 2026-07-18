"""OpenScript semantic analysis — Python port of the TS analyzer
(openalgo-indicator-engine/src/compiler/semantic.ts).

Resolves a parsed program against the built-in surface and reports semantic
diagnostics: declaration placement, symbol resolution, function/overload arity,
destructuring, and duplicate input/alert ids. Identical rules to the TS.
"""

from __future__ import annotations

from . import ast_nodes as ast
from .builtins_table import (
    CONSTANT_NAMESPACES,
    INPUT_FUNCTIONS,
    MATH_FUNCTIONS,
    OUTPUT_FUNCTIONS,
    SPECIAL_FUNCTIONS,
    TA_FUNCTIONS,
    ta_arities,
)
from .diagnostics import Diagnostic, Span, make_diagnostic

SOURCE_IDS = frozenset(
    {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"}
)


class Analyzer:
    def __init__(self) -> None:
        self._diagnostics: list[Diagnostic] = []
        self._scopes: list[set[str]] = [set()]
        self._functions: dict[str, int] = {}
        self._input_titles: set[str] = set()
        self._alert_titles: set[str] = set()
        self._current_function: str | None = None

    def analyze(self, program: ast.Program) -> list[Diagnostic]:
        for index, stmt in enumerate(program.body):
            if _is_indicator_call(stmt) and index != 0:
                self._error("OS1005", stmt.span)
            self._visit_stmt(stmt, True)
        return self._diagnostics

    def _error(self, code: str, span: Span, detail: str | None = None) -> None:
        self._diagnostics.append(make_diagnostic(code, "error", span, detail))

    def _scope(self) -> set[str]:
        return self._scopes[-1]

    def _declare_var(self, name: str, span: Span) -> None:
        if name in self._scope():
            self._error("OS2009", span, name)
            return
        self._scope().add(name)

    def _is_var_in_scope(self, name: str) -> bool:
        return any(name in s for s in self._scopes)

    # ── statements ──────────────────────────────────────────────────────────────

    def _visit_stmt(self, stmt: ast.Stmt, top_level: bool) -> None:
        kind = stmt.type
        if kind == "VarDecl":
            self._visit_expr(stmt.value, top_level)
            self._declare_var(stmt.name, stmt.name_span)
        elif kind == "TupleDecl":
            self._visit_expr(stmt.value, top_level)
            self._check_destructure(stmt.value, len(stmt.names), stmt.span)
            for n in stmt.names:
                self._declare_var(n.name, n.span)
        elif kind == "FunctionDecl":
            self._visit_function_decl(stmt)
        elif kind == "ExprStmt":
            self._visit_expr(stmt.expr, top_level)

    def _visit_function_decl(self, fn: ast.FunctionDecl) -> None:
        if fn.name in self._functions or fn.name in self._scope():
            self._error("OS2009", fn.name_span, fn.name)
        self._functions[fn.name] = len(fn.params)
        self._scopes.append({p.name for p in fn.params})
        outer = self._current_function
        self._current_function = fn.name
        self._visit_expr(fn.body, False)
        self._current_function = outer
        self._scopes.pop()

    def _visit_block(self, block: ast.Block) -> None:
        self._scopes.append(set())
        for stmt in block.statements:
            self._visit_stmt(stmt, False)
        self._scopes.pop()

    # ── expressions ─────────────────────────────────────────────────────────────

    def _visit_expr(self, e: ast.Expr, top_level: bool) -> None:
        kind = e.type
        if kind in ("Number", "String", "Color", "Bool", "Na"):
            return
        if kind == "Identifier":
            if not self._is_var_in_scope(e.name) and e.name not in SOURCE_IDS:
                self._error("OS2001", e.span, e.name)
        elif kind == "Member":
            ns = e.object.name if getattr(e.object, "type", None) == "Identifier" else ""
            self._visit_member_value(ns, e.property, e.span)
        elif kind == "Call":
            self._visit_call(e, top_level)
        elif kind == "Index":
            self._visit_expr(e.object, top_level)
            self._visit_expr(e.index, top_level)
        elif kind == "Unary":
            self._visit_expr(e.operand, top_level)
        elif kind == "Binary":
            self._visit_expr(e.left, top_level)
            self._visit_expr(e.right, top_level)
        elif kind == "Ternary":
            self._visit_expr(e.cond, top_level)
            self._visit_expr(e.then, top_level)
            self._visit_expr(e.else_, top_level)
        elif kind == "If":
            self._visit_expr(e.cond, top_level)
            self._visit_block(e.then)
            if e.else_:
                self._visit_block(e.else_)

    def _visit_member_value(self, ns: str, prop: str, span: Span) -> None:
        members = CONSTANT_NAMESPACES.get(ns)
        if members is not None:
            if prop not in members:
                self._error("OS2001", span, f"{ns}.{prop}")
            return
        self._error("OS2001", span, f"{ns}.{prop}" if ns else prop)

    def _visit_call(self, call: ast.CallExpr, top_level: bool) -> None:
        callee = call.callee
        if callee.type == "Member" and getattr(callee.object, "type", None) == "Identifier":
            self._visit_namespace_call(callee.object.name, callee.property, call, top_level)
        elif callee.type == "Identifier":
            self._visit_bare_call(callee.name, call, top_level)
        else:
            self._error("OS2002", call.span)
        for arg in call.args:
            self._visit_expr(arg.value, top_level)

    def _visit_namespace_call(self, ns: str, fn: str, call: ast.CallExpr, top_level: bool) -> None:
        if ns == "ta":
            spec = TA_FUNCTIONS.get(fn)
            if spec is None:
                self._error("OS2002", call.span, f"ta.{fn}")
                return
            arities = ta_arities(spec)
            if len(call.args) not in arities:
                joined = " or ".join(str(a) for a in arities)
                self._error("OS2003", call.span, f"got {len(call.args)}, expected {joined}")
        elif ns == "math":
            self._resolve_fn(MATH_FUNCTIONS.get(fn), fn, call)
        elif ns == "input":
            if fn not in INPUT_FUNCTIONS:
                self._error("OS2002", call.span, f"input.{fn}")
                return
            if not top_level:
                self._error("OS2005", call.span)
            self._register_title(call, self._input_titles, "OS2014")
        elif ns in ("color", "shape", "location", "size", "plot"):
            return  # calling a constant-namespace member (e.g. color.new) — allowed
        else:
            self._error("OS2002", call.span, f"{ns}.{fn}")

    def _resolve_fn(self, spec: dict | None, name: str, call: ast.CallExpr) -> None:
        if spec is None:
            self._error("OS2002", call.span, name)
            return
        if len(call.args) not in spec["arities"]:
            joined = " or ".join(str(a) for a in spec["arities"])
            self._error("OS2003", call.span, f"got {len(call.args)}, expected {joined}")

    def _visit_bare_call(self, name: str, call: ast.CallExpr, top_level: bool) -> None:
        if name == "indicator":
            return
        if name in SPECIAL_FUNCTIONS:
            if len(call.args) < 1 or len(call.args) > 2:
                self._error("OS2003", call.span, f"got {len(call.args)}, expected 1 or 2")
            return
        if name in OUTPUT_FUNCTIONS:
            if not top_level:
                self._error("OS2011", call.span, name)
            if name == "alertcondition":
                self._register_title(call, self._alert_titles, "OS2015")
            return
        if name in self._functions:
            arity = self._functions[name]
            if len(call.args) != arity:
                self._error("OS2003", call.span, f"got {len(call.args)}, expected {arity}")
            if name == self._current_function:
                self._error("OS2008", call.span, name)
            return
        self._error("OS2002", call.span, name)

    def _check_destructure(self, value: ast.Expr, count: int, span: Span) -> None:
        if value.type != "Call" or value.callee.type != "Member":
            self._error("OS2004", span, "right side is not a multi-output call")
            return
        callee = value.callee
        if getattr(callee.object, "type", None) != "Identifier" or callee.object.name != "ta":
            self._error("OS2004", span, "only ta.* functions return tuples")
            return
        spec = TA_FUNCTIONS.get(callee.property)
        if spec is None or spec["outputs"] != count:
            expected = spec["outputs"] if spec else "?"
            self._error("OS2004", span, f"expected {expected} names, got {count}")

    def _register_title(self, call: ast.CallExpr, seen: set[str], code: str) -> None:
        title = _title_of(call)
        if title is None:
            return
        if title in seen:
            self._error(code, call.span, title)
            return
        seen.add(title)


def _is_indicator_call(stmt: ast.Stmt) -> bool:
    return (
        stmt.type == "ExprStmt"
        and stmt.expr.type == "Call"
        and getattr(stmt.expr.callee, "type", None) == "Identifier"
        and stmt.expr.callee.name == "indicator"
    )


def _title_of(call: ast.CallExpr) -> str | None:
    for arg in call.args:
        if arg.name == "title" and arg.value.type == "String":
            return arg.value.value
    positional = [a for a in call.args if a.name is None]
    if len(positional) >= 2 and positional[1].value.type == "String":
        return positional[1].value.value
    return None


def analyze_program(program: ast.Program) -> list[Diagnostic]:
    return Analyzer().analyze(program)
