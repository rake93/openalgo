"""Is this Flask process running the OpenScript code currently on disk? (M6-A / trap T2)

With ``FLASK_DEBUG=False`` there is no reloader, so editing the Python OpenScript
service does not reach the server until ``app.py`` restarts -- and nothing says
so. The symptom is not an error but results that quietly disagree with the source
you are reading. On 2026-07-30 that made a live check impossible for ~14 hours.

THE MECHANISM IS EXACT, NOT HEURISTIC. Each fingerprint is computed ONCE, at
import, into a module constant. Recomputing from disk on demand and comparing
answers "has the code moved since this process started?" with no inference. That
is the entire idea, and it is why this is small.

WHY THREE SUBTREES RATHER THAN REUSING ``COMPILER_FINGERPRINT``. The obvious
implementation -- and the one the pending register originally specified -- reuses
the fingerprint P2 already ships. But that hashes only ``openscript/``, the
compiler front end. Measured over the 15 most recent commits touching this
service, SIX changed only ``runtime/`` or the service layer and would not have
moved it at all. A freshness check that answers "fresh" for the plurality of real
changes is a silent false negative, and this project holds that such a check is
worse than none (cf. C4: semantic acceptance without lowering was worse than
neither). Reporting per subtree also lets the message name what moved, so the
reader is told "runtime changed" instead of being sent hunting.

``test_every_service_py_file_is_covered_by_exactly_one_subtree`` is the permanent
guard that a new module cannot reintroduce the blind spot.

WHAT THIS DELIBERATELY DOES NOT DO. It does not compare the browser's TypeScript
compiler against the server's Python one -- those are different source trees whose
hashes always differ, so "they differ" would fire every time and mean nothing.
That was the original M6 proposal and it detects nothing; see the corrected entry.
Trap T1 (a stale ``frontend/dist``) is piece B and needs a build stamp, not this.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# name -> (root, recurse). ``service`` is deliberately NON-recursive: it is the
# top-level modules only, because recursing would swallow the other two subtrees
# and one edit would then report as several stale parts.
SUBTREES: dict[str, tuple[Path, bool]] = {
    "compiler": (_ROOT / "openscript", True),
    "runtime": (_ROOT / "runtime", True),
    "service": (_ROOT, False),
}


def files_for(name: str) -> list[Path]:
    """The sources a subtree fingerprints, in a deterministic order.

    Sorted so the digest cannot drift between machines, and ``__pycache__`` is
    excluded because bytecode is derived -- including it would make the
    fingerprint depend on whether the interpreter had written any yet.
    """
    root, recurse = SUBTREES[name]
    it = root.rglob("*.py") if recurse else root.glob("*.py")
    return sorted(p for p in it if "__pycache__" not in p.parts)


def fingerprint_tree(root: Path, *, recursive: bool = True) -> str:
    """sha-256 over a subtree's own sources.

    The relative name is hashed alongside the bytes so a RENAME counts as a
    change; paths are sorted so the order cannot drift between machines.

    This is the algorithm ``compiler_service._compiler_fingerprint`` has always
    used, generalized over the root. It must stay bit-for-bit identical for the
    compiler package: ``compiler_fingerprint`` is persisted in every stored
    version's ``metadata_json`` and P2 reads a mismatch as "recompile this
    indicator", so a change here would silently mark every saved indicator stale.
    """
    digest = hashlib.sha256()
    it = root.rglob("*.py") if recursive else root.glob("*.py")
    for path in sorted(p for p in it if "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


#: Bound ONCE at import. The comparison in `freshness()` is only meaningful
#: because these are never recomputed after the process starts.
IMPORTED: dict[str, str] = {
    name: fingerprint_tree(root, recursive=recurse) for name, (root, recurse) in SUBTREES.items()
}

#: The compiler half, re-exported by `compiler_service` so its long-standing
#: public name and persisted value are unchanged.
COMPILER_FINGERPRINT: str = IMPORTED["compiler"]


def freshness() -> dict:
    """Compare each subtree's import-time fingerprint against disk, right now.

    Returns digests and subtree names only -- never a filesystem path, since this
    is reachable from the browser.
    """
    parts: dict[str, dict] = {}
    for name, (root, recurse) in SUBTREES.items():
        live = fingerprint_tree(root, recursive=recurse)
        imported = IMPORTED[name]
        parts[name] = {"imported": imported, "live": live, "stale": live != imported}

    stale = [name for name, part in parts.items() if part["stale"]]
    return {
        "stale": bool(stale),
        "parts": parts,
        "action": (
            f"restart app.py - {', '.join(sorted(stale))} changed since import" if stale else ""
        ),
    }
