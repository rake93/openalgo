"""OpenScript lexer — Python port of the TS lexer
(openalgo-indicator-engine/src/compiler/lexer.ts).

Two layers: `scan_raw_token` classifies one lexeme (positional, value-free);
`tokenize` drives it across the source, decoding string values, tracking
line/column, applying newline significance, and synthesizing INDENT/DEDENT.
Kept structurally identical to the TS so both produce the same diagnostics.
"""

from __future__ import annotations

from .diagnostics import Diagnostic, Span, make_diagnostic
from .token import KEYWORDS, RawToken, Token

VALID_COLOR_LENGTHS = frozenset({3, 4, 6, 8})

TWO_CHAR = {
    "<=": "le",
    ">=": "ge",
    "==": "eq",
    "!=": "ne",
    "=>": "arrow",
    ":=": "reassign",
}
ONE_CHAR = {
    "+": "plus",
    "-": "minus",
    "*": "star",
    "/": "slash",
    "%": "percent",
    "<": "lt",
    ">": "gt",
    "=": "assign",
    "(": "lparen",
    ")": "rparen",
    "[": "lbracket",
    "]": "rbracket",
    ",": "comma",
    ".": "dot",
    "?": "question",
    ":": "colon",
}
DEPTH_OPEN = frozenset({"lparen", "lbracket"})
DEPTH_CLOSE = frozenset({"rparen", "rbracket"})
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}


def _at(s: str, i: int) -> str:
    """s[i] or '' when out of range (mirrors JS undefined char comparisons)."""
    return s[i] if 0 <= i < len(s) else ""


def _is_digit(c: str) -> bool:
    return "0" <= c <= "9"


def _is_hex(c: str) -> bool:
    return _is_digit(c) or ("a" <= c <= "f") or ("A" <= c <= "F")


def _is_ident_start(c: str) -> bool:
    return c == "_" or ("a" <= c <= "z") or ("A" <= c <= "Z")


def _is_ident_part(c: str) -> bool:
    return _is_ident_start(c) or _is_digit(c)


def scan_raw_token(source: str, pos: int) -> RawToken:
    """Scan one lexeme at `pos` (< len(source)); always advances."""
    c = source[pos]

    if c in " \t\r":
        i = pos + 1
        while i < len(source) and source[i] in " \t\r":
            i += 1
        return RawToken("whitespace", pos, i)

    if c == "\n":
        return RawToken("newline", pos, pos + 1)

    if c == "/" and _at(source, pos + 1) == "/":
        i = pos + 2
        while i < len(source) and source[i] != "\n":
            i += 1
        return RawToken("comment", pos, i)

    if _is_digit(c):
        return _scan_number(source, pos)

    if c == "#":
        return _scan_color(source, pos)

    if c in ('"', "'"):
        return _scan_string(source, pos, c)

    if _is_ident_start(c):
        i = pos + 1
        while i < len(source) and _is_ident_part(source[i]):
            i += 1
        text = source[pos:i]
        return RawToken("keyword" if text in KEYWORDS else "identifier", pos, i)

    return _scan_operator(source, pos)


def _scan_number(source: str, pos: int) -> RawToken:
    n = len(source)
    i = pos
    while i < n and _is_digit(source[i]):
        i += 1
    if _at(source, i) == "." and _is_digit(_at(source, i + 1)):
        i += 1
        while i < n and _is_digit(source[i]):
            i += 1
    if _at(source, i) in ("e", "E"):
        j = i + 1
        if _at(source, j) in ("+", "-"):
            j += 1
        if _is_digit(_at(source, j)):
            i = j + 1
            while i < n and _is_digit(source[i]):
                i += 1
    if i < n and _is_ident_start(source[i]):
        while i < n and _is_ident_part(source[i]):
            i += 1
        return RawToken("number", pos, i, "OS0003")
    return RawToken("number", pos, i)


def _scan_color(source: str, pos: int) -> RawToken:
    n = len(source)
    i = pos + 1
    while i < n and _is_ident_part(source[i]):
        i += 1
    body = source[pos + 1 : i]
    well_formed = len(body) in VALID_COLOR_LENGTHS and all(_is_hex(ch) for ch in body)
    return RawToken("color", pos, i) if well_formed else RawToken("color", pos, i, "OS0004")


def _scan_string(source: str, pos: int, quote: str) -> RawToken:
    n = len(source)
    i = pos + 1
    while i < n:
        ch = source[i]
        if ch == "\n":
            break
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == quote:
            return RawToken("string", pos, i + 1)
        i += 1
    return RawToken("string", pos, i, "OS0002")


def _scan_operator(source: str, pos: int) -> RawToken:
    two = source[pos : pos + 2]
    if two in TWO_CHAR:
        return RawToken(TWO_CHAR[two], pos, pos + 2)
    one = source[pos]
    if one in ONE_CHAR:
        return RawToken(ONE_CHAR[one], pos, pos + 1)
    return RawToken("identifier", pos, pos + 1, "OS0001")


def _decode_string(raw: str) -> str:
    quote = raw[0]
    body = raw[1:-1] if raw.endswith(quote) and len(raw) >= 2 else raw[1:]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(ESCAPES.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tokenize(source: str) -> tuple[list[Token], list[Diagnostic]]:
    """Tokenize the source into significant tokens plus lexical diagnostics."""
    tokens: list[Token] = []
    diagnostics: list[Diagnostic] = []
    indent_stack = [0]
    n = len(source)
    pos = 0
    line = 1
    line_start = 0
    depth = 0
    at_line_start = True
    line_has_tokens = False

    def span_at(start: int, end: int) -> Span:
        return Span(start, end, line, start - line_start + 1)

    def emit(type_: str, value: str, start: int, end: int) -> None:
        tokens.append(Token(type_, value, span_at(start, end)))

    while pos < n:
        # Reconcile indentation at the start of a logical line (depth 0 only).
        if at_line_start and depth == 0:
            i = pos
            while i < n and source[i] in " \t":
                i += 1
            nxt = _at(source, i)
            is_comment = nxt == "/" and _at(source, i + 1) == "/"
            if i >= n or nxt == "\n" or is_comment:
                j = i
                if is_comment:
                    while j < n and source[j] != "\n":
                        j += 1
                if j < n and source[j] == "\n":
                    pos = j + 1
                    line += 1
                    line_start = pos
                else:
                    pos = n
                continue
            width = i - pos
            if width > indent_stack[-1]:
                indent_stack.append(width)
                emit("indent", "", pos, i)
            elif width < indent_stack[-1]:
                while width < indent_stack[-1]:
                    indent_stack.pop()
                    emit("dedent", "", i, i)
                if width > indent_stack[-1]:
                    diagnostics.append(make_diagnostic("OS0005", "error", span_at(pos, i)))
                    indent_stack.append(width)
            pos = i
            at_line_start = False
            line_has_tokens = False
            continue

        raw = scan_raw_token(source, pos)
        type_, start, end, error = raw.type, raw.start, raw.end, raw.error

        if error:
            diagnostics.append(make_diagnostic(error, "error", span_at(start, end)))

        if type_ in ("whitespace", "comment"):
            pos = end
            continue

        if type_ == "newline":
            if depth == 0:
                if line_has_tokens:
                    emit("newline", "\n", start, end)
                at_line_start = True
                line_has_tokens = False
            line += 1
            line_start = end
            pos = end
            continue

        if error == "OS0001":
            pos = end
            continue

        if type_ in DEPTH_OPEN:
            depth += 1
        elif type_ in DEPTH_CLOSE and depth > 0:
            depth -= 1

        text = source[start:end]
        value = _decode_string(text) if type_ == "string" else text
        emit(type_, value, start, end)
        line_has_tokens = True
        pos = end

    while len(indent_stack) > 1:
        indent_stack.pop()
        emit("dedent", "", pos, pos)
    emit("eof", "", pos, pos)
    return tokens, diagnostics
