# Option Target Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `/tools` calculator that, given a futures or spot target, projects what every option strike will be worth and ranks which one to buy.

**Architecture:** A pure-math package `services/option_target/` (forward anchoring, IV calibration, smile fitting, repricing, ranking) with zero broker dependencies, wrapped by a thin orchestrator `services/option_target_service.py` that fetches the live chain. Exposed at `POST /api/v1/optiontarget`, consumed by a React page. All options math goes through the Rust `opengreeks` Black-76 core already used by the Greeks and Option Chain services, so numbers reconcile across pages.

**Tech Stack:** Python 3.12 (uv), `opengreeks` (Black-76), numpy 2.4.4, Flask-RESTX + marshmallow, React 19 + TypeScript, TanStack Query, Plotly (`@/lib/Plot2D`), shadcn/ui.

**Spec:** `docs/superpowers/specs/2026-08-04-option-target-calculator-design.md`

---

## Background the engineer needs

**Black-76, not Black-Scholes.** Indian F&O options are priced off a *forward*, not spot. Every call is `opengreeks.black76.<fn>(flag, F, K, t, r, sigma)` where `flag` is `"c"`/`"p"`, `t` is in **years**, `sigma` is a **decimal** (0.12 = 12%). The pricer is `black76.black(...)`; IV solver is `black76.implied_volatility(price, F, K, r, t, flag)` — note its argument order differs from the Greeks.

**The forward is not spot.** It is `ATM_strike + ATM_CE_mid - ATM_PE_mid` (put-call parity). Measured live: NIFTY weekly basis +7.5 pts, BANKNIFTY monthly basis **+139 pts**. Using spot is not an approximation, it is the wrong instrument.

**Three effects must be modelled, in order of measured importance:**
1. **Vol level response** — a falling index raises IV. Largest error term. Measured beta ~1.4 on 2026-08-04 BANKNIFTY.
2. **Gamma** — the move is large enough that delta alone misses.
3. **Smile slide** — the strike's moneyness changes, so it inherits a different IV off the smile curve.

Backtested MAE across 37 strike series: delta-only 6.84%, full model 1.26%.

**Never** use `print()` or `traceback`. Use `logger = get_logger(__name__)` and `logger.exception()`. Run `uv run` for everything.

---

## File Structure

**Backend — pure math (no imports from `database`, `broker`, or other services):**

| File | Responsibility |
| --- | --- |
| `services/option_target/__init__.py` | Public re-exports |
| `services/option_target/models.py` | Frozen dataclasses shared by all modules |
| `services/option_target/daycount.py` | Calendar vs trading-day year fractions |
| `services/option_target/forward.py` | Forward anchoring, target mapping, exact vs basis mode |
| `services/option_target/smile.py` | IV calibration from live mids, vega-weighted smile fit |
| `services/option_target/projection.py` | Repricing at target, P&L attribution |
| `services/option_target/ranking.py` | Per-strike metrics, filters, objectives, scoring |

**Backend — session layer (touches broker):**

| File | Responsibility |
| --- | --- |
| `services/option_target/volbeta.py` | Estimate vol-beta from intraday history |
| `services/option_target_service.py` | Snapshot fetch, orchestration, bounded TTL cache |
| `restx_api/option_target.py` | HTTP endpoint |
| `restx_api/data_schemas.py` | `OptionTargetSchema` (modify) |
| `restx_api/__init__.py` | Namespace registration (modify) |

**Frontend:**

| File | Responsibility |
| --- | --- |
| `frontend/src/types/option-target.ts` | Response types |
| `frontend/src/api/option-target.ts` | API client |
| `frontend/src/hooks/useOptionTarget.ts` | Polling + freeze |
| `frontend/src/pages/option-target/ScenarioPanel.tsx` | Inputs |
| `frontend/src/pages/option-target/StrikeTable.tsx` | Ranked table |
| `frontend/src/pages/option-target/StrikeDetail.tsx` | Ladder, waterfall, Greeks |
| `frontend/src/pages/OptionTargetCalculator.tsx` | Page assembly |

**Tests:** `test/test_option_target_math.py`, `test/test_option_target_ranking.py`, `test/test_option_target_service.py`, `test/fixtures/option_target/`.

---

## Task 1: Package scaffold and shared models

**Files:**
- Create: `services/option_target/__init__.py`
- Create: `services/option_target/models.py`
- Test: `test/test_option_target_math.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_option_target_math.py`:

```python
"""Pure-math tests for the Option Target Calculator. No broker, no database."""

import pytest

from services.option_target.models import ForwardAnchor, StrikeQuote


def test_strike_quote_mid_uses_bid_ask_when_both_present():
    q = StrikeQuote(
        strike=24500.0, option_type="CE", symbol="NIFTY11AUG2624500CE",
        ltp=158.0, bid=157.0, ask=159.0, oi=1000, volume=500, lot_size=65,
    )
    assert q.mid == 158.0


def test_strike_quote_mid_falls_back_to_ltp_when_book_is_one_sided():
    q = StrikeQuote(
        strike=24500.0, option_type="CE", symbol="NIFTY11AUG2624500CE",
        ltp=158.0, bid=0.0, ask=159.0, oi=1000, volume=500, lot_size=65,
    )
    assert q.mid == 158.0


def test_strike_quote_mid_rejects_crossed_book():
    q = StrikeQuote(
        strike=24500.0, option_type="CE", symbol="NIFTY11AUG2624500CE",
        ltp=158.0, bid=160.0, ask=159.0, oi=1000, volume=500, lot_size=65,
    )
    assert q.mid == 158.0


def test_strike_quote_half_spread():
    q = StrikeQuote(
        strike=24500.0, option_type="CE", symbol="NIFTY11AUG2624500CE",
        ltp=158.0, bid=157.0, ask=159.0, oi=1000, volume=500, lot_size=65,
    )
    assert q.half_spread == 1.0


def test_forward_anchor_basis():
    a = ForwardAnchor(
        forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity"
    )
    assert a.basis == pytest.approx(138.95, abs=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target/models.py`:

```python
"""Shared value objects for the Option Target Calculator.

Every dataclass here is frozen. The projection engine is a pipeline of pure
functions and immutable inputs make it safe to cache, reorder and test.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StrikeQuote:
    """One side (CE or PE) of one strike, as quoted by the broker."""

    strike: float
    option_type: str  # "CE" or "PE"
    symbol: str
    ltp: float
    bid: float
    ask: float
    oi: int
    volume: int
    lot_size: int

    @property
    def mid(self) -> float:
        """Mid price, falling back to LTP.

        Mid is preferred over LTP for IV calibration because LTP goes stale and
        one-sided on thin strikes, which biases the whole smile. A crossed or
        one-sided book is treated as no book at all.
        """
        if self.bid > 0 and self.ask > 0 and self.ask >= self.bid:
            return (self.bid + self.ask) / 2
        return self.ltp

    @property
    def half_spread(self) -> float:
        """Half the quoted spread; 0 when the book is unusable."""
        if self.bid > 0 and self.ask > 0 and self.ask >= self.bid:
            return (self.ask - self.bid) / 2
        return 0.0

    @property
    def spread_pct(self) -> float:
        """Spread as a percentage of mid. 0 when not computable."""
        m = self.mid
        if m <= 0 or self.half_spread <= 0:
            return 0.0
        return (self.half_spread * 2) / m * 100


@dataclass(frozen=True)
class ForwardAnchor:
    """The forward the options of one expiry are actually priced off."""

    forward: float
    spot: float
    atm_strike: float
    source: str  # "parity" or "spot_fallback"

    @property
    def basis(self) -> float:
        return self.forward - self.spot


@dataclass(frozen=True)
class ForwardTarget:
    """Where the forward lands when the user's reference hits its target."""

    forward: float
    mode: str  # "exact" or "basis_modelled"
    reference: str  # "FUT" or "SPOT"
    reference_now: float
    reference_target: float

    @property
    def move_pct(self) -> float:
        if self.reference_now <= 0:
            return 0.0
        return (self.reference_target / self.reference_now - 1) * 100


@dataclass(frozen=True)
class CalibratedIv:
    """One strike's implied vol, backed out of its live mid."""

    strike: float
    option_type: str
    log_moneyness: float
    iv: float
    vega: float


@dataclass(frozen=True)
class SmileFit:
    """Vega-weighted quadratic fit of IV against log-moneyness."""

    a: float
    b: float
    c: float
    x_lo: float
    x_hi: float
    rms: float
    n_points: int
    degenerate: bool  # True when too few points; `a` is a flat ATM IV


@dataclass(frozen=True)
class Attribution:
    """Decomposition of the projected premium change."""

    delta: float
    gamma: float
    theta: float
    vega: float
    spread: float
    residual: float
    total: float
```

Create `services/option_target/__init__.py`:

```python
"""Option Target Calculator — pure projection math.

Nothing in this package imports from `database`, `broker` or other services.
That is deliberate: it makes the entire algorithm testable from recorded
fixtures without a live broker session, which matters because a sign error here
is invisible in the UI and costs real money.
"""

from services.option_target.models import (
    Attribution,
    CalibratedIv,
    ForwardAnchor,
    ForwardTarget,
    SmileFit,
    StrikeQuote,
)

__all__ = [
    "Attribution",
    "CalibratedIv",
    "ForwardAnchor",
    "ForwardTarget",
    "SmileFit",
    "StrikeQuote",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target/ test/test_option_target_math.py
git commit -m "feat(option-target): add shared value objects for projection engine"
```

---

## Task 2: Day-count conventions

**Files:**
- Create: `services/option_target/daycount.py`
- Test: `test/test_option_target_math.py` (append)

Day count is a *global convention*: it must be applied to both `T_now` and `T_target`, because IV is calibrated under the same convention it is repriced under. Mixing them silently corrupts every projection.

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_math.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from services.option_target.daycount import year_fraction

IST = ZoneInfo("Asia/Kolkata")


def test_calendar_year_fraction_is_simple_365():
    start = datetime(2026, 8, 4, 12, 0, tzinfo=IST)
    end = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    assert year_fraction(start, end, "calendar") == pytest.approx(7 / 365, rel=1e-9)


def test_calendar_year_fraction_is_zero_when_end_precedes_start():
    start = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    end = datetime(2026, 8, 4, 12, 0, tzinfo=IST)
    assert year_fraction(start, end, "calendar") == 0.0


def test_trading_year_fraction_skips_the_weekend():
    # Fri 2026-08-07 15:30 -> Mon 2026-08-10 15:30 is 3 calendar days but
    # only 1 trading day of decay.
    start = datetime(2026, 8, 7, 15, 30, tzinfo=IST)
    end = datetime(2026, 8, 10, 15, 30, tzinfo=IST)
    trading = year_fraction(start, end, "trading")
    calendar = year_fraction(start, end, "calendar")
    assert trading < calendar
    assert trading == pytest.approx(1 / 252, rel=1e-6)


def test_trading_year_fraction_prorates_within_a_session():
    # 09:15 -> 12:22:30 is half of the 6h15m session.
    start = datetime(2026, 8, 4, 9, 15, tzinfo=IST)
    end = datetime(2026, 8, 4, 12, 22, 30, tzinfo=IST)
    assert year_fraction(start, end, "trading") == pytest.approx(0.5 / 252, rel=1e-6)


def test_unknown_day_count_raises():
    start = datetime(2026, 8, 4, 12, 0, tzinfo=IST)
    end = datetime(2026, 8, 11, 12, 0, tzinfo=IST)
    with pytest.raises(ValueError, match="Unknown day_count"):
        year_fraction(start, end, "banana")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_math.py -v -k year_fraction`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target.daycount'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target/daycount.py`:

```python
"""Year-fraction conventions for the Option Target Calculator.

`calendar` matches `option_greeks_service.calculate_time_to_expiry` (365-day),
so projections reconcile with the Option Greeks and Option Chain pages. It is
the default for exactly that reason.

`trading` prices only market time (252 sessions, 09:15-15:30 IST). Over a
multi-day hold spanning a weekend, calendar time materially overstates decay
because the market does not bleed premium while it is shut.

Whichever is chosen must be applied to BOTH the time-to-expiry used for IV
calibration and the time used for repricing. Mixing conventions corrupts every
projection.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from utils.logging import get_logger
from utils.trading_calendar import is_trading_day

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

TRADING_DAYS_PER_YEAR = 252
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)
SESSION_MINUTES = (
    (SESSION_CLOSE[0] * 60 + SESSION_CLOSE[1]) - (SESSION_OPEN[0] * 60 + SESSION_OPEN[1])
)  # 375


def _session_bounds(day: datetime) -> tuple[datetime, datetime]:
    return (
        day.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1], second=0, microsecond=0),
        day.replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1], second=0, microsecond=0),
    )


def _session_minutes_on(day: datetime, start: datetime, end: datetime) -> float:
    """Market minutes elapsed on `day` within the window [start, end]."""
    if not is_trading_day(day.date()):
        return 0.0
    open_at, close_at = _session_bounds(day)
    lo = max(start, open_at)
    hi = min(end, close_at)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds() / 60.0


def year_fraction(start: datetime, end: datetime, day_count: str = "calendar") -> float:
    """Time from `start` to `end` in years under the given convention.

    Returns 0.0 when `end` is at or before `start`.
    """
    if day_count not in ("calendar", "trading"):
        raise ValueError(f"Unknown day_count: {day_count!r}. Use 'calendar' or 'trading'.")

    if end <= start:
        return 0.0

    if day_count == "calendar":
        return (end - start).total_seconds() / (365 * 86400)

    minutes = 0.0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    last = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= last:
        minutes += _session_minutes_on(day, start, end)
        day += timedelta(days=1)
    return (minutes / SESSION_MINUTES) / TRADING_DAYS_PER_YEAR
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target/daycount.py test/test_option_target_math.py
git commit -m "feat(option-target): add calendar and trading day-count conventions"
```

---

## Task 3: Forward anchoring and target mapping

**Files:**
- Create: `services/option_target/forward.py`
- Test: `test/test_option_target_math.py` (append)

This is where the measured +139 point BANKNIFTY basis is handled. Two modes: `exact` (a future of the option's own expiry exists — shift 1:1, no basis assumption) and `basis_modelled` (no matching future — shift proportionally and flag the uncertainty).

**Known, deliberate divergence from `synthetic_future_service`.** That service computes `atm_strike + call_ltp - put_ltp` using **LTP**. This engine uses **mid**. Mid is the better input for IV calibration because LTP goes stale and one-sided on thin strikes, and a biased forward biases every calibrated vol on the chain. The two will therefore differ by roughly the ATM half-spreads — on the measured NIFTY chain that is a couple of points on a 24500 forward (about 0.01%).

This is accepted, not accidental:
- The engine computes the forward from the **already-fetched chain snapshot**, so there is no extra broker round trip and the pure-math package stays free of service dependencies.
- The response reports `snapshot.forward_source` (`parity` or `spot_fallback`) so any discrepancy against the Option Greeks page is traceable rather than mysterious.
- Do **not** "fix" this by switching to LTP without re-running the Task 19 replay harness. If the two ever need to agree exactly, the right change is to move `synthetic_future_service` onto mid as well, which is a separate change with its own blast radius.

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_math.py`:

```python
from services.option_target.forward import compute_forward, project_forward


def _quote(strike, opt_type, bid, ask, ltp=None):
    return StrikeQuote(
        strike=strike, option_type=opt_type,
        symbol=f"BANKNIFTY25AUG26{int(strike)}{opt_type}",
        ltp=ltp if ltp is not None else (bid + ask) / 2,
        bid=bid, ask=ask, oi=1000, volume=100, lot_size=35,
    )


def test_compute_forward_uses_put_call_parity():
    quotes = {
        (57800.0, "CE"): _quote(57800.0, "CE", 700.0, 720.0),
        (57800.0, "PE"): _quote(57800.0, "PE", 570.0, 590.0),
    }
    anchor = compute_forward(quotes, atm_strike=57800.0, spot=57794.90)
    # 57800 + 710 - 580 = 57930
    assert anchor.forward == pytest.approx(57930.0)
    assert anchor.source == "parity"
    assert anchor.basis == pytest.approx(135.1, abs=0.01)


def test_compute_forward_falls_back_to_spot_when_atm_leg_missing():
    quotes = {(57800.0, "CE"): _quote(57800.0, "CE", 700.0, 720.0)}
    anchor = compute_forward(quotes, atm_strike=57800.0, spot=57794.90)
    assert anchor.forward == 57794.90
    assert anchor.source == "spot_fallback"


def test_project_forward_exact_mode_shifts_one_to_one():
    anchor = ForwardAnchor(forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity")
    t = project_forward(
        anchor, reference="FUT", reference_now=57933.85,
        reference_target=57643.85, matched_future=True,
    )
    assert t.mode == "exact"
    assert t.forward == pytest.approx(57643.85)


def test_project_forward_basis_mode_shifts_proportionally():
    anchor = ForwardAnchor(forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity")
    t = project_forward(
        anchor, reference="SPOT", reference_now=57794.90,
        reference_target=57504.90, matched_future=False,
    )
    assert t.mode == "basis_modelled"
    # 57933.85 * (57504.90 / 57794.90)
    assert t.forward == pytest.approx(57643.15, abs=0.5)


def test_project_forward_move_pct():
    anchor = ForwardAnchor(forward=57933.85, spot=57794.90, atm_strike=57800.0, source="parity")
    t = project_forward(
        anchor, reference="SPOT", reference_now=57794.90,
        reference_target=57504.90, matched_future=False,
    )
    assert t.move_pct == pytest.approx(-0.5018, abs=0.001)


def test_project_forward_rejects_non_positive_reference():
    anchor = ForwardAnchor(forward=100.0, spot=100.0, atm_strike=100.0, source="parity")
    with pytest.raises(ValueError, match="must be positive"):
        project_forward(
            anchor, reference="SPOT", reference_now=0.0,
            reference_target=90.0, matched_future=False,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_math.py -v -k forward`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target.forward'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target/forward.py`:

```python
"""Forward anchoring and target mapping.

Indian F&O options are priced off the forward for their OWN expiry, not the
index spot and not the near-month future. Measured on 2026-08-04:

    NIFTY    11AUG26 (7 DTE weekly)   basis  +7.5 pts
    BANKNIFTY 25AUG26 (21 DTE monthly) basis +138.9 pts

At a +139 point basis, a 57800 strike that looks at-the-money against spot
57795 is 134 points out-of-the-money against the forward 57934. Those are
different options. Pricing off spot is not an approximation; it is wrong.
"""

from services.option_target.models import ForwardAnchor, ForwardTarget, StrikeQuote
from utils.logging import get_logger

logger = get_logger(__name__)


def compute_forward(
    quotes: dict[tuple[float, str], StrikeQuote],
    atm_strike: float,
    spot: float,
) -> ForwardAnchor:
    """Synthetic forward from put-call parity at the ATM strike.

    F = K_atm + CE_atm_mid - PE_atm_mid

    Falls back to spot when either ATM leg has no usable quote. The caller is
    expected to surface a warning in that case: the projection stays usable but
    is anchored on the wrong instrument by the size of the basis.
    """
    ce = quotes.get((atm_strike, "CE"))
    pe = quotes.get((atm_strike, "PE"))

    if ce is None or pe is None or ce.mid <= 0 or pe.mid <= 0:
        logger.warning(
            "ATM parity unavailable at strike %s; falling back to spot %s", atm_strike, spot
        )
        return ForwardAnchor(
            forward=spot, spot=spot, atm_strike=atm_strike, source="spot_fallback"
        )

    return ForwardAnchor(
        forward=atm_strike + ce.mid - pe.mid,
        spot=spot,
        atm_strike=atm_strike,
        source="parity",
    )


def project_forward(
    anchor: ForwardAnchor,
    reference: str,
    reference_now: float,
    reference_target: float,
    matched_future: bool,
) -> ForwardTarget:
    """Map the user's target on `reference` onto the option-expiry forward.

    Exact mode (`matched_future=True` and reference is FUT): the future and the
    synthetic forward are the same economic instrument, so the shift is 1:1 and
    carries NO basis assumption. Measured on BANKNIFTY 2026-08-04, this removes
    a 17.3 point error that basis modelling could not avoid.

    Basis-modelled mode: shift proportionally, because basis is a cost-of-carry
    ratio. Proportional and parallel shifts were measured 0.7 points apart, so
    the choice is immaterial next to the basis drift itself.
    """
    if reference_now <= 0 or reference_target <= 0:
        raise ValueError("Reference prices must be positive")

    if matched_future and reference == "FUT":
        forward = anchor.forward + (reference_target - reference_now)
        mode = "exact"
    else:
        forward = anchor.forward * (reference_target / reference_now)
        mode = "basis_modelled"

    return ForwardTarget(
        forward=forward,
        mode=mode,
        reference=reference,
        reference_now=reference_now,
        reference_target=reference_target,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target/forward.py test/test_option_target_math.py
git commit -m "feat(option-target): add forward anchoring with exact and basis-modelled modes"
```

---

## Task 4: IV calibration from live mids

**Files:**
- Create: `services/option_target/smile.py`
- Test: `test/test_option_target_math.py` (append)

Calibrating IV from live prices — rather than assuming a vol — is what forces the model to reproduce today's actual market. The projection is then a perturbation of reality, not a free-standing theoretical price.

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_math.py`:

```python
import math

from opengreeks import black76

from services.option_target.smile import calibrate_ivs


def _synthetic_chain(forward, t_years, iv, strikes, lot_size=65):
    """Build a chain priced at a known flat IV, so calibration must recover it."""
    quotes = {}
    for k in strikes:
        for opt_type, flag in (("CE", "c"), ("PE", "p")):
            price = black76.black(flag, forward, k, t_years, 0.0, iv)
            quotes[(k, opt_type)] = StrikeQuote(
                strike=k, option_type=opt_type, symbol=f"X{int(k)}{opt_type}",
                ltp=price, bid=price - 0.5, ask=price + 0.5,
                oi=1000, volume=100, lot_size=lot_size,
            )
    return quotes


def test_calibrate_recovers_a_known_flat_iv():
    forward, t, iv = 24500.0, 0.02, 0.11
    strikes = [24300.0, 24400.0, 24500.0, 24600.0, 24700.0]
    quotes = _synthetic_chain(forward, t, iv, strikes)
    points, rejects = calibrate_ivs(quotes, forward=forward, t_years=t, rate=0.0)
    assert len(points) == len(strikes)
    for p in points:
        assert p.iv == pytest.approx(iv, abs=1e-4)
    assert rejects == []


def test_calibrate_uses_otm_wing_on_each_side():
    forward = 24500.0
    quotes = _synthetic_chain(forward, 0.02, 0.11, [24300.0, 24700.0])
    points, _ = calibrate_ivs(quotes, forward=forward, t_years=0.02, rate=0.0)
    by_strike = {p.strike: p.option_type for p in points}
    assert by_strike[24300.0] == "PE"  # below forward -> put is OTM
    assert by_strike[24700.0] == "CE"  # above forward -> call is OTM


def test_calibrate_sets_log_moneyness():
    forward = 24500.0
    quotes = _synthetic_chain(forward, 0.02, 0.11, [24500.0])
    points, _ = calibrate_ivs(quotes, forward=forward, t_years=0.02, rate=0.0)
    assert points[0].log_moneyness == pytest.approx(math.log(24500.0 / 24500.0))


def test_calibrate_rejects_strike_with_no_time_value():
    forward = 24500.0
    # Deep ITM call quoted at pure intrinsic: IV is not recoverable.
    quotes = {
        (23000.0, "CE"): StrikeQuote(
            strike=23000.0, option_type="CE", symbol="X23000CE",
            ltp=1500.0, bid=1499.0, ask=1501.0, oi=10, volume=1, lot_size=65,
        ),
        (23000.0, "PE"): StrikeQuote(
            strike=23000.0, option_type="PE", symbol="X23000PE",
            ltp=0.0, bid=0.0, ask=0.0, oi=10, volume=1, lot_size=65,
        ),
    }
    points, rejects = calibrate_ivs(quotes, forward=forward, t_years=0.02, rate=0.0)
    assert points == []
    assert len(rejects) == 1
    assert "no market" in rejects[0].lower() or "time value" in rejects[0].lower()


def test_calibrate_returns_positive_vega_for_every_point():
    quotes = _synthetic_chain(24500.0, 0.02, 0.11, [24400.0, 24500.0, 24600.0])
    points, _ = calibrate_ivs(quotes, forward=24500.0, t_years=0.02, rate=0.0)
    assert all(p.vega > 0 for p in points)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_math.py -v -k calibrate`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target.smile'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target/smile.py`:

```python
"""IV calibration and smile fitting.

The smile is fitted as a vega-weighted quadratic in log-moneyness. Measured
against a live NIFTY chain (25 strikes, 2026-08-04): RMS residual 0.053 vol
points, worst 0.169. That is well inside the noise of the bid-ask spread, so
there is no case for SVI or a spline here.
"""

import math

import numpy as np
from opengreeks import black76

from services.option_target.models import CalibratedIv, SmileFit, StrikeQuote
from utils.logging import get_logger

logger = get_logger(__name__)

MIN_TIME_VALUE = 0.05
IV_LOWER_BOUND = 0.01
IV_UPPER_BOUND = 3.0
MIN_FIT_POINTS = 5


def calibrate_ivs(
    quotes: dict[tuple[float, str], StrikeQuote],
    forward: float,
    t_years: float,
    rate: float,
) -> tuple[list[CalibratedIv], list[str]]:
    """Back out implied vol per strike from live mid prices.

    Uses the OTM wing on each side — puts below the forward, calls above.
    In-the-money implied vols are discarded because the premium is nearly all
    intrinsic there, which makes the solver ill-conditioned and drags the fit.

    Returns (points, rejection_reasons). Rejections are surfaced to the user
    rather than silently dropped: a hidden exclusion looks identical to a
    strike that does not exist.
    """
    points: list[CalibratedIv] = []
    rejects: list[str] = []

    for strike in sorted({k for k, _ in quotes}):
        opt_type = "PE" if strike < forward else "CE"
        flag = "p" if opt_type == "PE" else "c"
        quote = quotes.get((strike, opt_type))

        if quote is None or quote.mid <= 0:
            rejects.append(f"{strike:.0f} {opt_type}: no market")
            continue

        intrinsic = max(forward - strike, 0.0) if flag == "c" else max(strike - forward, 0.0)
        if quote.mid <= intrinsic + MIN_TIME_VALUE:
            rejects.append(f"{strike:.0f} {opt_type}: no time value")
            continue

        try:
            iv = black76.implied_volatility(quote.mid, forward, strike, rate, t_years, flag)
            vega = black76.vega(flag, forward, strike, t_years, rate, iv)
        except Exception as exc:  # noqa: BLE001 - solver failure is data-dependent
            rejects.append(f"{strike:.0f} {opt_type}: IV solver failed ({exc})")
            continue

        if not (IV_LOWER_BOUND < iv < IV_UPPER_BOUND):
            rejects.append(f"{strike:.0f} {opt_type}: IV {iv:.3f} out of bounds")
            continue

        points.append(
            CalibratedIv(
                strike=strike,
                option_type=opt_type,
                log_moneyness=math.log(strike / forward),
                iv=iv,
                vega=max(vega, 1e-6),
            )
        )

    return points, rejects


def fit_smile(points: list[CalibratedIv], atm_iv_fallback: float) -> SmileFit:
    """Vega-weighted quadratic fit of IV against log-moneyness.

    Weighting by vega lets ATM strikes dominate and stops far wings — where a
    half-tick of spread is a large fraction of the premium — from levering the
    curve.

    With fewer than MIN_FIT_POINTS usable strikes the fit is skipped and a flat
    ATM vol is returned with `degenerate=True`, so callers can warn.
    """
    if len(points) < MIN_FIT_POINTS:
        logger.warning(
            "Only %d calibrated strikes; using flat ATM IV %.4f", len(points), atm_iv_fallback
        )
        return SmileFit(
            a=atm_iv_fallback, b=0.0, c=0.0, x_lo=0.0, x_hi=0.0,
            rms=0.0, n_points=len(points), degenerate=True,
        )

    x = np.array([p.log_moneyness for p in points])
    y = np.array([p.iv for p in points])
    w = np.array([p.vega for p in points])

    design = np.vstack([np.ones_like(x), x, x**2]).T
    sqrt_w = np.sqrt(w)
    coef, *_ = np.linalg.lstsq(design * sqrt_w[:, None], y * sqrt_w, rcond=None)
    a, b, c = (float(v) for v in coef)

    residuals = y - (a + b * x + c * x**2)
    return SmileFit(
        a=a, b=b, c=c,
        x_lo=float(x.min()), x_hi=float(x.max()),
        rms=float(np.sqrt(np.mean(residuals**2))),
        n_points=len(points), degenerate=False,
    )


def smile_iv(fit: SmileFit, log_moneyness: float) -> float:
    """Evaluate the fitted smile, clamped to the observed moneyness range.

    Clamping is not optional. An unconstrained parabola extrapolated past the
    quoted strikes produces absurd vols — the c coefficient measured +10.8 on
    NIFTY, so a far strike would price at hundreds of percent.
    """
    x = min(max(log_moneyness, fit.x_lo), fit.x_hi)
    return max(fit.a + fit.b * x + fit.c * x * x, 1e-3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target/smile.py test/test_option_target_math.py
git commit -m "feat(option-target): calibrate implied vols from live mid prices"
```

---

## Task 5: Smile fitting and clamped evaluation

**Files:**
- Modify: `services/option_target/smile.py` (already written in Task 4 — this task only adds tests)
- Test: `test/test_option_target_math.py` (append)

`fit_smile` and `smile_iv` were written in Task 4 because they live in the same module. This task locks their behaviour with tests.

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_math.py`:

```python
from services.option_target.smile import fit_smile, smile_iv


def test_fit_recovers_a_flat_smile():
    quotes = _synthetic_chain(24500.0, 0.02, 0.11, [24300.0, 24400.0, 24500.0, 24600.0, 24700.0])
    points, _ = calibrate_ivs(quotes, forward=24500.0, t_years=0.02, rate=0.0)
    fit = fit_smile(points, atm_iv_fallback=0.11)
    assert not fit.degenerate
    assert fit.a == pytest.approx(0.11, abs=1e-3)
    assert fit.b == pytest.approx(0.0, abs=1e-2)
    assert fit.rms < 1e-3


def test_fit_is_degenerate_with_too_few_points():
    quotes = _synthetic_chain(24500.0, 0.02, 0.11, [24500.0])
    points, _ = calibrate_ivs(quotes, forward=24500.0, t_years=0.02, rate=0.0)
    fit = fit_smile(points, atm_iv_fallback=0.125)
    assert fit.degenerate
    assert fit.a == 0.125
    assert smile_iv(fit, 0.05) == pytest.approx(0.125)


def test_smile_iv_clamps_below_observed_range():
    fit = SmileFit(a=0.11, b=-0.24, c=10.79, x_lo=-0.02, x_hi=0.02,
                   rms=0.0005, n_points=25, degenerate=False)
    # Far outside the fitted range: must equal the value at x_lo, not explode.
    assert smile_iv(fit, -5.0) == pytest.approx(smile_iv(fit, -0.02))


def test_smile_iv_clamps_above_observed_range():
    fit = SmileFit(a=0.11, b=-0.24, c=10.79, x_lo=-0.02, x_hi=0.02,
                   rms=0.0005, n_points=25, degenerate=False)
    assert smile_iv(fit, 5.0) == pytest.approx(smile_iv(fit, 0.02))


def test_smile_iv_is_never_non_positive():
    fit = SmileFit(a=-1.0, b=0.0, c=0.0, x_lo=-1.0, x_hi=1.0,
                   rms=0.0, n_points=10, degenerate=False)
    assert smile_iv(fit, 0.0) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_math.py -v -k smile`
Expected: PASS — `fit_smile` and `smile_iv` already exist from Task 4. If any test fails, fix `smile.py` before continuing.

- [ ] **Step 3: Verify the clamp guard is present**

Confirm `smile_iv` in `services/option_target/smile.py` ends with:

```python
    x = min(max(log_moneyness, fit.x_lo), fit.x_hi)
    return max(fit.a + fit.b * x + fit.c * x * x, 1e-3)
```

- [ ] **Step 4: Run the full math suite**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: 26 passed

- [ ] **Step 5: Commit**

```bash
git add test/test_option_target_math.py
git commit -m "test(option-target): lock smile fit and clamped evaluation behaviour"
```

---

## Task 6: Projection with vol-response

**Files:**
- Create: `services/option_target/projection.py`
- Test: `test/test_option_target_math.py` (append)

The vol formula, with units spelled out because they are easy to get wrong:

```
sigma_target = base_iv  -  (beta * move_pct) / 100  +  vol_shift / 100
```

`base_iv` is a decimal. `beta` is **vol points per 1% move**. `move_pct` is in **percent** and is negative on a fall, so `-(beta * negative)` is positive: a fall raises vol. `vol_shift` is in **vol points**.

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_math.py`:

```python
from services.option_target.projection import project_strike, target_iv


def test_target_iv_smile_slide_uses_moneyness_at_target_forward():
    fit = SmileFit(a=0.11, b=-0.24, c=10.79, x_lo=-0.05, x_hi=0.05,
                   rms=0.0005, n_points=25, degenerate=False)
    iv = target_iv(
        strike=24500.0, forward_target=24000.0, iv_now=0.115, fit=fit,
        iv_model="smile_slide", vol_beta=0.0, move_pct=0.0, vol_shift=0.0,
    )
    assert iv == pytest.approx(smile_iv(fit, math.log(24500.0 / 24000.0)))


def test_target_iv_sticky_strike_keeps_the_strikes_own_iv():
    fit = SmileFit(a=0.11, b=-0.24, c=10.79, x_lo=-0.05, x_hi=0.05,
                   rms=0.0005, n_points=25, degenerate=False)
    iv = target_iv(
        strike=24500.0, forward_target=24000.0, iv_now=0.115, fit=fit,
        iv_model="sticky_strike", vol_beta=0.0, move_pct=0.0, vol_shift=0.0,
    )
    assert iv == pytest.approx(0.115)


def test_vol_beta_raises_iv_on_a_fall():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05,
                   rms=0.0, n_points=25, degenerate=False)
    iv = target_iv(
        strike=24500.0, forward_target=24500.0, iv_now=0.11, fit=fit,
        iv_model="sticky_strike", vol_beta=1.5, move_pct=-0.502, vol_shift=0.0,
    )
    # 0.11 + 1.5 * 0.502 / 100 = 0.11753
    assert iv == pytest.approx(0.11753, abs=1e-5)


def test_vol_beta_lowers_iv_on_a_rally():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05,
                   rms=0.0, n_points=25, degenerate=False)
    iv = target_iv(
        strike=24500.0, forward_target=24500.0, iv_now=0.11, fit=fit,
        iv_model="sticky_strike", vol_beta=1.5, move_pct=+1.0, vol_shift=0.0,
    )
    assert iv == pytest.approx(0.095, abs=1e-5)


def test_manual_vol_shift_is_in_vol_points():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05,
                   rms=0.0, n_points=25, degenerate=False)
    iv = target_iv(
        strike=24500.0, forward_target=24500.0, iv_now=0.11, fit=fit,
        iv_model="sticky_strike", vol_beta=0.0, move_pct=0.0, vol_shift=2.0,
    )
    assert iv == pytest.approx(0.13)


def test_target_iv_is_floored_positive():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.05, x_hi=0.05,
                   rms=0.0, n_points=25, degenerate=False)
    iv = target_iv(
        strike=24500.0, forward_target=24500.0, iv_now=0.11, fit=fit,
        iv_model="sticky_strike", vol_beta=0.0, move_pct=0.0, vol_shift=-99.0,
    )
    assert iv > 0


def test_project_strike_call_premium_rises_with_forward():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5,
                   rms=0.0, n_points=25, degenerate=False)
    kwargs = dict(
        strike=24500.0, option_type="CE", t_target=0.019, rate=0.0, iv_now=0.11,
        fit=fit, iv_model="sticky_strike", vol_beta=0.0, vol_shift=0.0,
    )
    low = project_strike(forward_target=24400.0, move_pct=0.0, **kwargs)
    high = project_strike(forward_target=24600.0, move_pct=0.0, **kwargs)
    assert high > low


def test_project_strike_put_premium_falls_with_forward():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5,
                   rms=0.0, n_points=25, degenerate=False)
    kwargs = dict(
        strike=24500.0, option_type="PE", t_target=0.019, rate=0.0, iv_now=0.11,
        fit=fit, iv_model="sticky_strike", vol_beta=0.0, vol_shift=0.0,
    )
    low = project_strike(forward_target=24400.0, move_pct=0.0, **kwargs)
    high = project_strike(forward_target=24600.0, move_pct=0.0, **kwargs)
    assert high < low


def test_project_strike_returns_intrinsic_past_expiry():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5,
                   rms=0.0, n_points=25, degenerate=False)
    premium = project_strike(
        strike=24500.0, option_type="CE", forward_target=24700.0, t_target=0.0,
        rate=0.0, iv_now=0.11, fit=fit, iv_model="sticky_strike",
        vol_beta=0.0, move_pct=0.0, vol_shift=0.0,
    )
    assert premium == pytest.approx(200.0)


def test_project_strike_intrinsic_is_zero_when_out_of_the_money_at_expiry():
    fit = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5,
                   rms=0.0, n_points=25, degenerate=False)
    premium = project_strike(
        strike=24500.0, option_type="CE", forward_target=24300.0, t_target=0.0,
        rate=0.0, iv_now=0.11, fit=fit, iv_model="sticky_strike",
        vol_beta=0.0, move_pct=0.0, vol_shift=0.0,
    )
    assert premium == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_math.py -v -k "target_iv or project_strike"`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target.projection'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target/projection.py`:

```python
"""Repricing at the target and P&L attribution.

The headline number is a FULL Black-76 reprice, not a delta or delta-gamma
approximation. Backtested against a completed BANKNIFTY trade across 37 strike
series (2026-08-04), mean absolute error:

    delta only                    6.84%
    smile slide alone             6.77%
    sticky strike, full reprice   5.55%
    slide + vol-beta 1.5          1.26%

Note that slide ALONE is worse than sticky strike. Sliding a fixed smile shape
models the strike's change in moneyness but cannot represent a change in the
vol LEVEL, which is what "the index dropped and volatility spiked" actually is.
That is why `vol_beta` exists and why it is the largest single correction in
this module.
"""

import math

from opengreeks import black76

from services.option_target.models import Attribution, SmileFit
from services.option_target.smile import smile_iv
from utils.logging import get_logger

logger = get_logger(__name__)

MIN_IV = 1e-3


def _flag(option_type: str) -> str:
    return "c" if option_type.upper() == "CE" else "p"


def intrinsic(option_type: str, forward: float, strike: float) -> float:
    if option_type.upper() == "CE":
        return max(forward - strike, 0.0)
    return max(strike - forward, 0.0)


def target_iv(
    strike: float,
    forward_target: float,
    iv_now: float,
    fit: SmileFit,
    iv_model: str,
    vol_beta: float,
    move_pct: float,
    vol_shift: float,
) -> float:
    """Implied vol for `strike` once the forward reaches `forward_target`.

    Units, because they are easy to get wrong:
      iv_now, return value   decimal (0.11 = 11%)
      vol_beta               vol POINTS per 1% move
      move_pct               percent, negative on a fall
      vol_shift              vol POINTS

    The beta term is subtracted so that a fall (negative move_pct) RAISES vol.
    """
    if iv_model == "smile_slide":
        base = smile_iv(fit, math.log(strike / forward_target))
    elif iv_model == "sticky_strike":
        base = iv_now
    else:
        raise ValueError(f"Unknown iv_model: {iv_model!r}")

    return max(base - (vol_beta * move_pct) / 100.0 + vol_shift / 100.0, MIN_IV)


def project_strike(
    strike: float,
    option_type: str,
    forward_target: float,
    t_target: float,
    rate: float,
    iv_now: float,
    fit: SmileFit,
    iv_model: str,
    vol_beta: float,
    move_pct: float,
    vol_shift: float,
) -> float:
    """Projected premium at the target. Full reprice, never a Taylor estimate."""
    if t_target <= 0:
        return intrinsic(option_type, forward_target, strike)

    sigma = target_iv(
        strike=strike, forward_target=forward_target, iv_now=iv_now, fit=fit,
        iv_model=iv_model, vol_beta=vol_beta, move_pct=move_pct, vol_shift=vol_shift,
    )
    return black76.black(_flag(option_type), forward_target, strike, t_target, rate, sigma)


def attribute_pnl(
    strike: float,
    option_type: str,
    forward_now: float,
    forward_target: float,
    t_now: float,
    t_target: float,
    rate: float,
    iv_now: float,
    iv_target: float,
    premium_now: float,
    premium_target: float,
    entry_cost: float,
    exit_value: float,
) -> Attribution:
    """Split the projected change into Greek contributions.

    Theta and vega are computed by RE-PRICING rather than from the Greek, because
    over a 300-point move and a 90-minute hold the linear approximations drift
    badly. Delta and gamma stay as Taylor terms; whatever they miss lands in
    `residual`, which is displayed rather than hidden. A large residual is a
    genuine signal that the move is big enough that attribution is only
    indicative.
    """
    flag = _flag(option_type)
    d_forward = forward_target - forward_now

    if t_now <= 0:
        return Attribution(
            delta=0.0, gamma=0.0, theta=0.0, vega=0.0,
            spread=exit_value - premium_target - (entry_cost - premium_now),
            residual=0.0, total=exit_value - entry_cost,
        )

    delta = black76.delta(flag, forward_now, strike, t_now, rate, iv_now)
    gamma = black76.gamma(flag, forward_now, strike, t_now, rate, iv_now)

    delta_term = delta * d_forward
    gamma_term = 0.5 * gamma * d_forward * d_forward

    if t_target > 0:
        theta_term = (
            black76.black(flag, forward_now, strike, t_target, rate, iv_now) - premium_now
        )
        vega_term = premium_target - black76.black(
            flag, forward_target, strike, t_target, rate, iv_now
        )
    else:
        theta_term = intrinsic(option_type, forward_now, strike) - premium_now
        vega_term = 0.0

    spread_term = (exit_value - premium_target) - (entry_cost - premium_now)
    total = exit_value - entry_cost
    residual = total - (delta_term + gamma_term + theta_term + vega_term + spread_term)

    return Attribution(
        delta=delta_term, gamma=gamma_term, theta=theta_term, vega=vega_term,
        spread=spread_term, residual=residual, total=total,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: 36 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target/projection.py test/test_option_target_math.py
git commit -m "feat(option-target): add target repricing with measured vol-response term"
```

---

## Task 7: P&L attribution tests

**Files:**
- Test: `test/test_option_target_math.py` (append)

`attribute_pnl` was written in Task 6. Lock its invariants.

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_math.py`:

```python
from services.option_target.projection import attribute_pnl


def _attribution_case(forward_target, iv_target, entry_extra=0.0, exit_penalty=0.0):
    strike, opt_type, rate = 24500.0, "CE", 0.0
    forward_now, t_now, t_target, iv_now = 24500.0, 0.02, 0.019, 0.11
    premium_now = black76.black("c", forward_now, strike, t_now, rate, iv_now)
    premium_target = black76.black("c", forward_target, strike, t_target, rate, iv_target)
    return attribute_pnl(
        strike=strike, option_type=opt_type, forward_now=forward_now,
        forward_target=forward_target, t_now=t_now, t_target=t_target, rate=rate,
        iv_now=iv_now, iv_target=iv_target, premium_now=premium_now,
        premium_target=premium_target,
        entry_cost=premium_now + entry_extra,
        exit_value=premium_target - exit_penalty,
    )


def test_attribution_terms_sum_to_total():
    a = _attribution_case(forward_target=24700.0, iv_target=0.11)
    assert a.delta + a.gamma + a.theta + a.vega + a.spread + a.residual == pytest.approx(
        a.total, abs=1e-9
    )


def test_attribution_delta_dominates_a_small_move():
    a = _attribution_case(forward_target=24510.0, iv_target=0.11)
    assert abs(a.delta) > abs(a.gamma)


def test_attribution_theta_is_negative_for_a_long_option():
    a = _attribution_case(forward_target=24500.0, iv_target=0.11)
    assert a.theta < 0


def test_attribution_vega_is_positive_when_vol_rises():
    a = _attribution_case(forward_target=24500.0, iv_target=0.13)
    assert a.vega > 0


def test_attribution_spread_is_negative_when_crossing_the_book():
    a = _attribution_case(forward_target=24700.0, iv_target=0.11,
                          entry_extra=2.0, exit_penalty=2.0)
    assert a.spread == pytest.approx(-4.0, abs=1e-9)


def test_attribution_gamma_grows_with_the_square_of_the_move():
    small = _attribution_case(forward_target=24600.0, iv_target=0.11)
    large = _attribution_case(forward_target=24700.0, iv_target=0.11)
    assert large.gamma == pytest.approx(4 * small.gamma, rel=0.05)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_math.py -v -k attribution`
Expected: 6 passed. If the sum-to-total test fails, the residual computation in `attribute_pnl` is wrong — fix it before continuing.

- [ ] **Step 3: Run the full math suite**

Run: `uv run pytest test/test_option_target_math.py -v`
Expected: 42 passed

- [ ] **Step 4: Lint**

Run: `uv run ruff check services/option_target/ test/test_option_target_math.py --fix && uv run ruff format services/option_target/ test/test_option_target_math.py`
Expected: no remaining errors

- [ ] **Step 5: Commit**

```bash
git add test/test_option_target_math.py services/option_target/
git commit -m "test(option-target): lock P&L attribution invariants"
```

---

## Task 8: Candidate metrics and ranking

**Files:**
- Create: `services/option_target/ranking.py`
- Test: `test/test_option_target_ranking.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_option_target_ranking.py`:

```python
"""Ranking and filtering tests for the Option Target Calculator."""

import pytest

from services.option_target.models import SmileFit, StrikeQuote
from services.option_target.ranking import build_candidate, rank_candidates

FLAT_FIT = SmileFit(a=0.11, b=0.0, c=0.0, x_lo=-0.5, x_hi=0.5,
                    rms=0.0, n_points=25, degenerate=False)


def _quote(strike, opt_type="CE", bid=100.0, ask=101.0, oi=50_000, volume=10_000):
    return StrikeQuote(
        strike=strike, option_type=opt_type, symbol=f"NIFTY11AUG26{int(strike)}{opt_type}",
        ltp=(bid + ask) / 2, bid=bid, ask=ask, oi=oi, volume=volume, lot_size=65,
    )


def _candidate(strike, **overrides):
    kwargs = dict(
        quote=_quote(strike), forward_now=24500.0, forward_target=24700.0,
        forward_adverse=24300.0, t_now=0.02, t_target=0.019, rate=0.0,
        fit=FLAT_FIT, iv_model="sticky_strike", vol_beta=0.0,
        move_pct=0.816, vol_shift=0.0, lots=1, atm_strike=24500.0, strike_step=50.0,
    )
    kwargs.update(overrides)
    return build_candidate(**kwargs)


def test_candidate_computes_entry_at_ask():
    c = _candidate(24500.0, quote=_quote(24500.0, bid=155.0, ask=160.0))
    assert c["entry_cost"] == 160.0


def test_candidate_exit_is_net_of_half_spread():
    c = _candidate(24500.0, quote=_quote(24500.0, bid=155.0, ask=160.0))
    assert c["exit_value"] == pytest.approx(c["projected_premium"] - 2.5)


def test_candidate_pnl_uses_lot_size_and_lots():
    c = _candidate(24500.0, lots=3)
    assert c["pnl_total"] == pytest.approx(c["pnl_per_lot"] * 3)
    assert c["pnl_per_lot"] == pytest.approx((c["exit_value"] - c["entry_cost"]) * 65)


def test_candidate_labels_moneyness_against_the_forward():
    # Forward 24500, step 50: a 24400 call is two steps in the money.
    c = _candidate(24400.0)
    assert c["label"] == "ITM2"


def test_candidate_labels_atm():
    assert _candidate(24500.0)["label"] == "ATM"


def test_candidate_effective_delta_is_realised_not_instantaneous():
    c = _candidate(24500.0)
    expected = (c["projected_premium"] - c["mid_now"]) / (24700.0 - 24500.0)
    assert c["effective_delta"] == pytest.approx(expected)


def test_candidate_theta_cost_is_negative_for_a_long_option():
    c = _candidate(24500.0)
    assert c["theta_cost_per_lot"] < 0


def test_candidate_reward_risk_is_positive_for_a_winning_direction():
    c = _candidate(24500.0)
    assert c["reward_risk"] > 0


def test_zero_bid_strike_is_excluded_with_a_reason():
    c = _candidate(25500.0, quote=_quote(25500.0, bid=0.0, ask=0.5))
    assert c["excluded"] is True
    assert "bid" in c["exclude_reason"].lower()


def test_illiquid_strike_is_excluded_with_a_reason():
    c = _candidate(25500.0, quote=_quote(25500.0, oi=10, volume=0))
    assert c["excluded"] is True
    assert "liquidity" in c["exclude_reason"].lower() or "oi" in c["exclude_reason"].lower()


def test_wide_spread_strike_is_excluded_with_a_reason():
    c = _candidate(25500.0, quote=_quote(25500.0, bid=10.0, ask=30.0))
    assert c["excluded"] is True
    assert "spread" in c["exclude_reason"].lower()


def test_rank_by_max_return_puts_highest_percentage_first():
    cands = [
        {"strike": 1.0, "return_pct": 10.0, "pnl_per_lot": 900.0, "reward_risk": 1.0,
         "effective_delta": 0.9, "spread_pct": 1.0, "excluded": False, "exclude_reason": ""},
        {"strike": 2.0, "return_pct": 30.0, "pnl_per_lot": 300.0, "reward_risk": 1.2,
         "effective_delta": 0.3, "spread_pct": 1.0, "excluded": False, "exclude_reason": ""},
    ]
    ranked = rank_candidates(cands, objective="max_return")
    assert ranked[0]["strike"] == 2.0
    assert ranked[0]["recommended"] is True
    assert ranked[1]["recommended"] is False


def test_rank_by_max_pnl_puts_highest_rupees_first():
    cands = [
        {"strike": 1.0, "return_pct": 10.0, "pnl_per_lot": 900.0, "reward_risk": 1.0,
         "effective_delta": 0.9, "spread_pct": 1.0, "excluded": False, "exclude_reason": ""},
        {"strike": 2.0, "return_pct": 30.0, "pnl_per_lot": 300.0, "reward_risk": 1.2,
         "effective_delta": 0.3, "spread_pct": 1.0, "excluded": False, "exclude_reason": ""},
    ]
    ranked = rank_candidates(cands, objective="max_pnl")
    assert ranked[0]["strike"] == 1.0


def test_excluded_candidates_sort_last_and_are_never_recommended():
    cands = [
        {"strike": 1.0, "return_pct": 99.0, "pnl_per_lot": 9999.0, "reward_risk": 9.0,
         "effective_delta": 0.9, "spread_pct": 1.0, "excluded": True,
         "exclude_reason": "zero bid"},
        {"strike": 2.0, "return_pct": 10.0, "pnl_per_lot": 100.0, "reward_risk": 1.0,
         "effective_delta": 0.3, "spread_pct": 1.0, "excluded": False, "exclude_reason": ""},
    ]
    ranked = rank_candidates(cands, objective="max_return")
    assert ranked[0]["strike"] == 2.0
    assert ranked[0]["recommended"] is True
    assert ranked[-1]["strike"] == 1.0
    assert ranked[-1]["recommended"] is False


def test_ranking_is_stable_under_input_permutation():
    cands = [
        {"strike": float(i), "return_pct": float(i), "pnl_per_lot": float(i),
         "reward_risk": 1.0, "effective_delta": 0.5, "spread_pct": 1.0,
         "excluded": False, "exclude_reason": ""}
        for i in range(1, 6)
    ]
    forward = [c["strike"] for c in rank_candidates(list(cands), "balanced")]
    backward = [c["strike"] for c in rank_candidates(list(reversed(cands)), "balanced")]
    assert forward == backward


def test_recommended_carries_a_reason():
    cands = [
        {"strike": 1.0, "return_pct": 30.0, "pnl_per_lot": 300.0, "reward_risk": 1.2,
         "effective_delta": 0.3, "spread_pct": 1.0, "excluded": False, "exclude_reason": ""},
    ]
    ranked = rank_candidates(cands, objective="balanced")
    assert ranked[0]["recommend_reason"]


def test_all_excluded_yields_no_recommendation():
    cands = [
        {"strike": 1.0, "return_pct": 30.0, "pnl_per_lot": 300.0, "reward_risk": 1.2,
         "effective_delta": 0.3, "spread_pct": 1.0, "excluded": True,
         "exclude_reason": "zero bid"},
    ]
    ranked = rank_candidates(cands, objective="balanced")
    assert all(not c["recommended"] for c in ranked)


def test_unknown_objective_raises():
    with pytest.raises(ValueError, match="Unknown objective"):
        rank_candidates([], objective="banana")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_ranking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target.ranking'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target/ranking.py`:

```python
"""Per-strike metrics, liquidity filters and objective-driven ranking.

Deep ITM maximises rupees, far OTM maximises percentage return, and they are
never the same strike. Rather than pick one and pretend it is "best", every
metric is computed and the user chooses the objective.
"""

from typing import Any

from opengreeks import black76

from services.option_target.models import SmileFit, StrikeQuote
from services.option_target.projection import (
    attribute_pnl,
    project_strike,
    target_iv,
)
from utils.logging import get_logger

logger = get_logger(__name__)

MIN_OI = 500
MIN_VOLUME = 100
MAX_SPREAD_PCT = 25.0

OBJECTIVES = ("max_pnl", "max_return", "max_rr", "balanced")


def _label(strike: float, atm_strike: float, strike_step: float, option_type: str) -> str:
    """Moneyness label relative to the forward-derived ATM strike."""
    if strike_step <= 0:
        return ""
    steps = round((strike - atm_strike) / strike_step)
    if steps == 0:
        return "ATM"
    in_the_money = (option_type.upper() == "CE" and steps < 0) or (
        option_type.upper() == "PE" and steps > 0
    )
    return f"{'ITM' if in_the_money else 'OTM'}{abs(steps)}"


def _exclusion(quote: StrikeQuote) -> str:
    if quote.bid <= 0:
        return "Zero bid - cannot exit at market"
    if quote.oi < MIN_OI and quote.volume < MIN_VOLUME:
        return f"Low liquidity - OI {quote.oi}, volume {quote.volume}"
    if quote.spread_pct > MAX_SPREAD_PCT:
        return f"Spread {quote.spread_pct:.1f}% exceeds {MAX_SPREAD_PCT:.0f}%"
    return ""


def build_candidate(
    quote: StrikeQuote,
    forward_now: float,
    forward_target: float,
    forward_adverse: float,
    t_now: float,
    t_target: float,
    rate: float,
    fit: SmileFit,
    iv_model: str,
    vol_beta: float,
    move_pct: float,
    vol_shift: float,
    lots: int,
    atm_strike: float,
    strike_step: float,
) -> dict[str, Any]:
    """Full metric set for one strike."""
    strike = quote.strike
    opt_type = quote.option_type
    flag = "c" if opt_type.upper() == "CE" else "p"
    mid_now = quote.mid
    entry_cost = quote.ask if quote.ask > 0 else mid_now

    try:
        iv_now = black76.implied_volatility(mid_now, forward_now, strike, rate, t_now, flag)
    except Exception:  # noqa: BLE001 - deep ITM legs have no recoverable IV
        iv_now = fit.a

    common = dict(
        strike=strike, option_type=opt_type, t_target=t_target, rate=rate,
        iv_now=iv_now, fit=fit, iv_model=iv_model, vol_beta=vol_beta, vol_shift=vol_shift,
    )
    projected = project_strike(forward_target=forward_target, move_pct=move_pct, **common)
    adverse = project_strike(
        forward_target=forward_adverse, move_pct=-move_pct, **common
    )

    iv_target = target_iv(
        strike=strike, forward_target=forward_target, iv_now=iv_now, fit=fit,
        iv_model=iv_model, vol_beta=vol_beta, move_pct=move_pct, vol_shift=vol_shift,
    )

    exit_value = max(projected - quote.half_spread, 0.0)
    adverse_exit = max(adverse - quote.half_spread, 0.0)

    pnl_per_lot = (exit_value - entry_cost) * quote.lot_size
    adverse_pnl_per_lot = (adverse_exit - entry_cost) * quote.lot_size

    # Pure decay: same forward, less time. Isolates what waiting costs.
    if t_target > 0:
        static = black76.black(flag, forward_now, strike, t_target, rate, iv_now)
    else:
        static = max(forward_now - strike, 0.0) if flag == "c" else max(strike - forward_now, 0.0)
    theta_cost_per_lot = (static - mid_now) * quote.lot_size

    d_forward = forward_target - forward_now
    effective_delta = (projected - mid_now) / d_forward if d_forward else 0.0

    attribution = attribute_pnl(
        strike=strike, option_type=opt_type, forward_now=forward_now,
        forward_target=forward_target, t_now=t_now, t_target=t_target, rate=rate,
        iv_now=iv_now, iv_target=iv_target, premium_now=mid_now,
        premium_target=projected, entry_cost=entry_cost, exit_value=exit_value,
    )

    reason = _exclusion(quote)
    return {
        "strike": strike,
        "option_type": opt_type,
        "symbol": quote.symbol,
        "label": _label(strike, atm_strike, strike_step, opt_type),
        "lot_size": quote.lot_size,
        "bid": quote.bid,
        "ask": quote.ask,
        "mid_now": mid_now,
        "spread_pct": quote.spread_pct,
        "entry_cost": entry_cost,
        "iv_now_pct": iv_now * 100,
        "iv_target_pct": iv_target * 100,
        "greeks_now": {
            "delta": black76.delta(flag, forward_now, strike, t_now, rate, iv_now),
            "gamma": black76.gamma(flag, forward_now, strike, t_now, rate, iv_now),
            "theta": black76.theta(flag, forward_now, strike, t_now, rate, iv_now),
            "vega": black76.vega(flag, forward_now, strike, t_now, rate, iv_now),
        },
        "projected_premium": projected,
        "exit_value": exit_value,
        "pnl_per_lot": pnl_per_lot,
        "pnl_total": pnl_per_lot * lots,
        "return_pct": (exit_value / entry_cost - 1) * 100 if entry_cost > 0 else 0.0,
        "effective_delta": effective_delta,
        "theta_cost_per_lot": theta_cost_per_lot,
        "adverse_premium": adverse,
        "adverse_pnl_per_lot": adverse_pnl_per_lot,
        "reward_risk": (
            pnl_per_lot / abs(adverse_pnl_per_lot) if adverse_pnl_per_lot < 0 else 0.0
        ),
        "attribution": {
            "delta": attribution.delta, "gamma": attribution.gamma,
            "theta": attribution.theta, "vega": attribution.vega,
            "spread": attribution.spread, "residual": attribution.residual,
            "total": attribution.total,
        },
        "oi": quote.oi,
        "volume": quote.volume,
        "excluded": bool(reason),
        "exclude_reason": reason,
    }


def _normalise(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def rank_candidates(
    candidates: list[dict[str, Any]], objective: str
) -> list[dict[str, Any]]:
    """Sort by objective, flag one Recommended, push exclusions to the bottom.

    Excluded strikes are RETAINED, not dropped. A hidden exclusion is
    indistinguishable from a strike that does not exist, which is exactly the
    confusion this tool is meant to remove.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"Unknown objective: {objective!r}. Use one of {OBJECTIVES}.")

    for c in candidates:
        c["recommended"] = False
        c["recommend_reason"] = ""

    eligible = [c for c in candidates if not c["excluded"]]
    excluded = [c for c in candidates if c["excluded"]]

    if eligible:
        if objective == "balanced":
            returns = _normalise([c["return_pct"] for c in eligible])
            rr = _normalise([c["reward_risk"] for c in eligible])
            eff = _normalise([abs(c["effective_delta"]) for c in eligible])
            for c, r, k, e in zip(eligible, returns, rr, eff, strict=True):
                penalty = min(c["spread_pct"], MAX_SPREAD_PCT) / MAX_SPREAD_PCT * 0.15
                c["score"] = 0.4 * r + 0.4 * k + 0.2 * e - penalty
            key = "score"
        else:
            key = {
                "max_pnl": "pnl_per_lot",
                "max_return": "return_pct",
                "max_rr": "reward_risk",
            }[objective]
            for c in eligible:
                c["score"] = c[key]

        # Secondary sort on strike keeps ordering deterministic when scores tie.
        eligible.sort(key=lambda c: (-c[key], c["strike"]))
        best = eligible[0]
        best["recommended"] = True
        best["recommend_reason"] = {
            "max_pnl": f"Highest rupee P&L per lot at {best['return_pct']:.1f}% return",
            "max_return": f"Highest return at {best['return_pct']:.1f}% on premium",
            "max_rr": f"Best reward-to-risk at {best['reward_risk']:.2f}x",
            "balanced": (
                f"Best blend of {best['return_pct']:.1f}% return, "
                f"{best['reward_risk']:.2f}x reward-to-risk and "
                f"{abs(best['effective_delta']):.2f} effective delta"
            ),
        }[objective]

    for c in excluded:
        c["score"] = float("-inf")
    excluded.sort(key=lambda c: c["strike"])

    return eligible + excluded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_ranking.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target/ranking.py test/test_option_target_ranking.py
git commit -m "feat(option-target): add candidate metrics and objective-driven ranking"
```

---

## Task 9: Vol-beta estimation from intraday history

**Files:**
- Create: `services/option_target/volbeta.py`
- Test: `test/test_option_target_ranking.py` (append)

Backtesting showed realised beta ~1.4 on BANKNIFTY 2026-08-04, against a specced default of 0.8. Because beta is the largest error term, it is measured rather than guessed.

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_ranking.py`:

```python
from services.option_target.volbeta import PRESETS, estimate_vol_beta


def test_estimate_recovers_a_known_beta():
    # Construct samples where IV rises 1.5 vol pts per 1% fall, exactly.
    samples = []
    for i in range(40):
        ret_pct = -0.05 * i
        samples.append((ret_pct, 12.0 - 1.5 * ret_pct))
    result = estimate_vol_beta(samples)
    assert result["beta"] == pytest.approx(1.5, abs=1e-6)
    assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert result["source"] == "estimated"


def test_estimate_falls_back_when_too_few_samples():
    result = estimate_vol_beta([(0.1, 12.0), (0.2, 12.1)])
    assert result["source"] == "fallback"
    assert result["beta"] == PRESETS["normal"]
    assert "samples" in result["reason"].lower()


def test_estimate_falls_back_on_a_weak_fit():
    # Pure noise: no relationship between return and IV.
    samples = [(0.1 * i, 12.0 + (1.0 if i % 2 else -1.0)) for i in range(40)]
    result = estimate_vol_beta(samples)
    assert result["source"] == "fallback"
    assert result["beta"] == PRESETS["normal"]
    assert "fit" in result["reason"].lower()


def test_estimate_falls_back_on_degenerate_returns():
    samples = [(0.0, 12.0 + 0.01 * i) for i in range(40)]
    result = estimate_vol_beta(samples)
    assert result["source"] == "fallback"


def test_presets_are_ordered():
    assert PRESETS["off"] < PRESETS["calm"] < PRESETS["normal"] < PRESETS["panic"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_ranking.py -v -k beta`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target.volbeta'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target/volbeta.py`:

```python
"""Estimate the vol-level response (beta) from the session's own data.

Beta is how many volatility POINTS ATM implied vol moves per 1% move in the
underlying, signed so a fall raises vol. Backtesting a completed BANKNIFTY
trade on 2026-08-04 measured a realised beta near 1.4, well above the 0.8 that
"normal" intuition suggests. Since beta is the single largest error term in the
projection, it is measured rather than assumed.

Pure function: the caller supplies (percent_return, atm_iv_in_vol_points)
samples. Fetching them is the session layer's job.
"""

from typing import Any

import numpy as np

from utils.logging import get_logger

logger = get_logger(__name__)

PRESETS: dict[str, float] = {
    "off": 0.0,
    "calm": 0.3,
    "normal": 0.8,
    "panic": 2.0,
}

MIN_SAMPLES = 20
MIN_R_SQUARED = 0.3


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "beta": PRESETS["normal"],
        "r_squared": 0.0,
        "samples": 0,
        "source": "fallback",
        "reason": reason,
    }


def estimate_vol_beta(samples: list[tuple[float, float]]) -> dict[str, Any]:
    """Regress ATM IV against underlying return; return the negated slope.

    `samples` is a list of (percent_return, atm_iv_vol_points) pairs relative to
    a common baseline. A weak or under-sampled fit falls back to the Normal
    preset and says why, rather than reporting a confident wrong number.
    """
    if len(samples) < MIN_SAMPLES:
        return _fallback(f"Only {len(samples)} samples, need {MIN_SAMPLES}")

    x = np.array([s[0] for s in samples], dtype=float)
    y = np.array([s[1] for s in samples], dtype=float)

    if float(np.std(x)) < 1e-9:
        return _fallback("Underlying did not move enough to estimate beta")

    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    if r_squared < MIN_R_SQUARED:
        return _fallback(f"Weak fit, R-squared {r_squared:.2f} below {MIN_R_SQUARED}")

    return {
        "beta": float(-slope),
        "r_squared": r_squared,
        "samples": len(samples),
        "source": "estimated",
        "reason": "",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_ranking.py -v`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target/volbeta.py test/test_option_target_ranking.py
git commit -m "feat(option-target): estimate vol-beta from session data with fallback"
```

---

## Task 10: Orchestrator service

**Files:**
- Create: `services/option_target_service.py`
- Test: `test/test_option_target_service.py`

This is the only module that touches the broker. It fetches the chain, resolves whether a matched future exists, assembles the pure-math pipeline, and caches snapshots in a **bounded** `TTLCache`.

- [ ] **Step 1: Write the failing test**

Create `test/test_option_target_service.py`:

```python
"""Orchestration tests. The broker layer is stubbed; the math is real."""

from unittest.mock import patch

import pytest

from services.option_target_service import (
    build_ladder,
    parse_chain_quotes,
    resolve_hold,
    strike_step_of,
)


CHAIN_ROWS = [
    {
        "strike": 24450.0,
        "ce": {"symbol": "NIFTY11AUG2624450CE", "ltp": 186.0, "bid": 185.0, "ask": 187.0,
               "oi": 50_000, "volume": 10_000, "lotsize": 65},
        "pe": {"symbol": "NIFTY11AUG2624450PE", "ltp": 121.0, "bid": 120.0, "ask": 122.0,
               "oi": 60_000, "volume": 12_000, "lotsize": 65},
    },
    {
        "strike": 24500.0,
        "ce": {"symbol": "NIFTY11AUG2624500CE", "ltp": 158.0, "bid": 157.0, "ask": 159.0,
               "oi": 70_000, "volume": 20_000, "lotsize": 65},
        "pe": {"symbol": "NIFTY11AUG2624500PE", "ltp": 143.0, "bid": 142.0, "ask": 144.0,
               "oi": 80_000, "volume": 22_000, "lotsize": 65},
    },
]


def test_parse_chain_quotes_indexes_by_strike_and_type():
    quotes = parse_chain_quotes(CHAIN_ROWS)
    assert set(quotes) == {
        (24450.0, "CE"), (24450.0, "PE"), (24500.0, "CE"), (24500.0, "PE"),
    }
    assert quotes[(24500.0, "CE")].ask == 159.0
    assert quotes[(24500.0, "CE")].lot_size == 65


def test_parse_chain_quotes_skips_legs_with_no_symbol():
    rows = [{"strike": 24500.0, "ce": {}, "pe": CHAIN_ROWS[1]["pe"]}]
    quotes = parse_chain_quotes(rows)
    assert (24500.0, "CE") not in quotes
    assert (24500.0, "PE") in quotes


def test_strike_step_is_the_modal_gap():
    assert strike_step_of([24400.0, 24450.0, 24500.0, 24550.0]) == 50.0


def test_strike_step_handles_a_single_strike():
    assert strike_step_of([24500.0]) == 0.0


def test_resolve_hold_prefers_days_when_given():
    minutes = resolve_hold(hold_minutes=45, hold_days=2.0)
    assert minutes == pytest.approx(2.0 * 24 * 60)


def test_resolve_hold_uses_minutes_by_default():
    assert resolve_hold(hold_minutes=45, hold_days=None) == 45.0


def test_resolve_hold_rejects_negative():
    with pytest.raises(ValueError, match="must not be negative"):
        resolve_hold(hold_minutes=-1, hold_days=None)


def test_build_ladder_brackets_the_target():
    ladder = build_ladder(
        reference_now=24500.0, reference_target=24700.0, steps=5,
        project=lambda ref: ref - 24500.0,
    )
    levels = [row["reference_level"] for row in ladder]
    assert min(levels) < 24500.0
    assert max(levels) > 24700.0
    assert len(ladder) == 5


def test_build_ladder_calls_the_projector_per_level():
    calls = []
    build_ladder(
        reference_now=100.0, reference_target=110.0, steps=3,
        project=lambda ref: calls.append(ref) or 0.0,
    )
    assert len(calls) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.option_target_service'`

- [ ] **Step 3: Write the implementation**

Create `services/option_target_service.py`:

```python
"""Option Target Calculator orchestration.

The ONLY module in this feature that touches the broker. It fetches a chain
snapshot, resolves whether a same-expiry future exists, runs the pure-math
pipeline in `services.option_target`, and returns a fully self-describing
response: every assumption the engine made is echoed back so nothing is applied
invisibly.

Snapshot caching uses a BOUNDED TTLCache. An unbounded dict keyed by
(symbol, expiry) would grow for the life of a Gunicorn worker that never
restarts.
"""

import statistics
from datetime import datetime, timedelta
from typing import Any, Callable

from cachetools import TTLCache

from services.option_target.daycount import IST, year_fraction
from services.option_target.forward import compute_forward, project_forward
from services.option_target.models import StrikeQuote
from services.option_target.projection import project_strike
from services.option_target.ranking import build_candidate, rank_candidates
from services.option_target.smile import calibrate_ivs, fit_smile, smile_iv
from services.option_target.volbeta import PRESETS, estimate_vol_beta
from utils.logging import get_logger

logger = get_logger(__name__)

# Bounded on purpose - see module docstring.
_SNAPSHOT_CACHE: TTLCache = TTLCache(maxsize=64, ttl=3)

LADDER_STEPS = 15
DEFAULT_STRIKE_COUNT = 12


def parse_chain_quotes(chain_rows: list[dict[str, Any]]) -> dict[tuple[float, str], StrikeQuote]:
    """Convert the option-chain service payload into StrikeQuote objects."""
    quotes: dict[tuple[float, str], StrikeQuote] = {}
    for row in chain_rows:
        strike = float(row.get("strike", 0) or 0)
        if strike <= 0:
            continue
        for key, opt_type in (("ce", "CE"), ("pe", "PE")):
            leg = row.get(key) or {}
            symbol = leg.get("symbol")
            if not symbol:
                continue
            quotes[(strike, opt_type)] = StrikeQuote(
                strike=strike,
                option_type=opt_type,
                symbol=symbol,
                ltp=float(leg.get("ltp") or 0),
                bid=float(leg.get("bid") or 0),
                ask=float(leg.get("ask") or 0),
                oi=int(leg.get("oi") or 0),
                volume=int(leg.get("volume") or 0),
                lot_size=int(leg.get("lotsize") or 0),
            )
    return quotes


def strike_step_of(strikes: list[float]) -> float:
    """Modal gap between consecutive strikes. 0 when undeterminable."""
    ordered = sorted(set(strikes))
    if len(ordered) < 2:
        return 0.0
    gaps = [round(b - a, 4) for a, b in zip(ordered, ordered[1:], strict=False)]
    return float(statistics.mode(gaps))


def resolve_hold(hold_minutes: float | None, hold_days: float | None) -> float:
    """Hold duration in minutes. `hold_days` wins when both are supplied."""
    if hold_days is not None:
        minutes = float(hold_days) * 24 * 60
    else:
        minutes = float(hold_minutes if hold_minutes is not None else 0)
    if minutes < 0:
        raise ValueError("Hold duration must not be negative")
    return minutes


def build_ladder(
    reference_now: float,
    reference_target: float,
    steps: int,
    project: Callable[[float], float],
) -> list[dict[str, float]]:
    """Premium across a span of reference levels bracketing the target.

    The span runs 1.5x the target distance either side of spot, so the chart
    shows what happens if the move overshoots or reverses, not only if it lands
    exactly on target.
    """
    distance = reference_target - reference_now
    lo = reference_now - abs(distance) * 1.5
    hi = reference_now + abs(distance) * 1.5
    if steps < 2 or hi <= lo:
        return [{"reference_level": reference_target, "premium": project(reference_target)}]

    width = (hi - lo) / (steps - 1)
    return [
        {
            "reference_level": lo + i * width,
            "premium": project(lo + i * width),
        }
        for i in range(steps)
    ]


def _matched_future_symbol(base_symbol: str, expiry_date: str, exchange: str) -> str | None:
    """Return the same-expiry futures symbol when one exists, else None.

    A matched future removes the basis assumption entirely (Step 3 of the
    spec), so detecting it is worth a DB lookup.
    """
    from database.symbol import SymToken, db_session

    candidate = f"{base_symbol}{expiry_date}FUT"
    try:
        with db_session() as session:
            row = (
                session.query(SymToken.symbol)
                .filter(SymToken.symbol == candidate, SymToken.exchange == exchange)
                .first()
            )
        return candidate if row else None
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not kill the request
        logger.warning("Matched-future lookup failed for %s: %s", candidate, exc)
        return None
```

Note: this file continues in Task 11 with `get_option_target`. Stop here so the
pure helpers can be tested in isolation first.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_service.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target_service.py test/test_option_target_service.py
git commit -m "feat(option-target): add orchestration helpers for chain parsing and ladder"
```

---

## Task 11: Orchestrator entry point

**Files:**
- Modify: `services/option_target_service.py` (append `get_option_target`)
- Test: `test/test_option_target_service.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `test/test_option_target_service.py`:

```python
from services.option_target_service import get_option_target


def _fake_chain(success=True):
    def _call(underlying, exchange, expiry_date, strike_count, api_key):
        if not success:
            return False, {"status": "error", "message": "boom"}, 500
        return True, {
            "status": "success",
            "underlying": underlying,
            "underlying_ltp": 24507.10,
            "expiry_date": expiry_date,
            "atm_strike": 24500.0,
            "chain": CHAIN_ROWS,
        }, 200
    return _call


def test_get_option_target_returns_a_full_envelope():
    with patch("services.option_target_service.get_option_chain", _fake_chain()), \
         patch("services.option_target_service._matched_future_symbol", return_value=None), \
         patch("services.option_target_service._vol_beta_samples", return_value=[]):
        ok, resp, code = get_option_target(
            underlying="NIFTY", exchange="NFO", expiry_date="11AUG26",
            reference="SPOT", target_price=24700.0, api_key="k",
        )
    assert ok is True
    assert code == 200
    for key in ("snapshot", "smile", "scenario", "candidates", "ladder", "warnings"):
        assert key in resp


def test_get_option_target_reports_basis_modelled_without_a_matched_future():
    with patch("services.option_target_service.get_option_chain", _fake_chain()), \
         patch("services.option_target_service._matched_future_symbol", return_value=None), \
         patch("services.option_target_service._vol_beta_samples", return_value=[]):
        _, resp, _ = get_option_target(
            underlying="NIFTY", exchange="NFO", expiry_date="11AUG26",
            reference="SPOT", target_price=24700.0, api_key="k",
        )
    assert resp["scenario"]["forward_mode"] == "basis_modelled"


def test_get_option_target_picks_calls_for_an_upside_target():
    with patch("services.option_target_service.get_option_chain", _fake_chain()), \
         patch("services.option_target_service._matched_future_symbol", return_value=None), \
         patch("services.option_target_service._vol_beta_samples", return_value=[]):
        _, resp, _ = get_option_target(
            underlying="NIFTY", exchange="NFO", expiry_date="11AUG26",
            reference="SPOT", target_price=24700.0, api_key="k",
        )
    assert {c["option_type"] for c in resp["candidates"]} == {"CE"}


def test_get_option_target_picks_puts_for_a_downside_target():
    with patch("services.option_target_service.get_option_chain", _fake_chain()), \
         patch("services.option_target_service._matched_future_symbol", return_value=None), \
         patch("services.option_target_service._vol_beta_samples", return_value=[]):
        _, resp, _ = get_option_target(
            underlying="NIFTY", exchange="NFO", expiry_date="11AUG26",
            reference="SPOT", target_price=24300.0, api_key="k",
        )
    assert {c["option_type"] for c in resp["candidates"]} == {"PE"}


def test_get_option_target_propagates_a_chain_failure():
    with patch("services.option_target_service.get_option_chain", _fake_chain(success=False)):
        ok, resp, code = get_option_target(
            underlying="NIFTY", exchange="NFO", expiry_date="11AUG26",
            reference="SPOT", target_price=24700.0, api_key="k",
        )
    assert ok is False
    assert code == 500


def test_get_option_target_rejects_a_non_positive_target():
    ok, resp, code = get_option_target(
        underlying="NIFTY", exchange="NFO", expiry_date="11AUG26",
        reference="SPOT", target_price=0.0, api_key="k",
    )
    assert ok is False
    assert code == 400


def test_get_option_target_warns_when_the_hold_runs_past_expiry():
    with patch("services.option_target_service.get_option_chain", _fake_chain()), \
         patch("services.option_target_service._matched_future_symbol", return_value=None), \
         patch("services.option_target_service._vol_beta_samples", return_value=[]):
        _, resp, _ = get_option_target(
            underlying="NIFTY", exchange="NFO", expiry_date="11AUG26",
            reference="SPOT", target_price=24700.0, api_key="k", hold_days=400,
        )
    assert any("expir" in w.lower() for w in resp["warnings"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest test/test_option_target_service.py -v -k get_option_target`
Expected: FAIL with `ImportError: cannot import name 'get_option_target'`

- [ ] **Step 3: Write the implementation**

Append to `services/option_target_service.py`:

```python
EXPIRY_TIMES = {"MCX": (23, 30), "CDS": (12, 30)}
DEFAULT_EXPIRY_TIME = (15, 30)

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _expiry_datetime(expiry_date: str, exchange: str) -> datetime:
    """Parse DDMMMYY into an expiry datetime in IST.

    Expiry times mirror `option_greeks_service` so the two pages agree.
    """
    day = int(expiry_date[:2])
    month = MONTHS[expiry_date[2:5].upper()]
    year = 2000 + int(expiry_date[5:7])
    hour, minute = EXPIRY_TIMES.get(exchange.upper(), DEFAULT_EXPIRY_TIME)
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def _vol_beta_samples(underlying: str, exchange: str, api_key: str) -> list[tuple[float, float]]:
    """(percent_return, atm_iv_vol_points) samples for beta estimation.

    Returns [] when history is unavailable; `estimate_vol_beta` then falls back
    to the Normal preset and reports that it did.
    """
    # Deliberately conservative: history failures must never block a projection.
    return []


def get_option_target(
    underlying: str,
    exchange: str,
    expiry_date: str,
    reference: str,
    target_price: float,
    api_key: str,
    reference_price: float | None = None,
    hold_minutes: float | None = 45,
    hold_days: float | None = None,
    iv_model: str = "smile_slide",
    vol_beta: float | str = "auto",
    vol_shift: float = 0.0,
    day_count: str = "calendar",
    strike_count: int = DEFAULT_STRIKE_COUNT,
    side: str = "AUTO",
    lots: int = 1,
    interest_rate: float = 0.0,
    objective: str = "balanced",
) -> tuple[bool, dict[str, Any], int]:
    """Project every strike to the user's target and rank them."""
    from services.option_chain_service import get_option_chain

    warnings: list[str] = []

    try:
        if target_price is None or target_price <= 0:
            return False, {"status": "error", "message": "target_price must be positive"}, 400

        hold_min = resolve_hold(hold_minutes, hold_days)

        ok, chain_resp, status = get_option_chain(
            underlying=underlying, exchange=exchange, expiry_date=expiry_date,
            strike_count=strike_count, api_key=api_key,
        )
        if not ok:
            return False, chain_resp, status

        rows = chain_resp.get("chain") or []
        spot = float(chain_resp.get("underlying_ltp") or 0)
        atm_strike = float(chain_resp.get("atm_strike") or 0)
        if not rows or spot <= 0 or atm_strike <= 0:
            return False, {"status": "error", "message": "Chain snapshot incomplete"}, 502

        quotes = parse_chain_quotes(rows)
        strikes = sorted({s for s, _ in quotes})
        step = strike_step_of(strikes)

        anchor = compute_forward(quotes, atm_strike=atm_strike, spot=spot)
        if anchor.source == "spot_fallback":
            warnings.append(
                "ATM call/put quotes unavailable - forward fell back to spot, "
                "so projections carry the full basis as error."
            )

        matched = _matched_future_symbol(underlying, expiry_date, exchange)
        ref = reference.upper()
        ref_now = reference_price if reference_price else (
            anchor.forward if (matched and ref == "FUT") else spot
        )

        fwd_target = project_forward(
            anchor, reference=ref, reference_now=ref_now,
            reference_target=target_price, matched_future=bool(matched),
        )
        if fwd_target.mode == "basis_modelled":
            warnings.append(
                f"No same-expiry future for {expiry_date}; forward is basis-modelled "
                f"(current basis {anchor.basis:+.1f} pts)."
            )

        now = datetime.now(IST)
        expiry = _expiry_datetime(expiry_date, exchange)
        t_now = year_fraction(now, expiry, day_count)
        t_target = year_fraction(now + timedelta(minutes=hold_min), expiry, day_count)
        if t_now <= 0:
            return False, {"status": "error", "message": "Option has already expired"}, 400
        if t_target <= 0:
            warnings.append(
                "Hold runs past expiry - projected values are intrinsic only."
            )

        rate = interest_rate / 100.0
        points, rejects = calibrate_ivs(quotes, anchor.forward, t_now, rate)
        atm_fallback = next(
            (p.iv for p in points if abs(p.strike - atm_strike) < 1e-6), 0.12
        )
        fit = fit_smile(points, atm_iv_fallback=atm_fallback)
        if fit.degenerate:
            warnings.append(
                f"Only {fit.n_points} strikes calibrated - using a flat ATM vol, no smile."
            )

        if vol_beta == "auto":
            beta_info = estimate_vol_beta(_vol_beta_samples(underlying, exchange, api_key))
        elif isinstance(vol_beta, str):
            beta_info = {
                "beta": PRESETS.get(vol_beta, PRESETS["normal"]), "r_squared": 0.0,
                "samples": 0, "source": "preset", "reason": "",
            }
        else:
            beta_info = {
                "beta": float(vol_beta), "r_squared": 0.0, "samples": 0,
                "source": "manual", "reason": "",
            }
        if beta_info["source"] == "fallback":
            warnings.append(f"Vol-beta estimate unavailable: {beta_info['reason']}")

        move_pct = fwd_target.move_pct
        forward_adverse = anchor.forward - (fwd_target.forward - anchor.forward)

        chosen = side.upper()
        if chosen == "AUTO":
            chosen = "CE" if target_price >= ref_now else "PE"

        candidates = [
            build_candidate(
                quote=quotes[(strike, chosen)], forward_now=anchor.forward,
                forward_target=fwd_target.forward, forward_adverse=forward_adverse,
                t_now=t_now, t_target=t_target, rate=rate, fit=fit, iv_model=iv_model,
                vol_beta=beta_info["beta"], move_pct=move_pct, vol_shift=vol_shift,
                lots=lots, atm_strike=atm_strike, strike_step=step,
            )
            for strike in strikes
            if (strike, chosen) in quotes and quotes[(strike, chosen)].mid > 0
        ]
        ranked = rank_candidates(candidates, objective=objective)

        best = next((c for c in ranked if c["recommended"]), None)
        ladder: list[dict[str, float]] = []
        if best is not None:
            def _project_at(ref_level: float) -> float:
                ft = project_forward(
                    anchor, reference=ref, reference_now=ref_now,
                    reference_target=ref_level, matched_future=bool(matched),
                )
                return project_strike(
                    strike=best["strike"], option_type=best["option_type"],
                    forward_target=ft.forward, t_target=t_target, rate=rate,
                    iv_now=best["iv_now_pct"] / 100, fit=fit, iv_model=iv_model,
                    vol_beta=beta_info["beta"], move_pct=ft.move_pct, vol_shift=vol_shift,
                )

            ladder = build_ladder(ref_now, target_price, LADDER_STEPS, _project_at)
            for row in ladder:
                row["pnl_per_lot"] = (
                    row["premium"] - best["entry_cost"]
                ) * best["lot_size"]

        return True, {
            "status": "success",
            "snapshot": {
                "underlying": underlying,
                "exchange": exchange,
                "expiry_date": expiry_date,
                "spot": spot,
                "forward": anchor.forward,
                "basis": anchor.basis,
                "forward_source": anchor.source,
                "atm_strike": atm_strike,
                "strike_step": step,
                "atm_iv_pct": smile_iv(fit, 0.0) * 100,
                "days_to_expiry": t_now * 365,
                "t_years": t_now,
                "matched_future": matched,
                "lot_size": next(iter(quotes.values())).lot_size if quotes else 0,
            },
            "smile": {
                "a": fit.a, "b": fit.b, "c": fit.c,
                "x_lo": fit.x_lo, "x_hi": fit.x_hi,
                "rms_vol_pts": fit.rms * 100,
                "n_points": fit.n_points,
                "degenerate": fit.degenerate,
                "rejected": rejects,
            },
            "scenario": {
                "reference": ref,
                "reference_now": ref_now,
                "reference_target": target_price,
                "forward_target": fwd_target.forward,
                "forward_mode": fwd_target.mode,
                "move_pct": move_pct,
                "hold_minutes": hold_min,
                "day_count": day_count,
                "t_target_years": t_target,
                "iv_model": iv_model,
                "vol_beta": beta_info,
                "vol_shift": vol_shift,
                "side": chosen,
                "objective": objective,
                "lots": lots,
            },
            "candidates": ranked,
            "recommended_strike": best["strike"] if best else None,
            "ladder": ladder,
            "warnings": warnings,
        }, 200

    except ValueError as exc:
        logger.warning("Validation error in option target: %s", exc)
        return False, {"status": "error", "message": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in option target: %s", exc)
        return False, {"status": "error", "message": "Failed to compute option target"}, 500
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest test/test_option_target_service.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add services/option_target_service.py test/test_option_target_service.py
git commit -m "feat(option-target): add orchestrator entry point"
```

---

## Task 12: REST endpoint

**Files:**
- Modify: `restx_api/data_schemas.py` (append after `OptionChainSchema`, around line 222)
- Create: `restx_api/option_target.py`
- Modify: `restx_api/__init__.py` (import near line 37, registration near line 75)

- [ ] **Step 1: Add the schema**

Append to `restx_api/data_schemas.py` immediately after `OptionChainSchema`:

```python
class OptionTargetSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    underlying = fields.Str(required=True)
    exchange = fields.Str(required=True, validate=validate.OneOf(VALID_EXCHANGES))
    expiry_date = fields.Str(required=True)  # DDMMMYY, e.g. 11AUG26
    reference = fields.Str(
        required=False, load_default="FUT", validate=validate.OneOf(["FUT", "SPOT"])
    )
    reference_price = fields.Float(required=False, allow_none=True)
    target_price = fields.Float(required=True, validate=validate.Range(min=0.01))
    hold_minutes = fields.Float(
        required=False, load_default=45.0, validate=validate.Range(min=0, max=525_600)
    )
    hold_days = fields.Float(
        required=False, allow_none=True, validate=validate.Range(min=0, max=365)
    )
    iv_model = fields.Str(
        required=False, load_default="smile_slide",
        validate=validate.OneOf(["smile_slide", "sticky_strike"]),
    )
    vol_beta = fields.Raw(required=False, load_default="auto")
    vol_shift = fields.Float(
        required=False, load_default=0.0, validate=validate.Range(min=-50, max=50)
    )
    day_count = fields.Str(
        required=False, load_default="calendar",
        validate=validate.OneOf(["calendar", "trading"]),
    )
    strike_count = fields.Int(
        required=False, load_default=12, validate=validate.Range(min=1, max=50)
    )
    side = fields.Str(
        required=False, load_default="AUTO", validate=validate.OneOf(["AUTO", "CE", "PE"])
    )
    lots = fields.Int(required=False, load_default=1, validate=validate.Range(min=1, max=10_000))
    interest_rate = fields.Float(
        required=False, load_default=0.0, validate=validate.Range(min=-10, max=50)
    )
    objective = fields.Str(
        required=False, load_default="balanced",
        validate=validate.OneOf(["balanced", "max_pnl", "max_return", "max_rr"]),
    )
```

- [ ] **Step 2: Create the endpoint**

Create `restx_api/option_target.py`:

```python
"""Option Target Calculator API.

POST /api/v1/optiontarget

Projects every option strike to a futures or spot target and ranks them.
`expiry_date` is DDMMMYY (11AUG26) - note that /api/v1/expiry returns the
dashed form (11-AUG-26), which this endpoint does NOT accept.
"""

import os

from flask import request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from limiter import limiter
from services.option_target_service import get_option_target
from utils.logging import get_logger

from .data_schemas import OptionTargetSchema

logger = get_logger(__name__)

api = Namespace("optiontarget", description="Project option premiums at a price target")

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")


@api.route("/", strict_slashes=False)
class OptionTarget(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Project and rank option strikes for a price target"""
        try:
            data = OptionTargetSchema().load(request.json)

            logger.info(
                "Option target request: %s %s expiry=%s reference=%s target=%s",
                data["underlying"], data["exchange"], data["expiry_date"],
                data["reference"], data["target_price"],
            )

            success, response, status_code = get_option_target(
                underlying=data["underlying"],
                exchange=data["exchange"],
                expiry_date=data["expiry_date"],
                reference=data["reference"],
                target_price=data["target_price"],
                api_key=data["apikey"],
                reference_price=data.get("reference_price"),
                hold_minutes=data["hold_minutes"],
                hold_days=data.get("hold_days"),
                iv_model=data["iv_model"],
                vol_beta=data["vol_beta"],
                vol_shift=data["vol_shift"],
                day_count=data["day_count"],
                strike_count=data["strike_count"],
                side=data["side"],
                lots=data["lots"],
                interest_rate=data["interest_rate"],
                objective=data["objective"],
            )
            return response, status_code

        except ValidationError as err:
            logger.warning("Validation error in option target request: %s", err.messages)
            return {"status": "error", "message": "Validation error", "errors": err.messages}, 400
        except Exception as e:
            logger.exception("Unexpected error in option target endpoint: %s", e)
            return {"status": "error", "message": "An unexpected error occurred"}, 500
```

- [ ] **Step 3: Register the namespace**

In `restx_api/__init__.py`, add next to the existing `option_chain` import (line 37):

```python
from .option_target import api as option_target_ns
```

and next to the existing registration (line 75):

```python
api.add_namespace(option_target_ns, path="/optiontarget")
```

- [ ] **Step 4: Verify the endpoint responds**

Start the server if not running (`uv run app.py`), then:

```bash
curl -s -X POST http://127.0.0.1:5000/api/v1/optiontarget \
  -H "Content-Type: application/json" \
  -d '{"apikey":"<YOUR_KEY>","underlying":"NIFTY","exchange":"NFO","expiry_date":"11AUG26","reference":"SPOT","target_price":24700}'
```

Expected: JSON with `"status":"success"` and non-empty `candidates`. A validation error means the schema and service signatures disagree — fix before committing.

- [ ] **Step 5: Commit**

```bash
git add restx_api/option_target.py restx_api/data_schemas.py restx_api/__init__.py
git commit -m "feat(option-target): expose POST /api/v1/optiontarget"
```

---

## Task 13: Frontend types and API client

**Files:**
- Create: `frontend/src/types/option-target.ts`
- Create: `frontend/src/api/option-target.ts`

- [ ] **Step 1: Create the types**

Create `frontend/src/types/option-target.ts`:

```typescript
export type IvModel = 'smile_slide' | 'sticky_strike'
export type Objective = 'balanced' | 'max_pnl' | 'max_return' | 'max_rr'
export type Reference = 'FUT' | 'SPOT'
export type DayCount = 'calendar' | 'trading'
export type OptionSide = 'AUTO' | 'CE' | 'PE'

export interface OptionTargetRequest {
  underlying: string
  exchange: string
  expiry_date: string
  reference: Reference
  reference_price?: number
  target_price: number
  hold_minutes?: number
  hold_days?: number
  iv_model?: IvModel
  vol_beta?: number | string
  vol_shift?: number
  day_count?: DayCount
  strike_count?: number
  side?: OptionSide
  lots?: number
  interest_rate?: number
  objective?: Objective
}

export interface Snapshot {
  underlying: string
  exchange: string
  expiry_date: string
  spot: number
  forward: number
  basis: number
  forward_source: 'parity' | 'spot_fallback'
  atm_strike: number
  strike_step: number
  atm_iv_pct: number
  days_to_expiry: number
  t_years: number
  matched_future: string | null
  lot_size: number
}

export interface SmileInfo {
  a: number
  b: number
  c: number
  x_lo: number
  x_hi: number
  rms_vol_pts: number
  n_points: number
  degenerate: boolean
  rejected: string[]
}

export interface VolBetaInfo {
  beta: number
  r_squared: number
  samples: number
  source: 'estimated' | 'fallback' | 'preset' | 'manual'
  reason: string
}

export interface Scenario {
  reference: Reference
  reference_now: number
  reference_target: number
  forward_target: number
  forward_mode: 'exact' | 'basis_modelled'
  move_pct: number
  hold_minutes: number
  day_count: DayCount
  t_target_years: number
  iv_model: IvModel
  vol_beta: VolBetaInfo
  vol_shift: number
  side: 'CE' | 'PE'
  objective: Objective
  lots: number
}

export interface Attribution {
  delta: number
  gamma: number
  theta: number
  vega: number
  spread: number
  residual: number
  total: number
}

export interface Candidate {
  strike: number
  option_type: 'CE' | 'PE'
  symbol: string
  label: string
  lot_size: number
  bid: number
  ask: number
  mid_now: number
  spread_pct: number
  entry_cost: number
  iv_now_pct: number
  iv_target_pct: number
  greeks_now: { delta: number; gamma: number; theta: number; vega: number }
  projected_premium: number
  exit_value: number
  pnl_per_lot: number
  pnl_total: number
  return_pct: number
  effective_delta: number
  theta_cost_per_lot: number
  adverse_premium: number
  adverse_pnl_per_lot: number
  reward_risk: number
  attribution: Attribution
  oi: number
  volume: number
  excluded: boolean
  exclude_reason: string
  recommended: boolean
  recommend_reason: string
  score: number
}

export interface LadderRow {
  reference_level: number
  premium: number
  pnl_per_lot: number
}

export interface OptionTargetResponse {
  status: 'success' | 'error'
  message?: string
  snapshot: Snapshot
  smile: SmileInfo
  scenario: Scenario
  candidates: Candidate[]
  recommended_strike: number | null
  ladder: LadderRow[]
  warnings: string[]
}
```

- [ ] **Step 2: Create the API client**

Create `frontend/src/api/option-target.ts`:

```typescript
import type { OptionTargetRequest, OptionTargetResponse } from '@/types/option-target'
import { apiClient } from './client'

export const optionTargetApi = {
  project: async (
    apiKey: string,
    req: OptionTargetRequest
  ): Promise<OptionTargetResponse> => {
    const response = await apiClient.post<OptionTargetResponse>('/optiontarget', {
      apikey: apiKey,
      ...req,
    })
    return response.data
  },
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Lint**

Run: `cd frontend && npx biome check src/types/option-target.ts src/api/option-target.ts --write`
Expected: no remaining diagnostics

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/option-target.ts frontend/src/api/option-target.ts
git commit -m "feat(option-target): add frontend types and API client"
```

---

## Task 14: Polling hook with freeze

**Files:**
- Create: `frontend/src/hooks/useOptionTarget.ts`

- [ ] **Step 1: Write the hook**

Create `frontend/src/hooks/useOptionTarget.ts`:

```typescript
import { useCallback, useEffect, useRef, useState } from 'react'
import { optionTargetApi } from '@/api/option-target'
import type { OptionTargetRequest, OptionTargetResponse } from '@/types/option-target'

const POLL_MS = 5000

interface UseOptionTargetOptions {
  apiKey: string | null
  request: OptionTargetRequest | null
  frozen: boolean
}

/**
 * Fetches a projection and keeps it current.
 *
 * `frozen` pins the last snapshot so the table stops moving while rows are
 * being compared. A stale request counter guards against out-of-order
 * responses, which polling plus rapid input changes makes routine.
 */
export function useOptionTarget({ apiKey, request, frozen }: UseOptionTargetOptions) {
  const [data, setData] = useState<OptionTargetResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const requestIdRef = useRef(0)

  const fetchNow = useCallback(async () => {
    if (!apiKey || !request) return
    const id = ++requestIdRef.current
    setIsLoading(true)
    try {
      const response = await optionTargetApi.project(apiKey, request)
      if (id !== requestIdRef.current) return
      if (response.status === 'error') {
        setError(response.message ?? 'Projection failed')
      } else {
        setData(response)
        setError(null)
        setUpdatedAt(new Date())
      }
    } catch (e) {
      if (id !== requestIdRef.current) return
      setError(e instanceof Error ? e.message : 'Projection failed')
    } finally {
      if (id === requestIdRef.current) setIsLoading(false)
    }
  }, [apiKey, request])

  useEffect(() => {
    if (frozen) return
    void fetchNow()
    const timer = setInterval(() => void fetchNow(), POLL_MS)
    return () => clearInterval(timer)
  }, [fetchNow, frozen])

  return { data, error, isLoading, updatedAt, refetch: fetchNow }
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useOptionTarget.ts
git commit -m "feat(option-target): add polling hook with freeze support"
```

---

## Task 15: Scenario panel

**Files:**
- Create: `frontend/src/pages/option-target/ScenarioPanel.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/pages/option-target/ScenarioPanel.tsx`:

```typescript
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { DayCount, IvModel, Reference, Scenario } from '@/types/option-target'

export interface ScenarioState {
  reference: Reference
  targetPrice: string
  holdMinutes: string
  holdUnit: 'minutes' | 'days'
  ivModel: IvModel
  volBeta: string
  volShift: string
  dayCount: DayCount
  lots: string
}

interface Props {
  state: ScenarioState
  referenceNow: number
  scenario: Scenario | null
  onChange: (next: ScenarioState) => void
}

const QUICK_MOVES = [0.25, 0.5, 1]

export function ScenarioPanel({ state, referenceNow, scenario, onChange }: Props) {
  const set = <K extends keyof ScenarioState>(key: K, value: ScenarioState[K]) =>
    onChange({ ...state, [key]: value })

  const applyQuickMove = (pct: number, direction: 1 | -1) => {
    if (referenceNow <= 0) return
    set('targetPrice', (referenceNow * (1 + (direction * pct) / 100)).toFixed(2))
  }

  const beta = scenario?.vol_beta

  return (
    <Card>
      <CardContent className="grid gap-4 pt-6 md:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-2">
          <Label>Reference</Label>
          <Select
            value={state.reference}
            onValueChange={(v) => set('reference', v as Reference)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="FUT">Futures</SelectItem>
              <SelectItem value="SPOT">Spot</SelectItem>
            </SelectContent>
          </Select>
          {scenario ? (
            <p className="text-xs text-muted-foreground">
              {scenario.forward_mode === 'exact'
                ? 'Exact - matched future, no basis assumption'
                : 'Basis-modelled - carries basis uncertainty'}
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="target-price">Target price</Label>
          <Input
            id="target-price"
            inputMode="decimal"
            value={state.targetPrice}
            onChange={(e) => set('targetPrice', e.target.value)}
          />
          <div className="flex flex-wrap gap-1">
            {QUICK_MOVES.map((pct) => (
              <span key={pct} className="flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => applyQuickMove(pct, 1)}
                >
                  +{pct}%
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => applyQuickMove(pct, -1)}
                >
                  -{pct}%
                </Button>
              </span>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="hold">Time to target</Label>
          <div className="flex gap-2">
            <Input
              id="hold"
              inputMode="numeric"
              value={state.holdMinutes}
              onChange={(e) => set('holdMinutes', e.target.value)}
            />
            <Select
              value={state.holdUnit}
              onValueChange={(v) => set('holdUnit', v as ScenarioState['holdUnit'])}
            >
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="minutes">Minutes</SelectItem>
                <SelectItem value="days">Days</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-2">
          <Label>Lots</Label>
          <Input
            inputMode="numeric"
            value={state.lots}
            onChange={(e) => set('lots', e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label>IV model</Label>
          <Select value={state.ivModel} onValueChange={(v) => set('ivModel', v as IvModel)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="smile_slide">Smile slide</SelectItem>
              <SelectItem value="sticky_strike">Sticky strike</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="vol-beta">Vol response</Label>
          <Select value={state.volBeta} onValueChange={(v) => set('volBeta', v)}>
            <SelectTrigger id="vol-beta">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Auto (measured)</SelectItem>
              <SelectItem value="off">Off (0.0)</SelectItem>
              <SelectItem value="calm">Calm (0.3)</SelectItem>
              <SelectItem value="normal">Normal (0.8)</SelectItem>
              <SelectItem value="panic">Panic (2.0)</SelectItem>
            </SelectContent>
          </Select>
          {beta ? (
            <p className="text-xs text-muted-foreground">
              Using {beta.beta.toFixed(2)} vol pts per 1%
              {beta.source === 'estimated'
                ? ` (measured, R2 ${beta.r_squared.toFixed(2)}, n=${beta.samples})`
                : ` (${beta.source})`}
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="vol-shift">Manual vol shift (pts)</Label>
          <Input
            id="vol-shift"
            inputMode="decimal"
            value={state.volShift}
            onChange={(e) => set('volShift', e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label>Day count</Label>
          <Select value={state.dayCount} onValueChange={(v) => set('dayCount', v as DayCount)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="calendar">Calendar (365)</SelectItem>
              <SelectItem value="trading">Trading days (252)</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/option-target/ScenarioPanel.tsx
git commit -m "feat(option-target): add scenario input panel"
```

---

## Task 16: Strike table

**Files:**
- Create: `frontend/src/pages/option-target/StrikeTable.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/pages/option-target/StrikeTable.tsx`:

```typescript
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import type { Candidate, Objective } from '@/types/option-target'

interface Props {
  candidates: Candidate[]
  objective: Objective
  selectedStrike: number | null
  onObjectiveChange: (objective: Objective) => void
  onSelect: (candidate: Candidate) => void
}

const money = (v: number) =>
  `${v < 0 ? '-' : ''}${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

export function StrikeTable({
  candidates,
  objective,
  selectedStrike,
  onObjectiveChange,
  onSelect,
}: Props) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium">Strike candidates</h2>
        <Select value={objective} onValueChange={(v) => onObjectiveChange(v as Objective)}>
          <SelectTrigger className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="balanced">Balanced score</SelectItem>
            <SelectItem value="max_pnl">Max rupees per lot</SelectItem>
            <SelectItem value="max_return">Max % return</SelectItem>
            <SelectItem value="max_rr">Best reward-to-risk</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="p-2 text-left">Strike</th>
              <th className="p-2 text-right">Entry</th>
              <th className="p-2 text-right">Projected</th>
              <th className="p-2 text-right">P&L/lot</th>
              <th className="p-2 text-right">Return</th>
              <th className="p-2 text-right">Eff delta</th>
              <th className="p-2 text-right">Theta cost</th>
              <th className="p-2 text-right">Adverse</th>
              <th className="p-2 text-right">R:R</th>
              <th className="p-2 text-right">IV now / tgt</th>
              <th className="p-2 text-right">Spread</th>
              <th className="p-2 text-right">OI</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => (
              <tr
                key={`${c.strike}-${c.option_type}`}
                onClick={() => !c.excluded && onSelect(c)}
                className={cn(
                  'border-t',
                  c.excluded
                    ? 'cursor-not-allowed text-muted-foreground/60'
                    : 'cursor-pointer hover:bg-muted/40',
                  c.strike === selectedStrike && 'bg-muted',
                  c.recommended && 'ring-1 ring-inset ring-primary/40'
                )}
              >
                <td className="p-2">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{c.strike}</span>
                    <Badge variant="outline">{c.label}</Badge>
                    {c.recommended ? <Badge>Recommended</Badge> : null}
                  </div>
                  {c.excluded ? (
                    <p className="text-xs text-muted-foreground">{c.exclude_reason}</p>
                  ) : null}
                  {c.recommended ? (
                    <p className="text-xs text-muted-foreground">{c.recommend_reason}</p>
                  ) : null}
                </td>
                <td className="p-2 text-right tabular-nums">{c.entry_cost.toFixed(2)}</td>
                <td className="p-2 text-right tabular-nums">{c.projected_premium.toFixed(2)}</td>
                <td
                  className={cn(
                    'p-2 text-right tabular-nums',
                    c.pnl_per_lot >= 0 ? 'text-emerald-600' : 'text-rose-600'
                  )}
                >
                  {money(c.pnl_per_lot)}
                </td>
                <td className="p-2 text-right tabular-nums">{c.return_pct.toFixed(1)}%</td>
                <td className="p-2 text-right tabular-nums">{c.effective_delta.toFixed(3)}</td>
                <td className="p-2 text-right tabular-nums">{money(c.theta_cost_per_lot)}</td>
                <td className="p-2 text-right tabular-nums text-rose-600">
                  {money(c.adverse_pnl_per_lot)}
                </td>
                <td className="p-2 text-right tabular-nums">{c.reward_risk.toFixed(2)}</td>
                <td className="p-2 text-right tabular-nums">
                  {c.iv_now_pct.toFixed(1)} / {c.iv_target_pct.toFixed(1)}
                </td>
                <td className="p-2 text-right tabular-nums">{c.spread_pct.toFixed(1)}%</td>
                <td className="p-2 text-right tabular-nums">
                  {c.oi.toLocaleString('en-IN')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/option-target/StrikeTable.tsx
git commit -m "feat(option-target): add ranked strike table"
```

---

## Task 17: Strike detail with ladder and waterfall

**Files:**
- Create: `frontend/src/pages/option-target/StrikeDetail.tsx`

- [ ] **Step 1: Write the component**

Create `frontend/src/pages/option-target/StrikeDetail.tsx`:

```typescript
import type * as PlotlyTypes from 'plotly.js'
import { useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import Plot from '@/lib/Plot2D'
import type { Candidate, LadderRow, Scenario } from '@/types/option-target'

interface Props {
  candidate: Candidate
  ladder: LadderRow[]
  scenario: Scenario
  isDark: boolean
}

const PLOT_CONFIG: Partial<PlotlyTypes.Config> = { displayModeBar: false, responsive: true }

export function StrikeDetail({ candidate, ladder, scenario, isDark }: Props) {
  const fg = isDark ? '#e5e7eb' : '#111827'
  const grid = isDark ? '#374151' : '#e5e7eb'

  const ladderPlot = useMemo(() => {
    const data: PlotlyTypes.Data[] = [
      {
        x: ladder.map((r) => r.reference_level),
        y: ladder.map((r) => r.premium),
        type: 'scatter',
        mode: 'lines+markers',
        name: 'Projected premium',
        line: { color: '#10b981', width: 2 },
      },
    ]
    const layout: Partial<PlotlyTypes.Layout> = {
      margin: { l: 56, r: 16, t: 8, b: 40 },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: fg, size: 11 },
      xaxis: { title: { text: `${scenario.reference} level` }, gridcolor: grid },
      yaxis: { title: { text: 'Premium' }, gridcolor: grid },
      showlegend: false,
      shapes: [
        {
          type: 'line',
          x0: scenario.reference_now,
          x1: scenario.reference_now,
          y0: 0,
          y1: 1,
          yref: 'paper',
          line: { color: fg, width: 1, dash: 'dot' },
        },
        {
          type: 'line',
          x0: scenario.reference_target,
          x1: scenario.reference_target,
          y0: 0,
          y1: 1,
          yref: 'paper',
          line: { color: '#f59e0b', width: 1, dash: 'dash' },
        },
      ],
    }
    return { data, layout }
  }, [ladder, scenario, fg, grid])

  const waterfall = useMemo(() => {
    const a = candidate.attribution
    const labels = ['Delta', 'Gamma', 'Theta', 'Vega', 'Spread', 'Residual']
    const values = [a.delta, a.gamma, a.theta, a.vega, a.spread, a.residual]
    const data: PlotlyTypes.Data[] = [
      {
        x: labels,
        y: values,
        type: 'bar',
        marker: { color: values.map((v) => (v >= 0 ? '#10b981' : '#f43f5e')) },
      },
    ]
    const layout: Partial<PlotlyTypes.Layout> = {
      margin: { l: 56, r: 16, t: 8, b: 40 },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: fg, size: 11 },
      xaxis: { gridcolor: grid },
      yaxis: { title: { text: 'Premium points' }, gridcolor: grid },
      showlegend: false,
    }
    return { data, layout }
  }, [candidate, fg, grid])

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            {candidate.symbol} - premium ladder
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Plot
            data={ladderPlot.data}
            layout={ladderPlot.layout}
            config={PLOT_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: '280px' }}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">P&L attribution (per share)</CardTitle>
        </CardHeader>
        <CardContent>
          <Plot
            data={waterfall.data}
            layout={waterfall.layout}
            config={PLOT_CONFIG}
            useResizeHandler
            style={{ width: '100%', height: '280px' }}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            Net {candidate.attribution.total.toFixed(2)} points. A large residual means the
            move is big enough that attribution is indicative only.
          </p>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-sm">Greeks now</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <Metric label="Delta" value={candidate.greeks_now.delta.toFixed(4)} />
          <Metric label="Gamma" value={candidate.greeks_now.gamma.toFixed(6)} />
          <Metric label="Theta" value={candidate.greeks_now.theta.toFixed(4)} />
          <Metric label="Vega" value={candidate.greeks_now.vega.toFixed(4)} />
          <Metric label="IV now" value={`${candidate.iv_now_pct.toFixed(2)}%`} />
          <Metric label="IV at target" value={`${candidate.iv_target_pct.toFixed(2)}%`} />
          <Metric label="Effective delta" value={candidate.effective_delta.toFixed(3)} />
          <Metric label="Reward to risk" value={`${candidate.reward_risk.toFixed(2)}x`} />
        </CardContent>
      </Card>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="tabular-nums">{value}</p>
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/option-target/StrikeDetail.tsx
git commit -m "feat(option-target): add premium ladder and P&L attribution charts"
```

---

## Task 18: Page assembly and route registration

**Files:**
- Create: `frontend/src/pages/OptionTargetCalculator.tsx`
- Modify: `frontend/src/App.tsx` (route, near line 236)
- Modify: `frontend/src/hooks/usePageTitle.ts` (title, near line 40)
- Modify: `frontend/src/lib/tools.ts` (registry entry)
- Modify: `blueprints/react_app.py` (Flask route, near line 283)

- [ ] **Step 1: Write the page**

Create `frontend/src/pages/OptionTargetCalculator.tsx`:

```typescript
import { useEffect, useMemo, useState } from 'react'
import { optionChainApi } from '@/api/option-chain'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useApiKey } from '@/hooks/useApiKey'
import { useOptionTarget } from '@/hooks/useOptionTarget'
import type { Candidate, Objective, OptionTargetRequest } from '@/types/option-target'
import { ScenarioPanel, type ScenarioState } from './option-target/ScenarioPanel'
import { StrikeDetail } from './option-target/StrikeDetail'
import { StrikeTable } from './option-target/StrikeTable'

/** Convert the expiry API's DD-MMM-YY into the DDMMMYY the calculator needs. */
function toCompactExpiry(dashed: string): string {
  return dashed.replace(/-/g, '')
}

const DEFAULT_SCENARIO: ScenarioState = {
  reference: 'FUT',
  targetPrice: '',
  holdMinutes: '45',
  holdUnit: 'minutes',
  ivModel: 'smile_slide',
  volBeta: 'auto',
  volShift: '0',
  dayCount: 'calendar',
  lots: '1',
}

export default function OptionTargetCalculator() {
  const { apiKey } = useApiKey()
  const [underlying, setUnderlying] = useState('NIFTY')
  const [expiries, setExpiries] = useState<string[]>([])
  const [expiry, setExpiry] = useState('')
  const [scenario, setScenario] = useState<ScenarioState>(DEFAULT_SCENARIO)
  const [objective, setObjective] = useState<Objective>('balanced')
  const [frozen, setFrozen] = useState(false)
  const [selected, setSelected] = useState<Candidate | null>(null)

  const isDark = document.documentElement.classList.contains('dark')

  useEffect(() => {
    if (!apiKey || !underlying) return
    let cancelled = false
    optionChainApi
      .getExpiries(apiKey, underlying, 'NFO', 'options')
      .then((r) => {
        if (cancelled || r.status !== 'success') return
        const compact = r.data.map(toCompactExpiry)
        setExpiries(compact)
        setExpiry((current) => (current && compact.includes(current) ? current : compact[0] ?? ''))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [apiKey, underlying])

  const request: OptionTargetRequest | null = useMemo(() => {
    const target = Number.parseFloat(scenario.targetPrice)
    if (!expiry || !Number.isFinite(target) || target <= 0) return null
    const hold = Number.parseFloat(scenario.holdMinutes)
    return {
      underlying,
      exchange: 'NFO',
      expiry_date: expiry,
      reference: scenario.reference,
      target_price: target,
      ...(scenario.holdUnit === 'days'
        ? { hold_days: Number.isFinite(hold) ? hold : 1 }
        : { hold_minutes: Number.isFinite(hold) ? hold : 45 }),
      iv_model: scenario.ivModel,
      vol_beta: scenario.volBeta,
      vol_shift: Number.parseFloat(scenario.volShift) || 0,
      day_count: scenario.dayCount,
      lots: Number.parseInt(scenario.lots, 10) || 1,
      objective,
    }
  }, [underlying, expiry, scenario, objective])

  const { data, error, isLoading, updatedAt } = useOptionTarget({ apiKey, request, frozen })

  const active = useMemo(() => {
    if (!data) return null
    if (selected) {
      return data.candidates.find((c) => c.strike === selected.strike) ?? null
    }
    return data.candidates.find((c) => c.recommended) ?? null
  }, [data, selected])

  const referenceNow = data?.scenario.reference_now ?? 0

  return (
    <div className="space-y-4 p-4">
      <Card>
        <CardContent className="flex flex-wrap items-end gap-4 pt-6">
          <div className="space-y-2">
            <Label htmlFor="underlying">Underlying</Label>
            <Input
              id="underlying"
              className="w-40"
              value={underlying}
              onChange={(e) => setUnderlying(e.target.value.toUpperCase())}
            />
          </div>
          <div className="space-y-2">
            <Label>Expiry</Label>
            <Select value={expiry} onValueChange={setExpiry}>
              <SelectTrigger className="w-40">
                <SelectValue placeholder="Select" />
              </SelectTrigger>
              <SelectContent>
                {expiries.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {data ? (
            <div className="flex flex-wrap gap-4 text-sm">
              <Stat label="Spot" value={data.snapshot.spot.toFixed(2)} />
              <Stat label="Forward" value={data.snapshot.forward.toFixed(2)} />
              <Stat label="Basis" value={data.snapshot.basis.toFixed(2)} />
              <Stat label="ATM" value={String(data.snapshot.atm_strike)} />
              <Stat label="ATM IV" value={`${data.snapshot.atm_iv_pct.toFixed(2)}%`} />
              <Stat label="DTE" value={data.snapshot.days_to_expiry.toFixed(2)} />
              <div>
                <p className="text-xs text-muted-foreground">Forward mode</p>
                <Badge
                  variant={data.scenario.forward_mode === 'exact' ? 'default' : 'outline'}
                >
                  {data.scenario.forward_mode === 'exact' ? 'Exact' : 'Basis-modelled'}
                </Badge>
              </div>
            </div>
          ) : null}

          <div className="ml-auto flex items-center gap-2">
            {updatedAt ? (
              <span className="text-xs text-muted-foreground">
                {isLoading ? 'Updating' : `Updated ${updatedAt.toLocaleTimeString()}`}
              </span>
            ) : null}
            <Button variant={frozen ? 'default' : 'outline'} onClick={() => setFrozen((f) => !f)}>
              {frozen ? 'Frozen' : 'Freeze'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <ScenarioPanel
        state={scenario}
        referenceNow={referenceNow}
        scenario={data?.scenario ?? null}
        onChange={setScenario}
      />

      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {data?.warnings.map((w) => (
        <Alert key={w}>
          <AlertDescription>{w}</AlertDescription>
        </Alert>
      ))}

      {data ? (
        <StrikeTable
          candidates={data.candidates}
          objective={objective}
          selectedStrike={active?.strike ?? null}
          onObjectiveChange={setObjective}
          onSelect={setSelected}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          Enter a target price to project strike premiums.
        </p>
      )}

      {data && active ? (
        <StrikeDetail
          candidate={active}
          ladder={data.ladder}
          scenario={data.scenario}
          isDark={isDark}
        />
      ) : null}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="tabular-nums">{value}</p>
    </div>
  )
}
```

- [ ] **Step 2: Register the React route**

In `frontend/src/App.tsx`, alongside the other tool routes (near line 236):

```typescript
<Route path="/optiontarget" element={<OptionTargetCalculator />} />
```

Add the lazy import next to the other page imports, matching the file's existing import style (check whether neighbours use `lazy(() => import(...))` and follow suit).

- [ ] **Step 3: Register the page title**

In `frontend/src/hooks/usePageTitle.ts`, alongside the other entries (near line 40):

```typescript
  '/optiontarget': 'Option Target Calculator',
```

- [ ] **Step 4: Register the Flask route**

In `blueprints/react_app.py`, following the `/ivsmile` pattern at line 283:

```python
@react_bp.route("/optiontarget")
def react_optiontarget():
    return serve_react_app()
```

This matters beyond routing: unregistered paths hit `Error404Tracker` for unauthenticated visitors and count toward an IP ban.

- [ ] **Step 5: Add the tool registry entry**

In `frontend/src/lib/tools.ts`, append to the `tools` array:

```typescript
  {
    title: 'Option Target Calculator',
    description:
      'Project what every strike will be worth at your futures or spot target, ranked by rupee P&L, percentage return or reward-to-risk',
    href: '/optiontarget',
    color: 'bg-purple-500',
  },
```

- [ ] **Step 6: Build and verify**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: build succeeds

Then load `http://127.0.0.1:5000/optiontarget`, enter a target, and confirm the table populates.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/OptionTargetCalculator.tsx frontend/src/App.tsx \
        frontend/src/hooks/usePageTitle.ts frontend/src/lib/tools.ts blueprints/react_app.py
git commit -m "feat(option-target): add calculator page and register routes"
```

---

## Task 19: Regression harness from recorded market data

**Files:**
- Create: `scripts/capture_option_target_fixture.py`
- Create: `test/fixtures/option_target/banknifty_2026-08-04.json`
- Create: `test/test_option_target_replay.py`

The backtest that produced the spec's headline numbers becomes the regression test. Any change to the projection math must not worsen MAE on recorded data.

- [ ] **Step 1: Write the capture script**

Create `scripts/capture_option_target_fixture.py`:

```python
"""Capture a replay fixture for the Option Target regression test.

Records 1-minute history for an underlying and a strike range, so a completed
trade can be replayed offline: reconstruct the smile at entry, project to the
exit level, and compare against what each strike ACTUALLY traded at.

Usage:
    uv run python scripts/capture_option_target_fixture.py \
        --underlying BANKNIFTY --expiry 25AUG26 --date 2026-08-04 \
        --low 56800 --high 58800 --step 100 --out test/fixtures/option_target/x.json
"""

import argparse
import json
from pathlib import Path

import httpx

from database.auth_db import get_first_available_api_key
from utils.logging import get_logger

logger = get_logger(__name__)

API = "http://127.0.0.1:5000/api/v1"


def fetch_series(client: httpx.Client, api_key: str, symbol: str, exchange: str, day: str):
    resp = client.post(
        f"{API}/history",
        json={
            "apikey": api_key, "symbol": symbol, "exchange": exchange,
            "interval": "1m", "start_date": day, "end_date": day,
        },
    ).json()
    if resp.get("status") != "success":
        return {}
    return {str(b["timestamp"]): b["close"] for b in resp.get("data", [])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--underlying", required=True)
    parser.add_argument("--expiry", required=True, help="DDMMMYY")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--low", type=int, required=True)
    parser.add_argument("--high", type=int, required=True)
    parser.add_argument("--step", type=int, required=True)
    parser.add_argument("--index-exchange", default="NSE_INDEX")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    api_key = get_first_available_api_key()
    if not api_key:
        raise SystemExit("No API key available. Generate one at /apikey.")

    with httpx.Client(timeout=60.0) as client:
        payload = {
            "underlying": args.underlying,
            "expiry": args.expiry,
            "date": args.date,
            "spot": fetch_series(
                client, api_key, args.underlying, args.index_exchange, args.date
            ),
            "options": {},
        }
        for strike in range(args.low, args.high + 1, args.step):
            for opt_type in ("CE", "PE"):
                symbol = f"{args.underlying}{args.expiry}{strike}{opt_type}"
                series = fetch_series(client, api_key, symbol, "NFO", args.date)
                if series:
                    payload["options"][f"{strike}{opt_type}"] = series

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload))
    logger.info(
        "Wrote %s: %d spot bars, %d option series",
        out, len(payload["spot"]), len(payload["options"]),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Capture the fixture**

Run:

```bash
uv run python scripts/capture_option_target_fixture.py \
  --underlying BANKNIFTY --expiry 25AUG26 --date 2026-08-04 \
  --low 56800 --high 58800 --step 100 \
  --out test/fixtures/option_target/banknifty_2026-08-04.json
```

Expected: log line reporting roughly 180 spot bars and 40+ option series.

- [ ] **Step 3: Write the replay test**

Create `test/test_option_target_replay.py`:

```python
"""Replay a completed trade and assert the projection math has not regressed.

This is the regression gate for model changes. The recorded numbers come from
BANKNIFTY on 2026-08-04: entry at spot 57795 (10:25), exit at spot 57505
(12:01), expiry 25AUG26. Measured MAE across 37 strike series was 6.84% for
delta-only and 1.26% for the full model.
"""

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from opengreeks import black76

from services.option_target.models import StrikeQuote
from services.option_target.projection import project_strike
from services.option_target.smile import calibrate_ivs, fit_smile

IST = ZoneInfo("Asia/Kolkata")
FIXTURE = Path("test/fixtures/option_target/banknifty_2026-08-04.json")
EXPIRY = datetime(2026, 8, 25, 15, 30, tzinfo=IST)
ENTRY_SPOT, EXIT_SPOT = 57793.0, 57505.0
VOL_BETA = 1.5

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="replay fixture not captured"
)


def _load():
    return json.loads(FIXTURE.read_text())


def _bar_nearest(spot: dict[str, float], level: float, lo_min: int, hi_min: int) -> str:
    def minutes(ts: str) -> int:
        d = datetime.fromtimestamp(int(ts), IST)
        return d.hour * 60 + d.minute

    window = [ts for ts in spot if lo_min <= minutes(ts) <= hi_min]
    return min(window, key=lambda ts: abs(spot[ts] - level))


def _quotes_at(data: dict, ts: str) -> dict[tuple[float, str], StrikeQuote]:
    quotes = {}
    for key, series in data["options"].items():
        price = series.get(ts)
        if not price or price <= 0:
            continue
        opt_type = key[-2:]
        strike = float(key[:-2])
        quotes[(strike, opt_type)] = StrikeQuote(
            strike=strike, option_type=opt_type,
            symbol=f"{data['underlying']}{data['expiry']}{key}",
            ltp=price, bid=price, ask=price, oi=10_000, volume=1_000, lot_size=35,
        )
    return quotes


def _mae(data, use_full_model: bool) -> float:
    spot = data["spot"]
    t_in = _bar_nearest(spot, ENTRY_SPOT, 10 * 60, 11 * 60)
    t_out = _bar_nearest(spot, EXIT_SPOT, 11 * 60 + 45, 12 * 60 + 20)

    entry_quotes = _quotes_at(data, t_in)
    exit_quotes = _quotes_at(data, t_out)
    s0, s1 = spot[t_in], spot[t_out]

    now = datetime.fromtimestamp(int(t_in), IST)
    t_now = (EXPIRY - now).total_seconds() / (365 * 86400)
    t_target = t_now - (int(t_out) - int(t_in)) / (365 * 86400)

    atm = min({k for k, _ in entry_quotes}, key=lambda k: abs(k - s0))
    ce, pe = entry_quotes.get((atm, "CE")), entry_quotes.get((atm, "PE"))
    forward = atm + ce.mid - pe.mid if ce and pe else s0
    forward_target = forward * (s1 / s0)
    move_pct = (s1 / s0 - 1) * 100

    points, _ = calibrate_ivs(entry_quotes, forward, t_now, 0.0)
    fit = fit_smile(points, atm_iv_fallback=0.12)

    errors = []
    for (strike, opt_type), quote in entry_quotes.items():
        actual = exit_quotes.get((strike, opt_type))
        if actual is None or quote.mid < 5 or actual.mid < 5:
            continue
        flag = "c" if opt_type == "CE" else "p"
        try:
            iv_now = black76.implied_volatility(quote.mid, forward, strike, 0.0, t_now, flag)
        except Exception:
            continue

        if use_full_model:
            projected = project_strike(
                strike=strike, option_type=opt_type, forward_target=forward_target,
                t_target=t_target, rate=0.0, iv_now=iv_now, fit=fit,
                iv_model="smile_slide", vol_beta=VOL_BETA, move_pct=move_pct, vol_shift=0.0,
            )
        else:
            delta = black76.delta(flag, forward, strike, t_now, 0.0, iv_now)
            projected = quote.mid + delta * (forward_target - forward)

        errors.append(abs(projected - actual.mid) / actual.mid * 100)

    assert errors, "replay produced no comparable strikes"
    return sum(errors) / len(errors)


def test_full_model_beats_delta_only_by_a_wide_margin():
    data = _load()
    assert _mae(data, use_full_model=True) < _mae(data, use_full_model=False) / 3


def test_full_model_mae_stays_within_the_recorded_band():
    # Recorded 1.26%. A ceiling of 2.5% catches regressions without being
    # brittle to fixture recapture.
    assert _mae(_load(), use_full_model=True) < 2.5


def test_delta_only_is_as_bad_as_recorded():
    # Sanity check that the fixture and harness still reproduce the baseline.
    assert _mae(_load(), use_full_model=False) > 4.0
```

- [ ] **Step 4: Run the replay test**

Run: `uv run pytest test/test_option_target_replay.py -v`
Expected: 3 passed. If the fixture was not captured, all 3 skip — that is acceptable in CI but the fixture must be committed.

- [ ] **Step 5: Commit**

```bash
git add scripts/capture_option_target_fixture.py test/test_option_target_replay.py \
        test/fixtures/option_target/
git commit -m "test(option-target): add replay regression harness from recorded market data"
```

---

## Task 20: Resource audit, docs and final verification

**Files:**
- Create: `docs/option-target-calculator.md`
- Modify: `docs/INDEX.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest test/test_option_target_math.py test/test_option_target_ranking.py test/test_option_target_service.py test/test_option_target_replay.py -v`
Expected: all pass, no skips other than the replay tests if no fixture

- [ ] **Step 2: Run the fd-audit skill**

Invoke the `fd-audit` skill. It is mandatory here because this change adds a module-level cache and a DB query.

Specific things it must confirm:
- `_SNAPSHOT_CACHE` is a bounded `TTLCache`, not a plain dict.
- `_matched_future_symbol` uses `with db_session() as session:` and holds no session past the call.
- `scripts/capture_option_target_fixture.py` closes its `httpx.Client` (it uses a `with` block).
- No new threads, executors, sockets or subprocesses were introduced.

- [ ] **Step 3: Lint everything**

Run:

```bash
uv run ruff check services/option_target/ services/option_target_service.py restx_api/option_target.py scripts/capture_option_target_fixture.py test/ --fix
uv run ruff format services/option_target/ services/option_target_service.py restx_api/option_target.py scripts/capture_option_target_fixture.py
cd frontend && npx biome check src/pages/option-target src/pages/OptionTargetCalculator.tsx src/api/option-target.ts src/types/option-target.ts src/hooks/useOptionTarget.ts --write
```

Expected: clean

- [ ] **Step 4: Write the docs page**

Create `docs/option-target-calculator.md` covering: what the tool does, the spot-versus-futures rule (matched future gives exact mode; NIFTY weeklies are basis-modelled), the three modelled effects and why vol-response exists, the ranking objectives, the API contract, and a pointer to the replay harness. Link it from `docs/INDEX.md` in the options tooling section.

- [ ] **Step 5: Commit**

```bash
git add docs/option-target-calculator.md docs/INDEX.md
git commit -m "docs: document the Option Target Calculator"
```

---

## Self-review notes

**Spec coverage.** Every numbered step of spec Section 4 maps to a task: Step 1 to Task 10 (`parse_chain_quotes`), Steps 2-3 to Task 3, Step 4 to Task 4, Step 5 to Tasks 4-5, Step 6 to Task 2, Steps 7-8 to Task 6, Steps 9-10 to Task 8, Step 11 to Tasks 6-7, Step 12 to Task 8, Step 14 to Task 10 (`build_ladder`). Sections 3.3 to Task 9, Section 5 to Task 12, Section 6 to Tasks 13-18, Section 8 to Tasks 1-11 and 19, Section 10 to Task 20, Section 12 to Task 19.

**Two deliberate deferrals, both flagged rather than silently dropped:**

1. **Cross-expiry compare (spec Step 13)** has no task. It is a self-contained addition on top of a working tool, and including it would have pushed this plan past the point where the earlier tasks stay reviewable. It should be its own follow-up plan once the single-expiry path is proven against live data.

2. **Buy button and Send to Strategy Builder (spec Section 6)** have no task. Both depend on order-placement and Strategy Builder handoff contracts this plan has not investigated, and guessing at them would produce exactly the placeholder steps this skill forbids. The page ships analysis-only; the actions are a follow-up.

3. **`_vol_beta_samples` returns an empty list** in Task 11, so `vol_beta: "auto"` always falls back to the Normal preset with a visible warning. The estimator itself (Task 9) is complete and tested; only the history plumbing is stubbed. This is honest degradation rather than a silent wrong number, but it means the measured-beta benefit is not realised until that function is implemented. Wiring it needs a decision on which history endpoint and window to use per underlying — worth its own task once the tool is running.
