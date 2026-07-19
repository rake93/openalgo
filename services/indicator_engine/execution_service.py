"""Headless evaluation of a compiled OpenScript indicator.

Converts fetched OHLC history into the executor's numpy dataset, runs the IR,
and extracts the alert condition of interest. Pure and side-effect-free — the
alert scheduler (`alert_service`) owns history fetching, delivery, and dedup.
"""

from __future__ import annotations

import numpy as np


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _epoch(ts) -> int:
    """Coerce a history row timestamp to epoch seconds (best effort)."""
    if isinstance(ts, (int, float)):
        v = int(ts)
        return v // 1000 if v > 1_000_000_000_000 else v
    try:
        import pandas as pd  # local import; pandas is a heavy dep

        return int(pd.Timestamp(ts).timestamp())
    except Exception:
        return 0


def history_to_dataset(rows: list[dict]) -> dict:
    """Convert `get_history` row dicts (chronological) to the executor dataset."""

    def col(key: str) -> np.ndarray:
        return np.asarray([_num(r.get(key)) for r in rows], dtype=np.float64)

    return {k: col(k) for k in ("open", "high", "low", "close", "volume")}


def bar_timestamps(rows: list[dict]) -> list[int]:
    return [_epoch(r.get("timestamp")) for r in rows]


def find_alert_output(outputs: list[dict], condition_id: str) -> dict | None:
    """The alert output whose conditionId matches, or None."""
    for out in outputs:
        if out.get("kind") == "alert" and out.get("id") == condition_id:
            return out
    return None


def fired_on_last_bar(alert_output: dict, bar_count: int) -> bool:
    """True when the alert condition is true on the most recently closed bar."""
    if not alert_output or bar_count == 0:
        return False
    return (bar_count - 1) in set(alert_output.get("firedAtBar", []))
