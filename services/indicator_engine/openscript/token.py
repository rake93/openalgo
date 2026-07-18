"""OpenScript token model — Python port of the TS token set
(openalgo-indicator-engine/src/compiler/token.ts).

Token type values are the same string literals as the TS side. `RawToken` is
the positional lexeme (including trivia) that both the tokenizer and (on the TS
side) the CodeMirror highlighter share.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import Span

# Words that are part of the v1 grammar (not identifiers).
KEYWORDS: frozenset[str] = frozenset(
    {"if", "else", "and", "or", "not", "na", "true", "false"}
)


@dataclass
class Token:
    type: str
    value: str  # raw lexeme text, except strings which carry the decoded value
    span: Span


@dataclass
class RawToken:
    type: str  # a TokenType or a trivia kind: 'whitespace' | 'comment'
    start: int
    end: int
    error: str | None = None  # diagnostic code when the lexeme is malformed
