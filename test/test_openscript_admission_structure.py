"""Structural IR admission (Python) — mirror of the engine's
`tests/admission-structure.test.ts`.

The kind checks in `test_openscript_admission.py` cover VOCABULARY: an op or
output kind the runtime does not know. These cover SHAPE and REFERENTIAL
INTEGRITY: an IR whose ops are all known but whose wiring is not executable.

Both runtimes must reject the same malformed IR with the same codes, because
both execute the SAME persisted `compiled_ir` — the browser worker on reopen,
this runtime for headless alert evaluation. A gap on either side is a silent
divergence in what the platform considers a runnable indicator.
"""

import pytest

# `openscript` first: importing `runtime.admit` ahead of it trips the
# ir_gen -> plancost -> builtins_table -> ir_gen import cycle. Same order as
# test_openscript_admission.py.
from services.openscript import openscript  # noqa: F401
from services.openscript.runtime.admit import admit_ir


def _valid_ir() -> dict:
    return {
        "version": 1,
        "compilerVersion": "openscript-1.0",
        "sourceHash": "deadbeef",
        "header": {
            "major": 1,
            "minor": 0,
            "compilerVersion": "openscript-1.0",
            "requiredFeatures": [],
            "numericMode": "f64-strict",
        },
        "declaration": {"name": "T", "overlay": True},
        "inputs": [],
        "nodes": [{"id": 0, "op": "source", "source": "close"}],
        "outputs": [{"kind": "plot", "nodeId": 0, "title": "C", "style": {"color": "#fff"}}],
        "meta": {"warmupBars": 0, "spans": {}},
    }


def _codes(ir) -> set:
    return {e["code"] for e in admit_ir(ir)}


# ── contract version ─────────────────────────────────────────────────────────


def test_version_mismatch_rejected():
    ir = _valid_ir()
    ir["version"] = 2
    assert "IR_VERSION_MISMATCH" in _codes(ir)


def test_missing_version_rejected():
    ir = _valid_ir()
    del ir["version"]
    assert "IR_VERSION_MISMATCH" in _codes(ir)


def test_matching_version_alone_raises_nothing():
    assert admit_ir(_valid_ir()) == []


# ── malformed containers ─────────────────────────────────────────────────────


@pytest.mark.parametrize("field,value", [("nodes", {}), ("outputs", None), ("inputs", "none")])
def test_non_list_container_rejected_without_raising(field, value):
    ir = _valid_ir()
    ir[field] = value
    assert "IR_MALFORMED" in _codes(ir)


# ── malformed nodes ──────────────────────────────────────────────────────────


def test_non_dict_node_rejected():
    ir = _valid_ir()
    ir["nodes"].append(42)
    assert "IR_MALFORMED_NODE" in _codes(ir)


def test_non_integer_node_id_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": "1", "op": "source", "source": "close"})
    assert "IR_MALFORMED_NODE" in _codes(ir)


def test_duplicate_node_id_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 0, "op": "source", "source": "open"})
    assert "IR_DUPLICATE_NODE_ID" in _codes(ir)


def test_node_id_not_matching_position_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 7, "op": "source", "source": "open"})
    assert "IR_MALFORMED_NODE" in _codes(ir)


def test_bool_node_id_is_not_an_integer_id():
    """`True` is an int in Python but not a node id. The TS gate rejects it via
    `typeof v === 'number'`; without an explicit bool guard Python would admit
    it and index nodes[True] == nodes[1]."""
    ir = _valid_ir()
    ir["nodes"].append({"id": True, "op": "source", "source": "open"})
    assert "IR_MALFORMED_NODE" in _codes(ir)


# ── node reference integrity ─────────────────────────────────────────────────


def test_out_of_range_binop_arg_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "binop", "operator": "+", "args": [0, 99]})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_negative_arg_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "binop", "operator": "+", "args": [0, -1]})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_forward_reference_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "hist", "arg": 2, "offset": 1})
    ir["nodes"].append({"id": 2, "op": "source", "source": "open"})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_self_reference_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "unop", "operator": "-", "arg": 1})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_select_checks_all_three_branches():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "select", "cond": 0, "then": 0, "else": 50})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_nz_optional_replacement_checked_when_present():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "nz", "arg": 0, "replacement": 50})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_nz_absent_replacement_is_not_a_bad_reference():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "nz", "arg": 0})
    assert "IR_BAD_NODE_REF" not in _codes(ir)


def test_call_args_checked():
    ir = _valid_ir()
    ir["nodes"].append(
        {"id": 1, "op": "call", "namespace": "ta", "function": "sma", "args": [0, 99]}
    )
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_scan_inputs_checked():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "scan", "init": None, "expr": {"k": "prev"}, "inputs": [99]})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_scan_init_is_a_literal_seed_not_a_node_reference():
    """`scan_seed` reads `init` straight into the recurrence's `prev`. A seed of
    99 on a one-node program is ordinary, not an out-of-range reference."""
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "scan", "init": 99, "expr": {"k": "prev"}, "inputs": [0]})
    assert admit_ir(ir) == []


def test_scan_null_init_is_admitted():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "scan", "init": None, "expr": {"k": "prev"}, "inputs": [0]})
    assert admit_ir(ir) == []


def test_non_numeric_reference_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "unop", "operator": "-", "arg": "0"})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_bool_reference_rejected():
    """Same trap as the node id: `True` must not index node 1."""
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "unop", "operator": "-", "arg": True})
    assert "IR_BAD_NODE_REF" in _codes(ir)


# ── input reference integrity ────────────────────────────────────────────────


def test_undeclared_input_node_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "input", "inputId": "ghost"})
    assert "IR_BAD_INPUT_REF" in _codes(ir)


def test_declared_input_node_admitted():
    ir = _valid_ir()
    ir["inputs"].append(
        {"id": "len", "type": "integer", "label": "Length", "defaultValue": 14}
    )
    ir["nodes"].append({"id": 1, "op": "input", "inputId": "len"})
    assert admit_ir(ir) == []


def test_scan_expr_slot_beyond_node_inputs_rejected():
    ir = _valid_ir()
    ir["nodes"].append(
        {
            "id": 1,
            "op": "scan",
            "init": None,
            "expr": {"k": "bin", "op": "+", "a": {"k": "prev"}, "b": {"k": "input", "i": 3}},
            "inputs": [0],
        }
    )
    assert "IR_BAD_INPUT_REF" in _codes(ir)


def test_scan_expr_slot_within_node_inputs_admitted():
    ir = _valid_ir()
    ir["nodes"].append(
        {
            "id": 1,
            "op": "scan",
            "init": None,
            "expr": {"k": "bin", "op": "+", "a": {"k": "prev"}, "b": {"k": "input", "i": 0}},
            "inputs": [0],
        }
    )
    assert admit_ir(ir) == []


# ── output reference integrity ───────────────────────────────────────────────


def test_plot_pointing_at_missing_node_rejected():
    ir = _valid_ir()
    ir["outputs"].append({"kind": "plot", "nodeId": 99, "title": "X", "style": {"color": "#fff"}})
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_dynamic_color_node_id_checked():
    ir = _valid_ir()
    ir["outputs"].append(
        {"kind": "plot", "nodeId": 0, "title": "X", "style": {"color": "#fff", "colorNodeId": 99}}
    )
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_plotcandle_checks_every_node_reference():
    ir = _valid_ir()
    ir["outputs"].append(
        {
            "kind": "plotcandle",
            "openNodeId": 0,
            "highNodeId": 0,
            "lowNodeId": 0,
            "closeNodeId": 99,
            "title": "X",
            "upColor": "#0f0",
            "downColor": "#f00",
        }
    )
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_fill_index_out_of_range_rejected():
    ir = _valid_ir()
    ir["outputs"].append(
        {"kind": "fill", "topPlotIndex": 0, "bottomPlotIndex": 9, "color": "#fff", "title": "F"}
    )
    assert "IR_BAD_OUTPUT_REF" in _codes(ir)


def test_fill_pointing_at_non_plot_rejected():
    ir = _valid_ir()
    ir["outputs"].append({"kind": "hline", "price": 50, "title": "H", "style": {"color": "#fff"}})
    ir["outputs"].append(
        {"kind": "fill", "topPlotIndex": 0, "bottomPlotIndex": 1, "color": "#fff", "title": "F"}
    )
    assert "IR_BAD_OUTPUT_REF" in _codes(ir)


def test_fill_between_two_plots_admitted():
    ir = _valid_ir()
    ir["outputs"].append({"kind": "plot", "nodeId": 0, "title": "D", "style": {"color": "#fff"}})
    ir["outputs"].append(
        {"kind": "fill", "topPlotIndex": 0, "bottomPlotIndex": 1, "color": "#fff", "title": "F"}
    )
    assert admit_ir(ir) == []


def test_drawing_label_template_argument_checked():
    ir = _valid_ir()
    ir["header"]["requiredFeatures"] = ["drawing-streams"]
    ir["outputs"].append(
        {
            "kind": "level",
            "condNodeId": 0,
            "priceNodeId": 0,
            "title": "L",
            "style": {"color": "#fff"},
            "offset": 0,
            "rightPad": 0,
            "extend": "lastbar",
            "maxKept": 5,
            "label": {"kind": "template", "fmt": "{0}", "args": [99]},
            "labelLatestOnly": False,
        }
    )
    assert "IR_BAD_NODE_REF" in _codes(ir)


def test_color_input_id_naming_undeclared_input_rejected():
    ir = _valid_ir()
    ir["outputs"].append(
        {"kind": "plot", "nodeId": 0, "title": "X", "style": {"color": "#fff", "colorInputId": "ghost"}}
    )
    assert "IR_BAD_INPUT_REF" in _codes(ir)


def test_label_visible_input_id_naming_undeclared_input_rejected():
    """G6: `labelVisibleInputId` is an input binding like `colorInputId`."""
    ir = _valid_ir()
    ir["header"]["requiredFeatures"] = ["drawing-streams"]
    ir["outputs"].append(
        {
            "kind": "level",
            "condNodeId": 0,
            "priceNodeId": 0,
            "title": "L",
            "style": {"color": "#fff"},
            "offset": 0,
            "rightPad": 0,
            "extend": "lastbar",
            "maxKept": 5,
            "label": {"kind": "const", "value": "R1"},
            "labelLatestOnly": False,
            "labelVisibleInputId": "ghost",
        }
    )
    assert "IR_BAD_INPUT_REF" in _codes(ir)


def test_declared_label_visible_input_id_admits():
    ir = _valid_ir()
    ir["header"]["requiredFeatures"] = ["drawing-streams"]
    ir["inputs"].append({"id": "show", "type": "bool", "label": "Show", "defaultValue": True})
    ir["outputs"].append(
        {
            "kind": "level",
            "condNodeId": 0,
            "priceNodeId": 0,
            "title": "L",
            "style": {"color": "#fff"},
            "offset": 0,
            "rightPad": 0,
            "extend": "lastbar",
            "maxKept": 5,
            "label": {"kind": "const", "value": "R1"},
            "labelLatestOnly": False,
            "labelVisibleInputId": "show",
        }
    )
    assert admit_ir(ir) == []


def test_border_color_input_id_naming_undeclared_input_rejected():
    """The check was exact-key (`colorInputId`) and exempted the G8 siblings."""
    ir = _valid_ir()
    ir["header"]["requiredFeatures"] = ["drawing-streams"]
    ir["outputs"].append(
        {
            "kind": "zone",
            "condNodeId": 0,
            "topNodeId": 0,
            "bottomNodeId": 0,
            "title": "Z",
            "style": {"color": "#fff", "borderColor": "#000", "borderColorInputId": "ghost"},
            "offset": 0,
            "rightPad": 0,
            "extend": "lastbar",
            "maxKept": 5,
        }
    )
    assert "IR_BAD_INPUT_REF" in _codes(ir)


def test_violations_are_aggregated():
    ir = _valid_ir()
    ir["version"] = 9
    ir["nodes"].append({"id": 1, "op": "binop", "operator": "+", "args": [0, 99]})
    got = _codes(ir)
    assert "IR_VERSION_MISMATCH" in got
    assert "IR_BAD_NODE_REF" in got


# ── real compiler output still admits ────────────────────────────────────────


def test_every_positive_fixture_compiles_and_admits():
    """The gate must not reject anything the server's own compiler produces.
    Over-rejection here would make saved scripts unopenable — a worse failure
    than the under-rejection the structural codes were added to fix."""
    import json
    from pathlib import Path


    fixtures_dir = (
        Path(__file__).resolve().parents[1].parent
        / "openalgo-openscript"
        / "fixtures"
        / "openscript"
    )
    if not fixtures_dir.is_dir():
        pytest.skip("shared openscript fixtures not found (engine repo not a sibling)")
    checked = 0
    for path in sorted(fixtures_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        result = openscript.compile(data["source"])
        if result.ir is None:
            continue
        assert admit_ir(result.ir) == [], f"{data['name']} was rejected by admission"
        checked += 1
    assert checked > 0, "no positive fixture produced IR"
