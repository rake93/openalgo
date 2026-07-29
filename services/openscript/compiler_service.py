"""Server-side OpenScript compilation.

The server ALWAYS recompiles source and persists its own IR + sha-256; client
-submitted IR is never trusted (architecture doc §8). This wraps the Python
`openscript.compile` port with the storage-facing metadata the scripts CRUD
routes need.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .openscript import compile as _compile

COMPILER_VERSION = "openscript-1.0"


def _compiler_fingerprint() -> str:
    """sha-256 over the compiler package's own sources.

    `COMPILER_VERSION` is frozen per LANGUAGE revision, so it does not move when
    a lowering is fixed or extended -- which is precisely the case that leaves a
    stored IR stale while still admissible (finding P2). This does move, because
    it is derived from the code that produces the IR.

    Content-hashed rather than hand-maintained on purpose: a constant someone has
    to remember to bump is a constant that will not be bumped, and the failure is
    silent (a saved indicator quietly keeps old semantics). Paths are sorted and
    the relative name is hashed alongside the bytes, so a rename counts as a
    change and the order cannot drift between machines.

    Computed ONCE at import. `__pycache__` is excluded -- it is derived, and
    including it would make the fingerprint depend on whether the interpreter had
    written bytecode yet, i.e. move for reasons unrelated to the compiler.
    """
    package = Path(__file__).parent / "openscript"
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(package).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


COMPILER_FINGERPRINT = _compiler_fingerprint()


def compile_source(source: str) -> dict:
    """Compile OpenScript source for storage.

    Returns a dict with ``ok`` (no error diagnostics and an IR was produced),
    the ``ir`` (JSON IR or None), serializable ``diagnostics``, the canonical
    ``source_hash`` (sha-256), ``compiler_version``, and the
    ``compiler_fingerprint`` that identifies the exact compiler build.
    """
    result = _compile(source)
    diagnostics = [d.to_dict() for d in result.diagnostics]
    has_error = any(d["severity"] == "error" for d in diagnostics)
    return {
        "ok": result.ir is not None and not has_error,
        "ir": result.ir,
        "diagnostics": diagnostics,
        "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "compiler_version": COMPILER_VERSION,
        "compiler_fingerprint": COMPILER_FINGERPRINT,
    }
