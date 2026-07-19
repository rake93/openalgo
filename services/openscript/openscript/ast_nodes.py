"""OpenScript AST — Python port of the TS AST node shapes
(openalgo-openscript/src/compiler/ast.ts).

Each node exposes a `type` string discriminant (matching the TS) so the semantic
and IR-gen passes can branch identically. Purely syntactic; typing and lowering
happen later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .diagnostics import Span

# ── Expressions ──────────────────────────────────────────────────────────────


@dataclass
class NumberLiteral:
    value: float
    span: Span
    type: ClassVar[str] = "Number"


@dataclass
class StringLiteral:
    value: str
    span: Span
    type: ClassVar[str] = "String"


@dataclass
class ColorLiteral:
    value: str
    span: Span
    type: ClassVar[str] = "Color"


@dataclass
class BoolLiteral:
    value: bool
    span: Span
    type: ClassVar[str] = "Bool"


@dataclass
class NaLiteral:
    span: Span
    type: ClassVar[str] = "Na"


@dataclass
class Identifier:
    name: str
    span: Span
    type: ClassVar[str] = "Identifier"


@dataclass
class MemberExpr:
    object: Expr
    property: str
    property_span: Span
    span: Span
    type: ClassVar[str] = "Member"


@dataclass
class Argument:
    value: Expr
    span: Span
    name: str | None = None
    name_span: Span | None = None


@dataclass
class CallExpr:
    callee: Expr
    args: list[Argument]
    span: Span
    type: ClassVar[str] = "Call"


@dataclass
class IndexExpr:
    object: Expr
    index: Expr
    span: Span
    type: ClassVar[str] = "Index"


@dataclass
class UnaryExpr:
    op: str
    operand: Expr
    span: Span
    type: ClassVar[str] = "Unary"


@dataclass
class BinaryExpr:
    op: str
    left: Expr
    right: Expr
    span: Span
    type: ClassVar[str] = "Binary"


@dataclass
class TernaryExpr:
    cond: Expr
    then: Expr
    else_: Expr
    span: Span
    type: ClassVar[str] = "Ternary"


@dataclass
class IfExpr:
    cond: Expr
    then: Block
    span: Span
    else_: Block | None = None
    type: ClassVar[str] = "If"


@dataclass
class ArrayLiteralExpr:
    """`[expr, expr, ...]` — RESERVED everywhere in v1 EXCEPT as the value of
    the `options=` named argument of `input.string(...)` (the parser only
    ever constructs this node in that one gated position; see parser.py)."""

    elements: list[Expr]
    span: Span
    type: ClassVar[str] = "ArrayLiteral"


Expr = (
    NumberLiteral
    | StringLiteral
    | ColorLiteral
    | BoolLiteral
    | NaLiteral
    | Identifier
    | MemberExpr
    | CallExpr
    | IndexExpr
    | UnaryExpr
    | BinaryExpr
    | TernaryExpr
    | IfExpr
    | ArrayLiteralExpr
)

# ── Statements ───────────────────────────────────────────────────────────────


@dataclass
class Binding:
    name: str
    span: Span


@dataclass
class VarDecl:
    name: str
    name_span: Span
    value: Expr
    span: Span
    is_var: bool = False
    type: ClassVar[str] = "VarDecl"


@dataclass
class Reassign:
    """`name := expr` — per-bar reassignment of a declared var (scan lane)."""

    name: str
    name_span: Span
    value: Expr
    span: Span
    type: ClassVar[str] = "Reassign"


@dataclass
class TupleDecl:
    names: list[Binding]
    value: Expr
    span: Span
    type: ClassVar[str] = "TupleDecl"


@dataclass
class FunctionDecl:
    name: str
    name_span: Span
    params: list[Binding]
    body: Expr
    span: Span
    type: ClassVar[str] = "FunctionDecl"


@dataclass
class ExprStmt:
    expr: Expr
    span: Span
    type: ClassVar[str] = "ExprStmt"


Stmt = VarDecl | Reassign | TupleDecl | FunctionDecl | ExprStmt


@dataclass
class Block:
    statements: list[Stmt]
    span: Span
    type: ClassVar[str] = "Block"


@dataclass
class Program:
    body: list[Stmt]
    span: Span
    type: ClassVar[str] = "Program"
