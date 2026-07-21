"""IR admission gate (Python) — mirrors the TS admitIR unit tests and the
shared fixtures/admission corpus (drift guard with the engine)."""

import json
from pathlib import Path

import numpy as np
import pytest

from services.openscript import openscript
from services.openscript.runtime.admit import IRAdmissionError, admit_ir
from services.openscript.runtime.executor import execute_ir


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
    # `drawing-streams` is SUPPORTED as of Phase 1 (Pri 4) — use a feature the
    # runtime genuinely does not know so this still exercises the reject path.
    ir = _valid_ir()
    ir["header"]["requiredFeatures"] = ["nonexistent-feature"]
    assert "IR_UNSUPPORTED_FEATURE" in _codes(ir)


def test_bad_numeric_mode_rejected():
    ir = _valid_ir()
    ir["header"]["numericMode"] = "f32"
    assert "IR_BAD_NUMERIC_MODE" in _codes(ir)


def test_empty_dict_header_reports_both_major_and_numeric():
    ir = _valid_ir()
    ir["header"] = {}
    codes = _codes(ir)
    assert "IR_MAJOR_MISMATCH" in codes
    assert "IR_BAD_NUMERIC_MODE" in codes


def test_aggregates_across_categories():
    ir = _valid_ir()
    ir["header"]["requiredFeatures"] = ["nonexistent-feature"]
    ir["nodes"].append({"id": 1, "op": "frobnicate"})
    codes = _codes(ir)
    assert "IR_UNSUPPORTED_FEATURE" in codes
    assert "IR_UNKNOWN_NODE_OP" in codes


def test_ir_admission_error_carries_errors_and_codes():
    errs = [
        {"code": "IR_UNKNOWN_NODE_OP", "message": "x"},
        {"code": "IR_BAD_NUMERIC_MODE", "message": "y"},
    ]
    e = IRAdmissionError(errs)
    assert isinstance(e, Exception)
    assert e.errors is errs
    assert "IR_UNKNOWN_NODE_OP" in str(e)
    assert "IR_BAD_NUMERIC_MODE" in str(e)


def test_compiler_emits_header():
    result = openscript.compile('indicator("H", overlay=true)\nplot(close, "C")')
    assert result.ir is not None
    assert result.ir["header"] == {
        "major": 1,
        "minor": 0,
        "compilerVersion": "openscript-1.0",
        "requiredFeatures": [],
        "numericMode": "f64-strict",
    }


def test_execute_ir_rejects_unknown_node_op():
    ir = _valid_ir()
    ir["nodes"].append({"id": 1, "op": "frobnicate"})
    n = 8
    dataset = {k: np.arange(n, dtype=float) for k in ("open", "high", "low", "close", "volume", "time")}
    with pytest.raises(IRAdmissionError):
        execute_ir(ir, dataset)


_ADMIT_FIXTURES_DIR = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "admission"
)


def _load_admit_fixtures():
    if not _ADMIT_FIXTURES_DIR.is_dir():
        return []
    params = []
    for p in sorted(_ADMIT_FIXTURES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        params.append(pytest.param(data, id=data["name"]))
    return params


_ADMIT_FIXTURES = _load_admit_fixtures()


@pytest.mark.skipif(not _ADMIT_FIXTURES, reason="shared admission fixtures not found (engine repo not a sibling)")
@pytest.mark.parametrize("fixture", _ADMIT_FIXTURES)
def test_admission_conformance(fixture):
    got = sorted({e["code"] for e in admit_ir(fixture["ir"])})
    want = sorted(set(fixture["expectAdmissionErrors"]))
    assert got == want, f"{fixture['name']}: got {got}, want {want}"
