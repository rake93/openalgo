"""Server-side OpenScript compilation.

The server ALWAYS recompiles source and persists its own IR + sha-256; client
-submitted IR is never trusted (architecture doc §8). This wraps the Python
`openscript.compile` port with the storage-facing metadata the scripts CRUD
routes need.
"""

from __future__ import annotations

import hashlib

from .freshness import COMPILER_FINGERPRINT
from .openscript import compile as _compile

COMPILER_VERSION = "openscript-1.0"

# `COMPILER_FINGERPRINT` is a sha-256 over the compiler package's own sources,
# computed ONCE at import. It lives in `freshness` now because that module needs
# the same primitive for `runtime/` and the service layer (M6-A), and two
# definitions of one hash would eventually disagree. Re-exported here so its
# long-standing import path keeps working, and because this is where its MEANING
# belongs: it identifies the exact compiler build that produced a stored IR.
#
# `COMPILER_VERSION` cannot serve that purpose -- it is frozen per LANGUAGE
# revision, so it does not move when a lowering is fixed or extended, which is
# precisely the case that leaves a stored IR stale while still admissible
# (finding P2). The fingerprint is content-hashed rather than hand-maintained
# because a constant someone must remember to bump will not be bumped, and the
# failure is silent: a saved indicator quietly keeps old semantics.
__all__ = ["COMPILER_FINGERPRINT", "COMPILER_VERSION", "compile_source"]


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
