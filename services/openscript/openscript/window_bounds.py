"""G9 — warn when a window length is bound to an input with no ``maxval``.

Mirrors the TS ``src/compiler/window-bounds.ts``; the two must agree, because
the diagnostic set is part of the shared conformance corpus.

Admission prices an input-bound window as ``declared_max(decl) or
maximumLookback``. That rule is correct -- the bound must be a true upper bound
or ``charged <= estimate`` fails. But an input declared ``input.int(12,
minval=1)`` therefore costs the FULL ``maximumLookback``, 20,000 ops/bar, and
nothing said so.

MEASURED on the Super OrderBlock port: 20,312 ops/bar and REJECTED at 5,000
bars, versus 512 ops/bar and admitted to 66,844 once one ``maxval=200`` was
added. A 40x cost error turning on one missing argument, with no diagnostic
connecting the rejection to its cause.

WHY IT RECURS: Pine has no admission budget, so Pine sources routinely omit
``maxval``. Every faithful port of an input-driven window inherits the error
from a source where it was not a mistake.

NON-GATING. A warning, never an error: the script is priced correctly and may
well fit, and erroring would break working scripts to report a cost they are
already paying.

DISTINCT FROM M11's wrapper case, where ``math.max(1, input)`` defeats a bound
that WAS declared. Here the bound was never declared. The wrapper case is not
detected here -- the length argument is a ``call`` node, not an ``input`` node --
and that boundary is deliberate rather than overlooked.
"""

from __future__ import annotations

from ..limits import SCRIPT_LIMITS
from ..runtime.operator_cost import window_length_arg_indices
from .diagnostics import Span, make_diagnostic


def _declared_max(decl: dict) -> float | None:
    """An input's declared upper bound, or None when it has none.

    Mirrors ``plancost.declared_max``; kept local so the compiler does not
    depend on the admission module for a one-line predicate.
    """
    v = decl.get("max")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def analyze_window_bounds(ir: dict) -> list:
    """Advisory OS5008 diagnostics for unbounded input-bound window lengths."""
    decls = {d["id"]: d for d in ir.get("inputs", [])}
    spans = (ir.get("meta") or {}).get("spans") or {}
    nodes = ir.get("nodes", [])
    out = []
    # ONE warning per input, not per call site: an input priced in three windows
    # is still one argument to add, and three identical warnings for one fix is
    # how a useful diagnostic becomes something people filter out.
    reported: set[str] = set()

    for node in nodes:
        if node.get("op") != "call":
            continue
        args = node.get("args") or []
        for arg_idx in window_length_arg_indices(node["namespace"], node["function"], len(args)):
            if arg_idx >= len(args):
                continue
            arg_node_id = args[arg_idx]
            arg_node = nodes[arg_node_id] if 0 <= arg_node_id < len(nodes) else None
            # Only a DIRECT input binding is priced through `input_bound`.
            # Anything else is priced by its own rule and has no maxval to declare.
            if arg_node is None or arg_node.get("op") != "input":
                continue
            decl = decls.get(arg_node.get("inputId"))
            if decl is None or decl.get("type") not in ("integer", "float"):
                continue
            if _declared_max(decl) is not None:
                continue
            if decl["id"] in reported:
                continue
            reported.add(decl["id"])
            raw = spans.get(arg_node_id) or spans.get(str(arg_node_id))
            if raw is None:
                continue
            # `meta.spans` stores spans as PLAIN DICTS (ir_gen serialises them on
            # the way in), but a Diagnostic carries a `Span` and serialises it on
            # the way out. Passing the dict straight through raised
            # "'dict' object has no attribute 'to_dict'" from every save endpoint.
            span = Span(start=raw["start"], end=raw["end"], line=raw["line"], column=raw["column"])
            out.append(
                make_diagnostic(
                    "OS5008",
                    "warning",
                    span,
                    f"'{decl['id']}' has no maxval, so its window is priced at "
                    f"maximumLookback ({SCRIPT_LIMITS['maximumLookback']} ops/bar) "
                    f"— add maxval= to bound it",
                )
            )
    return out
