"""Structural object-stream diffs + the invalidation rule (design 0.5 §5).

Python mirror of openalgo-openscript/src/runtime/object-diff.ts.

A drawing output materializes to a list of objects keyed by a STABLE id
(``f"{outputIndex}:{spawnBarTimestamp}"`` — timestamp, not bar index, so identity
survives history trim / rebase / window slide). On a confirmed-bar recompute the
engine emits *structural* diffs keyed by that id — ``add`` (new spawn), ``update``
(terminate -> x2 finalized + open->false; mitigate -> recolor; restyle),
``remove`` (max_kept eviction of the oldest) — and NEVER replaces the whole list.

The cosmetic open-edge ride (an OPEN object's right edge riding the live bar) is
renderer-local and produces NO diff: two snapshots of the same open object whose
only difference is ``x2`` compare EQUAL here. That preserves tick-replay ==
full-history — the emitted diff stream is a pure function of confirmed history.

This is the Phase-5 output-diff seam: ``drawing_invalidation_rule`` +
``object_stream_invalidated`` let a caller recompute an object list ONLY when the
spawn-cond / value / terminate-edge series changed in the dirty range. Honored
trivially today; the rule is the hook a later incremental engine consults.
"""

from __future__ import annotations


def _is_zone(item: dict) -> bool:
    return "top" in item


# Sentinel for a GENUINELY ABSENT (missing / None) text/label — kept distinct from
# an empty-string label, which is a PRESENT, meaningful value. Must stay identical
# to the TS mirror (object-diff.ts), where the same token replaced a raw NUL byte
# that had turned that file into a git-binary blob. Chosen not to collide with a
# real drawing label.
_ABSENT_TEXT = "__absent__"


def _text_part(v) -> str:
    """Signature part for an optional text/label: the value when present (INCLUDING
    the empty string), the sentinel only when genuinely absent (None / missing key)."""
    return _ABSENT_TEXT if v is None else v


def _signature(item: dict) -> str:
    """The diff-relevant signature of an item — the COMMITTED lifecycle state only,
    so a pure history rebase (which shifts bar indices but leaves real timestamps
    fixed) produces ZERO diffs (design §5; Fable #1/#2).

    Left edge excluded entirely (fully determined by id + constant offset; its
    time may be a null overhang that drifts). Right-edge time included ONLY when
    CLOSED AND in-dataset (x2.time is not None) — an open/projected edge is 'proj'
    so it never drifts; a not-yet-reached extend.bars end commits (one update) only
    once history covers it. Frozen values/label/mitigation/open always included."""
    open_ = item["open"]
    committed = (not open_) and item["x2"]["time"] is not None
    right_edge = str(item["x2"]["time"]) if committed else "proj"
    parts: list = [item["id"], open_, right_edge]
    if _is_zone(item):
        parts += ["z", item["top"], item["bottom"], item.get("mitigated") is True, _text_part(item.get("text"))]
    else:
        # G-LIVE §5: a tracked price and its label enter the committed signature
        # EXACTLY when the right-edge time does. Until then they are 'live', for
        # the same reason a projected edge is 'proj' -- including them would make
        # every forming tick a structural diff on an open tracked object and
        # break the zero-diff-on-rebase invariant. The live value still reaches
        # the screen through the re-materialized outputs.
        live = item.get("tracks") is True and not committed
        parts += [
            "l",
            "live" if live else item["price"],
            "live" if live else _text_part(item.get("label")),
        ]
    return "|".join(str(p) for p in parts)


def diff_object_streams(prev: list[dict], nxt: list[dict]) -> list[dict]:
    """Structural diff between a previous and a new object list, keyed by stable
    id. ``add`` for ids only in ``nxt``, ``remove`` for ids only in ``prev``,
    ``update`` for ids in both whose committed signature changed. Deterministic:
    diffs sorted by id. Never a full-list replacement."""
    prev_by_id = {it["id"]: it for it in prev}
    next_by_id = {it["id"]: it for it in nxt}

    diffs: list[dict] = []
    for it in nxt:
        before = prev_by_id.get(it["id"])
        if before is None:
            diffs.append({"op": "add", "id": it["id"], "item": it})
        elif _signature(before) != _signature(it):
            diffs.append({"op": "update", "id": it["id"], "item": it})
    for it in prev:
        if it["id"] not in next_by_id:
            diffs.append({"op": "remove", "id": it["id"]})
    diffs.sort(key=lambda d: d["id"])
    return diffs


def drawing_invalidation_rule(o: dict, output_index: int) -> dict | None:
    """Build the invalidation rule for a ``level``/``zone`` output, else None.
    The object list is recomputed when any series it depends on changed in the
    dirty range: spawn ``cond``, the value channels, or — for ``extend:'until'``
    — the OHLC terminate-edge series."""
    kind = o.get("kind")
    if kind == "level":
        return {
            "outputIndex": output_index,
            "nodeDeps": [o["condNodeId"], o["priceNodeId"]],
            "readsEdges": o.get("extend") == "until",
        }
    if kind == "zone":
        return {
            "outputIndex": output_index,
            "nodeDeps": [o["condNodeId"], o["topNodeId"], o["bottomNodeId"]],
            "readsEdges": o.get("extend") == "until",
        }
    return None


def object_stream_invalidated(rule: dict, changed_node_ids: set, edges_changed: bool) -> bool:
    """Whether a drawing output's object list must be recomputed. Honored
    trivially today (a confirmed-bar recompute passes every dep); the seam a
    later incremental engine consults to skip untouched streams."""
    if rule["readsEdges"] and edges_changed:
        return True
    return any(dep in changed_node_ids for dep in rule["nodeDeps"])


def drawing_invalidation_rules(ir: dict) -> list[dict]:
    """All drawing outputs' invalidation rules, in output order."""
    rules: list[dict] = []
    for idx, o in enumerate(ir.get("outputs", [])):
        r = drawing_invalidation_rule(o, idx)
        if r is not None:
            rules.append(r)
    return rules
