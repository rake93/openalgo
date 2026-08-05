# GEX Levels: Delta Exposure and the Metric toggle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** SHIPPED 2026-08-05. All 8 tasks implemented, each through spec and code-quality review, verified against a live NIFTY chain and on the running chart.

**Goal:** Add per-strike Delta Exposure (DEX) to the GEX Levels study and a Gamma/Delta metric toggle that re-renders the existing strike-bar profile against the selected metric.

**Architecture:** A new pure module `services/gex_levels/delta_exposure.py` mirrors `exposure.py` but computes signed delta notional. `gex_levels_service` is refactored to resolve implied volatilities **once** and price both gamma and delta from that single solve, so DEX costs no extra solver calls and no extra broker call. The frontend gains a `metric` setting that selects which field the existing bar geometry reads.

**Tech Stack:** Python 3.12 + uv, SQLAlchemy-free pure modules, pytest. React 19 + TypeScript, Vitest, Biome. `opengreeks.black76` for the Greeks.

**Spec:** [`docs/superpowers/specs/2026-08-05-gex-advanced-visualisations-design.md`](../specs/2026-08-05-gex-advanced-visualisations-design.md) — phases 1-2 only. No recorder, no database, no scheduler.

---

## Context an engineer needs before starting

**Read [`services/gex_levels/exposure.py`](../../../services/gex_levels/exposure.py) first.** The new module mirrors its structure exactly: a `resolve_ivs` / `price_*` seam, injected `black76`, no network, no logging, no clock.

**The critical trap.** GEX applies dealer-position constants (`DEALER_CALL_SIGN = +1.0`, `DEALER_PUT_SIGN = -1.0`) because gamma is positive for both legs, so those constants are the only source of sign. **Delta already carries its own sign** — `black76.delta` returns `+0.551` for a call and `-0.448` for the matching put. Applying the same constants to delta makes both legs positive and produces a number that is always positive and says nothing. DEX must therefore use each leg's natural delta with **no dealer flip**:

```
DEX_k = (delta_call_k * w_call_k + delta_put_k * w_put_k) * F
```

Positive means calls dominate that strike and the open-interest book is net long delta. Dealers are the counterparty, so dealer delta is the negation.

**Open interest is already lot-multiplied.** As with GEX, there is **no `lot_size` factor**. See the comment block in `price_exposures`.

**Commands.** `uv run python -m pytest test/<file> -v` (NOT `uv run pytest` — the trampoline fails on this machine). Frontend: `cd frontend && npx vitest run <path>`. Lint: `uv run ruff check <files>`. **Never run `npm run build` or `npm run check`** — the first rewrites tracked `dist/`, the second reformats all ~446 source files.

---

## File structure

| File | Responsibility |
| --- | --- |
| `services/gex_levels/blackscholes.py` | **Modify.** Add `safe_delta` beside `safe_gamma`. |
| `services/gex_levels/exposure.py` | **Modify (2 lines).** Promote `_finite` to public `finite_weight` so the delta module reuses it instead of duplicating or importing a private. |
| `services/gex_levels/delta_exposure.py` | **Create.** Pure per-strike DEX. |
| `services/gex_levels_service.py` | **Modify.** One IV solve feeding both gamma and delta; `strikes[]` gains DEX fields. |
| `test/test_gex_levels_delta.py` | **Create.** `safe_delta` and `delta_exposure` together — one feature, one reviewer. |
| `frontend/src/api/gex.ts` | **Modify.** `GEXStrikeLevel` gains DEX fields; export `GexMetric`. |
| `frontend/src/lib/charts/gex-levels.ts` | **Modify.** `metric` in config and defaults, passed to the primitive. |
| `frontend/src/lib/charts/gex-levels-primitive.ts` | **Modify.** Geometry selects the metric. |
| `frontend/src/lib/charts/gex-levels-primitive.test.ts` | **Modify.** Metric selection tests. |
| `frontend/src/components/charts/workspace/StudiesPanel.tsx` | **Modify.** The Metric control. |
| `docs/chart-workspace-studies.md` | **Modify.** Document the metric. |

---

### Task 1: `safe_delta`

**Files:**
- Modify: `services/gex_levels/blackscholes.py`
- Test: `test/test_gex_levels_delta.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_delta.py`:

```python
"""Delta exposure: the Black-76 helper and the per-strike DEX profile."""

import math

from services.gex_levels.blackscholes import safe_delta


class _Delta:
    """Delta independent of strike, so DEX arithmetic is checkable by hand."""

    def __init__(self, call=0.6, put=-0.4):
        self._call = call
        self._put = put

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def delta(self, flag, F, K, t, r, sigma):
        return self._call if flag == "c" else self._put


class _Raises:
    def delta(self, flag, F, K, t, r, sigma):
        raise RuntimeError("solver blew up")


def test_a_put_delta_stays_negative():
    """The single most important guard in this file. safe_gamma rejects
    negatives because gamma cannot be negative; copying that rule here would
    silently delete the entire put side of every delta profile."""
    d = safe_delta(_Delta(), "p", 24600.0, 24600.0, 0.02, 0.065, 0.20)
    assert d == -0.4


def test_a_call_delta_is_returned_unchanged():
    assert safe_delta(_Delta(), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.6


def test_non_positive_inputs_yield_zero_rather_than_raising():
    stub = _Delta()
    assert safe_delta(stub, "c", 0.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(stub, "c", 24600.0, 0.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(stub, "c", 24600.0, 24600.0, 0.0, 0.065, 0.20) == 0.0
    assert safe_delta(stub, "c", 24600.0, 24600.0, 0.02, 0.065, 0.0) == 0.0


def test_a_solver_exception_yields_zero():
    assert safe_delta(_Raises(), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0


def test_a_non_finite_delta_yields_zero():
    assert safe_delta(_Delta(call=math.nan), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(_Delta(call=math.inf), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0


def test_an_implausible_delta_yields_zero():
    """Black-76 delta is bounded by +/-1. Anything well outside that is a solver
    artefact, not a position."""
    assert safe_delta(_Delta(call=7.5), "c", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
    assert safe_delta(_Delta(put=-7.5), "p", 24600.0, 24600.0, 0.02, 0.065, 0.20) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest test/test_gex_levels_delta.py -v`
Expected: FAIL — `ImportError: cannot import name 'safe_delta'`

- [ ] **Step 3: Write minimal implementation**

In `services/gex_levels/blackscholes.py`, add after `_MAX_PLAUSIBLE_IV = 5.0`:

```python
# Black-76 delta is bounded by +/-1 (call 0..1, put -1..0). Anything well
# outside that band is a solver artefact rather than a position.
_MAX_PLAUSIBLE_DELTA = 1.5
```

Then add this function after `safe_gamma`:

```python
def safe_delta(black76, flag: str, F: float, K: float, t: float, r: float, sigma: float) -> float:
    """
    Black-76 delta, or 0.0 on any numerical failure.

    Unlike `safe_gamma` this must NOT reject negative results. A put's delta is
    negative by definition, and rejecting negatives here would silently delete
    the entire put side of every delta profile - leaving a chart that looks
    plausible and is directionally meaningless.

    Args:
        black76: The opengreeks.black76 module (injected so this stays pure).
        flag: 'c' for a call, 'p' for a put.
        F: Forward price of the underlying.
        K: Strike.
        t: Time to expiry in years.
        r: Risk-free rate as a decimal (0.065, not 6.5).
        sigma: Volatility as a decimal.

    Returns:
        The delta - positive for calls, negative for puts - or 0.0 when the
        inputs are non-positive, the calculation raises, or the result is
        non-finite or implausibly large in magnitude.
    """
    if not sigma or sigma <= 0 or F <= 0 or K <= 0 or t <= 0:
        return 0.0
    try:
        d = black76.delta(flag, F, K, t, r, sigma)
    except Exception:
        return 0.0
    if d is None or not math.isfinite(d) or abs(d) > _MAX_PLAUSIBLE_DELTA:
        return 0.0
    return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest test/test_gex_levels_delta.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Confirm no regression and lint**

Run: `uv run python -m pytest test/test_gex_levels_math.py -v && uv run ruff check services/gex_levels/blackscholes.py test/test_gex_levels_delta.py`
Expected: all pass, `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add services/gex_levels/blackscholes.py test/test_gex_levels_delta.py
git commit -m "feat(gex-levels): add safe_delta, which must not clamp negatives

safe_gamma rejects negative results because gamma cannot be negative.
Copying that rule to delta would delete the entire put side of every
profile, since a put delta is negative by definition - a chart that still
looks plausible and is directionally meaningless. The guard here rejects
only non-finite and implausibly large magnitudes."
```

---

### Task 2: Promote `_finite` to `finite_weight`

The delta module needs the same NaN-weight guard. Importing a private name across modules is worse than either duplicating it or promoting it; promoting is the DRY choice and the diff is two lines.

**Files:**
- Modify: `services/gex_levels/exposure.py`

- [ ] **Step 1: Rename the function**

In `services/gex_levels/exposure.py`, change the definition at the bottom of the file from `def _finite(weight: float) -> float:` to:

```python
def finite_weight(weight: float) -> float:
```

Leave the docstring and body exactly as they are.

- [ ] **Step 2: Update both call sites**

In `price_exposures`, change:

```python
        call_weight = _finite(row.call_volume if use_volume else row.call_oi)
        put_weight = _finite(row.put_volume if use_volume else row.put_oi)
```

to:

```python
        call_weight = finite_weight(row.call_volume if use_volume else row.call_oi)
        put_weight = finite_weight(row.put_volume if use_volume else row.put_oi)
```

- [ ] **Step 3: Verify nothing else referenced the old name**

Run: `grep -rn "_finite" services/ test/ --include=*.py`
Expected: only `services/gex_levels/sentiment.py` matches, which has its **own** unrelated private `_finite(value)` — do not touch it.

- [ ] **Step 4: Run the full gex_levels suite**

Run: `uv run python -m pytest test/test_gex_levels_exposure.py test/test_gex_levels_walls.py test/test_gex_levels_zero_gamma.py test/test_gex_levels_service.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add services/gex_levels/exposure.py
git commit -m "refactor(gex-levels): make the NaN-weight guard public as finite_weight

The delta exposure module needs the same guard. Importing a private name
across modules is worse than promoting it, and duplicating it would let
the two copies drift."
```

---

### Task 3: `delta_exposure.py`

**Files:**
- Create: `services/gex_levels/delta_exposure.py`
- Test: `test/test_gex_levels_delta.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `test/test_gex_levels_delta.py`:

```python
import pytest

from services.gex_levels.delta_exposure import StrikeDelta, price_delta_exposures
from services.gex_levels.exposure import ChainRow, resolve_ivs

FORWARD = 24600.0
T_YEARS = 0.02
RATE = 0.065


def _rows():
    return [
        ChainRow(
            strike=24600.0,
            call_price=120.0,
            put_price=80.0,
            call_oi=1000,
            put_oi=4000,
            call_volume=300,
            put_volume=100,
            lot_size=75,
        ),
        ChainRow(
            strike=24500.0,
            call_price=180.0,
            put_price=40.0,
            call_oi=2000,
            put_oi=500,
            call_volume=50,
            put_volume=25,
            lot_size=75,
        ),
    ]


def _priced(rows=None, weight_by="oi", stub=None):
    stub = stub or _Delta()
    rows = rows if rows is not None else _rows()
    ivs = resolve_ivs(stub, rows, forward=FORWARD, t_years=T_YEARS, r=RATE, atm_strike=24600.0)
    return price_delta_exposures(
        stub, rows, ivs, forward=FORWARD, t_years=T_YEARS, r=RATE, weight_by=weight_by
    )


def test_the_worked_example_is_exact():
    """The one assertion that pins the sign convention, with numbers a reader
    can check by hand. Call delta +0.6 on 1000 OI and put delta -0.4 on 4000 OI
    at a 24600 forward:

        call_dex = +0.6 * 1000 * 24600 =  14,760,000
        put_dex  = -0.4 * 4000 * 24600 = -39,360,000
        net_dex                        = -24,600,000

    Net is NEGATIVE because puts dominate this strike. If a future change makes
    this positive, the dealer-sign trap described in the module docstring has
    been reintroduced."""
    at_atm = next(e for e in _priced() if e.strike == 24600.0)
    assert at_atm.call_dex == pytest.approx(14_760_000.0)
    assert at_atm.put_dex == pytest.approx(-39_360_000.0)
    assert at_atm.net_dex == pytest.approx(-24_600_000.0)


def test_a_call_heavy_strike_is_net_positive():
    """The mirror of the worked example: 2000 call OI against 500 put OI."""
    at_lower = next(e for e in _priced() if e.strike == 24500.0)
    assert at_lower.net_dex > 0


def test_results_are_sorted_by_strike_ascending():
    assert [e.strike for e in _priced()] == [24500.0, 24600.0]


def test_volume_weighting_uses_volume_not_open_interest():
    by_oi = next(e for e in _priced(weight_by="oi") if e.strike == 24600.0)
    by_volume = next(e for e in _priced(weight_by="volume") if e.strike == 24600.0)
    # 300 call volume vs 1000 call OI, 100 put volume vs 4000 put OI.
    assert by_volume.call_dex == pytest.approx(0.6 * 300 * FORWARD)
    assert by_volume.put_dex == pytest.approx(-0.4 * 100 * FORWARD)
    assert by_volume.net_dex != by_oi.net_dex


def test_an_unknown_weighting_raises_rather_than_defaulting():
    with pytest.raises(ValueError, match="weight_by"):
        _priced(weight_by="notional")


def test_a_nan_weight_contributes_nothing_rather_than_poisoning_the_profile():
    rows = _rows()
    rows[0] = ChainRow(
        strike=24600.0,
        call_price=120.0,
        put_price=80.0,
        call_oi=math.nan,
        put_oi=4000,
        call_volume=300,
        put_volume=100,
        lot_size=75,
    )
    at_atm = next(e for e in _priced(rows=rows) if e.strike == 24600.0)
    assert at_atm.call_dex == 0.0
    assert math.isfinite(at_atm.net_dex)


def test_a_non_finite_forward_yields_no_exposure_rather_than_nan():
    stub = _Delta()
    rows = _rows()
    ivs = resolve_ivs(stub, rows, forward=FORWARD, t_years=T_YEARS, r=RATE, atm_strike=24600.0)
    out = price_delta_exposures(
        stub, rows, ivs, forward=math.nan, t_years=T_YEARS, r=RATE, weight_by="oi"
    )
    assert all(e.net_dex == 0.0 for e in out)


def test_rows_not_matching_the_resolved_ivs_raise():
    stub = _Delta()
    ivs = resolve_ivs(stub, _rows(), forward=FORWARD, t_years=T_YEARS, r=RATE, atm_strike=24600.0)
    stranger = [
        ChainRow(
            strike=99999.0,
            call_price=1.0,
            put_price=1.0,
            call_oi=1,
            put_oi=1,
            call_volume=1,
            put_volume=1,
            lot_size=75,
        )
    ]
    with pytest.raises(ValueError, match="resolve"):
        price_delta_exposures(
            stub, stranger, ivs, forward=FORWARD, t_years=T_YEARS, r=RATE, weight_by="oi"
        )


def test_the_raw_deltas_are_carried_through_for_display():
    at_atm = next(e for e in _priced() if e.strike == 24600.0)
    assert at_atm.call_delta == pytest.approx(0.6)
    assert at_atm.put_delta == pytest.approx(-0.4)
    assert isinstance(at_atm, StrikeDelta)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest test/test_gex_levels_delta.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.gex_levels.delta_exposure'`

- [ ] **Step 3: Write the implementation**

Create `services/gex_levels/delta_exposure.py`:

```python
"""
Per-strike signed delta exposure (DEX).

    DEX_k = (delta_k(call) * w_k(call) + delta_k(put) * w_k(put)) * F

WHOSE delta this is: the **open-interest book's**, not the dealer's. A positive
DEX means calls dominate that strike and the book is net long delta; negative
means puts dominate. Dealers stand on the other side, so dealer delta is the
negation of this number.

Why this does NOT mirror `exposure.py`'s dealer-sign convention, which is the
trap this module exists to avoid. Gamma is positive for both legs, so
DEALER_CALL_SIGN / DEALER_PUT_SIGN are GEX's only source of sign. Delta already
carries its own: a call's delta is positive and a put's is negative. Applying
the dealer constants on top gives

    +1 * delta_call * w   ->  positive
    -1 * delta_put  * w   ->  negative x negative -> ALSO positive

so every strike contributes positively and the total is always positive,
carrying no direction at all. That is why no published DEX is defined that way.
Each leg here keeps its natural delta sign and there is no dealer flip.

As in `exposure.py` there is **no lot-size factor**: this broker's chain reports
open interest and volume already multiplied by the lot size, so multiplying
again would double-count it.

Units are currency notional - delta per unit, times the weight in units, times
the forward. The `* F` factor is constant across strikes, so it scales the
profile without moving where its extremes sit.

Purity: no network, database, logging or clock. Plain inputs, plain values.
"""

import math
from dataclasses import dataclass

from services.gex_levels.blackscholes import safe_delta
from services.gex_levels.exposure import ChainRow, ResolvedIVs, WeightBy, finite_weight


@dataclass(frozen=True)
class StrikeDelta:
    """
    Signed delta exposure at one strike, in currency notional.

    Attributes:
        strike: The strike.
        call_dex: Call-leg notional delta. Positive.
        put_dex: Put-leg notional delta. Negative.
        net_dex: call_dex + put_dex. Positive where calls dominate.
        call_delta: Raw Black-76 call delta, carried for display.
        put_delta: Raw Black-76 put delta, carried for display. Negative.
    """

    strike: float
    call_dex: float
    put_dex: float
    net_dex: float
    call_delta: float
    put_delta: float


def price_delta_exposures(
    black76,
    rows: list[ChainRow],
    ivs: ResolvedIVs,
    forward: float,
    t_years: float,
    r: float,
    weight_by: WeightBy,
) -> list[StrikeDelta]:
    """
    Signed DEX at `forward`, using PRE-RESOLVED volatilities.

    Takes `ivs` rather than resolving its own so that a caller computing both
    gamma and delta pays for the Black-76 inversion once. That solve is the
    expensive half of this pipeline - two solver calls per strike - and it is
    identical for both metrics, since `resolve_ivs` does not depend on the Greek
    being priced.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows, any order.
        ivs: Volatilities from `resolve_ivs`, inverted at the real forward, and
            resolved from this exact same `rows` list.
        forward: The price to evaluate delta at.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        One StrikeDelta per input row, sorted by strike ascending, matching
        `price_exposures` so the two can be zipped by position.

    Raises:
        ValueError: If `weight_by` is neither 'oi' nor 'volume'. An unrecognised
            weighting must never quietly read as open interest.
        ValueError: If a row's strike is absent from `ivs`, which means `rows`
            does not match what `resolve_ivs` was given. Distinct from a strike
            present but None, which is a leg that did not invert and legitimately
            takes the fallback.
    """
    if weight_by not in ("oi", "volume"):
        raise ValueError(f"weight_by must be 'oi' or 'volume', got {weight_by!r}")

    ordered = sorted(rows, key=lambda row: row.strike)
    use_volume = weight_by == "volume"

    # A non-finite forward yields no exposure rather than a profile of NaN,
    # matching price_exposures' handling of the same case.
    scale = forward if math.isfinite(forward) else 0.0

    out: list[StrikeDelta] = []
    for row in ordered:
        if row.strike not in ivs.call or row.strike not in ivs.put:
            raise ValueError(
                f"ivs was not resolved for strike {row.strike}; resolve_ivs and "
                "price_delta_exposures must be given the same rows"
            )
        call_iv = ivs.call.get(row.strike)
        put_iv = ivs.put.get(row.strike)
        call_weight = finite_weight(row.call_volume if use_volume else row.call_oi)
        put_weight = finite_weight(row.put_volume if use_volume else row.put_oi)

        call_sigma = call_iv if call_iv is not None else ivs.fallback
        put_sigma = put_iv if put_iv is not None else ivs.fallback

        call_delta = safe_delta(black76, "c", forward, row.strike, t_years, r, call_sigma)
        put_delta = safe_delta(black76, "p", forward, row.strike, t_years, r, put_sigma)

        # No dealer sign flip and no lot_size - see the module docstring for
        # why both would be wrong here.
        call_dex = call_delta * call_weight * scale
        put_dex = put_delta * put_weight * scale

        out.append(
            StrikeDelta(
                strike=row.strike,
                call_dex=call_dex,
                put_dex=put_dex,
                net_dex=call_dex + put_dex,
                call_delta=call_delta,
                put_delta=put_delta,
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest test/test_gex_levels_delta.py -v`
Expected: PASS, 15 passed

- [ ] **Step 5: Lint**

Run: `uv run ruff check services/gex_levels/delta_exposure.py test/test_gex_levels_delta.py && uv run ruff format --check services/gex_levels/delta_exposure.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add services/gex_levels/delta_exposure.py test/test_gex_levels_delta.py
git commit -m "feat(gex-levels): add per-strike delta exposure

Deliberately does not mirror exposure.py's dealer-sign convention. Gamma
is positive for both legs so the dealer constants are GEX's only source of
sign; delta already carries its own, so applying them on top makes both
legs positive and the total always positive - a number with no direction
in it. Each leg keeps its natural sign instead.

The worked example in the test pins that: +0.6 delta on 1000 call OI and
-0.4 on 4000 put OI at a 24600 forward gives a net -24,600,000, negative
because puts dominate. If that ever turns positive the trap is back."
```

---

### Task 4: One IV solve feeding both metrics

**Files:**
- Modify: `services/gex_levels_service.py`
- Test: `test/test_gex_levels_service.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_gex_levels_service.py`. That file stubs only the two IO boundaries via its existing `_patched()` helper — the real `opengreeks.black76` runs — so no delta stub is needed here; the worked example in Task 3 already pins the arithmetic.

```python
def test_every_strike_carries_delta_exposure_alongside_gamma():
    """The Metric toggle switches which field the bar column reads, so both
    must be present on every strike of the same payload - not fetched twice."""
    chain, forward = _patched()
    with chain, forward:
        ok, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    assert ok is True
    assert payload["strikes"]
    for item in payload["strikes"]:
        assert set(item) == {
            "strike",
            "call_gex",
            "put_gex",
            "net_gex",
            "call_dex",
            "put_dex",
            "net_dex",
        }


def test_delta_exposure_is_signed_by_leg_not_by_dealer_convention():
    """Deep strikes must straddle zero: a low strike is call-dominant (both
    deltas near +1 and 0) and a high strike put-dominant (near 0 and -1). If
    the dealer sign flip is ever applied to delta, every strike turns positive
    and this fails."""
    chain, forward = _patched()
    with chain, forward:
        _, payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    net = [item["net_dex"] for item in payload["strikes"]]
    assert any(v < 0 for v in net), f"no negative net_dex in {net}"
    assert any(v > 0 for v in net), f"no positive net_dex in {net}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest test/test_gex_levels_service.py -v`
Expected: FAIL — `KeyError` or a set mismatch showing `call_dex` absent

- [ ] **Step 3: Change the import**

> **Amended during execution.** Task 3's code review found that `price_exposures` and `price_delta_exposures` duplicated their whole preamble, and that the position-alignment contract the `zip` below depends on was documented only in prose. The preamble was extracted to `weighted_legs(rows, ivs, weight_by)` in `exposure.py`, and both pricers now take the resulting legs list instead of `(rows, ivs, weight_by)`. Hoisting it out of the 60-step zero-gamma scan also took that scan from 24.46 ms to 12.21 ms. The signatures below reflect the shipped code, not the original plan text.

In `services/gex_levels_service.py`, find the import of `compute_exposures` from `services.gex_levels.exposure` and replace `compute_exposures` with `price_exposures, resolve_ivs, weighted_legs`. Add a new import line:

```python
from services.gex_levels.delta_exposure import price_delta_exposures
```

- [ ] **Step 4: Replace the single-shot call with a shared solve**

Replace this block (around line 152):

```python
        exposures = compute_exposures(
            black76,
            rows,
            forward=F,
            t_years=t_years,
            r=r,
            atm_strike=atm_strike,
            weight_by=weight_by,
        )
```

with:

```python
        # Resolved once and priced twice. resolve_ivs does not depend on which
        # Greek is being priced, and it is the expensive half of this pipeline
        # - two solver calls per strike - so delta exposure costs no extra
        # inversion and no extra broker call.
        ivs = resolve_ivs(
            black76,
            rows,
            forward=F,
            t_years=t_years,
            r=r,
            atm_strike=atm_strike,
        )
        # Built ONCE and handed to both pricers. The zip below then walks two
        # lists derived from the same object rather than two merely equal ones,
        # so a strike's gamma and its delta cannot drift apart.
        legs = weighted_legs(rows, ivs, weight_by)
        exposures = price_exposures(black76, legs, forward=F, t_years=t_years, r=r)
        delta_exposures = price_delta_exposures(black76, legs, forward=F, t_years=t_years, r=r)
```

- [ ] **Step 5: Add the DEX fields to the payload**

Replace the `"strikes"` list comprehension (around line 213):

```python
                "strikes": [
                    {
                        "strike": e.strike,
                        "call_gex": round(e.call_gex, 2),
                        "put_gex": round(e.put_gex, 2),
                        "net_gex": round(e.net_gex, 2),
                    }
                    for e in exposures
                ],
```

with:

```python
                # Both metrics on every strike. price_exposures and
                # price_delta_exposures both sort ascending by strike and are
                # built from the same rows, so zip pairs them correctly.
                "strikes": [
                    {
                        "strike": e.strike,
                        "call_gex": round(e.call_gex, 2),
                        "put_gex": round(e.put_gex, 2),
                        "net_gex": round(e.net_gex, 2),
                        "call_dex": round(d.call_dex, 2),
                        "put_dex": round(d.put_dex, 2),
                        "net_dex": round(d.net_dex, 2),
                    }
                    for e, d in zip(exposures, delta_exposures, strict=True)
                ],
```

- [ ] **Step 6: Run the tests**

Run: `uv run python -m pytest test/test_gex_levels_service.py test/test_gex_levels_endpoint.py -v`
Expected: PASS

- [ ] **Step 7: Run the whole gex suite for regressions**

Run: `uv run python -m pytest test/test_gex_levels_delta.py test/test_gex_levels_endpoint.py test/test_gex_levels_exposure.py test/test_gex_levels_math.py test/test_gex_levels_quality.py test/test_gex_levels_sentiment.py test/test_gex_levels_service.py test/test_gex_levels_walls.py test/test_gex_levels_zero_gamma.py test/test_gex_service.py -v`
Expected: all pass — 140+ tests

- [ ] **Step 8: Commit**

```bash
git add services/gex_levels_service.py test/test_gex_levels_service.py
git commit -m "feat(gex-levels): return delta exposure alongside gamma

The Metric toggle switches which field the bar column reads, so both must
arrive in one payload rather than costing a second request.

Priced from a single resolve_ivs. That inversion does not depend on which
Greek is being priced and is the expensive half of the pipeline at two
solver calls per strike, so delta costs no extra solve and no extra broker
call. zip(strict=True) because both sort ascending from the same rows -
a length mismatch is a bug, not something to silently truncate."
```

---

### Task 5: Frontend types

**Files:**
- Modify: `frontend/src/api/gex.ts`

- [ ] **Step 1: Extend `GEXStrikeLevel` and add the metric type**

Replace:

```typescript
export interface GEXStrikeLevel {
  strike: number
  call_gex: number
  put_gex: number
  net_gex: number
}
```

with:

```typescript
export interface GEXStrikeLevel {
  strike: number
  call_gex: number
  put_gex: number
  net_gex: number
  /**
   * Delta exposure, signed by leg rather than by dealer convention: positive
   * where calls dominate the strike. Required rather than optional - the
   * server always sends both metrics in one payload, and an optional field
   * defaulted to 0 would draw a flat profile that looks like real data.
   */
  call_dex: number
  put_dex: number
  net_dex: number
}

/** Which Greek the strike-bar profile is drawn from. */
export type GexMetric = 'gamma' | 'delta'
```

- [ ] **Step 2: Fix the test helper the new required fields break**

Making the fields required is a compile error for every existing object literal typed as `GEXStrikeLevel`. There is exactly one: the `strike()` helper in `frontend/src/lib/charts/gex-levels-primitive.test.ts:79`. Replace it with:

```typescript
function strike(strikePrice: number, netGex: number, netDex: number = netGex): GEXStrikeLevel {
  return {
    strike: strikePrice,
    call_gex: Math.max(netGex, 0),
    put_gex: Math.max(-netGex, 0),
    net_gex: netGex,
    call_dex: Math.max(netDex, 0),
    put_dex: Math.min(netDex, 0),
    net_dex: netDex,
  }
}
```

`netDex` defaults to `netGex` so every existing call site keeps working unchanged; the metric tests in Task 6 pass a distinct third argument.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.app.json`
Expected: no output (clean)

- [ ] **Step 4: Run the affected suite**

Run: `cd frontend && npx vitest run src/lib/charts/gex-levels-primitive.test.ts`
Expected: PASS — the helper change is shape-only

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/gex.ts frontend/src/lib/charts/gex-levels-primitive.test.ts
git commit -m "feat(gex-levels): type delta exposure on the strike level

Required, not optional. The server always sends both metrics, and an
optional field defaulted to zero would render a flat profile
indistinguishable from a real one - the failure this codebase already
forbids elsewhere as 'a missing input must never read as a zero'."
```

---

### Task 6: The primitive reads the selected metric

**Files:**
- Modify: `frontend/src/lib/charts/gex-levels-primitive.ts`
- Test: `frontend/src/lib/charts/gex-levels-primitive.test.ts`

- [ ] **Step 1: Write the failing test**

Append inside the existing `describe('computeGexBarGeometry', ...)` block, using the `strike(price, netGex, netDex)` helper updated in Task 5:

```typescript
  it('reads net_gex under the gamma metric and net_dex under delta', () => {
    // Opposite signs between the two metrics, so a geometry that ignored the
    // metric would be caught by the direction flip alone.
    const strikes = [strike(24_200, 100, -80), strike(24_400, -50, 40)]

    const gamma = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(gamma.bars.map((b) => b.positive)).toEqual([true, false])
    // Gamma peak is 100, so 24_200 is the full column.
    expect(gamma.bars.find((b) => b.strike === 24_200)?.length).toBe(120)

    const delta = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'delta')
    expect(delta.bars.map((b) => b.positive)).toEqual([false, true])
    // Delta peak is 80, so 24_200 is full and 24_400 is half.
    expect(delta.bars.find((b) => b.strike === 24_200)?.length).toBe(120)
    expect(delta.bars.find((b) => b.strike === 24_400)?.length).toBe(60)
  })

  it('scales each metric against its own peak, never the other metric', () => {
    const strikes = [strike(24_200, 1000, 10), strike(24_400, 500, 5)]

    const delta = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'delta')
    // If the gamma peak of 1000 leaked into the delta scaling these would be
    // 1.2px and 0.6px - a column of invisible slivers rather than an
    // obviously wrong chart, which is why it needs pinning.
    expect(delta.bars.find((b) => b.strike === 24_200)?.length).toBe(120)
    expect(delta.bars.find((b) => b.strike === 24_400)?.length).toBe(60)
  })

  it('defaults to the gamma metric when none is passed', () => {
    const strikes = [strike(24_200, 100, -80)]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(bars[0]?.positive).toBe(true)
  })

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/charts/gex-levels-primitive.test.ts`
Expected: FAIL — the 5th argument is ignored, so the delta assertions get gamma's directions

- [ ] **Step 3: Add the metric parameter to the geometry**

In `gex-levels-primitive.ts`, import the metric type by adding `GexMetric` to the existing `@/api/gex` type import.

Then change the signature and the two lines that read `net_gex`:

```typescript
export function computeGexBarGeometry(
  strikes: readonly GEXStrikeLevel[],
  priceToY: (price: number) => number,
  plotHeight: number,
  columnWidth: number,
  metric: GexMetric = 'gamma'
): { bars: GexBarGeometry[]; rowHeight: number } {
```

Immediately after the `visible` filter and its empty check, insert:

```typescript
  // One accessor for both the peak and the per-bar value, so the two can never
  // be scaled against different metrics.
  const valueOf = (s: GEXStrikeLevel): number => (metric === 'delta' ? s.net_dex : s.net_gex)
```

Then replace:

```typescript
  const peak = visible.reduce((max, s) => Math.max(max, Math.abs(s.net_gex)), 0)
```

with:

```typescript
  const peak = visible.reduce((max, s) => Math.max(max, Math.abs(valueOf(s))), 0)
```

and replace the `bars` mapping body's two `s.net_gex` references:

```typescript
    length: peak > 0 ? (Math.abs(valueOf(s)) / peak) * columnWidth : 0,
    positive: valueOf(s) >= 0,
```

- [ ] **Step 4: Add `metric` to the primitive options**

In `GexLevelsPrimitiveOptions`, add after `columnWidth: number`:

```typescript
  /** Which Greek the bar column is drawn from. */
  metric: GexMetric
```

In the defaults object, add after `columnWidth: 120,`:

```typescript
  metric: 'gamma',
```

In `drawBars`, pass it through:

```typescript
    const { bars, rowHeight } = computeGexBarGeometry(
      strikes,
      priceToY,
      rc.plotHeight,
      this.opts.columnWidth,
      this.opts.metric
    )
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/lib/charts/gex-levels-primitive.test.ts`
Expected: PASS

- [ ] **Step 6: Typecheck and commit**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.app.json`
Expected: clean

```bash
git add frontend/src/lib/charts/gex-levels-primitive.ts frontend/src/lib/charts/gex-levels-primitive.test.ts
git commit -m "feat(gex-levels): draw the strike profile from the selected metric

One valueOf accessor feeds both the peak and the per-bar length, so the
two can never end up scaled against different metrics - delta values are
orders of magnitude apart from gamma ones, and mixing them would render a
column of invisible slivers rather than an obviously wrong chart."
```

---

### Task 7: The Metric control

**Files:**
- Modify: `frontend/src/lib/charts/gex-levels.ts`
- Modify: `frontend/src/components/charts/workspace/StudiesPanel.tsx`
- Test: `frontend/src/lib/charts/gex-levels.test.ts`

> **Two requirements added during execution, from Task 6's code review.**
>
> **1. The active metric MUST be labelled on screen. This is a blocker, not a nicety.** Everything else in the study stays gamma when the user selects Delta — the Call Wall, Put Wall and Zero-Gamma levels are computed server-side from gamma only, Regime is the sign of net GEX, and the readout card shows gamma totals. Two concrete misreads follow:
>
> - Under gamma the longest bar lands on a wall — but only **while both walls are on screen**. `find_walls` takes `max(net_gex)` and `min(net_gex)` over the **full fetched chain**, whereas `computeGexBarGeometry` filters to *visible* strikes before scaling `max(abs(exposure))` to `columnWidth`. So the full-length bar is whichever wall carries the larger magnitude *among the strikes currently in view*; pan a dominant wall off screen and the longest bar is an ordinary strike with no line on it, under gamma too. (This claim was wrong twice before landing: first as "always the Call Wall" — it is whichever wall has the larger magnitude, routinely the Put Wall — then as an unconditional guarantee, which the visible-versus-full-chain scope mismatch breaks. `gex-levels-primitive.test.ts` pins the clipping with an off-screen `net_gex = 9999` that must not win the peak.) Under delta the coincidence breaks even with both walls in view, because the walls are gamma-derived and the bars are not. A user reads the unlabelled full-length bar as a second wall the dashboard forgot, or as a broken study.
> - Worse, **the frame of reference silently inverts.** The whole study speaks in the dealer frame — a Call Wall is a dealer-gamma concentration, Regime describes what dealers do. But DEX is the open-interest *book's* delta (see `delta_exposure.py`'s module docstring), so dealers hold the negation. Green means "dealers long" under gamma and "dealers short" under delta. Someone who learned the palette on gamma reads delta exactly backwards.
>
> Layering a delta profile under fixed gamma levels is legitimate — comparing one profile against a stable set of reference levels is useful. Doing it without saying so is not.
>
> **2. `primitiveOptions()` in `gex-levels.ts` returns a `Partial<GexLevelsPrimitiveOptions>`, so omitting `metric` there is not a type error.** Adding `metric` to `GexLevelsConfig` and wiring the select without adding the line in `primitiveOptions()` yields a control that compiles, renders, updates state, and does nothing at all. Verify the wiring end to end, not just that it typechecks.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/lib/charts/gex-levels.test.ts`, inside the existing `describe('GexLevelsManager settings', ...)` block. That file's harness is `make()`, returning `{ manager, onChange, fetchLevels }`; the accessor is `manager.config` and the mutator is `manager.setConfig(...)`:

```typescript
  it('defaults to gamma and carries the metric through a snapshot round-trip', () => {
    const { manager } = make()
    expect(DEFAULT_GEX_LEVELS_SETTINGS.metric).toBe('gamma')
    expect(manager.config.metric).toBe('gamma')

    manager.setConfig({ metric: 'delta' })
    expect(manager.config.metric).toBe('delta')

    const { manager: restored } = make()
    restored.restore(manager.snapshot())
    expect(restored.config.metric).toBe('delta')
  })

  it('fills the metric from the defaults when restoring a snapshot saved before it existed', () => {
    const { manager } = make()
    manager.restore({ enabled: true })
    expect(manager.config.metric).toBe('gamma')
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/charts/gex-levels.test.ts`
Expected: FAIL — `metric` is undefined

- [ ] **Step 3: Add `metric` to the config**

In `gex-levels.ts`, add `GexMetric` to the existing `@/api/gex` type import. Then in `GexLevelsConfig`, add after `weightBy: GEXWeightBy`:

```typescript
  /**
   * Which Greek the strike-bar profile is drawn from. Gamma says how hard
   * dealers must hedge; delta says which way the book already leans. Both
   * arrive in one payload, so switching costs no refetch.
   */
  metric: GexMetric
```

In `DEFAULT_GEX_LEVELS_SETTINGS`, add after `weightBy: 'oi',`:

```typescript
  metric: 'gamma',
```

- [ ] **Step 4: Pass it to the primitive**

Find where `syncPrimitive` builds its `GexLevelsPrimitiveOptions` and add a `metric` entry alongside the existing `columnWidth` one, reading from the same settings object the neighbouring entries use (the field is `metric`, so e.g. `metric: this.settings.metric,` — match whatever receiver the adjacent lines already use rather than assuming).

- [ ] **Step 5: Add the panel control**

In `StudiesPanel.tsx`, insert immediately **before** the existing `<Field label="Weight by" ...>` block:

```tsx
          <Field label="Metric" hint="Gamma is hedging pressure; delta is which way the book leans">
            <TinySelect
              value={p.gex.metric}
              onChange={(e) => p.onGex({ metric: e.target.value as GexLevelsConfig['metric'] })}
            >
              <option value="gamma">Gamma (GEX)</option>
              <option value="delta">Delta (DEX)</option>
            </TinySelect>
          </Field>
```

- [ ] **Step 6: Run the tests and typecheck**

Run: `cd frontend && npx vitest run src/lib/charts/ && npx tsc --noEmit -p tsconfig.app.json`
Expected: all pass, typecheck clean

- [ ] **Step 7: Lint only the changed files**

Run: `cd frontend && npx biome check src/lib/charts/gex-levels.ts src/components/charts/workspace/StudiesPanel.tsx`
Expected: any complaint should be pre-existing CRLF formatting only. **Do not run `biome check --write` across the tree** and **do not run `npm run check`** — it reformats all ~446 source files.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/charts/gex-levels.ts frontend/src/lib/charts/gex-levels.test.ts frontend/src/components/charts/workspace/StudiesPanel.tsx
git commit -m "feat(gex-levels): add the Gamma/Delta metric toggle

Both metrics arrive in one payload, so switching re-renders from data
already on the client rather than refetching. Defaults to gamma, and
persists with the saved layout like every other study setting."
```

---

### Task 8: Document it

**Files:**
- Modify: `docs/chart-workspace-studies.md`

- [ ] **Step 1: Add the metric section**

In `docs/chart-workspace-studies.md`, insert a new subsection immediately after `### Weight by open interest or volume`:

```markdown
### Metric: gamma or delta

The strike-bar profile is drawn from one of two Greeks, on whichever weighting
is selected.

| Metric | Reads |
|---|---|
| **Gamma (GEX)** | How hard dealers must hedge. Positive bars are call-dominant and stabilising |
| **Delta (DEX)** | Which way the open-interest book already leans. Positive bars are call-dominant |

Both are computed from the same option-chain fetch and arrive in one payload, so
switching between them costs no refetch and no extra broker call.

**DEX is the book's delta, not the dealer's.** Positive means calls dominate the
strike; dealers stand on the other side, so dealer delta is the negation. Unlike
GEX it carries no dealer-position sign convention: a call's delta is already
positive and a put's already negative, so applying one would make every strike
positive and the total meaningless.
```

- [ ] **Step 2: Verify the link target still resolves**

Run: `grep -n "Metric: gamma or delta" docs/chart-workspace-studies.md`
Expected: one match

- [ ] **Step 3: Commit**

```bash
git add docs/chart-workspace-studies.md
git commit -m "docs(gex-levels): document the Gamma/Delta metric toggle

Includes the part a reader cannot infer from the label: DEX is the
open-interest book's delta and dealers hold the negation, and it carries
no dealer-sign convention because delta already has its own."
```

---

## Final verification

- [ ] **Run the complete backend suite**

Run:
```bash
uv run python -m pytest test/test_gex_levels_delta.py test/test_gex_levels_endpoint.py \
  test/test_gex_levels_exposure.py test/test_gex_levels_math.py test/test_gex_levels_quality.py \
  test/test_gex_levels_sentiment.py test/test_gex_levels_service.py test/test_gex_levels_walls.py \
  test/test_gex_levels_zero_gamma.py test/test_gex_service.py -v
```
Expected: all pass, 140+ tests, zero failures

- [ ] **Run the complete frontend suite for these files**

Run: `cd frontend && npx vitest run src/lib/charts/ src/components/charts/workspace/`
Expected: all pass

- [ ] **Typecheck**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.app.json`
Expected: clean

- [ ] **Lint**

Run: `uv run ruff check services/ test/`
Expected: `All checks passed!`

- [ ] **Confirm nothing outside scope changed**

Run: `git status --porcelain -- . ':!frontend/dist'`
Expected: only `untitled.md` (the user's own untracked notes) remains

**No `fd-audit` run is needed for this plan** — it adds no database, socket, thread, subprocess or scheduler. That skill becomes mandatory in the next plan, which adds all of them.

**Live verification is not part of this plan.** The maths is pinned by a worked numeric example, and the study renders from stubbed data in tests. A live check requires a rebuilt `frontend/dist` and a server restart, which is the user's call.
