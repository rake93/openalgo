"""Phase 1 Pri 4 — object-stream structural diffs + the invalidation seam
(Python port of openalgo-openscript/tests/object-diff.test.ts)."""

from services.openscript.runtime.object_diff import (
    diff_object_streams,
    drawing_invalidation_rule,
    object_stream_invalidated,
)


def _a(bar, time):
    return {"bar": bar, "time": time}


def _lvl(id_, x1, x2, price, open_, label=None):
    it = {"id": id_, "x1": x1, "x2": x2, "price": price, "open": open_}
    if label is not None:
        it["label"] = label
    return it


def _zone(id_, x1, x2, top, bottom, open_, mitigated=False):
    it = {"id": id_, "x1": x1, "x2": x2, "top": top, "bottom": bottom, "open": open_}
    if mitigated:
        it["mitigated"] = True
    return it


def test_new_spawn_is_add():
    a = _lvl("0:1000", _a(0, 1000), _a(9, 1009), 5, True)
    b = _lvl("0:1002", _a(2, 1002), _a(9, 1009), 6, True)
    assert diff_object_streams([a], [a, b]) == [{"op": "add", "id": "0:1002", "item": b}]


def test_terminate_is_update():
    before = _lvl("0:1000", _a(0, 1000), _a(9, 1009), 5, True)
    after = _lvl("0:1000", _a(0, 1000), _a(4, 1004), 5, False)
    assert diff_object_streams([before], [after]) == [{"op": "update", "id": "0:1000", "item": after}]


def test_max_kept_eviction_is_remove():
    a = _lvl("0:1000", _a(0, 1000), _a(9, 1009), 5, True)
    b = _lvl("0:1002", _a(2, 1002), _a(9, 1009), 6, True)
    c = _lvl("0:1004", _a(4, 1004), _a(9, 1009), 7, True)
    assert diff_object_streams([a, b], [b, c]) == [
        {"op": "remove", "id": "0:1000"},
        {"op": "add", "id": "0:1004", "item": c},
    ]


def test_open_edge_ride_is_no_diff():
    before = _lvl("0:1000", _a(0, 1000), _a(8, 1008), 5, True)
    after = _lvl("0:1000", _a(0, 1000), _a(12, 1012), 5, True)
    assert diff_object_streams([before], [after]) == []


def test_pure_rebase_is_stable():
    # bar indices shift, times fixed -> zero churn (identity keyed on time).
    before = _lvl("0:1005", _a(5, 1005), _a(9, 1009), 5, False)
    rebased = _lvl("0:1005", _a(3, 1005), _a(7, 1009), 5, False)
    assert diff_object_streams([before], [rebased]) == []


def test_left_overhang_no_diff():
    # Left edge excluded from the signature (Fable #2): in-dataset vs null overhang.
    in_data = _lvl("0:1005", _a(2, 1002), _a(9, 1009), 5, False)
    overhang = _lvl("0:1005", _a(-1, None), _a(7, 1009), 5, False)
    assert diff_object_streams([in_data], [overhang]) == []


def test_projected_bars_end_stable_while_drifting():
    # A not-yet-reached extend.bars end (null x2.time) must not diff (Fable #1).
    before = _lvl("0:1000", _a(0, 1000), _a(12, None), 5, False)
    after = _lvl("0:1000", _a(0, 1000), _a(14, None), 5, False)
    assert diff_object_streams([before], [after]) == []


def test_projected_bars_end_commits_on_reach():
    projected = _lvl("0:1000", _a(0, 1000), _a(11, None), 5, False)
    committed = _lvl("0:1000", _a(0, 1000), _a(11, 1011), 5, False)
    assert diff_object_streams([projected], [committed]) == [
        {"op": "update", "id": "0:1000", "item": committed}
    ]


def test_zone_mitigation_is_update():
    before = _zone("0:1000", _a(0, 1000), _a(9, 1009), 60, 55, True)
    after = _zone("0:1000", _a(0, 1000), _a(6, 1006), 60, 55, False, mitigated=True)
    assert diff_object_streams([before], [after]) == [{"op": "update", "id": "0:1000", "item": after}]


def test_diffs_sorted_by_id():
    a = _lvl("0:1000", _a(0, 1000), _a(9, 1009), 1, True)
    b = _lvl("0:1002", _a(2, 1002), _a(9, 1009), 2, True)
    c = _lvl("0:1004", _a(4, 1004), _a(9, 1009), 3, True)
    diffs = diff_object_streams([b], [c, a, b])
    assert [d["id"] for d in diffs] == ["0:1000", "0:1004"]
    assert [d["op"] for d in diffs] == ["add", "add"]


_LEVEL_OUT = {
    "kind": "level",
    "condNodeId": 2,
    "priceNodeId": 3,
    "extend": "until",
    "terminate": "close_above",
    "maxKept": 20,
}


def test_invalidation_rule_captures_deps():
    rule = drawing_invalidation_rule(_LEVEL_OUT, 0)
    assert rule == {"outputIndex": 0, "nodeDeps": [2, 3], "readsEdges": True}


def test_invalidation_recomputes_iff_dep_changed():
    rule = drawing_invalidation_rule(_LEVEL_OUT, 0)
    assert object_stream_invalidated(rule, {2}, False) is True
    assert object_stream_invalidated(rule, {3}, False) is True
    assert object_stream_invalidated(rule, {99}, False) is False


def test_until_rule_recomputes_on_edge_change():
    rule = drawing_invalidation_rule(_LEVEL_OUT, 0)
    assert object_stream_invalidated(rule, {99}, True) is True


def test_lastbar_rule_ignores_edges():
    rule = drawing_invalidation_rule({**_LEVEL_OUT, "extend": "lastbar"}, 1)
    assert rule["readsEdges"] is False
    assert object_stream_invalidated(rule, {99}, True) is False


def test_non_drawing_has_no_rule():
    assert drawing_invalidation_rule({"kind": "plot", "nodeId": 0}, 0) is None
