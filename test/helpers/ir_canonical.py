"""Canonical form of an OpenScript IR program, for cross-compiler comparison.

Why canonical rather than literal: the TS and Python front ends are independent
implementations of one language, and they are permitted to lower the same source
into *different but equivalent* graphs. Two such differences exist today and are
benign:

  1. Constant-node CSE granularity. TS interns `0` once and reuses that node for
     both a palette index and the literal in `volume > 0`; Python emits the
     palette index as int `0` and the literal as float `0.0`, which are distinct
     CSE keys, so it produces one extra node. Every downstream node id then
     shifts, making a literal diff report the whole tail as divergent.
  2. Integer-valued floats. Python's `9.0` and TS's `9` are the same JSON number
     to every consumer of this IR.

Forcing byte equality would therefore mean either changing a compiler to satisfy
a test or accepting a permanently red guard. Instead this reduces both sides to
what actually determines behaviour — for each output, the full expression tree
reachable from its node references — and compares that.

Node identity is a memoized structural hash rather than an inlined tree: IR is a
DAG with heavy sharing, and naive expansion is exponential (the SuperTrend
Cluster fixture does not terminate under it).

What this does NOT normalize away, and must therefore still fail:
    any difference in node ops, operators, kernel names, wiring, output kinds,
    output ordering, styles, declarations, inputs, or the header.
"""

from __future__ import annotations

import hashlib
import json

# Node fields holding a reference to another node, per op. Mirrors
# services/openscript/runtime/admit.py `_NODE_REF_FIELDS` — `scan.init` is a
# literal seed, not a reference.
_NODE_REF_FIELDS = {
    "binop": ("args",),
    "unop": ("arg",),
    "select": ("cond", "then", "else"),
    "hist": ("arg",),
    "nz": ("arg", "replacement"),
    "call": ("args",),
    "scan": ("inputs",),
}

from .ir_integers import integer_semantic_values

# IR fields that carry compiler provenance or telemetry rather than behaviour.
# Each is excluded deliberately; see `DIVERGENCES` for what that concedes.
_PROVENANCE_FIELDS = ("sourceHash", "compilerVersion", "meta")

DIVERGENCES = {
    "sourceHash": (
        "TS `hashSource` is a 53-bit content hash explicitly documented as NOT "
        "sha-256 because client IR is preview-only; the server computes the "
        "canonical sha-256. The stored artifact carries the server's hash, so "
        "the two are not comparable by construction."
    ),
    "compilerVersion": (
        "Provenance of the compiler that emitted the IR, not a behavioural field."
    ),
    "meta": (
        "`meta.planCost` is a telemetry/explain hint that admission recomputes "
        "and never trusts, and `meta.spans` maps nodes back to source offsets, "
        "so both follow the node numbering that CSE granularity shifts."
    ),
}


def normalize_numbers(x):
    """Collapse integer-valued floats. `bool` is left alone: it is a distinct IR
    value (`const` may hold a boolean) and must not become 0/1."""
    if isinstance(x, bool):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, dict):
        return {k: normalize_numbers(v) for k, v in x.items()}
    if isinstance(x, list):
        return [normalize_numbers(v) for v in x]
    return x


def _node_hasher(ir: dict):
    """Return `hash(node_id) -> str`, memoized, giving each node a structural
    identity independent of its position and of how constants were interned."""
    nodes = {n["id"]: n for n in ir["nodes"]}
    memo: dict[int, str] = {}

    def h(node_id):
        if node_id is None:
            return "na"
        if node_id in memo:
            return memo[node_id]
        # A cycle cannot occur in well-formed IR (admission rejects forward and
        # self references); the sentinel keeps a malformed input from recursing
        # forever instead of hanging the test run.
        memo[node_id] = "<cycle>"
        node = nodes[node_id]
        refs = _NODE_REF_FIELDS.get(node.get("op"), ())
        parts = {}
        for key, value in node.items():
            if key == "id":
                continue
            if key in refs:
                parts[key] = [h(v) for v in value] if isinstance(value, list) else h(value)
            else:
                parts[key] = normalize_numbers(value)
        digest = hashlib.sha1(
            json.dumps(parts, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        memo[node_id] = digest
        return digest

    return h


def canonical_outputs(ir: dict) -> list:
    """Every output with its node references replaced by structural hashes."""
    h = _node_hasher(ir)
    result = []
    for output in ir["outputs"]:
        entry = {}
        for key, value in output.items():
            if key == "nodeId" or key.endswith("NodeId"):
                entry[key] = h(value)
            elif key == "style" and isinstance(value, dict):
                entry[key] = {
                    sk: (h(sv) if sk.endswith("NodeId") else normalize_numbers(sv))
                    for sk, sv in value.items()
                }
            elif (
                key in ("label", "text")
                and isinstance(value, dict)
                and value.get("kind") == "template"
            ):
                entry[key] = {
                    "kind": "template",
                    "fmt": value["fmt"],
                    "args": [h(a) for a in value.get("args", [])],
                }
            else:
                entry[key] = normalize_numbers(value)
        result.append(entry)
    return result


def canonical_ir(ir: dict) -> dict:
    """The behaviour-determining projection of an IR program.

    `normalize_numbers` collapses integer-valued floats, and it has to: the
    Python lexer floats every numeric literal, so without it every golden would
    differ on values that are genuinely f64 either way (`nodes.value`,
    `hline.price`, `input.float` bounds).

    That same collapse is what let `offset: -2.0` pass as `-2` while the
    server-side executor crashed on it. So the INTEGER-SEMANTIC fields — bar
    indices, counts and widths, where a float is a latent `IndexError` rather
    than a formatting difference — are carried alongside WITH THEIR TYPE and
    compared exactly. Narrow on purpose: a blanket strict comparison would fail
    every golden and prove nothing, because JS cannot emit `2.0` at all.
    """
    return {
        "version": ir.get("version"),
        "header": normalize_numbers(ir.get("header")),
        "declaration": normalize_numbers(ir.get("declaration")),
        "inputs": normalize_numbers(ir.get("inputs")),
        "outputs": canonical_outputs(ir),
        "palette": normalize_numbers(ir.get("palette")),
        "integerSemantics": [
            (field, type(value).__name__, value) for field, value in integer_semantic_values(ir)
        ],
    }


def unexplained_fields(ir: dict) -> set:
    """Top-level IR fields that `canonical_ir` neither compares nor explains.

    Guards the projection itself: if a future compiler revision adds a field,
    this set becomes non-empty and the conformance test fails rather than
    silently skipping the new field on both sides.
    """
    compared = {"version", "header", "declaration", "inputs", "outputs", "palette", "nodes"}
    return set(ir) - compared - set(_PROVENANCE_FIELDS)
