"""OpenScript parser — Python port of the TS parser
(openalgo-openscript/src/compiler/parser.ts).

Recursive-descent for statements, Pratt for expressions, with panic-mode
recovery. Structurally identical to the TS so the same sources yield the same
diagnostic codes (the shared conformance fixtures assert this).
"""

from __future__ import annotations

import math

from ..limits import SCRIPT_LIMITS
from . import ast_nodes as ast
from .diagnostics import Diagnostic, Span, make_diagnostic
from .lexer import tokenize
from .token import Token

RESERVED_KEYWORDS = frozenset({"var", "for", "while"})

COMPARISON_OPS = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "==", "ne": "!="}
ADDITIVE_OPS = {"plus": "+", "minus": "-"}
MULTIPLICATIVE_OPS = {"star": "*", "slash": "/", "percent": "%"}


class _ParseError(Exception):
    """Unwinds to the enclosing statement boundary for panic-mode recovery."""


def _to_number(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return math.nan


class Parser:
    def __init__(self, tokens: list[Token], diagnostics: list[Diagnostic]):
        self._tokens = tokens
        self._diagnostics = diagnostics
        self._pos = 0

    # ── cursor ────────────────────────────────────────────────────────────────

    def _current(self) -> Token:
        return self._tokens[self._pos]

    def _peek(self, offset: int) -> Token:
        i = self._pos + offset
        return self._tokens[i] if 0 <= i < len(self._tokens) else self._tokens[-1]

    def _prev(self) -> Token:
        return self._tokens[self._pos - 1]

    def _check(self, type_: str) -> bool:
        return self._current().type == type_

    def _check_keyword(self, word: str) -> bool:
        t = self._current()
        return t.type == "keyword" and t.value == word

    def _advance(self) -> Token:
        t = self._current()
        if t.type != "eof":
            self._pos += 1
        return t

    def _match(self, type_: str) -> bool:
        if self._check(type_):
            self._advance()
            return True
        return False

    def _expect(self, type_: str, code: str) -> Token:
        if self._check(type_):
            return self._advance()
        self._fail(code, self._current().span)

    def _fail(self, code: str, span: Span, detail: str | None = None):
        self._diagnostics.append(make_diagnostic(code, "error", span, detail))
        raise _ParseError()

    def _span_from(self, start_span: Span, end_tok: Token) -> Span:
        return Span(start_span.start, end_tok.span.end, start_span.line, start_span.column)

    def _span_tok(self, start_tok: Token, end_tok: Token) -> Span:
        return self._span_from(start_tok.span, end_tok)

    # ── recovery / terminators ──────────────────────────────────────────────────

    def _skip_newlines(self) -> None:
        while self._check("newline"):
            self._advance()

    def _recover_to_line_end(self) -> None:
        while not self._check("newline") and not self._check("dedent") and not self._check("eof"):
            self._advance()

    def _consume_terminator(self, boundary: str) -> None:
        if self._match("newline"):
            return
        if self._check(boundary) or self._check("eof") or self._check("dedent"):
            return
        if self._prev().type == "dedent":
            return
        self._diagnostics.append(make_diagnostic("OS1001", "error", self._current().span))
        self._recover_to_line_end()

    # ── program / statements ────────────────────────────────────────────────────

    def parse_program(self) -> ast.Program:
        start_tok = self._current()
        body: list[ast.Stmt] = []
        self._skip_newlines()
        while not self._check("eof"):
            if self._check("indent") or self._check("dedent"):
                self._diagnostics.append(make_diagnostic("OS1001", "error", self._current().span))
                self._advance()
                continue
            before = self._pos
            stmt = self._parse_statement(True)
            if stmt is not None:
                body.append(stmt)
            self._consume_terminator("eof")
            self._skip_newlines()
            if self._pos == before:
                self._advance()
        end = self._prev() if self._pos > 0 else start_tok
        return ast.Program(body=body, span=self._span_tok(start_tok, end))

    def _parse_block(self) -> ast.Block:
        indent = self._expect("indent", "OS1001")
        statements: list[ast.Stmt] = []
        self._skip_newlines()
        while not self._check("dedent") and not self._check("eof"):
            if self._check("indent"):
                self._diagnostics.append(make_diagnostic("OS1001", "error", self._current().span))
                self._advance()
                continue
            before = self._pos
            stmt = self._parse_statement(False)
            if stmt is not None:
                statements.append(stmt)
            self._consume_terminator("dedent")
            self._skip_newlines()
            if self._pos == before:
                self._advance()
        self._expect("dedent", "OS1001")
        return ast.Block(statements=statements, span=self._span_tok(indent, self._prev()))

    def _parse_statement(self, allow_functions: bool):
        try:
            if self._check("identifier") and self._current().value in RESERVED_KEYWORDS:
                self._fail("OS1009", self._current().span, self._current().value)
            if self._check("identifier") and self._peek(1).type == "reassign":
                self._fail("OS1010", self._peek(1).span)
            if self._check("lbracket"):
                return self._parse_bracket_statement()
            if allow_functions and self._check("identifier") and self._is_function_def():
                return self._parse_function_decl()
            if self._check("identifier") and self._peek(1).type == "assign":
                return self._parse_var_decl()
            return self._parse_expr_statement()
        except _ParseError:
            self._recover_to_line_end()
            return None

    def _parse_var_decl(self) -> ast.Stmt:
        name_tok = self._advance()
        self._expect("assign", "OS1001")
        value = self.parse_expression()
        return ast.VarDecl(
            name=name_tok.value,
            name_span=name_tok.span,
            value=value,
            span=self._span_tok(name_tok, self._prev()),
        )

    def _parse_bracket_statement(self) -> ast.Stmt:
        lb = self._advance()  # '['
        names: list[ast.Binding] = []
        if not self._check("rbracket"):
            while True:
                if not self._check("identifier"):
                    self._fail("OS1011", self._span_tok(lb, self._current()))
                t = self._advance()
                names.append(ast.Binding(name=t.value, span=t.span))
                if not self._match("comma"):
                    break
        rb = self._expect("rbracket", "OS1011")
        if not self._match("assign"):
            self._fail("OS1011", self._span_tok(lb, rb))
        value = self.parse_expression()
        return ast.TupleDecl(names=names, value=value, span=self._span_tok(lb, self._prev()))

    def _parse_function_decl(self) -> ast.Stmt:
        name_tok = self._advance()
        self._expect("lparen", "OS1001")
        params: list[ast.Binding] = []
        if not self._check("rparen"):
            while True:
                p = self._expect("identifier", "OS1002")
                params.append(ast.Binding(name=p.value, span=p.span))
                if not self._match("comma"):
                    break
        self._expect("rparen", "OS1003")
        self._expect("arrow", "OS1001")
        if self._check("newline") or self._check("eof") or self._check("dedent"):
            self._fail("OS1008", self._current().span)
        body = self.parse_expression()
        return ast.FunctionDecl(
            name=name_tok.value,
            name_span=name_tok.span,
            params=params,
            body=body,
            span=self._span_tok(name_tok, self._prev()),
        )

    def _parse_expr_statement(self) -> ast.Stmt:
        start = self._current()
        expr = self.parse_expression()
        if self._check("assign"):
            self._fail("OS1007", self._current().span, "assignment target must be a name")
        if self._check("reassign"):
            self._fail("OS1010", self._current().span)
        return ast.ExprStmt(expr=expr, span=self._span_tok(start, self._prev()))

    def _is_function_def(self) -> bool:
        if self._peek(1).type != "lparen":
            return False
        depth = 0
        i = self._pos + 1
        while i < len(self._tokens):
            t = self._tokens[i].type
            if t == "lparen":
                depth += 1
            elif t == "rparen":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            elif t == "eof":
                return False
            i += 1
        nxt = self._tokens[i] if i < len(self._tokens) else None
        return nxt is not None and nxt.type == "arrow"

    # ── expressions (Pratt) ─────────────────────────────────────────────────────

    def parse_expression(self) -> ast.Expr:
        return self._parse_ternary()

    def _parse_ternary(self) -> ast.Expr:
        cond = self._parse_or()
        if self._match("question"):
            then = self.parse_expression()
            self._expect("colon", "OS1001")
            els = self.parse_expression()
            return ast.TernaryExpr(
                cond=cond, then=then, else_=els, span=self._span_from(cond.span, self._prev())
            )
        return cond

    def _parse_or(self) -> ast.Expr:
        left = self._parse_and()
        while self._check_keyword("or"):
            self._advance()
            right = self._parse_and()
            left = ast.BinaryExpr(op="or", left=left, right=right, span=self._span_from(left.span, self._prev()))
        return left

    def _parse_and(self) -> ast.Expr:
        left = self._parse_comparison()
        while self._check_keyword("and"):
            self._advance()
            right = self._parse_comparison()
            left = ast.BinaryExpr(op="and", left=left, right=right, span=self._span_from(left.span, self._prev()))
        return left

    def _parse_comparison(self) -> ast.Expr:
        left = self._parse_additive()
        op = COMPARISON_OPS.get(self._current().type)
        while op:
            self._advance()
            right = self._parse_additive()
            left = ast.BinaryExpr(op=op, left=left, right=right, span=self._span_from(left.span, self._prev()))
            op = COMPARISON_OPS.get(self._current().type)
        return left

    def _parse_additive(self) -> ast.Expr:
        left = self._parse_multiplicative()
        op = ADDITIVE_OPS.get(self._current().type)
        while op:
            self._advance()
            right = self._parse_multiplicative()
            left = ast.BinaryExpr(op=op, left=left, right=right, span=self._span_from(left.span, self._prev()))
            op = ADDITIVE_OPS.get(self._current().type)
        return left

    def _parse_multiplicative(self) -> ast.Expr:
        left = self._parse_unary()
        op = MULTIPLICATIVE_OPS.get(self._current().type)
        while op:
            self._advance()
            right = self._parse_unary()
            left = ast.BinaryExpr(op=op, left=left, right=right, span=self._span_from(left.span, self._prev()))
            op = MULTIPLICATIVE_OPS.get(self._current().type)
        return left

    def _parse_unary(self) -> ast.Expr:
        t = self._current()
        if self._check("minus"):
            self._advance()
            operand = self._parse_unary()
            return ast.UnaryExpr(op="-", operand=operand, span=self._span_tok(t, self._prev()))
        if self._check_keyword("not"):
            self._advance()
            operand = self._parse_unary()
            return ast.UnaryExpr(op="not", operand=operand, span=self._span_tok(t, self._prev()))
        return self._parse_postfix()

    def _parse_postfix(self) -> ast.Expr:
        e = self._parse_primary()
        start = e.span
        while True:
            if self._check("dot"):
                self._advance()
                p = self._expect("identifier", "OS1001")
                e = ast.MemberExpr(
                    object=e, property=p.value, property_span=p.span, span=self._span_from(start, self._prev())
                )
            elif self._check("lparen"):
                self._advance()
                args = self._parse_args()
                self._expect("rparen", "OS1003")
                e = ast.CallExpr(callee=e, args=args, span=self._span_from(start, self._prev()))
            elif self._check("lbracket"):
                self._advance()
                index = self.parse_expression()
                self._expect("rbracket", "OS1003")
                e = ast.IndexExpr(object=e, index=index, span=self._span_from(start, self._prev()))
            else:
                break
        return e

    def _parse_args(self) -> list[ast.Argument]:
        args: list[ast.Argument] = []
        if self._check("rparen"):
            return args
        while True:
            start = self._current()
            name = None
            name_span = None
            if self._check("identifier") and self._peek(1).type == "assign":
                name = self._current().value
                name_span = self._current().span
                self._advance()  # name
                self._advance()  # '='
            value = self.parse_expression()
            args.append(
                ast.Argument(value=value, span=self._span_tok(start, self._prev()), name=name, name_span=name_span)
            )
            if not self._match("comma"):
                break
        return args

    def _parse_primary(self) -> ast.Expr:
        t = self._current()
        kind = t.type
        if kind == "number":
            self._advance()
            return ast.NumberLiteral(value=_to_number(t.value), span=t.span)
        if kind == "string":
            self._advance()
            return ast.StringLiteral(value=t.value, span=t.span)
        if kind == "color":
            self._advance()
            return ast.ColorLiteral(value=t.value, span=t.span)
        if kind == "identifier":
            self._advance()
            return ast.Identifier(name=t.value, span=t.span)
        if kind == "lparen":
            self._advance()
            inner = self.parse_expression()
            self._expect("rparen", "OS1003")
            return inner
        if kind == "lbracket":
            self._fail("OS1011", t.span)
        if kind == "keyword":
            return self._parse_keyword_primary(t)
        self._fail("OS1002", t.span)

    def _parse_keyword_primary(self, t: Token) -> ast.Expr:
        word = t.value
        if word == "true":
            self._advance()
            return ast.BoolLiteral(value=True, span=t.span)
        if word == "false":
            self._advance()
            return ast.BoolLiteral(value=False, span=t.span)
        if word == "na":
            self._advance()
            return ast.NaLiteral(span=t.span)
        if word == "if":
            return self._parse_if_expr()
        self._fail("OS1002", t.span)

    def _parse_if_expr(self) -> ast.Expr:
        if_tok = self._advance()  # 'if'
        cond = self.parse_expression()
        self._expect("newline", "OS1001")
        then_block = self._parse_block()
        else_block = None
        if self._check_keyword("else"):
            self._advance()
            if self._check_keyword("if"):
                nested = self._parse_if_expr()
                else_block = ast.Block(
                    statements=[ast.ExprStmt(expr=nested, span=nested.span)], span=nested.span
                )
            else:
                self._expect("newline", "OS1001")
                else_block = self._parse_block()
        span = self._span_tok(if_tok, self._prev())
        return ast.IfExpr(cond=cond, then=then_block, span=span, else_=else_block)


# ── AST node counting (limits) ───────────────────────────────────────────────


def _count_expr(e: ast.Expr) -> int:
    kind = e.type
    if kind == "Member":
        return 1 + _count_expr(e.object)
    if kind == "Call":
        return 1 + _count_expr(e.callee) + sum(_count_expr(a.value) for a in e.args)
    if kind == "Index":
        return 1 + _count_expr(e.object) + _count_expr(e.index)
    if kind == "Unary":
        return 1 + _count_expr(e.operand)
    if kind == "Binary":
        return 1 + _count_expr(e.left) + _count_expr(e.right)
    if kind == "Ternary":
        return 1 + _count_expr(e.cond) + _count_expr(e.then) + _count_expr(e.else_)
    if kind == "If":
        return 1 + _count_expr(e.cond) + _count_block(e.then) + (_count_block(e.else_) if e.else_ else 0)
    return 1


def _count_stmt(s: ast.Stmt) -> int:
    kind = s.type
    if kind == "VarDecl":
        return 1 + _count_expr(s.value)
    if kind == "TupleDecl":
        return 1 + len(s.names) + _count_expr(s.value)
    if kind == "FunctionDecl":
        return 1 + len(s.params) + _count_expr(s.body)
    return 1 + _count_expr(s.expr)  # ExprStmt


def _count_block(b: ast.Block) -> int:
    return 1 + sum(_count_stmt(s) for s in b.statements)


def _count_ast_nodes(program: ast.Program) -> int:
    return 1 + sum(_count_stmt(s) for s in program.body)


def parse(source: str) -> tuple[ast.Program, list[Diagnostic]]:
    """Compile source to an AST, collecting lexical + syntactic + limit diagnostics."""
    tokens, diagnostics = tokenize(source)

    byte_length = len(source.encode("utf-8"))
    if byte_length > SCRIPT_LIMITS["maximumSourceBytes"]:
        diagnostics.append(
            make_diagnostic("OS3001", "error", Span(0, 0, 1, 1), f"{byte_length} bytes")
        )

    parser = Parser(tokens, diagnostics)
    program = parser.parse_program()

    node_count = _count_ast_nodes(program)
    if node_count > SCRIPT_LIMITS["maximumAstNodes"]:
        diagnostics.append(make_diagnostic("OS3002", "error", program.span, f"{node_count} nodes"))

    return program, diagnostics
