"""OpenScript compiler (Python) — a faithful port of the TypeScript front end
(openalgo-openscript/src/compiler). `compile(source)` runs parse →
semantic → IR-gen and stops at the first stage that reports errors. Behavioral
equivalence with the TS is enforced by the shared conformance fixtures.

The server ALWAYS recompiles source and never trusts client-submitted IR.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import Diagnostic
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
    if semantic:
        return CompileResult(ir=None, diagnostics=semantic)
    ir, ir_diagnostics = generate_ir(source, program)
    return CompileResult(ir=ir, diagnostics=ir_diagnostics)


__all__ = ["CompileResult", "compile"]
