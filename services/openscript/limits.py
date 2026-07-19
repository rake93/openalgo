"""Deterministic script limits — Python mirror of the TS `SCRIPT_LIMITS`
(openalgo-openscript/src/types/limits.ts, architecture doc §17).

These MUST stay identical to the TS values; `test_openscript_conformance.py`
pins them. Change both sides together.
"""

from types import MappingProxyType

SCRIPT_LIMITS = MappingProxyType(
    {
        "maximumSourceBytes": 100_000,
        "maximumAstNodes": 10_000,
        "maximumOutputs": 64,
        "maximumInputs": 100,
        "maximumVariables": 2_000,
        "maximumFunctionDepth": 32,
        "maximumHistoryBars": 100_000,
        "maximumLookback": 20_000,
        "maximumOperationsPerBar": 100_000,
        "maximumTotalOperations": 100_000_000,
        "maximumExecutionMilliseconds": 2_000,
        "maximumWorkerMemoryMb": 256,
    }
)

RECOMPUTE_FULL_THRESHOLD_BARS = 50_000
