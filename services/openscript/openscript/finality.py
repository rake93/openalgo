"""Phase 0.4 — confirmation/finality effect (Python mirror of
openalgo-openscript/src/compiler/finality.ts). Pure, integer lattice, byte-identical."""
from __future__ import annotations

from services.openscript.openscript.semantic import SOURCE_IDS

_RANK = {"historical-final": 0, "confirmed": 1, "provisional": 2}
_BY_RANK = ["historical-final", "confirmed", "provisional"]


def lub(a: str, b: str) -> str:
    return _BY_RANK[max(_RANK[a], _RANK[b])]


_PRICE_SOURCES = frozenset(SOURCE_IDS)


def source_finality(source_id: str) -> str:
    return "confirmed" if source_id in _PRICE_SOURCES else "historical-final"


LOOKAHEAD_OPS = frozenset({"ta.pivothigh", "ta.pivotlow"})
