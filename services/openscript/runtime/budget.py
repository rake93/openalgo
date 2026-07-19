"""Deterministic execution budget — Python port of the TS OperationBudget
(openalgo-openscript/src/runtime/budget.ts, architecture doc §17).

The executor is a single vectorized pass over the IR DAG. Each node processes
the whole bar-count-length series, so one `step()` per node accounts
`bar_count` element-operations. The budget bounds total operations (OS4001)
and wall-clock time (OS4002) so an untrusted script can never hang the server.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

from ..limits import SCRIPT_LIMITS


class BudgetExceeded(Exception):
    """Raised when a script crosses an execution limit. `code` is the
    runtime diagnostic code (OS4001 op budget, OS4002 time budget)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class OperationBudget:
    """Mirrors the TS OperationBudget exactly: `ops_per_bar` (node count) is
    checked against maximumOperationsPerBar at construction; each `step()`
    adds `bar_count` ops and checks maximumTotalOperations and wall-clock."""

    def __init__(self, bar_count: int, ops_per_bar: int, limits: Mapping = SCRIPT_LIMITS):
        self._bar_count = bar_count
        self._limits = limits
        self._ops = 0
        self._start = time.perf_counter()
        if ops_per_bar > limits["maximumOperationsPerBar"]:
            raise BudgetExceeded(
                "OS4001",
                f"{ops_per_bar} operations/bar exceeds {limits['maximumOperationsPerBar']}",
            )

    def step(self) -> None:
        """Account one node's work; raises when a limit is crossed."""
        self._ops += self._bar_count
        if self._ops > self._limits["maximumTotalOperations"]:
            raise BudgetExceeded(
                "OS4001", f"{self._ops} operations exceeds {self._limits['maximumTotalOperations']}"
            )
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        if elapsed_ms > self._limits["maximumExecutionMilliseconds"]:
            raise BudgetExceeded(
                "OS4002",
                f"execution exceeded {self._limits['maximumExecutionMilliseconds']}ms",
            )

    def spent(self) -> int:
        return self._ops
