"""OpenScript compiler diagnostics — Python port of the TS diagnostics table
(openalgo-openscript/src/types/diagnostics.ts).

Codes are shared VERBATIM with the TypeScript front end; the conformance
fixtures assert on them, so this table is append-only and must stay identical.
OS0xxx lexical · OS1xxx syntax · OS2xxx semantic/type · OS3xxx limits ·
OS4xxx runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

DIAGNOSTIC_CODES: dict[str, str] = {
    # Lexical
    "OS0001": "Unexpected character",
    "OS0002": "Unterminated string literal",
    "OS0003": "Malformed number literal",
    "OS0004": "Malformed color literal",
    "OS0005": "Inconsistent indentation",
    # Syntax
    "OS1001": "Unexpected token",
    "OS1002": "Expected expression",
    "OS1003": "Expected closing parenthesis",
    "OS1004": "Expression requires warm-up bars",
    "OS1005": "indicator() declaration must be the first statement",
    "OS1006": "Duplicate named argument",
    "OS1007": "Invalid statement",
    "OS1008": "Expected function body expression",
    "OS1009": "Reserved keyword not supported in OpenScript v1",
    "OS1010": "Reassignment (:=) not supported in OpenScript v1",
    "OS1011": "Array and collection literals are not supported in OpenScript v1",
    # Semantic / type
    "OS2001": "Unknown identifier",
    "OS2002": "Unknown function",
    "OS2003": "Wrong number of arguments",
    "OS2004": "Type mismatch",
    "OS2005": "input.* calls are only allowed at top level",
    "OS2006": "Argument must be a compile-time constant",
    "OS2007": "Historical reference requires a series value",
    "OS2008": "Recursive function calls are not supported",
    "OS2009": "Variable redefinition",
    "OS2010": "Unknown named argument",
    "OS2011": "plot/output calls are only allowed at top level",
    "OS2012": "fill() must reference two plot outputs",
    "OS2013": "Unknown source name",
    "OS2014": "Duplicate input id",
    "OS2015": "Duplicate alertcondition id",
    "OS2016": "Invalid stateful declaration or reassignment",
    # Limits
    "OS3001": "Source exceeds maximum size",
    "OS3002": "Program exceeds maximum AST nodes",
    "OS3003": "Program exceeds maximum outputs",
    "OS3004": "Program exceeds maximum inputs",
    "OS3005": "Program exceeds maximum variables",
    "OS3006": "Function nesting exceeds maximum depth",
    "OS3007": "Historical offset exceeds maximum lookback",
    # Runtime
    "OS4001": "Operation budget exceeded",
    "OS4002": "Execution time budget exceeded",
    "OS4003": "Invalid input value",
    "OS4004": "Kernel execution failed",
}


@dataclass
class Span:
    """A source span; offsets are 0-based, line/column are 1-based."""

    start: int
    end: int
    line: int
    column: int

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "line": self.line, "column": self.column}


@dataclass
class Diagnostic:
    code: str
    severity: str  # 'error' | 'warning'
    message: str
    span: Span

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "span": self.span.to_dict(),
        }


def make_diagnostic(code: str, severity: str, span: Span, detail: str | None = None) -> Diagnostic:
    base = DIAGNOSTIC_CODES[code]
    message = f"{base}: {detail}" if detail else base
    return Diagnostic(code=code, severity=severity, message=message, span=span)
