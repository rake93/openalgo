"""IR admission gate (server, Python) — Python port of the TS admit gate
(openalgo-openscript/src/runtime/admit.ts).

A class of errors DISTINCT from the OS#### source diagnostics: these describe an
IR the runtime refuses to execute. Run once before execution (top of
execute_ir). No silent degradation — an unknown node/output kind is rejected
here, never treated as a call during evaluation.

Phase-0.2 (weighted PlanCost budgeting) admission codes — no logic here yet;
these are string codes (unlike the TS `AdmissionErrorCode` union) produced by
the Task 7 PlanCost resolver, which sits beside `admit_ir` (not inside it):
  - IR_OPERATION_BUDGET_EXCEEDED: perBarOperations/totalOperations over cap
  - IR_MEMORY_BUDGET_EXCEEDED: estimatedPeakBytes over maximumExecutionMemoryMb
  - IR_UNPRICED_OPERATOR: an IR op/kind absent from the operator-cost registry
  - IR_DATASET_TOO_LARGE: barCount over maximumHistoryBars
"""

from __future__ import annotations

from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.cost_expr import eval_cost_expr
from services.openscript.runtime.plancost import admission_cost_ctx, estimate_plan_cost

IR_MAJOR = 1
NUMERIC_MODE = "f64-strict"

# Bytes per megabyte — identical 1024*1024 on both runtimes.
_MB = 1024 * 1024

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
        "level",
        "zone",
    }
)
# Feature tags this runtime supports. `drawing-streams` flipped ON in Phase 1
# (Pri 4) — the level/zone materializer (executor._materialize_drawing) now
# executes, so a well-formed drawing IR ADMITS + materializes. Mirror of the TS
# SUPPORTED_FEATURES.
SUPPORTED_FEATURES: frozenset[str] = frozenset({"drawing-streams"})

# Output kinds gated behind an IR feature — mirror of the TS GATED_OUTPUT_FEATURE.
_GATED_OUTPUT_FEATURE = {"level": "drawing-streams", "zone": "drawing-streams"}


class IRAdmissionError(Exception):
    """Raised when an IRProgram fails admission. Carries every reason."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("IR admission failed: " + ", ".join(e["code"] for e in errors))


def admit_ir(ir: dict) -> list[dict]:
    """Return every reason the runtime would refuse this IR (empty = admitted)."""
    errors: list[dict] = []
    # Match the TS gate exactly: distinguish an ABSENT header from a PRESENT-but-empty
    # one. In TS `h = ir.header`, `{}` is truthy (numericMode still checked) while
    # `undefined` is falsy (skipped). A non-dict header is treated as absent (rejected).
    header = ir.get("header")
    if not isinstance(header, dict):
        header = None
    major = header.get("major") if header is not None else None
    if header is None or major != IR_MAJOR:
        major_display = major if major is not None else "(none)"
        errors.append(
            {
                "code": "IR_MAJOR_MISMATCH",
                "message": f"IR major {major_display} is not supported by runtime major {IR_MAJOR}",
                "detail": "none" if major is None else str(major),
            }
        )
    if header is not None and header.get("numericMode") != NUMERIC_MODE:
        errors.append(
            {
                "code": "IR_BAD_NUMERIC_MODE",
                "message": f"unsupported numericMode: {header.get('numericMode')}",
                "detail": str(header.get("numericMode")),
            }
        )
    # Hardening (design §13 #1): normalize a non-list requiredFeatures to []
    # BEFORE iterating — a bare string would otherwise iterate char-by-char and
    # unhashable elements would raise TypeError. Matches the TS `Array.isArray`
    # guard, so both runtimes treat a malformed header identically.
    raw_features = (header or {}).get("requiredFeatures")
    required_features = raw_features if isinstance(raw_features, list) else []
    for f in required_features:
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
    declared_features = set(required_features)
    for o in ir.get("outputs", []):
        feat = _GATED_OUTPUT_FEATURE.get(o.get("kind"))
        if feat and feat not in declared_features:
            errors.append(
                {
                    "code": "IR_FEATURE_NOT_DECLARED",
                    "message": f"output kind '{o.get('kind')}' requires feature '{feat}' to be declared",
                    "detail": o.get("kind"),
                }
            )
    return errors


def resolve_plan_cost(ir: dict, bar_count: int, limits=SCRIPT_LIMITS, mode: str = "observe") -> dict:
    """Recompute the plan cost from the IR and return every reason enforcement
    would refuse it — Python mirror of the TS `resolvePlanCost`
    (openalgo-openscript/src/runtime/admit-plancost.ts).

    Sits BESIDE the structural `admit_ir` gate, never inside it. The load-bearing
    security property: the decision reads ONLY the recompute — ir["meta"]["planCost"]
    is NEVER an input to any admission decision (a forged tiny meta on an expensive
    IR is still rejected; a forged huge meta on a cheap IR is still admitted). In
    "observe", "errors" is always empty and the would-be verdict is in "observed";
    in "enforce", "errors" == "observed".
    """
    observed: list[dict] = []
    ctx = admission_cost_ctx(ir, bar_count, limits)
    recomputed: dict | None = None
    # Worst-case drawing-materialization ops (design §7): kept OUT of
    # totalOperations (node-only field) but ENFORCED here in Phase 1 — the runtime
    # charges these per object-bar into the SAME budget, so the cap must account
    # for totalOperations + objectLifecycleChecks. Mirror of the TS resolver.
    drawing_ops = 0

    # barCount is independent of pricing — a too-large dataset is rejected even
    # when the IR is otherwise unpriceable.
    if bar_count > limits["maximumHistoryBars"]:
        observed.append(
            {
                "code": "IR_DATASET_TOO_LARGE",
                "message": f"barCount {bar_count} exceeds maximumHistoryBars {limits['maximumHistoryBars']}",
                "detail": str(bar_count),
            }
        )

    # RECOMPUTE authoritatively from the IR nodes. An unpriced operator (or any
    # pricing failure) MUST NOT escape — collapse it into one IR_UNPRICED_OPERATOR.
    try:
        cost = estimate_plan_cost(ir)
        recomputed = {
            "totalOperations": eval_cost_expr(cost["totalOperations"], ctx),
            "perBarOperations": eval_cost_expr(cost["perBarOperations"], ctx),
            "estimatedPeakBytes": eval_cost_expr(cost["estimatedPeakBytes"], ctx),
        }
        dim = cost["dims"]["objectLifecycleChecks"]
        drawing_ops = 0 if dim == "n/a" else eval_cost_expr(dim, ctx)
    except Exception as err:  # never let a pricing failure escape admission
        observed.append({"code": "IR_UNPRICED_OPERATOR", "message": str(err)})

    if recomputed is not None:
        if recomputed["perBarOperations"] > limits["maximumOperationsPerBar"]:
            observed.append(
                {
                    "code": "IR_OPERATION_BUDGET_EXCEEDED",
                    "message": (
                        f"perBarOperations {recomputed['perBarOperations']} exceeds "
                        f"maximumOperationsPerBar {limits['maximumOperationsPerBar']}"
                    ),
                    "detail": f"perBar={recomputed['perBarOperations']}",
                }
            )
        # Node ops + worst-case drawing object-bar ops vs the total cap. For a
        # non-drawing IR drawing_ops is 0, so this is byte-identical to the prior
        # node-only check (message + detail included).
        total_with_drawings = recomputed["totalOperations"] + drawing_ops
        if total_with_drawings > limits["maximumTotalOperations"]:
            observed.append(
                {
                    "code": "IR_OPERATION_BUDGET_EXCEEDED",
                    "message": (
                        f"totalOperations {total_with_drawings} exceeds "
                        f"maximumTotalOperations {limits['maximumTotalOperations']}"
                    ),
                    "detail": f"total={total_with_drawings}",
                }
            )
        if recomputed["estimatedPeakBytes"] > limits["maximumExecutionMemoryMb"] * _MB:
            observed.append(
                {
                    "code": "IR_MEMORY_BUDGET_EXCEEDED",
                    "message": (
                        f"estimatedPeakBytes {recomputed['estimatedPeakBytes']} exceeds "
                        f"{limits['maximumExecutionMemoryMb']}MB"
                    ),
                    "detail": f"bytes={recomputed['estimatedPeakBytes']}",
                }
            )

    return {
        "errors": observed if mode == "enforce" else [],
        "observed": observed,
        "recomputed": recomputed,
        "embeddedMismatch": _embedded_mismatch(ir, ctx, recomputed),
        "mode": mode,
    }


def _embedded_mismatch(ir: dict, ctx, recomputed: dict | None) -> bool:
    """Telemetry-only: does the embedded meta.planCost disagree with the recompute?
    Evaluated under the SAME admission ctx. A malformed/unpriceable embedded cost,
    or one present when the IR itself is unpriceable, counts as a mismatch. NEVER
    gates admission — a tamper/drift signal for shadow calibration only."""
    embedded = (ir.get("meta") or {}).get("planCost")
    if not embedded:
        return False
    if recomputed is None:
        return True
    try:
        return (
            eval_cost_expr(embedded["totalOperations"], ctx) != recomputed["totalOperations"]
            or eval_cost_expr(embedded["perBarOperations"], ctx) != recomputed["perBarOperations"]
            or eval_cost_expr(embedded["estimatedPeakBytes"], ctx) != recomputed["estimatedPeakBytes"]
        )
    except Exception:  # malformed/forged embedded cost ⇒ cannot match ⇒ mismatch
        return True
