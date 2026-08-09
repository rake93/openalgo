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
from services.openscript.runtime.session_string import SESSION_DAY_FIELDS

IR_MAJOR = 1
IR_VERSION = 1
NUMERIC_MODE = "f64-strict"

# Node fields holding a reference to ANOTHER node, per op. A referenced id must
# be an integer strictly below the referring node's own id: `nodes` is
# topologically ordered and ids are array positions, so a forward or self
# reference reads a slot the executor has not filled yet. Mirror of the TS
# NODE_REF_FIELDS.
_NODE_REF_FIELDS = {
    "binop": ("args",),
    "unop": ("arg",),
    "select": ("cond", "then", "else"),
    "hist": ("arg",),
    "nz": ("arg", "replacement"),
    "call": ("args",),
    # `scan.init` is NOT listed: the executor seeds the recurrence with it as a
    # literal number (`prev = init`), not as a node id.
    "scan": ("inputs",),
}


def _is_node_index(v, exclusive_max: int) -> bool:
    """A node reference is valid only as an in-range integer array position.

    `bool` is excluded explicitly: `True` is an `int` in Python, so without this
    guard a forged `arg: true` would index node 1 here while the TS gate
    (`typeof v === 'number'`) rejects it — a cross-language divergence in what
    counts as a runnable IR.
    """
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < exclusive_max


def _scan_expr_input_slots(expr, out: list) -> None:
    """Collect `{k:'input', i}` slots from a ScanExpr. These index the SCAN
    NODE's own `inputs` array, not the program's `inputs`."""
    if not isinstance(expr, dict):
        return
    if expr.get("k") == "input":
        out.append(expr.get("i"))
        return
    for v in expr.values():
        if isinstance(v, list):
            for e in v:
                _scan_expr_input_slots(e, out)
        elif isinstance(v, dict):
            _scan_expr_input_slots(v, out)


def _declared_input_ids(ir: dict) -> set:
    return {
        str(d.get("id")) for d in ir.get("inputs", []) if isinstance(d, dict) and d.get("id") is not None
    }


# The nine session-input facets an `input` node's `field` may name (design
# §5.2). The seven day fields are SPREAD FROM `SESSION_DAY_FIELDS` — the same
# tuple the executor's field resolution reads — so that family is exhaustive by
# construction: a day field added there arrives here automatically.
# `open`/`close` are the two fixed clock facets. Mirror of the TS
# SESSION_FIELD_LIST in admit.ts.
_SESSION_FIELDS = frozenset(("open", "close", *SESSION_DAY_FIELDS))


def _admit_node_structure(ir: dict, errors: list[dict]) -> None:
    """Node shape: each entry must be a dict whose `id` is its own array
    position. The executor indexes its values by position, so an id that
    disagrees with the position does not fail — it silently evaluates the wrong
    node."""
    seen: set[int] = set()
    for i, node in enumerate(ir["nodes"]):
        if not isinstance(node, dict):
            errors.append(
                {
                    "code": "IR_MALFORMED_NODE",
                    "message": f"node at position {i} is not an object",
                    "detail": str(i),
                }
            )
            continue
        node_id = node.get("id")
        if not isinstance(node_id, int) or isinstance(node_id, bool):
            errors.append(
                {
                    "code": "IR_MALFORMED_NODE",
                    "message": f"node at position {i} has a non-integer id: {node_id}",
                    "detail": str(i),
                }
            )
            continue
        if node_id in seen:
            errors.append(
                {
                    "code": "IR_DUPLICATE_NODE_ID",
                    "message": f"duplicate node id: {node_id}",
                    "detail": str(node_id),
                }
            )
            continue
        seen.add(node_id)
        if node_id != i:
            errors.append(
                {
                    "code": "IR_MALFORMED_NODE",
                    "message": f"node id {node_id} is not its array position {i}",
                    "detail": str(i),
                }
            )


def _admit_node_references(ir: dict, errors: list[dict]) -> None:
    """Node-to-node and node-to-input wiring."""
    declared_inputs = _declared_input_ids(ir)
    # Input id -> declared type, for the session `field` gate below.
    decl_type_by_id = {
        str(d.get("id")): str(d.get("type")) for d in ir.get("inputs", []) if isinstance(d, dict)
    }

    def bad_ref(node_id, field, value):
        errors.append(
            {
                "code": "IR_BAD_NODE_REF",
                "message": (
                    f"node {node_id} field '{field}' references {value}, "
                    "which is not an evaluable earlier node"
                ),
                "detail": str(value),
            }
        )

    for node in ir["nodes"]:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        # Bound is the node's OWN id, not len(nodes): forward and self
        # references read an unfilled slot.
        bound = node_id if isinstance(node_id, int) and not isinstance(node_id, bool) else 0
        for field in _NODE_REF_FIELDS.get(node.get("op"), ()):
            value = node.get(field)
            # Optional/absent: `nz.replacement` may be omitted, `scan.init` may be na.
            if value is None:
                continue
            if isinstance(value, list):
                for v in value:
                    if not _is_node_index(v, bound):
                        bad_ref(node_id, field, v)
            elif not _is_node_index(value, bound):
                bad_ref(node_id, field, value)
        if node.get("op") == "input":
            if str(node.get("inputId")) not in declared_inputs:
                errors.append(
                    {
                        "code": "IR_BAD_INPUT_REF",
                        "message": f"node {node_id} reads undeclared input '{node.get('inputId')}'",
                        "detail": str(node.get("inputId")),
                    }
                )
            elif "field" in node:
                # Session `field` gate (design §5.2): a `field` is only
                # meaningful on a session-typed input, and only the nine names
                # exist. The compiler never emits anything else — this is the
                # §13 hand-forged-IR discipline. A presence check, not a
                # None-check: hand-forged IR carrying an explicit `"field":
                # null` must still enter this branch and be rejected, not slip
                # through as if `field` were absent. Only the WIRING is
                # checked here; the bound string's VALUE is — like every input
                # default this file leaves unvalidated — a runtime concern, and
                # an unparseable one fails loudly there as OS4005. Mirror of the
                # TS admit.ts gate.
                if decl_type_by_id.get(str(node.get("inputId"))) != "session":
                    errors.append(
                        {
                            "code": "IR_BAD_INPUT_REF",
                            "message": (
                                f"node {node_id} field '{node.get('field')}' binds "
                                f"non-session input '{node.get('inputId')}'"
                            ),
                            "detail": str(node.get("inputId")),
                        }
                    )
                elif str(node.get("field")) not in _SESSION_FIELDS:
                    errors.append(
                        {
                            "code": "IR_BAD_INPUT_REF",
                            "message": (
                                f"node {node_id} has unknown session field '{node.get('field')}'"
                            ),
                            "detail": str(node.get("field")),
                        }
                    )
        if (
            node.get("op") == "htf"
            and node.get("timeframeInputId") is not None
            and str(node.get("timeframeInputId")) not in declared_inputs
        ):
            errors.append(
                {
                    "code": "IR_BAD_INPUT_REF",
                    "message": (
                        f"node {node_id} reads undeclared timeframe input "
                        f"'{node.get('timeframeInputId')}'"
                    ),
                    "detail": str(node.get("timeframeInputId")),
                }
            )
        if node.get("op") == "scan":
            node_inputs = node.get("inputs")
            arity = len(node_inputs) if isinstance(node_inputs, list) else 0
            slots: list = []
            _scan_expr_input_slots(node.get("expr"), slots)
            for slot in slots:
                if not _is_node_index(slot, arity):
                    errors.append(
                        {
                            "code": "IR_BAD_INPUT_REF",
                            "message": (
                                f"scan node {node_id} expr reads slot {slot}, "
                                f"outside its {arity} inputs"
                            ),
                            "detail": str(slot),
                        }
                    )


def _admit_output_references(ir: dict, errors: list[dict]) -> None:
    """Output wiring: `*NodeId` must resolve to a node, every `*InputId` to a
    declared input, and a `fill`'s plot indices to `plot` outputs."""
    bound = len(ir["nodes"])
    outputs = ir["outputs"]
    declared_inputs = _declared_input_ids(ir)

    def check_refs(holder: dict, kind, where: str) -> None:
        for key, value in holder.items():
            if key == "nodeId" or key.endswith("NodeId"):
                if not _is_node_index(value, bound):
                    errors.append(
                        {
                            "code": "IR_BAD_NODE_REF",
                            "message": (
                                f"output '{kind}'{where} field '{key}' references {value}, "
                                "which is not a node"
                            ),
                            "detail": str(value),
                        }
                    )
            elif key.endswith("InputId") and str(value) not in declared_inputs:
                # Every output-level `*InputId` is an input binding: `colorInputId`,
                # the zone's `borderColorInputId`/`mitigatedColorInputId` (G8), and
                # the label-visibility pair (G6). The check used to name
                # `colorInputId` exactly, which silently exempted the G8 siblings —
                # an undeclared id there degraded to the baked default with nothing
                # saying so.
                errors.append(
                    {
                        "code": "IR_BAD_INPUT_REF",
                        "message": (
                            f"output '{kind}'{where} field '{key}' binds undeclared input '{value}'"
                        ),
                        "detail": str(value),
                    }
                )

    for o in outputs:
        if not isinstance(o, dict):
            continue
        kind = o.get("kind")
        check_refs(o, kind, "")
        if isinstance(o.get("style"), dict):
            check_refs(o["style"], kind, " style")
        # `label` (level) / `text` (zone) may be a numeric format template whose
        # args are node ids sampled at the spawn bar.
        for field in ("label", "text"):
            t = o.get(field)
            if isinstance(t, dict) and t.get("kind") == "template" and isinstance(t.get("args"), list):
                for a in t["args"]:
                    if not _is_node_index(a, bound):
                        errors.append(
                            {
                                "code": "IR_BAD_NODE_REF",
                                "message": (
                                    f"output '{kind}' {field} template references {a}, "
                                    "which is not a node"
                                ),
                                "detail": str(a),
                            }
                        )
        if kind == "fill":
            for field in ("topPlotIndex", "bottomPlotIndex"):
                idx = o.get(field)
                target = outputs[idx] if _is_node_index(idx, len(outputs)) else None
                if not isinstance(target, dict) or target.get("kind") != "plot":
                    errors.append(
                        {
                            "code": "IR_BAD_OUTPUT_REF",
                            "message": f"fill '{field}' references {idx}, which is not a plot output",
                            "detail": str(idx),
                        }
                    )

# Bytes per megabyte — identical 1024*1024 on both runtimes.
_MB = 1024 * 1024

# Keep in sync with the executor's _eval_node dispatch (executor.py).
_KNOWN_NODE_OPS = frozenset(
    {"source", "const", "input", "binop", "unop", "select", "hist", "nz", "call", "scan", "htf"}
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
SUPPORTED_FEATURES: frozenset[str] = frozenset({"drawing-streams", "request-security"})

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
    version = ir.get("version")
    if version != IR_VERSION:
        errors.append(
            {
                "code": "IR_VERSION_MISMATCH",
                "message": (
                    f"IR version {version if version is not None else '(none)'} is not "
                    f"supported by runtime version {IR_VERSION}"
                ),
                "detail": "none" if version is None else str(version),
            }
        )
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
    # Containers first: every pass below iterates these, so a non-list is
    # reported once here and the dependent passes are skipped rather than
    # raising a TypeError out of the gate.
    nodes_ok = isinstance(ir.get("nodes"), list)
    outputs_ok = isinstance(ir.get("outputs"), list)
    inputs_ok = isinstance(ir.get("inputs"), list)
    for field, ok in (("nodes", nodes_ok), ("outputs", outputs_ok), ("inputs", inputs_ok)):
        if not ok:
            errors.append(
                {
                    "code": "IR_MALFORMED",
                    "message": f"IR field '{field}' must be an array",
                    "detail": field,
                }
            )
    if nodes_ok:
        for node in ir["nodes"]:
            op = node.get("op") if isinstance(node, dict) else None
            if op not in _KNOWN_NODE_OPS:
                errors.append(
                    {
                        "code": "IR_UNKNOWN_NODE_OP",
                        "message": f"unknown node op: {op}",
                        "detail": str(op),
                    }
                )
    if outputs_ok:
        for o in ir["outputs"]:
            kind = o.get("kind") if isinstance(o, dict) else None
            if kind not in _KNOWN_OUTPUT_KINDS:
                errors.append(
                    {
                        "code": "IR_UNKNOWN_OUTPUT_KIND",
                        "message": f"unknown output kind: {kind}",
                        "detail": str(kind),
                    }
                )
    if nodes_ok:
        _admit_node_structure(ir, errors)
    if nodes_ok and inputs_ok:
        _admit_node_references(ir, errors)
    if nodes_ok and outputs_ok and inputs_ok:
        _admit_output_references(ir, errors)
    declared_features = set(required_features)
    for o in ir["outputs"] if outputs_ok else []:
        feat = _GATED_OUTPUT_FEATURE.get(o.get("kind")) if isinstance(o, dict) else None
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
