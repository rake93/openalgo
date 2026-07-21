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
    KERNELS_FUNCTIONS,
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
# Pine time/context series (P-time) — bare identifiers that resolve without a
# declaration, kept separate from price sources so input.source never offers
# them. See openalgo-openscript/src/types/dataset.ts CONTEXT_IDS.
CONTEXT_IDS = frozenset(
    {"time", "bar_index", "last_bar_index", "dayofweek", "dayofmonth", "hour", "minute", "month", "year"}
)
KNOWN_SERIES = SOURCE_IDS | CONTEXT_IDS


class Analyzer:
    def __init__(self) -> None:
        self._diagnostics: list[Diagnostic] = []
        self._scopes: list[set[str]] = [set()]
        self._functions: dict[str, int] = {}
        self._input_titles: set[str] = set()
        self._alert_titles: set[str] = set()
        # Names bound to `p = plot(...)` — usable only as fill() args (OS2012).
        self._plot_handles: set[str] = set()
        # Names bound to `input.color(...)` — usable only as a `color=` argument (OS2017).
        self._color_inputs: set[str] = set()
        # True while visiting the direct value of a `color=` named argument.
        self._in_color_arg_position = False
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
            if _is_input_color_call(stmt.value):
                self._color_inputs.add(stmt.name)
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
            if e.name in self._color_inputs and not self._in_color_arg_position:
                self._error("OS2017", e.span, e.name)
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
            if not self._is_var_in_scope(e.name) and e.name not in KNOWN_SERIES:
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
        elif kind == "ArrayLiteral":
            # Only ever reachable as input.string's `options=` value
            # (parser-gated); fully validated by _check_string_options when
            # the enclosing call is visited.
            return

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
            # ta.*/kernels.* calls are windowed; math.sum is the one math.*
            # exception (it's the rolling-sum kernel, not elementwise).
            windowed = callee.object.name in ("ta", "kernels") or (
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
            self._visit_arg(arg, top_level)
        if windowed:
            self._windowed_call_depth -= 1

    def _visit_arg(self, arg: ast.Argument, top_level: bool) -> None:
        prev = self._in_color_arg_position
        self._in_color_arg_position = arg.name == "color" and arg.value.type == "Identifier"
        self._visit_expr(arg.value, top_level)
        self._in_color_arg_position = prev

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
            self._visit_arg(arg, top_level)
        if positional_seen < 2:
            self._error("OS2012", call.span)

    def _visit_namespace_call(self, ns: str, fn: str, call: ast.CallExpr, top_level: bool) -> None:
        if ns in ("ta", "kernels"):
            table = TA_FUNCTIONS if ns == "ta" else KERNELS_FUNCTIONS
            spec = table.get(fn)
            if spec is None:
                self._error("OS2002", call.span, f"{ns}.{fn}")
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
            if fn == "string":
                self._check_string_options(call)
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
            if name in ("plotlevel", "plotzone"):
                self._validate_drawing_output(name, call)
            return
        if name in self._functions:
            arity = self._functions[name]
            if len(call.args) != arity:
                self._error("OS2003", call.span, f"got {len(call.args)}, expected {arity}")
            if name == self._current_function:
                self._error("OS2008", call.span, name)
            return
        self._error("OS2002", call.span, name)

    def _validate_drawing_output(self, fn: str, call: ast.CallExpr) -> None:
        """`plotlevel`/`plotzone` argument-consistency (design 0.5 §2/§4). These
        are ERRORS (they halt compilation); the `max_kept` cap check is a
        non-gating OS5001 warning wired in ir-gen where the clamp lives. Unknown
        `extend`/`terminate`/`line` enum members are caught by the generic member
        visit (OS2001), consistent with `shape`/`location`/`alert`."""
        extend_arg = self._named_arg_value(call, "extend")
        # Absent extend= defaults to extend.lastbar (design §2).
        extend_mode = "lastbar" if extend_arg is None else self._enum_member(extend_arg)
        has_terminate = self._named_arg_value(call, "terminate") is not None
        has_bars = self._named_arg_value(call, "bars") is not None
        mitigated_arg = self._named_arg_value(call, "mitigated_color")

        if has_terminate and extend_mode != "until":
            self._error("OS2018", call.span)
        if extend_mode == "until" and not has_terminate:
            self._error("OS2019", call.span)
        if has_bars and extend_mode != "bars":
            self._error("OS2020", call.span)
        if extend_mode == "bars" and not has_bars:
            self._error("OS2021", call.span)
        if mitigated_arg is not None:
            # mitigated_color= is a zone-only styling of a terminate.touch close.
            term_arg = self._named_arg_value(call, "terminate")
            term_mode = self._enum_member(term_arg) if term_arg is not None else None
            if fn == "plotlevel" or term_mode != "touch":
                self._error("OS2022", call.span)
        offset = self._numeric_arg_value(call, "offset")
        if offset is not None and offset > 0:
            self._error("OS2023", call.span, "offset must be <= 0")
        right_pad = self._numeric_arg_value(call, "right_pad")
        if right_pad is not None and right_pad < 0:
            self._error("OS2023", call.span, "right_pad must be >= 0")
        # A negative max_kept is grammar-legal but nonsensical — reject it at
        # COMPILE time (OS2023) so it never reaches admission as
        # IR_UNPRICED_OPERATOR (Fable #6). ir-gen still clamps the UPPER side.
        max_kept = self._numeric_arg_value(call, "max_kept")
        if max_kept is not None and max_kept < 0:
            self._error("OS2023", call.span, "max_kept must be >= 0")
        # Required-const drawing args (design 0.5 §2: "const or input"; input
        # support for these is deferred, so v1 is const-only). A PRESENT-but-non-
        # const arg must be OS2023, never a silent fallback to the default. Numeric
        # args check const-numeric-resolvability; enum args check STRUCTURE (is it a
        # `ns.member`?) so an unknown MEMBER stays OS2001 via the generic member
        # visit rather than double-reporting here.
        for name in ("bars", "offset", "right_pad", "max_kept"):
            e = self._named_arg_value(call, name)
            if e is not None and self._numeric_arg_value(call, name) is None:
                self._error("OS2023", e.span, f"{name}= must be a compile-time constant")
        for name in ("extend", "terminate"):
            e = self._named_arg_value(call, name)
            if e is not None and self._enum_member(e) is None:
                self._error("OS2023", e.span, f"{name}= must be a constant enum member")

    def _named_arg_value(self, call: ast.CallExpr, name: str):
        """The value expression of a named argument, if present."""
        for arg in call.args:
            if arg.name == name:
                return arg.value
        return None

    def _enum_member(self, expr) -> str | None:
        """The member property of an enum access `ns.MEMBER` (e.g. extend.until)."""
        if expr.type == "Member" and getattr(expr.object, "type", None) == "Identifier":
            return expr.property
        return None

    def _numeric_arg_value(self, call: ast.CallExpr, name: str):
        """A numeric literal argument (a plain Number or a negated one), else None."""
        e = self._named_arg_value(call, name)
        if e is None:
            return None
        if e.type == "Number":
            return e.value
        if e.type == "Unary" and e.op == "-" and e.operand.type == "Number":
            return -e.operand.value
        return None

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

    def _check_string_options(self, call: ast.CallExpr) -> None:
        """`input.string(default, ..., options=[...])` (P4.4): every options
        element must be a string literal (else OS2004), and when they all
        are, `default` (the first argument) must be one of them (else
        OS2004). Reuses OS2004 ('Type mismatch') rather than allocating a
        new code — both are "argument doesn't match the expected shape"
        cases."""
        options_arg = next((a for a in call.args if a.name == "options"), None)
        if options_arg is None or options_arg.value.type != "ArrayLiteral":
            return
        strings: list[str] = []
        all_strings = True
        for el in options_arg.value.elements:
            if el.type == "String":
                strings.append(el.value)
            else:
                all_strings = False
                self._error("OS2004", el.span, "input.string options elements must be string literals")
        if not all_strings:
            return
        default_arg = call.args[0] if call.args else None
        if default_arg is not None and default_arg.value.type == "String" and default_arg.value.value not in strings:
            self._error("OS2004", default_arg.span, "input.string default must be one of its declared options")

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


def _is_input_color_call(e: ast.Expr) -> bool:
    return (
        e.type == "Call"
        and e.callee.type == "Member"
        and getattr(e.callee.object, "type", None) == "Identifier"
        and e.callee.object.name == "input"
        and e.callee.property == "color"
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
