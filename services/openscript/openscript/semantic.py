"""OpenScript semantic analysis — Python port of the TS analyzer
(openalgo-openscript/src/compiler/semantic.ts).

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
        # Names bound to `p = plot(...)` — usable only as fill() args (OS2012).
        self._plot_handles: set[str] = set()
        # Scan state per `:=` target: decl seen (const-init) / reassign visited.
        self._scan_vars: dict[str, dict] = {}
        # The scan var whose Reassign RHS we are currently inside.
        self._current_scan_var: str | None = None
        # Depth of ta.* / math.sum calls (self-refs inside are OS2016).
        self._windowed_call_depth = 0
        self._current_function: str | None = None

    def analyze(self, program: ast.Program) -> list[Diagnostic]:
        # Pre-pass: which names are reassigned with `:=` at top level.
        for stmt in program.body:
            if stmt.type == "Reassign" and stmt.name not in self._scan_vars:
                self._scan_vars[stmt.name] = {"declared": False, "reassigned": False}
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
            if _is_plot_call(stmt.value):
                self._plot_handles.add(stmt.name)
            scan = self._scan_vars.get(stmt.name)
            if scan is not None:
                if not top_level:
                    self._error("OS2016", stmt.span, f"'{stmt.name}' is reassigned - declare it at top level")
                elif not _is_const_seed(stmt.value):
                    self._error("OS2016", stmt.span, f"'{stmt.name}' needs a constant initial value")
                else:
                    scan["declared"] = True
        elif kind == "Reassign":
            self._visit_reassign(stmt, top_level)
        elif kind == "TupleDecl":
            self._visit_expr(stmt.value, top_level)
            self._check_destructure(stmt.value, len(stmt.names), stmt.span)
            for n in stmt.names:
                self._declare_var(n.name, n.span)
        elif kind == "FunctionDecl":
            self._visit_function_decl(stmt)
        elif kind == "ExprStmt":
            self._visit_expr(stmt.expr, top_level)

    def _visit_reassign(self, stmt, top_level: bool) -> None:
        if not top_level:
            self._error("OS2016", stmt.span, "':=' is only allowed at top level")
            return
        scan = self._scan_vars.get(stmt.name)
        if scan is None or not scan["declared"]:
            self._error(
                "OS2016", stmt.name_span, f"'{stmt.name}' must be declared with a constant value before ':='"
            )
            return
        if scan["reassigned"]:
            self._error("OS2016", stmt.name_span, f"'{stmt.name}' is reassigned more than once")
            return
        self._current_scan_var = stmt.name
        self._visit_expr(stmt.value, True)
        self._current_scan_var = None
        scan["reassigned"] = True

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
            if e.name in self._plot_handles:
                self._error("OS2012", e.span, e.name)
                return
            scan = self._scan_vars.get(e.name)
            if scan is not None and not scan["reassigned"]:
                if self._current_scan_var == e.name:
                    if self._windowed_call_depth > 0:
                        self._error(
                            "OS2016", e.span, f"'{e.name}' cannot appear inside a windowed call in its own ':='"
                        )
                else:
                    self._error("OS2016", e.span, f"'{e.name}' is used before its ':=' reassignment")
                return
            if not self._is_var_in_scope(e.name) and e.name not in SOURCE_IDS:
                self._error("OS2001", e.span, e.name)
        elif kind == "Member":
            ns = e.object.name if getattr(e.object, "type", None) == "Identifier" else ""
            self._visit_member_value(ns, e.property, e.span)
        elif kind == "Call":
            self._visit_call(e, top_level)
        elif kind == "Index":
            if (
                getattr(e.object, "type", None) == "Identifier"
                and e.object.name == self._current_scan_var
                and e.index.type == "Number"
                and e.index.value >= 2
            ):
                self._error(
                    "OS2016", e.span, f"only '{e.object.name}[1]' self-history is supported in ':='"
                )
                return
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
        windowed = False
        if callee.type == "Member" and getattr(callee.object, "type", None) == "Identifier":
            self._visit_namespace_call(callee.object.name, callee.property, call, top_level)
            windowed = callee.object.name == "ta" or (
                callee.object.name == "math" and callee.property == "sum"
            )
        elif callee.type == "Identifier":
            self._visit_bare_call(callee.name, call, top_level)
            if callee.name == "fill":
                self._visit_fill_args(call, top_level)
                return
        else:
            self._error("OS2002", call.span)
        if windowed:
            self._windowed_call_depth += 1
        for arg in call.args:
            self._visit_expr(arg.value, top_level)
        if windowed:
            self._windowed_call_depth -= 1

    def _visit_fill_args(self, call: ast.CallExpr, top_level: bool) -> None:
        """fill(p1, p2, ...) — the first two positional args must be plot
        handles (OS2012); remaining args are visited normally."""
        positional_seen = 0
        for arg in call.args:
            if arg.name is None and positional_seen < 2:
                positional_seen += 1
                v = arg.value
                if v.type != "Identifier" or v.name not in self._plot_handles:
                    self._error("OS2012", v.span)
                continue
            self._visit_expr(arg.value, top_level)
        if positional_seen < 2:
            self._error("OS2012", call.span)

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
            max_args = 1 if name == "na" else 2
            if len(call.args) < 1 or len(call.args) > max_args:
                hint = "1 or 2" if max_args == 2 else "1"
                self._error("OS2003", call.span, f"got {len(call.args)}, expected {hint}")
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


def _is_const_seed(e: ast.Expr) -> bool:
    """A valid scan seed: number / bool / na, optionally negated."""
    if e.type in ("Number", "Bool", "Na"):
        return True
    return e.type == "Unary" and e.op == "-" and e.operand.type == "Number"


def _is_plot_call(e: ast.Expr) -> bool:
    return (
        e.type == "Call"
        and getattr(e.callee, "type", None) == "Identifier"
        and e.callee.name == "plot"
    )


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
