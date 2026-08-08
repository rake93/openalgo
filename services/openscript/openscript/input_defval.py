"""`defval` resolution shared by every `input.*` constructor (N16) — Python
port of the TS `input-defval.ts`
(openalgo-openscript/src/compiler/input-defval.ts).

`defval` is an admitted named argument (`INPUT_NAMED_ARGS`), so
`input.int(title="Length", defval=14)` is legal OpenScript. A naive
`call.args[0]` read grabs whatever argument happens to be FIRST IN CALL ORDER,
not the positional `defval` slot — for a named-first call that is `title`.
Before this fix that meant `input.int`/`float`/`bool` silently substituted
0/0/False for the user's declared default (compiling clean, with the wrong
IR) and `input.string`'s options check (`_check_string_options`) false-
positived OS2004 against `title`'s value instead of the real default.

The rule — proven first on `input.session` in isolation (`_check_session_defval`
/ `_lower_input`'s session arm, session-surface design §4.1): `defval=` if
named, else the first UNNAMED (positional) argument. Never `call.args[0]` —
that is the first argument in SOURCE ORDER, which is `title` for a
named-first call. A call that supplies BOTH (`input.int(5, defval=14)`)
resolves to the named value: this mirrors `_arg_expr` (ir_gen.py), which
checks the name before ever looking at position, so named always wins over a
positional value in the same call.

ONE resolver for both compiler passes — semantic (`_check_string_options`,
`_check_session_defval`) and ir_gen (`_lower_input`) — so the rule cannot
drift between the check that validates a default and the lowering that emits
it.
"""

from __future__ import annotations

from . import ast_nodes as ast


def defval_of(call: ast.CallExpr):
    for arg in call.args:
        if arg.name == "defval":
            return arg.value
    for arg in call.args:
        if arg.name is None:
            return arg.value
    return None
