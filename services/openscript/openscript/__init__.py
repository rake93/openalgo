"""OpenScript compiler (Python) — a faithful port of the TypeScript front end
(openalgo-openscript/src/compiler). `compile(source)` runs parse →
semantic → IR-gen and stops at the first stage that reports errors. Behavioral
equivalence with the TS is enforced by the shared conformance fixtures.

The server ALWAYS recompiles source and never trusts client-submitted IR.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import Diagnostic
from .finality import analyze_finality
from .ir_gen import generate_ir
from .parser import parse
from .semantic import analyze_program


@dataclass
class CompileResult:
    ir: dict | None
    diagnostics: list[Diagnostic]


def compile(source: str) -> CompileResult:
    """Compile OpenScript source to a JSON IR plus the complete diagnostic set."""
    program, diagnostics = parse(source)
    if diagnostics:
        return CompileResult(ir=None, diagnostics=diagnostics)
    semantic = analyze_program(program)
    # Halt on ERRORS only, riding warnings through with a built IR -- the same rule
    # ir-gen already follows and `analyze_finality` relies on.
    #
    # This gate used to be `if semantic:`. That was indistinguishable from
    # errors-only for as long as semantic emitted nothing but errors, and it broke
    # the moment OS2010 shipped as a warning: an advisory "you passed an argument
    # I ignore" produced NO IR, turning a non-breaking migration into a hard break.
    #
    # OS2010 has since become an error, so semantic currently has NO warning
    # emitter and this is once again equivalent to the old form. Kept in the
    # correct shape deliberately, so the next advisory semantic diagnostic does
    # not have to rediscover that trap.
    if any(d.severity == "error" for d in semantic):
        return CompileResult(ir=None, diagnostics=semantic)
    ir, ir_diagnostics = generate_ir(source, program)
    if ir is None:
        return CompileResult(ir=None, diagnostics=[*semantic, *ir_diagnostics])
    fin_diagnostics = analyze_finality(ir)  # mutates ir["meta"]; returns repaint warnings
    return CompileResult(ir=ir, diagnostics=[*semantic, *ir_diagnostics, *fin_diagnostics])


__all__ = ["CompileResult", "compile"]
