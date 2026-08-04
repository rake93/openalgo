"""
Directional Sentiment for the GEX Levels study - deliberately NOT the sign of
net GEX.

Regime (in `gex_levels_service.py`) is about volatility: positive net gamma is
suppressive, negative is amplifying, and negative gamma extends a move in
BOTH directions. Reading that sign as bullish/bearish - what the reference GEX
products do - would print BEARISH at the exact moment a gamma-driven squeeze
is running upward. Sentiment is a separate, genuinely directional read that
sits ALONGSIDE Regime rather than replacing it, and none of the three signals
below ever look at net GEX or its sign.

Three signals, each independently `bullish` / `bearish` / `neutral` /
`unavailable`:

  1. Wall position (weight 2) - where spot sits relative to the Call Wall and
     Put Wall this study already computes. The most direct directional read
     this study's own structure can support.
  2. Put-call ratio (weight 1) - on the SAME weighting (OI or volume) the
     user selected, so it agrees with the profile on screen.
  3. Implied-volatility skew (weight 1) - which side of the chain is bid up,
     from the per-strike IVs already inverted for the exposure calculation.

Composite mirrors `frontend/src/lib/charts/direction.ts` exactly: unavailable
signals are skipped entirely (excluded from both the numerator and the
denominator), the net is normalised by the weight that actually participated,
and `agreeing` / `participating` travel with the verdict so a one-signal read
can never display as if every signal agreed.

Purity: no network, database, logging or clock. Plain inputs, plain verdict.
"""

import math
from dataclasses import dataclass
from typing import Literal

from services.gex_levels.exposure import ChainRow, StrikeExposure, WeightBy
from services.gex_levels.levels import Walls

Bias = Literal["bullish", "bearish", "neutral", "unavailable"]

# Normalised score needed for a directional composite - a third net agreement.
# Same value and meaning as direction.ts's COMPOSITE_BAND.
COMPOSITE_BAND = 0.34

# Put-call ratio: >= this is bullish (put writers dominate), <= its
# reciprocal-ish counterpart is bearish. See `_pcr_signal` for the convention.
_PCR_BULLISH = 1.2
_PCR_BEARISH = 0.8

# IV skew, in volatility POINTS (already *100). Below this the skew is noise.
_SKEW_BAND = 1.5


@dataclass(frozen=True)
class SentimentSignal:
    """One directional reading, with its weight in the composite.

    Attributes:
        key: Stable identifier ('walls', 'pcr', 'skew') for the frontend.
        label: Human-readable name shown in the panel.
        detail: One-line human-readable reading, e.g. "PCR 1.34 by open interest".
        bias: This signal's own verdict.
        weight: Its share of the composite when it participates. Sent to the
            frontend so a reader can see, e.g., that walls counts double.
        why: One line stating which threshold decided `bias`, and by how
            much - generated right beside the threshold it describes so the
            two can never drift apart. e.g. a PCR of 0.81 against the 0.80
            bearish threshold reads neutral, but only by 0.01; `why` is the
            only place that margin is visible at all.
    """

    key: str
    label: str
    detail: str
    bias: Bias
    weight: float
    why: str


@dataclass(frozen=True)
class Sentiment:
    """The combined directional verdict.

    Attributes:
        bias: Never 'unavailable' - an all-unavailable chain reads 'neutral'
            with score 0, same as direction.ts's composite.
        score: Weighted net, normalised to the participating weight: -1..1.
        agreeing: Count of signals whose own bias matches `bias`.
        participating: Count of signals that were not 'unavailable'. Travels
            with the verdict so the panel can never imply more agreement
            than there actually was.
        signals: All three signals, in evaluation order.
    """

    bias: Literal["bullish", "bearish", "neutral"]
    score: float
    agreeing: int
    participating: int
    signals: list[SentimentSignal]


def _finite(value: float | None) -> float:
    """A missing or non-finite weight is no position at all, not a zero read."""
    if value is None or not math.isfinite(value):
        return 0.0
    return value


def _fmt_strike(value: float) -> str:
    """Strikes are usually whole numbers; only show decimals when present
    (e.g. VEDL's half-rupee strikes)."""
    return f"{value:.0f}" if value == int(value) else f"{value:.2f}"


def _unavailable(key: str, label: str, weight: float, reason: str, why: str) -> SentimentSignal:
    return SentimentSignal(
        key=key, label=label, detail=reason, bias="unavailable", weight=weight, why=why
    )


def _wall_signal(spot: float | None, walls: Walls) -> SentimentSignal:
    """
    Where spot sits relative to the Call Wall and Put Wall.

    Breaking above the largest positive-gamma concentration means dealers are
    short gamma above it and must chase price up (bullish); breaking below
    the largest negative-gamma concentration is the mirror (bearish).

    Deliberately does NOT infer direction from proximity between the walls -
    e.g. "closer to the put wall so leaning bullish". That is a weaker claim
    than this study can support; the honest read when spot sits inside the
    range is simply that it is pinned there, so the neutral case names the
    range instead of a lean.
    """
    key, label, weight = "walls", "Wall position", 2.0
    unavailable_why = "Needs both walls and a spot price"
    if walls.call_wall is None or walls.put_wall is None:
        return _unavailable(
            key, label, weight, "Call wall or put wall unavailable", unavailable_why
        )
    if spot is None or not math.isfinite(spot):
        return _unavailable(key, label, weight, "Spot price unavailable", unavailable_why)

    call_wall, put_wall = walls.call_wall, walls.put_wall
    if call_wall == put_wall:
        return SentimentSignal(
            key,
            label,
            f"Both walls concentrated at {_fmt_strike(call_wall)}",
            "neutral",
            weight,
            "Both walls are on one strike, so there is no range to position within",
        )

    if spot > call_wall:
        return SentimentSignal(
            key,
            label,
            f"Spot {_fmt_strike(spot)} above the call wall {_fmt_strike(call_wall)}",
            "bullish",
            weight,
            f"Spot {_fmt_strike(spot)} is above the call wall {_fmt_strike(call_wall)}",
        )
    if spot < put_wall:
        return SentimentSignal(
            key,
            label,
            f"Spot {_fmt_strike(spot)} below the put wall {_fmt_strike(put_wall)}",
            "bearish",
            weight,
            f"Spot {_fmt_strike(spot)} is below the put wall {_fmt_strike(put_wall)}",
        )

    lo, hi = min(call_wall, put_wall), max(call_wall, put_wall)
    pct = (spot - lo) / (hi - lo) * 100
    return SentimentSignal(
        key,
        label,
        f"Pinned between put wall {_fmt_strike(put_wall)} and call wall "
        f"{_fmt_strike(call_wall)} ({pct:.0f}% of the way up)",
        "neutral",
        weight,
        "Spot sits between the walls, so neither has given way",
    )


def _pcr_signal(rows: list[ChainRow], weight_by: WeightBy) -> SentimentSignal:
    """
    Put-call ratio on the selected weighting.

    Convention: in Indian index options a HIGH put-call ratio means put
    writers dominate the book, which is traditionally read as support for the
    market (bullish) - the writers are effectively short volatility on the
    downside and long the underlying. It inverts at extremes as a contrarian
    signal (an extreme PCR can mark crowded positioning about to unwind) - this
    implementation deliberately uses the plain reading, not the contrarian one.
    """
    key, label, weight = "pcr", "Put-call ratio", 1.0
    use_volume = weight_by == "volume"
    call_total = sum(_finite(r.call_volume if use_volume else r.call_oi) for r in rows)
    put_total = sum(_finite(r.put_volume if use_volume else r.put_oi) for r in rows)

    if call_total <= 0:
        weighting = "volume" if use_volume else "open interest"
        return _unavailable(
            key,
            label,
            weight,
            "No call open interest or volume to form a ratio",
            f"No call {weighting} to divide by",
        )

    pcr = put_total / call_total
    basis = "volume" if use_volume else "open interest"
    detail = f"PCR {pcr:.2f} by {basis}"
    if pcr >= _PCR_BULLISH:
        why = f"{pcr:.2f} is at or above the {_PCR_BULLISH:.2f} bullish threshold"
        return SentimentSignal(key, label, detail, "bullish", weight, why)
    if pcr <= _PCR_BEARISH:
        why = f"{pcr:.2f} is at or below the {_PCR_BEARISH:.2f} bearish threshold"
        return SentimentSignal(key, label, detail, "bearish", weight, why)

    # Name whichever threshold is nearer - that is the margin the reading was
    # actually decided by, and it is otherwise invisible on the panel (e.g. a
    # PCR of 0.81 reads neutral, but only 0.01 clear of the 0.80 threshold).
    gap_to_bearish = pcr - _PCR_BEARISH
    gap_to_bullish = _PCR_BULLISH - pcr
    if gap_to_bearish <= gap_to_bullish:
        why = f"{pcr:.2f} clears the {_PCR_BEARISH:.2f} bearish threshold by {gap_to_bearish:.2f}"
    else:
        why = f"{pcr:.2f} is {gap_to_bullish:.2f} below the {_PCR_BULLISH:.2f} bullish threshold"
    return SentimentSignal(key, label, detail, "neutral", weight, why)


def _skew_signal(exposures: list[StrikeExposure], forward: float | None) -> SentimentSignal:
    """
    Implied-volatility skew either side of the forward.

    Puts richer than calls (positive skew) means downside protection is bid -
    bearish. Calls richer than puts means upside is being chased - bullish.
    Uses the per-strike IVs already inverted for the exposure calculation, so
    this costs no extra solver calls.
    """
    key, label, weight = "skew", "IV skew", 1.0
    unavailable_why = "One side has no invertible implied volatility"
    if forward is None or not math.isfinite(forward) or forward <= 0:
        return _unavailable(key, label, weight, "Forward price unavailable", unavailable_why)

    put_ivs = [e.put_iv for e in exposures if e.strike < forward and e.put_iv is not None]
    call_ivs = [e.call_iv for e in exposures if e.strike > forward and e.call_iv is not None]
    if not put_ivs or not call_ivs:
        return _unavailable(
            key, label, weight, "No invertible implied volatility on one side", unavailable_why
        )

    put_avg = sum(put_ivs) / len(put_ivs)
    call_avg = sum(call_ivs) / len(call_ivs)
    diff = (put_avg - call_avg) * 100  # volatility points
    detail = f"puts {put_avg * 100:.1f}% vs calls {call_avg * 100:.1f}%"

    if diff >= _SKEW_BAND:
        why = f"Puts richer by {diff:.1f} vol points, past the {_SKEW_BAND:.1f} point band"
        return SentimentSignal(key, label, detail, "bearish", weight, why)
    if diff <= -_SKEW_BAND:
        why = f"Calls richer by {abs(diff):.1f} vol points, past the {_SKEW_BAND:.1f} point band"
        return SentimentSignal(key, label, detail, "bullish", weight, why)
    why = f"{diff:+.1f} vol points, inside the +/-{_SKEW_BAND:.1f} band"
    return SentimentSignal(key, label, detail, "neutral", weight, why)


def read_sentiment(
    exposures: list[StrikeExposure],
    walls: Walls,
    rows: list[ChainRow],
    spot: float | None,
    forward: float | None,
    weight_by: WeightBy,
) -> Sentiment:
    """
    Read all three signals and combine them into one verdict.

    Unavailable signals are skipped entirely - excluded from both the net and
    the participating weight, not counted as a neutral zero - so a chain
    missing one input still produces an honest verdict on what is left.

    Args:
        exposures: Per-strike exposures (for the IV skew signal).
        walls: Call Wall / Put Wall (for the wall-position signal).
        rows: Raw chain rows carrying OI and volume (for the PCR signal).
        spot: Underlying LTP. Not the forward - wall position is a spot-price
            question (has price traded through the wall), not a
            fair-value one.
        forward: Per-expiry forward price the chain's IVs were inverted at.
        weight_by: 'oi' for the standing book, 'volume' for today's flow -
            the same weighting the chart's own profile is drawn on.

    Returns:
        Sentiment, never with bias 'unavailable' - an all-unavailable
        chain reads 'neutral' with score 0.
    """
    signals = [
        _wall_signal(spot, walls),
        _pcr_signal(rows, weight_by),
        _skew_signal(exposures, forward),
    ]

    net = 0.0
    weight = 0.0
    participating = 0
    for s in signals:
        if s.bias == "unavailable":
            continue
        participating += 1
        weight += s.weight
        if s.bias == "bullish":
            net += s.weight
        elif s.bias == "bearish":
            net -= s.weight

    score = net / weight if weight > 0 else 0.0
    bias: Literal["bullish", "bearish", "neutral"] = (
        "bullish"
        if score >= COMPOSITE_BAND
        else "bearish"
        if score <= -COMPOSITE_BAND
        else "neutral"
    )
    agreeing = sum(1 for s in signals if s.bias == bias)

    return Sentiment(
        bias=bias, score=score, agreeing=agreeing, participating=participating, signals=signals
    )
