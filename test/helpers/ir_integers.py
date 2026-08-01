"""The integer-semantic IR fields, and how to find them in a program.

WHY THIS EXISTS. The Python lexer produces a FLOAT for every numeric literal,
while JavaScript has no int/float distinction at all — `JSON.stringify(2)` and
`JSON.stringify(2.0)` both write `2`. Measured across the goldens: **zero**
integral floats in 10,673 numeric literals. So the TS side is structurally
incapable of emitting `2.0`, and any `2.0` on the Python side is a
one-directional artifact, not a symmetric drift the comparison should smooth
over.

That artifact is not cosmetic. `offset=-2` lowered to `-2.0`, `x1 = spawn +
offset` became a float, and indexing the numpy time column raised `IndexError` —
so every drawing with a non-zero offset crashed the server while previewing
fine in the browser. It hid because Python compares `-2 == -2.0` as equal, so
the IR-conformance guard saw no difference against a contract that says
byte-identical.

WHY A DECLARED REGISTRY RATHER THAN "EVERY INT IN THE GOLDEN". A golden holding
`0` does not prove the field is integer-semantic: `input.float(1.5, minval=0)`
also serializes its min as `0`. Inferring the contract from the goldens would
therefore demand ints for genuinely-float fields. The set below is a statement
about MEANING — these values are bar indices, counts and widths — and meaning is
not recoverable from JSON.

WHY NON-VACUITY IS ENFORCED. `_as_bar_count`'s own docstring already listed four
fields it covered — `offset`, `right_pad`, `bars`, `max_kept` — while `bars`
never routed through it. A registry that silently covers nothing is the same
failure with more ceremony, so `EXERCISED_BY` must make every entry fire.
"""

from __future__ import annotations

#: Source that reaches every field in `INTEGER_SEMANTIC` at least once. Kept
#: beside the registry so the two cannot drift apart.
EXERCISED_BY = "\n".join(
    [
        'p = input.int(9, "P", minval=1, maxval=50)',
        'f = input.float(1.5, "F", minval=0, maxval=10)',
        'plot(close[2], "H", linewidth=2)',
        'plotlevel(bar_index == 1, close, "L1", offset=-2, right_pad=3, max_kept=7,'
        " width=2, extend=extend.bars, bars=4)",
        'plotzone(bar_index == 2, high, low, "Z", offset=-1, right_pad=1, max_kept=5,'
        " extend=extend.until, terminate=terminate.touch)",
    ]
)

#: Every IR field whose value is a bar index, a count, or a pixel width. A float
#: here is a latent defect, not a formatting difference.
INTEGER_SEMANTIC = frozenset(
    {
        "nodes.hist.offset",
        "inputs[integer].defaultValue",
        "inputs[integer].min",
        "inputs[integer].max",
        "outputs.offset",
        "outputs.rightPad",
        "outputs.maxKept",
        "outputs.bars",
        "outputs.style.lineWidth",
    }
)


def integer_semantic_values(ir: dict) -> list[tuple[str, object]]:
    """Every `(field, value)` in `ir` that INTEGER_SEMANTIC declares integral.

    Returns pairs rather than a verdict so a failing test can name the value it
    objected to; a bare bool would report "something is a float somewhere".
    """
    found: list[tuple[str, object]] = []

    for node in ir.get("nodes", []):
        if node.get("op") == "hist" and node.get("offset") is not None:
            found.append(("nodes.hist.offset", node["offset"]))

    for decl in ir.get("inputs", []):
        # Only an INTEGER input's metadata is integral. `input.float`'s bounds
        # are genuinely float even when they happen to hold a whole number.
        if decl.get("type") != "integer":
            continue
        for field in ("defaultValue", "min", "max"):
            if decl.get(field) is not None:
                found.append((f"inputs[integer].{field}", decl[field]))

    for out in ir.get("outputs", []):
        for field in ("offset", "rightPad", "maxKept", "bars"):
            if out.get(field) is not None:
                found.append((f"outputs.{field}", out[field]))
        style = out.get("style") or {}
        if style.get("lineWidth") is not None:
            found.append(("outputs.style.lineWidth", style["lineWidth"]))

    return found


def float_valued(pairs: list[tuple[str, object]]) -> list[tuple[str, object]]:
    """The pairs whose value is a float — `bool` excluded, it is its own IR type."""
    return [(f, v) for f, v in pairs if isinstance(v, float) and not isinstance(v, bool)]
