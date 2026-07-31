"""The engine-bundled standard library -- Python mirror.

Port of ``openalgo-openscript/src/compiler/stdlib.ts``. Design:
``openscript-stdlib-design.md`` in the engine repo.

WHAT THIS IS NOT. It is not a runtime feature. Stdlib functions INLINE into the
caller's DAG exactly as user functions do, so a script using ``fvg.up(0)``
produces the same IR as one that spelled the predicate out. Nothing about the IR,
admission or the executor changes -- which is why this mirror touches only
``builtins_table``/``semantic``/``ir_gen`` and leaves ``admit.py``, ``limits.py``
and ``executor.py`` alone.

WHY THE RULES BELOW RAISE INSTEAD OF DIAGNOSING. A stdlib body is source the
script author cannot see, edit or work around, so a diagnostic pointing into it
would be unactionable. Every malformed-body case is rejected HERE, at import, so
the user-facing surface is only OS2002 (no such function) and OS2003 (wrong
arity) -- both of which already exist in both languages. That is what let this
feature ship with zero new diagnostic codes.

WHY CYCLES CANNOT BE EXPRESSED. Modules register in manifest order and may
reference only symbols already registered, and every stdlib reference must be
QUALIFIED. A forward reference names a symbol not yet seen; a self-reference
names the one being defined. Both fail the build, so there is no cycle to detect.
"""

from __future__ import annotations

from . import ast_nodes as ast
from .builtins_table import MATH_FUNCTIONS, OUTPUT_FUNCTIONS, SPECIAL_FUNCTIONS, TA_FUNCTIONS
from .parser import parse
from .stdlib_src import STDLIB_MODULES

# Series and context identifiers a body may read without declaring them. Mirrors
# the TS AMBIENT set (SOURCE_IDS + BARE_CONTEXT_IDS).
_SOURCE_IDS = frozenset({"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"})
_CONTEXT_IDS = frozenset(
    {"time", "bar_index", "last_bar_index", "dayofweek", "dayofmonth", "month", "year", "hour", "minute", "second"}
)
_AMBIENT = _SOURCE_IDS | _CONTEXT_IDS

# Namespaces a body may call into that are not stdlib modules.
_HOST_NAMESPACES = frozenset({"ta", "math", "kernels"})


class StdlibBuildError(Exception):
    """A shipped module is malformed. An engine defect, never a user's problem."""

    def __init__(self, module: str, message: str) -> None:
        super().__init__(f"stdlib module '{module}': {message}")


def _check_body(
    expr,
    module: str,
    scope: frozenset[str],
    resolved: set[str],
    windowed_symbols: set[str],
) -> bool:
    """Validate one body; return whether it reaches a windowed kernel.

    The windowed flag is not bookkeeping: ``x := bos.up_from(x, 2, 2)`` inlines a
    self-reference into ``ta.crossover``, which is exactly what OS2013 rejects,
    and the consumer's guard cannot see through an opaque call.
    """
    windowed = False

    def fail(msg: str):
        raise StdlibBuildError(module, msg)

    def walk(e) -> None:
        nonlocal windowed
        t = e.type
        if t == "Identifier":
            if e.name not in scope and e.name not in _AMBIENT:
                fail(f"body references '{e.name}', which is neither a parameter nor a series")
            return
        if t == "Member":
            obj = getattr(e.object, "name", None)
            fail(f"body reads '{obj}.{e.property}' as a value; only calls are allowed")
            return
        if t == "Call":
            callee = e.callee
            if callee.type == "Identifier":
                name = callee.name
                if name in OUTPUT_FUNCTIONS:
                    fail(f"body calls the output function '{name}'; outputs are top-level only")
                if name not in SPECIAL_FUNCTIONS:
                    fail(f"body calls '{name}' unqualified; stdlib references must be qualified (ns.fn)")
            elif callee.type == "Member" and getattr(callee.object, "type", None) == "Identifier":
                ns = callee.object.name
                fn = callee.property
                if ns == "input":
                    fail(f"body declares an input ('input.{fn}'); inputs belong to the consumer")
                if ns == "request":
                    fail(f"body calls 'request.{fn}'; request.* is not available to the stdlib")
                if ns not in _HOST_NAMESPACES:
                    if f"{ns}.{fn}" not in resolved:
                        fail(
                            f"body calls '{ns}.{fn}', which is not a stdlib symbol "
                            "registered before this module"
                        )
                    if f"{ns}.{fn}" in windowed_symbols:
                        windowed = True
                elif ns == "ta" and fn not in TA_FUNCTIONS:
                    fail(f"body calls unknown 'ta.{fn}'")
                elif ns == "math" and fn not in MATH_FUNCTIONS:
                    fail(f"body calls unknown 'math.{fn}'")
                # Mirrors the consumer-side rule in semantic._visit_call.
                if ns in ("ta", "kernels") or (ns == "math" and fn == "sum"):
                    windowed = True
            else:
                fail("body contains a call with an unsupported callee form")
            for a in e.args:
                walk(a.value)
            return
        if t == "Index":
            # The 0-anchor rule: a body may look back a LITERAL number of bars,
            # never a computed one, because _lower_hist cannot fold a parameter.
            if e.index.type != "Number":
                fail("history offset must be a literal; stdlib primitives are 0-anchored")
            walk(e.object)
            return
        if t == "Unary":
            walk(e.operand)
            return
        if t == "Binary":
            walk(e.left)
            walk(e.right)
            return
        if t == "Ternary":
            walk(e.cond)
            walk(e.then)
            walk(getattr(e, "else_", None) or e.else_)
            return
        if t == "If":
            fail("block if/else is not allowed in a stdlib body; use a ternary")
            return
        if t == "ArrayLiteral":
            fail("array literals are reserved in v1 and not allowed in a stdlib body")
            return
        # Literals -- nothing to check.

    walk(expr)
    return windowed


def build_stdlib(modules) -> tuple[dict[str, dict[str, ast.FunctionDecl]], set[str]]:
    """Build a registry from module sources, in order. Raises on the first rule
    violation."""
    registry: dict[str, dict[str, ast.FunctionDecl]] = {}
    resolved: set[str] = set()
    windowed: set[str] = set()

    for name, source in modules:
        if name in registry:
            raise StdlibBuildError(name, "declared twice in the manifest")
        program, diagnostics = parse(source)
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            joined = "; ".join(f"{d.code} {d.message}" for d in errors)
            raise StdlibBuildError(name, f"does not parse: {joined}")
        fns: dict[str, ast.FunctionDecl] = {}
        for stmt in program.body:
            if stmt.type != "FunctionDecl":
                raise StdlibBuildError(name, "contains only function declarations at top level")
            if stmt.name in fns:
                raise StdlibBuildError(name, f"declares '{stmt.name}' twice")
            params = frozenset(p.name for p in stmt.params)
            if len(params) != len(stmt.params):
                raise StdlibBuildError(name, f"'{stmt.name}' has duplicate parameter names")
            # Registered AFTER its own body is checked, so a self-reference fails.
            is_windowed = _check_body(stmt.body, name, params, resolved, windowed)
            fns[stmt.name] = stmt
            resolved.add(f"{name}.{stmt.name}")
            if is_windowed:
                windowed.add(f"{name}.{stmt.name}")
        if not fns:
            raise StdlibBuildError(name, "declares no functions")
        registry[name] = fns
    return registry, windowed


STDLIB, _WINDOWED = build_stdlib(STDLIB_MODULES)

STDLIB_NAMESPACES = frozenset(STDLIB.keys())


def stdlib_has(ns: str, fn: str) -> bool:
    return fn in STDLIB.get(ns, {})


def stdlib_arity(ns: str, fn: str):
    """Declared parameter count, or None when the symbol does not exist."""
    decl = STDLIB.get(ns, {}).get(fn)
    return None if decl is None else len(decl.params)


def stdlib_function(ns: str, fn: str):
    """The declaration to inline for ``ns.fn``, or None."""
    return STDLIB.get(ns, {}).get(fn)


def stdlib_is_windowed(ns: str, fn: str) -> bool:
    """Whether calling ``ns.fn`` puts the caller's arguments inside a windowed
    kernel -- true after inlining, so the consumer's OS2013 guard needs it."""
    return f"{ns}.{fn}" in _WINDOWED


def stdlib_symbols() -> list[str]:
    return [f"{ns}.{fn}" for ns, fns in STDLIB.items() for fn in fns]
