"""Server-side OpenScript compilation.

The server ALWAYS recompiles source and persists its own IR + sha-256; client
-submitted IR is never trusted (architecture doc §8). This wraps the Python
`openscript.compile` port with the storage-facing metadata the scripts CRUD
routes need.
"""

from __future__ import annotations

import hashlib

from .openscript import compile as _compile

COMPILER_VERSION = "openscript-1.0"


def compile_source(source: str) -> dict:
    """Compile OpenScript source for storage.

    Returns a dict with ``ok`` (no error diagnostics and an IR was produced),
    the ``ir`` (JSON IR or None), serializable ``diagnostics``, the canonical
    ``source_hash`` (sha-256), and ``compiler_version``.
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
    }
