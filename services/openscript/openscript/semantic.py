"""OpenScript semantic analysis — Python port of the TS analyzer
(openalgo-openscript/src/compiler/semantic.ts).

Resolves a parsed program against the built-in surface and reports semantic
diagnostics: declaration placement, symbol resolution, function/overload arity,
destructuring, and duplicate input/alert ids. Identical rules to the TS.
"""

from __future__ import annotations

# Session-string literals and defaults are validated with the SAME parser the
# runtime bind uses (OS2031 at compile time, OS4005 at bind time), the identical
# share-one-parser rule `parse_timeframe` below already follows.
from ..runtime.session_string import SessionParseError, parse_session_string

# request.security validates its timeframe string with the SAME parser the runtime
# resampler uses, so the compiler can never accept a timeframe the executor cannot
# bucket (register C4).
from ..runtime.timeframe import parse_timeframe
from . import ast_nodes as ast
from .builtins_table import (
    CONSTANT_NAMESPACES,
    CONTEXT_MEMBERS,
    HTF_SOURCE_KINDS,
    INPUT_FUNCTIONS,
    INPUT_NAMED_ARGS,
    KERNELS_FUNCTIONS,
    MATH_FUNCTIONS,
    NAMED_ARGS,
    OUTPUT_FUNCTIONS,
    REQUEST_FUNCTIONS,
    SESSION_FUNCTIONS,
    SPECIAL_FUNCTIONS,
    TA_FUNCTIONS,
    ta_arities,
)
from .diagnostics import Diagnostic, Span, make_diagnostic
from .input_defval import defval_of
from .stdlib import STDLIB_NAMESPACES, stdlib_arity, stdlib_is_windowed

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

# Argument names an `input.color` may legally flow into (OS2017).
#
# `color` alone until G8: a zone's `border_color=` and `mitigated_color=` are
# colour slots too, and rejecting an input there while accepting it in `color=`
# made the settings surface arbitrary rather than principled. Everything here is
# a slot both materializers now substitute at render time, so admitting a name
# without wiring its `colorInputId` would recreate the placebo control this set
# exists to prevent. Mirrors COLOR_ARG_NAMES in the TS semantic analyser.
_COLOR_ARG_NAMES = frozenset({"color", "border_color", "mitigated_color"})


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
        # Names bound to `input.timeframe(...)` / `input.string(...)` — accepted as the
        # timeframe argument of request.security in place of a const string, because
        # the value resolves at RUNTIME (a settings change re-resamples, no recompile).
        self._timeframe_inputs: set[str] = set()
        # Names bound to `input.session(...)` — accepted as a `session.*`
        # argument (the OS2032 emitter, session-surface design §4.1).
        self._session_inputs: set[str] = set()
        # Names bound to `input.bool(...)` — accepted as a `label_visible=`/
        # `text_visible=` binding (G6).
        self._bool_inputs: set[str] = set()
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

    def _check_named_args(self, fn_label: str, accepted, call) -> None:
        """Warn (OS2010) on a named argument this compiler does not read.

        Such an argument is silently dropped -- the mechanism that let
        `label_size` sit advertised-and-inert. ERROR since 2026-07-29: it shipped
        as a warning first so nothing broke mid-flight, and the flip is safe for
        the known corpus because no fixture and no shipped indicator passes an
        argument the compiler ignores. A saved script that does stops compiling,
        and its stored IR keeps running because the P2 refresh declines to replace
        a working artifact with a failed recompile.
        """
        for arg in call.args:
            if arg.name is not None and arg.name not in accepted:
                span = getattr(arg.value, "span", None) or call.span
                self._error("OS2010", span, f"{arg.name} on {fn_label}")

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
            if _is_input_timeframe_call(stmt.value):
                self._timeframe_inputs.add(stmt.name)
            if _is_input_session_call(stmt.value):
                self._session_inputs.add(stmt.name)
            if _is_input_bool_call(stmt.value):
                self._bool_inputs.add(stmt.name)
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
        # Execution-resolved context properties (`timeframe.in_seconds`) are
        # checked against the SAME table ir_gen lowers from, so acceptance and
        # lowering cannot drift apart.
        context_members = CONTEXT_MEMBERS.get(ns)
        if context_members is not None:
            if prop not in context_members:
                self._error("OS2001", span, f"{ns}.{prop}")
            return
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
            # A stdlib call counts when its body reaches such a kernel: after
            # inlining the argument really is inside one, so
            # `x := bos.up_from(x, 2, 2)` must be caught here rather than becoming
            # a silently wrong recurrence.
            windowed = (
                callee.object.name in ("ta", "kernels")
                or (callee.object.name == "math" and callee.property == "sum")
                or stdlib_is_windowed(callee.object.name, callee.property)
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
        self._in_color_arg_position = (
            arg.name in _COLOR_ARG_NAMES and arg.value.type == "Identifier"
        )
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
        elif ns == "session":
            spec = SESSION_FUNCTIONS.get(fn)
            if spec is None:
                self._error("OS2002", call.span, f"session.{fn}")
                return
            if len(call.args) not in spec["arities"]:
                joined = " or ".join(str(a) for a in spec["arities"])
                self._error("OS2003", call.span, f"got {len(call.args)}, expected {joined}")
                return
            self._check_session_arg(call.args[0])
        elif ns == "input":
            if fn not in INPUT_FUNCTIONS:
                self._error("OS2002", call.span, f"input.{fn}")
                return
            if not top_level:
                self._error("OS2005", call.span)
            self._register_title(call, self._input_titles, "OS2014")
            self._check_named_args(f"input.{fn}", INPUT_NAMED_ARGS, call)
            if fn == "string":
                self._check_string_options(call)
            if fn == "session":
                self._check_session_defval(call)
        elif ns == "request":
            if fn not in REQUEST_FUNCTIONS:
                self._error("OS2002", call.span, f"request.{fn}")
                return
            self._visit_request_security(call)
        elif ns in ("color", "shape", "location", "size", "plot"):
            return  # calling a constant-namespace member (e.g. color.new) — allowed
        elif ns in STDLIB_NAMESPACES:
            # Bundled standard library (openscript-stdlib-design.md §4). Resolved
            # from the registry, NOT a hand-maintained table, so the surface the
            # analyzer accepts and the surface ir-gen can inline are the same set
            # by construction. Deliberately reuses OS2002/OS2003 rather than
            # minting stdlib-specific codes: "no such function" and "wrong arity"
            # are the only failures a caller can reach.
            arity = stdlib_arity(ns, fn)
            if arity is None:
                self._error("OS2002", call.span, f"{ns}.{fn}")
                return
            if len(call.args) != arity:
                self._error("OS2003", call.span, f"got {len(call.args)}, expected {arity}")
        else:
            self._error("OS2002", call.span, f"{ns}.{fn}")

    def _visit_request_security(self, call: ast.CallExpr) -> None:
        """The five `request.security` checks (design §2). Mirrors the TS
        `visitRequestSecurity` argument for argument, including which span each
        diagnostic attaches to -- a mismatch there changes where the editor
        underlines even when the code is right.
        """
        positional = [a for a in call.args if getattr(a, "name", None) is None]
        symbol = positional[0].value if len(positional) > 0 else None
        tf = positional[1].value if len(positional) > 1 else None
        source = positional[2].value if len(positional) > 2 else None

        # arg 0 — the symbol must be `syminfo.tickerid`. Same-symbol HTF only:
        # cross-symbol requests need a second dataset and are out of scope for v1.
        is_ticker_id = (
            symbol is not None
            and symbol.type == "Member"
            and getattr(symbol.object, "type", None) == "Identifier"
            and symbol.object.name == "syminfo"
            and symbol.property == "tickerid"
        )
        if not is_ticker_id:
            self._error("OS2024", (symbol or call).span)

        # arg 1 — a const string that parses, or an input.timeframe/string variable.
        if tf is not None and tf.type == "String":
            if parse_timeframe(tf.value) is None:
                self._error("OS2026", tf.span, tf.value)
        elif not (
            tf is not None and tf.type == "Identifier" and tf.name in self._timeframe_inputs
        ):
            self._error("OS2025", (tf or call).span)

        # arg 2 — a source series (optionally `[n]`), or a `[S1, S2, ...]` array of
        # such for the tuple form. An inner `ta.*` on an HTF series is out of scope,
        # so it must be REJECTED here rather than silently resampled.
        if source is not None and source.type == "ArrayLiteral":
            if len(source.elements) == 0:
                self._error("OS2027", source.span)
            for el in source.elements:
                if not _is_htf_source_expr(el):
                    self._error("OS2027", el.span)
        elif not _is_htf_source_expr(source):
            self._error("OS2027", (source or call).span)

        # optional lookahead — named `lookahead=` or a 4th positional. Only
        # `lookahead_off` is supported; anything else that IS a barmerge member is
        # rejected, which is why the table lists lookahead_on (it must resolve first).
        lookahead = None
        for a in call.args:
            if getattr(a, "name", None) == "lookahead":
                lookahead = a.value
                break
        if lookahead is None and len(positional) > 3:
            lookahead = positional[3].value
        if (
            lookahead is not None
            and lookahead.type == "Member"
            and getattr(lookahead.object, "type", None) == "Identifier"
            and lookahead.object.name == "barmerge"
            and lookahead.property != "lookahead_off"
        ):
            self._error("OS2028", lookahead.span)

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
            accepted = NAMED_ARGS.get(name)
            if accepted is not None:
                self._check_named_args(name, accepted, call)
            if name == "alertcondition":
                self._register_title(call, self._alert_titles, "OS2015")
            if name in ("plotlevel", "plotzone"):
                self._validate_drawing_output(name, call)
            if name in ("plotshape", "plotchar"):
                self._validate_marker_output(call)
            return
        if name in self._functions:
            arity = self._functions[name]
            if len(call.args) != arity:
                self._error("OS2003", call.span, f"got {len(call.args)}, expected {arity}")
            if name == self._current_function:
                self._error("OS2008", call.span, name)
            return
        self._error("OS2002", call.span, name)

    def _validate_marker_output(self, call: ast.CallExpr) -> None:
        """`location.absolute` and `price=` are meaningless apart and must pair.

        The one-directional half is the important one. `location.absolute`
        already lowered to position 'atPrice', and the renderer's atPrice branch
        requires a price -- without one it falls through and draws the glyph at
        the BAR MIDPOINT. So the pre-`price=` behaviour was not a missing feature
        but a silent misplacement, and OS2029 turns it into a compile error.
        """
        loc_arg = self._named_arg_value(call, "location")
        is_absolute = loc_arg is not None and self._enum_member(loc_arg) == "absolute"
        has_price = self._named_arg_value(call, "price") is not None
        if is_absolute and not has_price:
            self._error("OS2029", call.span)
        if has_price and not is_absolute:
            self._error("OS2030", call.span)

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
            # mitigated_color= is a zone-only styling of a PRICE-terminated close.
            term_arg = self._named_arg_value(call, "terminate")
            term_mode = self._enum_member(term_arg) if term_arg is not None else None
            # A zone is mitigated when price TAKES IT OUT, so every directional
            # predicate carries the styling -- `touch`/`straddle` (entered), the
            # `close_*` pair (closed through), and the `cross_*` pair (wicked
            # through). `cross_above` is what an equal-high liquidity sweep
            # actually is (`high > top`); restricting the styling to `touch`
            # forced that shape to spell its sweep as a retest, which fires on
            # the wrong bar.
            #
            # `new_session` is the sole exception, and stays out on purpose: it
            # is a TIME expiry, so the object aged out untouched -- nothing
            # mitigated it.
            price_terminated = term_mode is not None and term_mode != "new_session"
            if fn == "plotlevel" or not price_terminated:
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
        # `offset` is NOT in this list: it accepts a series, sampled at spawn
        # (spawn-sampled-drawing-values design 3.1). The others stay const
        # because they are plan inputs, not per-object geometry -- `bars`/
        # `right_pad` size the object stream the planner admits, `max_kept` caps it.
        # `offset` and `bars` are NOT in this list: both are per-object GEOMETRY
        # and both accept a series sampled at spawn. They travel together -- a
        # projected object is drawn at `left = offset` and `right = offset +
        # width`, so leaving `bars` const while `offset` varies lets x1 overtake
        # x2. `right_pad`/`max_kept` size and cap the object STREAM the planner
        # admits, which has to be knowable before execution.
        for name in ("right_pad", "max_kept"):
            e = self._named_arg_value(call, name)
            if e is not None and self._numeric_arg_value(call, name) is None:
                self._error("OS2023", e.span, f"{name}= must be a compile-time constant")
        for name in ("extend", "terminate"):
            e = self._named_arg_value(call, name)
            if e is not None and self._enum_member(e) is None:
                self._error("OS2023", e.span, f"{name}= must be a constant enum member")
        # G6: `label_visible=` (level) / `text_visible=` (zone) — a bool literal
        # or an `input.bool` variable, and only WITH the text argument it gates.
        # A toggle admitted without its label would be a control bound to
        # nothing — the placebo-control class G8 exists to prevent.
        visible_name = "label_visible" if fn == "plotlevel" else "text_visible"
        text_name = "label" if fn == "plotlevel" else "text"
        visible_arg = self._named_arg_value(call, visible_name)
        if visible_arg is not None:
            is_bool_literal = visible_arg.type == "Bool"
            is_bool_input = (
                visible_arg.type == "Identifier" and visible_arg.name in self._bool_inputs
            )
            if not is_bool_literal and not is_bool_input:
                self._error(
                    "OS2023",
                    visible_arg.span,
                    f"{visible_name}= must be a bool literal or an input.bool variable",
                )
            if self._named_arg_value(call, text_name) is None:
                self._error(
                    "OS2023", visible_arg.span, f"{visible_name}= requires a {text_name}= to gate"
                )

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
        # `[a, b] = request.security(sym, tf, [S1, S2])` — the arity comes from the
        # ARRAY LENGTH, not from a builtin's outputMap, so it is checked before the
        # ta.*-only rule below. A non-array third argument is the single-series form,
        # which destructures to exactly one name.
        if (
            getattr(callee.object, "type", None) == "Identifier"
            and callee.object.name == "request"
            and callee.property == "security"
        ):
            positional = [a for a in value.args if getattr(a, "name", None) is None]
            arr = positional[2].value if len(positional) > 2 else None
            length = len(arr.elements) if arr is not None and arr.type == "ArrayLiteral" else 1
            if length != count:
                self._error("OS2004", span, f"expected {length} names, got {count}")
            return
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
        are, `default` must be one of them (else OS2004). Reuses OS2004
        ('Type mismatch') rather than allocating a new code — both are
        "argument doesn't match the expected shape" cases.

        `default` is resolved via `defval_of` (named-first, else the first
        positional argument), NOT `call.args[0]` — that index is the first
        argument IN CALL ORDER, so a named-first call
        (`input.string(title="Method", defval="MACD", options=[...])`) would
        grab `title`'s value instead and false-positive OS2004 on it (N16).
        """
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
        default_expr = defval_of(call)
        if default_expr is not None and default_expr.type == "String" and default_expr.value not in strings:
            self._error("OS2004", default_expr.span, "input.string default must be one of its declared options")

    def _check_session_defval(self, call: ast.CallExpr) -> None:
        """`input.session(defval, ...)` (session-surface design §4.1): when the
        default is a string literal, it must parse as a well-formed session
        string (OS2031). A non-literal default is left to fall through silently,
        mirroring every other `input.*` constructor's leniency toward a default
        that isn't a recognizable literal (e.g. `input.color`, `input.timeframe`).

        `defval` can be named (`input.session(title="Session", defval="0915-1530")`)
        as well as positional — resolved via `defval_of` (named-first, else the
        first POSITIONAL/unnamed argument), never `call.args[0]` blindly: that
        index is the first argument IN CALL ORDER, so a named-first call would
        grab `title`'s value instead and false-positive OS2031 on it. This is
        the rule N16 generalized to every `input.*` constructor.
        """
        default_expr = defval_of(call)
        if default_expr is None or default_expr.type != "String":
            return
        parsed = parse_session_string(default_expr.value)
        if isinstance(parsed, SessionParseError):
            self._error("OS2031", default_expr.span, parsed.error)

    def _check_session_arg(self, arg: ast.Argument) -> None:
        """`session.*`'s sole argument (session-surface design §4.2): a
        session-string literal (OS2031 on malformed grammar — the same parser
        and message as `_check_session_defval`) or a top-level variable bound to
        `input.session` (`self._session_inputs` — the identical rule
        `request.security`'s timeframe argument already applies via
        `_timeframe_inputs`, reused rather than re-derived). Anything else is
        OS2032: ir_gen specializes `session.*` at compile time on either the
        literal VALUE or the input's id, and a plain string expression (or any
        other kind of value) carries neither.
        """
        e = arg.value
        if e.type == "String":
            parsed = parse_session_string(e.value)
            if isinstance(parsed, SessionParseError):
                self._error("OS2031", e.span, parsed.error)
            return
        if e.type == "Identifier" and e.name in self._session_inputs:
            return
        self._error("OS2032", e.span)

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


def _is_input_timeframe_call(e: ast.Expr) -> bool:
    """`input.timeframe(...)` or `input.string(...)`.

    `input.string` is included because a Pine author commonly builds a timeframe
    dropdown with `input.string(options=[...])` rather than `input.timeframe`; the
    TS side accepts both and the two must agree or a script compiles in one place
    only.
    """
    return (
        e.type == "Call"
        and e.callee.type == "Member"
        and getattr(e.callee.object, "type", None) == "Identifier"
        and e.callee.object.name == "input"
        and e.callee.property in ("timeframe", "string")
    )


def _is_htf_source_expr(e) -> bool:
    """A bare source identifier, optionally with a history offset (`close[1]`).

    Deliberately narrow: only the nine HTF_SOURCE_KINDS qualify, so an inner `ta.*`
    call or any arithmetic is OS2027 rather than being silently resampled.
    """
    if e is None:
        return False
    base = e.object if e.type == "Index" else e
    return getattr(base, "type", None) == "Identifier" and base.name in HTF_SOURCE_KINDS


def _is_input_session_call(e: ast.Expr) -> bool:
    """`input.session(...)` — a session-string-valued input (session-surface design §4.1)."""
    return (
        e.type == "Call"
        and e.callee.type == "Member"
        and getattr(e.callee.object, "type", None) == "Identifier"
        and e.callee.object.name == "input"
        and e.callee.property == "session"
    )


def _is_input_color_call(e: ast.Expr) -> bool:
    return (
        e.type == "Call"
        and e.callee.type == "Member"
        and getattr(e.callee.object, "type", None) == "Identifier"
        and e.callee.object.name == "input"
        and e.callee.property == "color"
    )


def _is_input_bool_call(e: ast.Expr) -> bool:
    """`input.bool(...)` — bindable to `label_visible=`/`text_visible=` (G6)."""
    return (
        e.type == "Call"
        and e.callee.type == "Member"
        and getattr(e.callee.object, "type", None) == "Identifier"
        and e.callee.object.name == "input"
        and e.callee.property == "bool"
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
