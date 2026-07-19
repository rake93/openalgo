"""IR admission gate (Python) — mirrors the TS admitIR unit tests and the
shared fixtures/admission corpus (drift guard with the engine)."""

import pytest

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


def test_valid_ir_is_admitted():
    assert admit_ir(_valid_ir()) == []


def test_unknown_node_op_rejected():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "frobnicate"})
    assert "IR_UNKNOWN_NODE_OP" in _codes(ir)


def test_unknown_output_kind_rejected():
    ir = _valid_ir()
    ir["outputs"].append({"kind": "hologram"})
    assert "IR_UNKNOWN_OUTPUT_KIND" in _codes(ir)


def test_major_mismatch_rejected():
    ir = _valid_ir()
    ir["header"]["major"] = 2
    assert "IR_MAJOR_MISMATCH" in _codes(ir)


def test_missing_header_rejected_as_major_mismatch():
    ir = _valid_ir()
    del ir["header"]
    assert "IR_MAJOR_MISMATCH" in _codes(ir)


def test_unsupported_feature_rejected():
    ir = _valid_ir()
    ir["header"]["requiredFeatures"] = ["drawing-streams"]
    assert "IR_UNSUPPORTED_FEATURE" in _codes(ir)


def test_bad_numeric_mode_rejected():
    ir = _valid_ir()
    ir["header"]["numericMode"] = "f32"
    assert "IR_BAD_NUMERIC_MODE" in _codes(ir)
