"""Phase 0.4 — confirmation/finality effect (Python mirror of
openalgo-openscript/src/compiler/finality.ts). Pure, integer lattice, byte-identical."""
from __future__ import annotations

import math

from services.openscript.openscript.semantic import SOURCE_IDS

from ..limits import SCRIPT_LIMITS
from .diagnostics import Diagnostic, Span, make_diagnostic

_RANK = {"historical-final": 0, "confirmed": 1, "provisional": 2}
_BY_RANK = ["historical-final", "confirmed", "provisional"]


def lub(a: str, b: str) -> str:
    return _BY_RANK[max(_RANK[a], _RANK[b])]


_PRICE_SOURCES = frozenset(SOURCE_IDS)


def source_finality(source_id: str) -> str:
    return "confirmed" if source_id in _PRICE_SOURCES else "historical-final"


LOOKAHEAD_OPS = frozenset({"ta.pivothigh", "ta.pivotlow"})


# ── analyze_finality (design §4) ─────────────────────────────────────────────

_ZERO_SPAN = {"start": 0, "end": 0, "line": 1, "column": 1}


def _input_ids_of(node: dict) -> list:
    """The node-ref (node-id) fields per op — the finality-carrying inputs the
    LUB walk taints through (`source`/`const`/`input`/`call` have own branches).
    `nz.replacement` and `scan.init` are compile-time CONST values (a fill/seed),
    NOT node ids, so they carry no taint (historical-final) and are excluded."""
    op = node["op"]
    if op == "binop":
        return [node["args"][0], node["args"][1]]
    if op == "unop":
        return [node["arg"]]
    if op == "select":
        return [node["cond"], node["then"], node["else"]]
    if op == "hist":
        return [node["arg"]]
    if op == "nz":
        return [node["arg"]]
    if op == "scan":
        return node["inputs"]
    return []


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _const_or_bound(ir: dict, node_id: int) -> int:
    """The pivot `confirmationDelay` from its `right` node: a `const` numeric
    value verbatim; an `input`'s finite declared `max` (else `maximumLookback`);
    any other (non-static) node is treated conservatively as `maximumLookback`.
    Coerced to int — a bar count — so it is byte-identical to the TS side (whose
    numbers are already integral, while this lexer makes every literal a float)."""
    nodes = ir["nodes"]
    if node_id < 0 or node_id >= len(nodes):
        return SCRIPT_LIMITS["maximumLookback"]
    node = nodes[node_id]
    if node["op"] == "const" and _is_number(node.get("value")):
        return int(node["value"])
    if node["op"] == "input":
        decl = next((d for d in ir["inputs"] if d["id"] == node["inputId"]), None)
        if decl is not None and decl["type"] in ("integer", "float"):
            mx = decl.get("max")
            if _is_number(mx) and math.isfinite(mx):
                return int(mx)
        return SCRIPT_LIMITS["maximumLookback"]
    return SCRIPT_LIMITS["maximumLookback"]


def _dedupe_sources(sources: list) -> list:
    """Unique FinalitySources by nodeId, first-seen order preserved."""
    seen: set = set()
    out = []
    for s in sources:
        nid = s["nodeId"]
        if nid not in seen:
            seen.add(nid)
            out.append(s)
    return out


def _driver_node_of(o: dict):
    """The single node whose span attributes an output's diagnostic (None for
    the node-less hline/fill)."""
    kind = o["kind"]
    if kind == "plot":
        return o["nodeId"]
    if kind in ("plotshape", "plotchar", "barcolor", "bgcolor", "alertcondition", "level", "zone"):
        return o["condNodeId"]
    if kind == "plotcandle":
        return o["openNodeId"]
    return None


def _span_of(o: dict, ir: dict) -> dict:
    driver = _driver_node_of(o)
    if driver is None:
        return dict(_ZERO_SPAN)
    return ir["meta"]["spans"].get(driver, dict(_ZERO_SPAN))


def _lub_nodes(ids, node_fin, node_src):
    """LUB over a set of node-id channels (None channels skipped), sources unioned."""
    f = "historical-final"
    sources: list = []
    for nid in ids:
        if nid is None:
            continue
        f = lub(f, node_fin[nid])
        sources = sources + node_src[nid]
    return f, _dedupe_sources(sources)


def _output_finality_of(o: dict, node_fin: list, node_src: list, output_so_far: list):
    """An output's finality + tainting sources. Every RENDERED node-id channel
    counts — not just the primary value: a provisional-derived `color=` repaints
    the bar's colour even when the plotted value is confirmed, so `colorNodeId` is
    LUBed in (Fable review, P0.4); likewise `level`/`zone` value channels."""
    kind = o["kind"]
    if kind == "plot":
        return _lub_nodes([o["nodeId"], (o.get("style") or {}).get("colorNodeId")], node_fin, node_src)
    if kind in ("plotshape", "plotchar", "barcolor", "bgcolor"):
        return _lub_nodes([o["condNodeId"], o.get("colorNodeId")], node_fin, node_src)
    if kind == "alertcondition":
        return _lub_nodes([o["condNodeId"]], node_fin, node_src)
    # A drawing is a CONFIRMED-spawn object (design 0.5 §5): floor at `confirmed`
    # even when every value channel is historical-final; rise to `provisional`
    # only via a lookahead-tainted value channel (RESERVED forward-offset/HTF
    # slot, no lattice change). Mirror of the TS finality floor.
    if kind == "level":
        f, sources = _lub_nodes([o["condNodeId"], o.get("priceNodeId")], node_fin, node_src)
        return lub("confirmed", f), sources
    if kind == "zone":
        f, sources = _lub_nodes(
            [o["condNodeId"], o.get("topNodeId"), o.get("bottomNodeId")], node_fin, node_src
        )
        return lub("confirmed", f), sources
    if kind == "plotcandle":
        return _lub_nodes(
            [o["openNodeId"], o["highNodeId"], o["lowNodeId"], o["closeNodeId"]], node_fin, node_src
        )
    if kind == "hline":
        return "historical-final", []
    if kind == "fill":
        top = output_so_far[o["topPlotIndex"]] if 0 <= o["topPlotIndex"] < len(output_so_far) else "historical-final"
        bottom = (
            output_so_far[o["bottomPlotIndex"]]
            if 0 <= o["bottomPlotIndex"] < len(output_so_far)
            else "historical-final"
        )
        return lub(top, bottom), []
    return "historical-final", []


def _mk_warn(code: str, span: dict, sources: list) -> Diagnostic:
    """A repaint `warning` diagnostic, detail = the max delay + tainting operators."""
    max_delay = max((s["confirmationDelay"] for s in sources), default=0)
    ops = ", ".join(dict.fromkeys(s["operator"] for s in sources))
    detail = f"delay={max_delay} ({ops})" if ops else f"delay={max_delay}"
    return make_diagnostic(code, "warning", Span(**span), detail)


def analyze_finality(ir: dict) -> list[Diagnostic]:
    """Propagate the finality effect over the post-ir-gen DAG (design §4).
    Mutates ``ir["meta"]`` with per-output finality + the repaint-risk report and
    returns the OS5002/OS5003 warnings for the ``compile()`` diagnostic list.
    Integer lattice + stable order → byte-identical with the TypeScript mirror."""
    nodes = ir["nodes"]
    node_fin: list = [None] * len(nodes)
    node_src: list = [None] * len(nodes)
    decl_type = {d["id"]: d["type"] for d in ir["inputs"]}

    for node in nodes:
        f = "historical-final"
        sources: list = []
        op = node["op"]
        if op == "const":
            f = "historical-final"
        elif op == "source":
            f = source_finality(node["source"])
        elif op == "input":
            f = "confirmed" if decl_type.get(node["inputId"]) == "source" else "historical-final"
        elif op == "call":
            for a in node["args"]:
                f = lub(f, node_fin[a])
                sources = sources + node_src[a]
            key = f'{node["namespace"]}.{node["function"]}'
            if key in LOOKAHEAD_OPS:
                args = node["args"]
                last = args[-1] if args else node["id"]
                delay = _const_or_bound(ir, last)
                if delay > 0:
                    f = "provisional"
                    sources = sources + [
                        {
                            "operator": key,
                            "confirmationDelay": delay,
                            "nodeId": node["id"],
                            "span": ir["meta"]["spans"].get(node["id"], dict(_ZERO_SPAN)),
                        }
                    ]
        elif op == "htf":
            # Same-symbol HTF (request.security): the FORMING bucket (offset 0)
            # repaints until it closes -> provisional; a CLOSED bucket (offset >= 1)
            # is settled market data -> confirmed (Phase 3 design §4).
            #
            # confirmationDelay is 0 because the delay is VARIABLE: it resolves at
            # the HTF bucket boundary, not after a fixed number of base bars, so
            # there is no constant to report. This is the first genuinely variable
            # delay in the corpus -- a pivot's delay is a constant `rightbars`.
            # The MINIMUM offset this node reads in HTF space. Without `inner`
            # that is just `offset`. With it, the kernel reads `sourceOffset`
            # buckets back BEFORE the result offset applies, and v1 admits no
            # offset-0 inner -- so an inner node is confirmed always (design §7).
            # Written as the general expression rather than an `inner ⇒ confirmed`
            # special case, so the arm is already correct if an offset-0 inner is
            # ever admitted. Mirror: finality.ts minReadOffset.
            _inner = node.get("inner")
            _min_read_offset = int(node["offset"]) + (
                int(_inner["sourceOffset"]) if _inner is not None else 0
            )
            if _min_read_offset == 0:
                f = "provisional"
                sources = sources + [
                    {
                        "operator": "request.security",
                        "confirmationDelay": 0,
                        "nodeId": node["id"],
                        "span": ir["meta"]["spans"].get(node["id"], dict(_ZERO_SPAN)),
                    }
                ]
            else:
                f = "confirmed"
        else:
            for a in _input_ids_of(node):
                f = lub(f, node_fin[a])
                sources = sources + node_src[a]
        node_fin[node["id"]] = f
        node_src[node["id"]] = _dedupe_sources(sources)

    output_finality: list = []
    repaint_risks: list = []
    diagnostics: list[Diagnostic] = []
    for index, o in enumerate(ir["outputs"]):
        f, sources = _output_finality_of(o, node_fin, node_src, output_finality)
        output_finality.append(f)
        if f != "provisional":
            continue
        if o["kind"] == "alertcondition":
            if o["on"] == "bar.close":
                repaint_risks.append(
                    {"target": {"kind": "alert", "index": index, "on": o["on"]}, "finality": f, "sources": sources}
                )
                diagnostics.append(_mk_warn("OS5003", _span_of(o, ir), sources))
            # on == "tick" → acknowledged: no risk, no diagnostic
        else:
            repaint_risks.append(
                {"target": {"kind": "output", "index": index}, "finality": f, "sources": sources}
            )
            diagnostics.append(_mk_warn("OS5002", _span_of(o, ir), sources))

    ir["meta"]["outputFinality"] = output_finality
    if repaint_risks:
        ir["meta"]["repaintRisks"] = repaint_risks
    return diagnostics
