"""Phase 1 Pri 4 — per-object-bar budget charge (Python port of
openalgo-openscript/tests/materializer-budget.test.ts).

The runtime charges DRAW_BASE_OPS + DRAW_SCAN_WEIGHT*scan_bars +
DRAW_OBJECT_WEIGHT*obj_bars into the SAME OperationBudget as node steps, so
real <= charged <= estimate. Includes the 500-object x 2000-bar until.touch
stress staying inside OS4001/OS4002.
"""

import json
import time as _time
from pathlib import Path

import numpy as np
import pytest

from services.openscript import openscript  # noqa: F401  (first, avoids plancost<->ir_gen cycle)
from services.openscript.limits import SCRIPT_LIMITS
from services.openscript.runtime.admit import resolve_plan_cost
from services.openscript.runtime.budget import OperationBudget
from services.openscript.runtime.cost_expr import eval_cost_expr
from services.openscript.runtime.executor import execute_ir
from services.openscript.runtime.plancost import (
    admission_cost_ctx,
    estimate_plan_cost,
    runtime_cost_ctx,
)

_MATDIR = Path(__file__).resolve().parents[1].parent / "openalgo-openscript" / "fixtures" / "materializer"


def _eval_or0(dim, ctx):
    return 0 if dim == "n/a" else eval_cost_expr(dim, ctx)


@pytest.mark.skipif(not _MATDIR.is_dir(), reason="engine repo not a sibling checkout")
def test_drawing_charge_formula_and_bound():
    fx = json.loads((_MATDIR / "mat-zone-until-touch.json").read_text(encoding="utf-8"))
    ir = fx["ir"]
    dataset = {k: np.asarray(v, dtype=float) for k, v in fx["dataset"].items()}
    n = len(dataset["close"])  # 10
    ctx = runtime_cost_ctx(ir, {}, n)
    budget = OperationBudget(ir, ctx)
    execute_ir(ir, dataset, {}, budget)

    cost = estimate_plan_cost(ir)
    node_ops = eval_cost_expr(cost["totalOperations"], ctx)  # == sum(node weights)
    obj_lifecycle = _eval_or0(cost["dims"]["objectLifecycleChecks"], ctx)
    drawing_charged = budget.spent() - node_ops
    # spawn scan visited all 10 bars; the one object scanned bars 5,6 -> objBars=2.
    assert drawing_charged == 64 + 2 * 10 + 8 * 2  # 100
    assert budget.spent() <= node_ops + obj_lifecycle  # charged <= estimate
    assert drawing_charged > 0


def _stress_dataset(n: int) -> dict:
    seed = 12345

    def rnd_series():
        nonlocal seed
        out = np.empty(n)
        for i in range(n):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            out[i] = seed / 0x7FFFFFFF
        return out

    step = rnd_series() - 0.5
    close = 100 + np.cumsum(step)
    openp = close + (rnd_series() - 0.5)
    high = np.maximum(openp, close) + rnd_series()
    low = np.minimum(openp, close) - rnd_series()
    return {
        "time": (1_600_000_000 + np.arange(n) * 60).astype(float),
        "open": openp,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.full(n, 1000.0),
    }


def _five_zone_ir() -> dict:
    outputs = [
        {
            "kind": "zone",
            "condNodeId": 2,
            "topNodeId": 3,
            "bottomNodeId": 4,
            "title": f"Z{i}",
            "style": {"color": "#0f0"},
            "offset": 0,
            "rightPad": 0,
            "extend": "until",
            "terminate": "touch",
            "maxKept": 100,
        }
        for i in range(5)
    ]
    return {
        "version": 1,
        "compilerVersion": "openscript-1.0",
        "sourceHash": "stress",
        "header": {
            "major": 1,
            "minor": 0,
            "compilerVersion": "openscript-1.0",
            "requiredFeatures": ["drawing-streams"],
            "numericMode": "f64-strict",
        },
        "declaration": {"name": "Stress", "overlay": True},
        "inputs": [],
        "nodes": [
            {"id": 0, "op": "source", "source": "close"},
            {"id": 1, "op": "source", "source": "open"},
            {"id": 2, "op": "binop", "operator": ">", "args": [0, 1]},
            {"id": 3, "op": "source", "source": "high"},
            {"id": 4, "op": "source", "source": "low"},
        ],
        "outputs": outputs,
        "meta": {"warmupBars": 0, "spans": {}},
    }


def test_500x2000_until_touch_stress_inside_caps():
    ir = _five_zone_ir()
    n = 2000
    dataset = _stress_dataset(n)

    # Admission enforces node ops + objectLifecycleChecks <= maximumTotalOperations.
    res = resolve_plan_cost(ir, n, SCRIPT_LIMITS, "enforce")
    assert res["errors"] == []
    cost = estimate_plan_cost(ir)
    obj_lifecycle = _eval_or0(cost["dims"]["objectLifecycleChecks"], admission_cost_ctx(ir, n))
    assert obj_lifecycle == 5 * (64 + 2 * n + 8 * 100 * n)  # 8,020,320

    ctx = runtime_cost_ctx(ir, {}, n)
    budget = OperationBudget(ir, ctx)
    t0 = _time.perf_counter()
    outputs = execute_ir(ir, dataset, {}, budget)
    elapsed_ms = (_time.perf_counter() - t0) * 1000

    total_objects = sum(len(o["items"]) for o in outputs if o["kind"] == "zones")
    assert total_objects <= SCRIPT_LIMITS["maximumTotalObjects"]  # <= 500
    assert budget.spent() <= SCRIPT_LIMITS["maximumTotalOperations"]
    assert elapsed_ms < SCRIPT_LIMITS["maximumExecutionMilliseconds"]
    assert budget.spent() <= eval_cost_expr(cost["totalOperations"], ctx) + obj_lifecycle


def test_object_lifecycle_checks_enforced():
    # 5 zones * max_kept 100 * 100k bars -> ~401M object-lifecycle ops > 100M cap.
    ir = _five_zone_ir()
    res = resolve_plan_cost(ir, 100_000, SCRIPT_LIMITS, "enforce")
    assert any(e["code"] == "IR_OPERATION_BUDGET_EXCEEDED" for e in res["errors"])
