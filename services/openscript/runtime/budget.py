"""Deterministic execution budget — WEIGHTED charger (Python port of the TS
OperationBudget, openalgo-openscript/src/runtime/budget.ts; design spec §6/§7).

Rather than charging a flat `bar_count` per node, this budget charges each node
its real weighted cost from the Phase-0.2 operator-cost model
(`plancost.per_node_weights`, which mirrors `estimate_plan_cost`'s cache-key
grouping). Per-node weights are precomputed at construction from the IR + a
runtime `CostCtx` whose window lengths are clamped to [min, max].

The soundness invariant: charged <= estimate — the total the runtime charges
(spent()) can never exceed the admission-time symbolic estimate (evaluated with
input_bound = decl.max). This holds by construction because the weights reuse
the estimator's grouping and every cost term is monotonic non-decreasing in its
clamped input_bound (see per_node_weights).

Enforcement:
  - construction: weighted perBarOperations > maximumOperationsPerBar -> OS4001;
  - step(node): charge weights[node id]; cumulative > maximumTotalOperations ->
    OS4001; wall-clock > maximumExecutionMilliseconds -> OS4002;
  - checkpoint(): wall-clock-only check (-> OS4002), called by the executor
    after each expensive (call/scan) node;
  - record_bytes(n): series-buffer high-water; peak_bytes() reports it;
  - advisory_heap_peak(): ADVISORY real-heap high-water (tracemalloc, only if
    the host already enabled it) — recorded separately, NEVER gates.

Cancellation granularity: a run is interruptible ONLY between nodes / after a
kernel returns. A single monster kernel call cannot be interrupted mid-call;
OS4002 fires at the next step/checkpoint.
"""

from __future__ import annotations

import time
import tracemalloc
from collections.abc import Mapping

from ..limits import SCRIPT_LIMITS
from .cost_expr import CostCtx, eval_cost_expr
from .plancost import estimate_plan_cost, per_node_weights


class BudgetExceeded(Exception):
    """Raised when a script crosses an execution limit. `code` is the runtime
    diagnostic code (OS4001 op budget, OS4002 time budget)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _advisory_heap() -> int | None:
    """ADVISORY current traced-heap peak, only if the host already enabled
    tracemalloc (never started here — starting it has real overhead)."""
    if tracemalloc.is_tracing():
        return tracemalloc.get_traced_memory()[1]
    return None


class OperationBudget:
    """Weighted execution budget. Construct with the IR + a runtime CostCtx (see
    plancost.runtime_cost_ctx); `step(node)` charges the node's precomputed
    weight; `checkpoint()` is a wall-clock-only guard for after expensive nodes."""

    def __init__(self, ir: dict, ctx: CostCtx, limits: Mapping = SCRIPT_LIMITS):
        self._limits = limits
        per_bar = eval_cost_expr(estimate_plan_cost(ir)["perBarOperations"], ctx)
        if per_bar > limits["maximumOperationsPerBar"]:
            raise BudgetExceeded(
                "OS4001",
                f"{per_bar} operations/bar exceeds {limits['maximumOperationsPerBar']}",
            )
        self._weights = per_node_weights(ir, ctx)
        self._ops = 0.0
        self._live_bytes = 0
        self._peak = 0
        self._heap_peak: int | None = None
        self._start = time.perf_counter()
        self._sample_heap()

    def step(self, node: dict) -> None:
        """Charge one node's weighted work; raises when a limit is crossed."""
        self._ops += self._weights[node["id"]]
        if self._ops > self._limits["maximumTotalOperations"]:
            raise BudgetExceeded(
                "OS4001", f"{self._ops} operations exceeds {self._limits['maximumTotalOperations']}"
            )
        self._check_time()

    def checkpoint(self) -> None:
        """Wall-clock-only checkpoint (called after each expensive call/scan node)."""
        self._check_time()
        self._sample_heap()

    def record_bytes(self, n_bytes: int) -> None:
        """Record an allocated series buffer's byte length; updates the peak."""
        self._live_bytes += n_bytes
        if self._live_bytes > self._peak:
            self._peak = self._live_bytes

    def peak_bytes(self) -> int:
        """Deterministic series-buffer peak (bytes) — the enforcement figure."""
        return self._peak

    def spent(self) -> float:
        """Cumulative weighted charge."""
        return self._ops

    def advisory_heap_peak(self) -> int | None:
        """ADVISORY real-heap high-water (bytes), or None if unavailable. NEVER gates."""
        return self._heap_peak

    def _check_time(self) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        if elapsed_ms > self._limits["maximumExecutionMilliseconds"]:
            raise BudgetExceeded(
                "OS4002",
                f"execution exceeded {self._limits['maximumExecutionMilliseconds']}ms",
            )

    def _sample_heap(self) -> None:
        h = _advisory_heap()
        if h is not None and (self._heap_peak is None or h > self._heap_peak):
            self._heap_peak = h
