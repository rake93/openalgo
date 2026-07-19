"""IR admission gate (server, Python) — port of
openalgo-openscript/src/runtime/admit.py [admit.ts].

A class of errors DISTINCT from the OS#### source diagnostics: these describe an
IR the runtime refuses to execute. Run once before execution (top of
execute_ir). No silent degradation — an unknown node/output kind is rejected
here, never treated as a call during evaluation.
"""

from __future__ import annotations

IR_MAJOR = 1
NUMERIC_MODE = "f64-strict"

# Keep in sync with the executor's _eval_node dispatch (executor.py).
_KNOWN_NODE_OPS = frozenset(
    {"source", "const", "input", "binop", "unop", "select", "hist", "nz", "call", "scan"}
)
# Keep in sync with _collect_outputs (executor.py).
_KNOWN_OUTPUT_KINDS = frozenset(
    {
        "plot",
        "hline",
        "fill",
        "plotshape",
        "plotchar",
        "barcolor",
        "bgcolor",
        "plotcandle",
        "alertcondition",
    }
)
# Feature tags this runtime supports. Empty in v1 — grows as phases add IR kinds.
SUPPORTED_FEATURES: frozenset[str] = frozenset()


class IRAdmissionError(Exception):
    """Raised when an IRProgram fails admission. Carries every reason."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("IR admission failed: " + ", ".join(e["code"] for e in errors))


def admit_ir(ir: dict) -> list[dict]:
    """Return every reason the runtime would refuse this IR (empty = admitted)."""
    errors: list[dict] = []
    header = ir.get("header") or {}
    if header.get("major") != IR_MAJOR:
        errors.append(
            {
                "code": "IR_MAJOR_MISMATCH",
                "message": f"IR major {header.get('major')} is not supported by runtime major {IR_MAJOR}",
            }
        )
    if header and header.get("numericMode") != NUMERIC_MODE:
        errors.append(
            {
                "code": "IR_BAD_NUMERIC_MODE",
                "message": f"unsupported numericMode: {header.get('numericMode')}",
                "detail": str(header.get("numericMode")),
            }
        )
    for f in header.get("requiredFeatures", []) or []:
        if f not in SUPPORTED_FEATURES:
            errors.append(
                {
                    "code": "IR_UNSUPPORTED_FEATURE",
                    "message": f"unsupported required feature: {f}",
                    "detail": f,
                }
            )
    for node in ir.get("nodes", []):
        if node.get("op") not in _KNOWN_NODE_OPS:
            errors.append(
                {
                    "code": "IR_UNKNOWN_NODE_OP",
                    "message": f"unknown node op: {node.get('op')}",
                    "detail": str(node.get("op")),
                }
            )
    for o in ir.get("outputs", []):
        if o.get("kind") not in _KNOWN_OUTPUT_KINDS:
            errors.append(
                {
                    "code": "IR_UNKNOWN_OUTPUT_KIND",
                    "message": f"unknown output kind: {o.get('kind')}",
                    "detail": str(o.get("kind")),
                }
            )
    return errors
