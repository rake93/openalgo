# GEX Levels Chart Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GEX Levels study to the `/charts` workspace that draws Call Wall, Put Wall and Zero-Gamma on the price axis, plus a per-strike signed gamma column and a numeric dashboard.

**Architecture:** Pure Black-76 math in `services/gex_levels/` with no IO, a thin orchestrator that fetches the option chain once, one new route on the existing GEX blueprint, and a frontend `GexLevelsManager` that mirrors `ProfileManager` — settings object driving primitive lifecycle, persisted with the layout.

**Tech Stack:** Python 3.12 via `uv`, `opengreeks` (Rust-core Black-76), Flask blueprints, pytest. React 19, TypeScript, `openalgo-charts` primitives, vitest, Biome.

**Spec:** `docs/superpowers/specs/2026-08-04-gex-levels-chart-study-design.md`

---

## Amendments during execution

Code review found four defects in this plan's own dictated code. The committed
implementation is correct; the Task 2, 4 and 5 code blocks below are superseded
in the ways listed here. Read this section before treating those blocks as
authoritative.

**1. `compute_exposures` had to be split (Task 2, Task 4).** As originally
written, `scan_zero_gamma` called `compute_exposures(forward=level)` at each of
60 scan levels — and `compute_exposures` derives implied volatility from the
premiums against whatever forward it is given. So each step re-inverted today's
observed premium against a forward the market never traded at, producing a
meaningless sigma that degenerates far from spot (the premium becomes
unattainable, `safe_iv` returns `None`, and it falls back to an ATM volatility
computed at the fake forward). Task 4's own docstring asserted the opposite.

The fix splits the seam:

```
resolve_ivs(...)       invert once, at the REAL forward -> ResolvedIVs
price_exposures(...)   gamma only, from pre-resolved IVs, at ANY forward
compute_exposures(...) the two composed - unchanged signature and behaviour
```

`scan_zero_gamma` resolves once and re-prices 60 times. This also removed about
5,500 redundant solver calls per request. Commit `1f5732b53`.

The original tests could not catch it: their gamma double ignored its `sigma`
argument. A recording double now pins it — measured 31 distinct sigmas before
the fix, 1 after.

**2. The scan returned the wrong crossing (Task 4).** It walked upward from 80%
of the forward and returned the FIRST sign change, i.e. the lowest-priced
crossing. A profile can flip several times; the regime boundary a trader means
is the crossing adjacent to spot — which is why the empty case reads "no LOCAL
cross". Now returns the crossing nearest the forward.

**3. The priced-share threshold needed `<=`, not `<` (Task 5).** `3/5 == 0.6`
is exactly True in IEEE754, so the strict form graded the 3-of-5 boundary case
"good" and its own test failed. Fixed inline below, with the rationale kept as
a code comment so nobody "tidies" it back.

**4. Smaller corrections carried in the same commits.** Non-finite guards
(a NaN first in a list wins BOTH walls, because every later comparison against
it is False); `weight_by` now raises on an unknown value instead of silently
reading as open interest; `Quality.may_draw` so the draw-or-not rule is not
tribal knowledge in a docstring; and the quality notes rewritten as copy a
trader reads in a panel rather than Black-76 solver vocabulary.

**5. Task 11's draft primitive used an API that does not exist.** It called
`priceScale.priceToCoordinate(...)`; the real method on `openalgo-charts`'
`PriceScale` is **`priceToY(price): number`**, returning a plain number.

The draft was also wrong about scaling. `PrimitiveRenderContext.plotWidth` and
`plotHeight` are **unscaled CSS pixels** — the canvas backing store is not
pre-scaled by device pixel ratio (see `pane.ts:paintBase`), so every draw call
must multiply by `rc.dpr` itself. That is the pattern `price-line.ts`,
`dom-ladder.ts` and `volume-profile-primitive.ts` all follow.

As drafted, Task 11 would not have compiled, and had it compiled it would have
rendered at the wrong scale on any HiDPI display. The shipped implementation was
written against the library source instead. **Read
`openalgo-charts/src/scale/price-scale.ts` and an existing primitive before
writing a new one** — do not trust a signature quoted in a plan, including this
one.

**Testing lesson worth carrying forward.** Every one of these survived
implementation and was caught in review. The implementers followed the plan
faithfully — the defects were in the plan. Where a test double ignores an
argument, it cannot pin that argument's behaviour; prefer doubles that record
what they were called with.

---

## Domain primer (read this first)

You are unlikely to know this domain. Four facts you need:

1. **Gamma Exposure (GEX)** measures how much delta-hedging dealers must do per 1% move in the underlying. Positive net GEX means dealers stabilise price (sell rallies, buy dips). Negative means they amplify moves **in both directions**.
2. **Black-76**, not Black-Scholes. Indian F&O options are priced off a **forward** (`F`), not spot. `opengreeks.black76` takes `(flag, F, K, t, r, sigma)` where `flag` is `'c'` or `'p'`, `t` is in **years**, `r` and `sigma` are **decimals** (0.065, not 6.5).
3. **A "strike count" is per side.** `strike_count=23` means 23 above ATM plus 23 below plus ATM = 47 strikes = 94 option symbols. **Never raise this.** `oi_tracker_service.py:142` documents why: the Fyers multiquote OI bucket holds ≤100 symbols, and exceeding it returns **empty OI rather than an error** — which would silently zero this entire feature.
4. **Sign convention:** calls contribute positive GEX, puts negative. This is the industry standard and encodes "dealers are long calls, short puts".

## File structure

**Create — pure math, no IO, no network, no clock:**

| File | Responsibility |
| --- | --- |
| `services/gex_levels/__init__.py` | Public re-exports |
| `services/gex_levels/blackscholes.py` | `safe_iv`, `safe_gamma`, `atm_iv_from` — extracted from `gamma_density_service` |
| `services/gex_levels/expiry.py` | `expiry_datetime` — extracted from `gamma_density_service` |
| `services/gex_levels/exposure.py` | `StrikeExposure`, `compute_exposures` |
| `services/gex_levels/levels.py` | `find_walls`, `scan_zero_gamma` |
| `services/gex_levels/quality.py` | `assess_quality` |
| `services/gex_levels_service.py` | Orchestration: chain fetch, forward, assembly |

**Create — frontend:**

| File | Responsibility |
| --- | --- |
| `frontend/src/lib/charts/gex-levels.ts` | `GexLevelsManager` — settings, poll, primitive lifecycle |
| `frontend/src/lib/charts/gex-levels-primitive.ts` | `GexLevelsPrimitive implements IPrimitive` |
| `frontend/src/components/charts/workspace/GexDashboard.tsx` | HTML overlay |

**Modify:**

| File | Change |
| --- | --- |
| `services/gamma_density_service.py` | Import the extracted helpers instead of defining them |
| `blueprints/gex.py` | Add `POST /gex/api/gex-levels` |
| `frontend/src/api/gex.ts` | Add types + `getGEXLevels` |
| `frontend/src/lib/charts/workspace.ts` | Construct, attach, snapshot/restore, dispose the manager |
| `frontend/src/components/charts/workspace/StudiesPanel.tsx` | Fourth section |
| `frontend/src/pages/charts/ChartWorkspace.tsx` | Panel props + dashboard overlay |
| `docs/chart-workspace-studies.md` | GEX Levels section |

---

## Task 1: Extract the shared Black-76 helpers

`gamma_density_service.py` defines four private helpers this feature needs. Importing `_`-prefixed names across service modules is a smell, and duplicating a Black-76 IV inversion is worse. Extract them, then rewire the original to import them — there must be exactly one inversion in the codebase.

**Files:**
- Create: `services/gex_levels/__init__.py`
- Create: `services/gex_levels/blackscholes.py`
- Create: `services/gex_levels/expiry.py`
- Modify: `services/gamma_density_service.py:26-124` (remove the local definitions, import instead)
- Test: `test/test_gex_levels_math.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_math.py`:

```python
"""Pure Black-76 helpers shared by Gamma Density and GEX Levels."""

import math

import pytest

from services.gex_levels.blackscholes import atm_iv_from, safe_gamma, safe_iv
from services.gex_levels.expiry import expiry_datetime


class _FakeBlack76:
    """Stands in for opengreeks.black76 so these tests need no Rust core."""

    def __init__(self, iv=0.15, gamma=0.0004, raises=False):
        self._iv = iv
        self._gamma = gamma
        self._raises = raises

    def implied_volatility(self, price, F, K, r, t, flag):
        if self._raises:
            raise ValueError("no solution")
        return self._iv

    def gamma(self, flag, F, K, t, r, sigma):
        if self._raises:
            raise ValueError("domain error")
        return self._gamma


def test_safe_iv_returns_the_inverted_value():
    assert safe_iv(_FakeBlack76(iv=0.184), 120.0, 24600.0, 24600.0, 0.065, 0.02, "c") == 0.184


@pytest.mark.parametrize("bad", [0.0, -0.1, 5.5, float("nan"), float("inf")])
def test_safe_iv_rejects_implausible_values(bad):
    assert safe_iv(_FakeBlack76(iv=bad), 120.0, 24600.0, 24600.0, 0.065, 0.02, "c") is None


@pytest.mark.parametrize(
    "price,F,K,t",
    [(0.0, 24600.0, 24600.0, 0.02), (120.0, 0.0, 24600.0, 0.02), (120.0, 24600.0, 0.0, 0.02), (120.0, 24600.0, 24600.0, 0.0)],
)
def test_safe_iv_rejects_non_positive_inputs(price, F, K, t):
    assert safe_iv(_FakeBlack76(), price, F, K, 0.065, t, "c") is None


def test_safe_iv_swallows_solver_failure():
    assert safe_iv(_FakeBlack76(raises=True), 120.0, 24600.0, 24600.0, 0.065, 0.02, "c") is None


def test_safe_gamma_returns_the_value():
    assert safe_gamma(_FakeBlack76(gamma=0.00031), "c", 24600.0, 24600.0, 0.02, 0.065, 0.15) == 0.00031


@pytest.mark.parametrize("bad", [-0.001, float("nan"), float("inf")])
def test_safe_gamma_floors_bad_values_at_zero(bad):
    assert safe_gamma(_FakeBlack76(gamma=bad), "c", 24600.0, 24600.0, 0.02, 0.065, 0.15) == 0.0


def test_safe_gamma_swallows_failure():
    assert safe_gamma(_FakeBlack76(raises=True), "c", 24600.0, 24600.0, 0.02, 0.065, 0.15) == 0.0


def test_atm_iv_prefers_the_atm_strike():
    per_strike = {24500.0: 0.20, 24600.0: 0.17, 24700.0: 0.22}
    assert atm_iv_from(per_strike, atm_strike=24600.0) == 0.17


def test_atm_iv_falls_back_to_the_median_when_atm_is_unpriced():
    per_strike = {24500.0: 0.10, 24600.0: None, 24700.0: 0.30, 24800.0: 0.20}
    assert atm_iv_from(per_strike, atm_strike=24600.0) == 0.20


def test_atm_iv_falls_back_to_the_constant_when_nothing_is_priced():
    assert atm_iv_from({24600.0: None}, atm_strike=24600.0) == 0.15


@pytest.mark.parametrize(
    "exchange,hour,minute",
    [("NFO", 15, 30), ("BFO", 15, 30), ("CDS", 12, 30), ("MCX", 23, 30)],
)
def test_expiry_datetime_uses_the_exchange_close(exchange, hour, minute):
    dt = expiry_datetime("11AUG26", exchange)
    assert (dt.year, dt.month, dt.day) == (2026, 8, 11)
    assert (dt.hour, dt.minute) == (hour, minute)


def test_expiry_datetime_accepts_lowercase_month():
    assert expiry_datetime("11aug26", "NFO").month == 8
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest test/test_gex_levels_math.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'services.gex_levels'`.

- [ ] **Step 3: Create the package and the expiry module**

Create `services/gex_levels/__init__.py`:

```python
"""
GEX Levels — pure Black-76 math for dealer gamma exposure.

Everything in this package is IO-free: plain inputs to plain outputs, no
network, no database, no clock beyond what is passed in. Broker-touching
orchestration lives in `services/gex_levels_service.py`.
"""
```

Create `services/gex_levels/expiry.py`:

```python
"""Expiry-string parsing shared by the options analytics services."""

from datetime import datetime

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def expiry_datetime(expiry_date: str, exchange: str) -> datetime:
    """
    Build an expiry datetime from a DDMMMYY string and its exchange.

    Uses the same default expiry times as
    `option_greeks_service.parse_option_symbol`: NFO/BFO 15:30, CDS 12:30,
    MCX 23:30.

    Args:
        expiry_date: Expiry in DDMMMYY format (e.g. 11AUG26).
        exchange: Options exchange (NFO, BFO, CDS, MCX, ...).

    Returns:
        Naive datetime at the exchange close, interpreted as IST downstream.
    """
    day = int(expiry_date[:2])
    month = _MONTH_MAP[expiry_date[2:5].upper()]
    year = 2000 + int(expiry_date[5:7])

    ex = exchange.upper()
    if ex == "MCX":
        hour, minute = 23, 30
    elif ex == "CDS":
        hour, minute = 12, 30
    else:  # NFO, BFO, crypto, equity
        hour, minute = 15, 30

    return datetime(year, month, day, hour, minute)
```

- [ ] **Step 4: Create the Black-76 helpers**

Create `services/gex_levels/blackscholes.py`:

```python
"""
Black-76 helpers, hardened against the numerical failures a live option chain
produces every session: unpriced strikes, stale premiums that will not invert,
and deep-ITM legs whose solver diverges.

Every function fails to a stated value rather than raising, because one bad
strike must not destroy a whole chain's worth of levels.
"""

import math

# Used only when not one strike in the chain yields an invertible IV, e.g. a
# fully stale chain with no usable premiums. Keeps the levels drawable and is
# reported through the quality gate so it never passes as a market reading.
FALLBACK_IV = 0.15

# Above this, an "IV" is a solver artefact rather than a market volatility.
_MAX_PLAUSIBLE_IV = 5.0


def safe_iv(black76, price: float, F: float, K: float, r: float, t: float, flag: str) -> float | None:
    """
    Black-76 implied volatility as a decimal, or None if it cannot be inverted.

    Args:
        black76: The opengreeks.black76 module (injected so this stays pure).
        price: Option premium.
        F: Forward price of the underlying.
        K: Strike.
        r: Risk-free rate as a decimal (0.065, not 6.5).
        t: Time to expiry in years.
        flag: 'c' for a call, 'p' for a put.

    Returns:
        The IV as a decimal, or None when the inputs are non-positive, the
        solver raises, or the result is non-finite or implausible.
    """
    if not price or price <= 0 or F <= 0 or K <= 0 or t <= 0:
        return None
    try:
        iv = black76.implied_volatility(price, F, K, r, t, flag)
    except Exception:
        return None
    if iv is None or not math.isfinite(iv) or iv <= 0 or iv > _MAX_PLAUSIBLE_IV:
        return None
    return iv


def safe_gamma(black76, flag: str, F: float, K: float, t: float, r: float, sigma: float) -> float:
    """
    Black-76 gamma, or 0.0 on any numerical failure.

    Zero is the correct failure value here: a strike whose gamma cannot be
    computed contributes no hedging pressure to the profile.
    """
    if not sigma or sigma <= 0 or F <= 0 or K <= 0 or t <= 0:
        return 0.0
    try:
        g = black76.gamma(flag, F, K, t, r, sigma)
    except Exception:
        return 0.0
    if g is None or not math.isfinite(g) or g < 0:
        return 0.0
    return g


def atm_iv_from(per_strike_iv: dict[float, float | None], atm_strike: float | None) -> float:
    """
    The volatility to price the whole chain with when a strike has none of its own.

    Prefers the ATM strike's own IV. Falls back to the median of every
    invertible strike IV, which is robust to the handful of far-OTM strikes
    whose premiums are a tick and whose inverted IV is nonsense. Falls back
    finally to FALLBACK_IV.

    Args:
        per_strike_iv: Strike to its IV (decimal), or None where it did not invert.
        atm_strike: The at-the-money strike, or None if unknown.

    Returns:
        An IV as a decimal. Never None.
    """
    if atm_strike is not None:
        at_the_money = per_strike_iv.get(atm_strike)
        if at_the_money is not None:
            return at_the_money

    valid = sorted(v for v in per_strike_iv.values() if v is not None)
    if valid:
        return valid[len(valid) // 2]
    return FALLBACK_IV
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest test/test_gex_levels_math.py -v
```

Expected: PASS — 20 tests.

- [ ] **Step 6: Rewire `gamma_density_service` to the extracted helpers**

In `services/gamma_density_service.py`, delete the local `_MONTH_MAP`, `_expiry_datetime`, `_safe_iv`, `_safe_gamma` and `_FALLBACK_IV` definitions (lines 51-124), and replace the import block at the top with:

```python
from services.gex_levels.blackscholes import FALLBACK_IV as _FALLBACK_IV
from services.gex_levels.blackscholes import atm_iv_from, safe_gamma, safe_iv
from services.gex_levels.expiry import expiry_datetime as _expiry_datetime
```

Then replace the two call sites so the names resolve — `_safe_iv(` becomes `safe_iv(` and `_safe_gamma(` becomes `safe_gamma(` throughout the file.

Finally, replace the ATM IV fallback block (the `if atm_iv is None:` branch) with:

```python
        # ATM IV, with median-of-valid then constant fallback.
        atm_iv = atm_iv_from({s["strike"]: s["strike_iv"] for s in strikes}, atm_strike)
```

- [ ] **Step 7: Verify Gamma Density still imports and its own tests pass**

```bash
uv run python -c "import services.gamma_density_service; print('ok')"
uv run pytest test/ -k "gamma or density" -v
```

Expected: `ok`, and no failures. If there is no existing Gamma Density test, the import check alone is the gate — record that in the commit message.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check services/gex_levels services/gamma_density_service.py test/test_gex_levels_math.py --fix
uv run ruff format services/gex_levels services/gamma_density_service.py test/test_gex_levels_math.py
git add services/gex_levels test/test_gex_levels_math.py services/gamma_density_service.py
git commit -m "refactor(gex-levels): extract the shared Black-76 helpers

Gamma Density defined safe_iv, safe_gamma, the ATM IV fallback and the
DDMMMYY expiry parser privately. GEX Levels needs all four, and a second
Black-76 inversion in the codebase would be a correctness hazard rather
than a convenience. Lifted to services/gex_levels/, which is IO-free.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Per-strike signed exposure

**Files:**
- Create: `services/gex_levels/exposure.py`
- Test: `test/test_gex_levels_exposure.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_exposure.py`:

```python
"""Per-strike signed dealer gamma exposure."""

import json

import pytest

from services.gex_levels.exposure import ChainRow, compute_exposures


class _FlatGamma:
    """Gamma independent of strike, so exposure arithmetic is checkable by hand."""

    def __init__(self, gamma=0.001):
        self._gamma = gamma

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def gamma(self, flag, F, K, t, r, sigma):
        return self._gamma


def _rows():
    return [
        ChainRow(strike=24500.0, call_price=180.0, put_price=40.0, call_oi=1000, put_oi=4000,
                 call_volume=100, put_volume=900, lot_size=75),
        ChainRow(strike=24600.0, call_price=120.0, put_price=80.0, call_oi=3000, put_oi=3000,
                 call_volume=500, put_volume=500, lot_size=75),
    ]


def test_calls_are_positive_and_puts_negative():
    out = compute_exposures(_FlatGamma(), _rows(), forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="oi")
    first = out[0]
    assert first.call_gex > 0
    assert first.put_gex < 0


def test_net_is_the_signed_sum():
    out = compute_exposures(_FlatGamma(), _rows(), forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="oi")
    for row in out:
        assert row.net_gex == pytest.approx(row.call_gex + row.put_gex)


def test_a_balanced_strike_nets_to_zero():
    """Equal call and put OI at one gamma must cancel exactly."""
    out = compute_exposures(_FlatGamma(), _rows(), forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="oi")
    assert out[1].net_gex == pytest.approx(0.0)


def test_the_notional_scaling_is_applied():
    """GEX = gamma * weight * lot * F^2 * 0.01, calls positive."""
    out = compute_exposures(_FlatGamma(gamma=0.001), _rows(), forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="oi")
    expected = 0.001 * 1000 * 75 * (24600.0 ** 2) * 0.01
    assert out[0].call_gex == pytest.approx(expected)


def test_volume_weighting_uses_volume_not_oi():
    oi = compute_exposures(_FlatGamma(), _rows(), forward=24600.0, t_years=0.02,
                           r=0.065, atm_strike=24600.0, weight_by="oi")
    vol = compute_exposures(_FlatGamma(), _rows(), forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="volume")
    # Row 0 carries OI 1000/4000 against volume 100/900 — different magnitudes.
    assert vol[0].call_gex == pytest.approx(oi[0].call_gex / 10.0)


def test_rows_are_returned_in_ascending_strike_order():
    shuffled = list(reversed(_rows()))
    out = compute_exposures(_FlatGamma(), shuffled, forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="oi")
    assert [r.strike for r in out] == [24500.0, 24600.0]


def test_every_field_is_json_serialisable_without_nan():
    """float('inf') serialises as Infinity, which JSON.parse rejects outright."""
    out = compute_exposures(_FlatGamma(), _rows(), forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="oi")
    payload = [{"strike": r.strike, "call_gex": r.call_gex,
                "put_gex": r.put_gex, "net_gex": r.net_gex} for r in out]
    json.dumps(payload, allow_nan=False)


def test_an_unpriced_strike_contributes_zero_rather_than_being_dropped():
    rows = [ChainRow(strike=24500.0, call_price=0.0, put_price=0.0, call_oi=0, put_oi=0,
                     call_volume=0, put_volume=0, lot_size=75)]
    out = compute_exposures(_FlatGamma(), rows, forward=24600.0, t_years=0.02,
                            r=0.065, atm_strike=24600.0, weight_by="oi")
    assert len(out) == 1
    assert out[0].net_gex == 0.0
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest test/test_gex_levels_exposure.py -v
```

Expected: `ModuleNotFoundError: No module named 'services.gex_levels.exposure'`.

- [ ] **Step 3: Write the implementation**

Create `services/gex_levels/exposure.py`:

```python
"""
Per-strike signed dealer gamma exposure.

    GEX_k = gamma_k(call) * w_k(call) * lot * F^2 * 0.01
          - gamma_k(put)  * w_k(put)  * lot * F^2 * 0.01

Calls positive, puts negative. That is the standard convention across every
published GEX product and encodes the approximation that dealers are long
calls and short puts at the index level. It is deliberately a single constant
here rather than a setting — if Indian market structure is ever shown to
warrant inverting it, DEALER_CALL_SIGN is the one place to change.

Units are currency delta change per 1% move in the underlying. The F^2 * 0.01
factor is constant across strikes, so it moves neither the walls nor the
zero-gamma level relative to an unscaled profile — it converts units only.
"""

from dataclasses import dataclass
from typing import Literal

from services.gex_levels.blackscholes import safe_gamma, safe_iv

WeightBy = Literal["oi", "volume"]

DEALER_CALL_SIGN = 1.0
DEALER_PUT_SIGN = -1.0

# Converts unit gamma into delta change per 1% move: (0.01 * F)^2 / F^2 * F^2.
_ONE_PERCENT = 0.01


@dataclass(frozen=True)
class ChainRow:
    """One strike of the option chain, both legs, as fetched."""

    strike: float
    call_price: float
    put_price: float
    call_oi: float
    put_oi: float
    call_volume: float
    put_volume: float
    lot_size: int


@dataclass(frozen=True)
class StrikeExposure:
    """Signed gamma exposure at one strike, in currency per 1% move."""

    strike: float
    call_gex: float
    put_gex: float
    net_gex: float
    call_iv: float | None
    put_iv: float | None


def compute_exposures(
    black76,
    rows: list[ChainRow],
    forward: float,
    t_years: float,
    r: float,
    atm_strike: float | None,
    weight_by: WeightBy,
) -> list[StrikeExposure]:
    """
    Signed GEX for every strike, ascending.

    Two passes, because a strike whose own premium will not invert must still
    be priced — with the chain's ATM volatility — rather than dropped. Dropping
    it would move the walls by removing real open interest from the profile.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows, any order.
        forward: Per-expiry forward price (F). Never spot.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        atm_strike: ATM strike, for the IV fallback.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        One StrikeExposure per input row, sorted by strike ascending.
    """
    from services.gex_levels.blackscholes import atm_iv_from

    ordered = sorted(rows, key=lambda row: row.strike)

    per_strike_iv: dict[float, float | None] = {}
    call_ivs: dict[float, float | None] = {}
    put_ivs: dict[float, float | None] = {}
    for row in ordered:
        call_iv = safe_iv(black76, row.call_price, forward, row.strike, r, t_years, "c")
        put_iv = safe_iv(black76, row.put_price, forward, row.strike, r, t_years, "p")
        call_ivs[row.strike] = call_iv
        put_ivs[row.strike] = put_iv
        sides = [v for v in (call_iv, put_iv) if v is not None]
        per_strike_iv[row.strike] = sum(sides) / len(sides) if sides else None

    fallback_iv = atm_iv_from(per_strike_iv, atm_strike)
    notional = forward * forward * _ONE_PERCENT

    out: list[StrikeExposure] = []
    for row in ordered:
        call_iv = call_ivs[row.strike]
        put_iv = put_ivs[row.strike]
        call_weight = row.call_volume if weight_by == "volume" else row.call_oi
        put_weight = row.put_volume if weight_by == "volume" else row.put_oi

        call_gamma = safe_gamma(black76, "c", forward, row.strike, t_years, r, call_iv or fallback_iv)
        put_gamma = safe_gamma(black76, "p", forward, row.strike, t_years, r, put_iv or fallback_iv)

        call_gex = DEALER_CALL_SIGN * call_gamma * call_weight * row.lot_size * notional
        put_gex = DEALER_PUT_SIGN * put_gamma * put_weight * row.lot_size * notional

        out.append(
            StrikeExposure(
                strike=row.strike,
                call_gex=call_gex,
                put_gex=put_gex,
                net_gex=call_gex + put_gex,
                call_iv=call_iv,
                put_iv=put_iv,
            )
        )
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test/test_gex_levels_exposure.py -v
```

Expected: PASS — 8 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff check services/gex_levels test/test_gex_levels_exposure.py --fix
uv run ruff format services/gex_levels test/test_gex_levels_exposure.py
git add services/gex_levels/exposure.py test/test_gex_levels_exposure.py
git commit -m "feat(gex-levels): compute signed per-strike gamma exposure

Calls positive, puts negative, scaled by lot size and F^2 * 0.01 so the
units are currency per 1% move. A strike whose own premium will not invert
is priced with the chain's ATM volatility rather than dropped, since
dropping it would remove real open interest from the profile and move the
walls.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Call Wall and Put Wall

**Files:**
- Create: `services/gex_levels/levels.py`
- Test: `test/test_gex_levels_walls.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_walls.py`:

```python
"""Call Wall and Put Wall selection."""

from services.gex_levels.exposure import StrikeExposure
from services.gex_levels.levels import find_walls


def _exposure(strike, net, call=None, put=None):
    call = net if call is None else call
    put = 0.0 if put is None else put
    return StrikeExposure(strike=strike, call_gex=call, put_gex=put,
                          net_gex=net, call_iv=0.2, put_iv=0.2)


def test_call_wall_is_the_largest_positive_net():
    rows = [_exposure(24500, -50.0), _exposure(24600, 10.0), _exposure(24800, 90.0)]
    assert find_walls(rows).call_wall == 24800


def test_put_wall_is_the_most_negative_net():
    rows = [_exposure(24500, -50.0), _exposure(24600, 10.0), _exposure(24800, 90.0)]
    assert find_walls(rows).put_wall == 24500


def test_both_walls_may_be_the_same_strike():
    """One dominant strike can hold both extremes. Nothing may assume they differ."""
    rows = [_exposure(29500, 0.0, call=500.0, put=-900.0)]
    walls = find_walls(rows)
    assert walls.call_wall == 29500
    assert walls.put_wall == 29500


def test_an_all_positive_profile_still_reports_a_put_wall():
    """The least positive strike is the put wall; None would read as 'no support'."""
    rows = [_exposure(24500, 10.0), _exposure(24600, 90.0)]
    walls = find_walls(rows)
    assert walls.call_wall == 24600
    assert walls.put_wall == 24500


def test_empty_input_yields_no_walls():
    walls = find_walls([])
    assert walls.call_wall is None
    assert walls.put_wall is None


def test_walls_report_whether_they_sit_at_the_window_edge():
    """A wall on the first or last strike may be an artefact of the fetch window."""
    rows = [_exposure(24500, -50.0), _exposure(24600, 10.0), _exposure(24800, 90.0)]
    walls = find_walls(rows)
    assert walls.call_wall_at_edge is True
    assert walls.put_wall_at_edge is True

    interior = [_exposure(24400, 0.0), _exposure(24500, -50.0),
                _exposure(24800, 90.0), _exposure(24900, 0.0)]
    assert find_walls(interior).call_wall_at_edge is False
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest test/test_gex_levels_walls.py -v
```

Expected: `ImportError: cannot import name 'find_walls'`.

- [ ] **Step 3: Write the implementation**

Create `services/gex_levels/levels.py`:

```python
"""
The three levels GEX Levels draws: Call Wall, Put Wall and Zero-Gamma.

Walls are the signed extremes of the per-strike profile. Zero-Gamma is not a
strike at all — see `scan_zero_gamma`.
"""

from dataclasses import dataclass

from services.gex_levels.exposure import StrikeExposure


@dataclass(frozen=True)
class Walls:
    """The two gamma concentrations, and whether either is a window artefact."""

    call_wall: float | None
    put_wall: float | None
    call_wall_at_edge: bool
    put_wall_at_edge: bool


def find_walls(exposures: list[StrikeExposure]) -> Walls:
    """
    Call Wall is the strike with the greatest net GEX, Put Wall the least.

    Both may be the same strike — a single dominant expiry-day strike routinely
    holds the largest call gamma and the largest put gamma at once, so no caller
    may assume they differ.

    A wall landing on the first or last strike of the fetched window is flagged:
    it may be a real concentration, or it may simply be where the window stopped.
    The quality gate turns that flag into a user-visible caveat.

    Args:
        exposures: Per-strike exposures, ascending by strike.

    Returns:
        Walls, with None levels when there is nothing to rank.
    """
    if not exposures:
        return Walls(call_wall=None, put_wall=None, call_wall_at_edge=False, put_wall_at_edge=False)

    call = max(exposures, key=lambda e: e.net_gex)
    put = min(exposures, key=lambda e: e.net_gex)
    edges = {exposures[0].strike, exposures[-1].strike}

    return Walls(
        call_wall=call.strike,
        put_wall=put.strike,
        call_wall_at_edge=call.strike in edges,
        put_wall_at_edge=put.strike in edges,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test/test_gex_levels_walls.py -v
```

Expected: PASS — 6 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff check services/gex_levels test/test_gex_levels_walls.py --fix
uv run ruff format services/gex_levels test/test_gex_levels_walls.py
git add services/gex_levels/levels.py test/test_gex_levels_walls.py
git commit -m "feat(gex-levels): select the call and put walls

Both may legitimately be the same strike, so nothing assumes they differ.
A wall on the first or last strike of the fetch window is flagged as a
possible window artefact rather than reported as a concentration.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: The zero-gamma profile scan

The important task. **Zero-Gamma is not the strike where a running total crosses zero.** It is the *price* at which aggregate dealer gamma changes sign, found by re-pricing every contract's gamma at a range of hypothetical forwards. That is why real products report values between strikes (`7532.43`), and why "no crossing" is a normal outcome rather than an error.

**Files:**
- Modify: `services/gex_levels/levels.py` (append)
- Test: `test/test_gex_levels_zero_gamma.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_zero_gamma.py`:

```python
"""The zero-gamma scan: re-price the profile across hypothetical forwards."""

from services.gex_levels.exposure import ChainRow
from services.gex_levels.levels import scan_zero_gamma


class _PeakedGamma:
    """
    Gamma peaked at the strike, so the sign of the aggregate profile depends on
    where the hypothetical forward sits relative to the call and put strikes.
    Crude but monotone, which is all the scan needs to be exercised.
    """

    def implied_volatility(self, price, F, K, r, t, flag):
        return 0.20

    def gamma(self, flag, F, K, t, r, sigma):
        return 1.0 / (1.0 + abs(F - K))


def _split_chain():
    """Puts concentrated low, calls concentrated high — a profile that must cross."""
    return [
        ChainRow(strike=24000.0, call_price=10.0, put_price=100.0, call_oi=0, put_oi=10000,
                 call_volume=0, put_volume=10000, lot_size=75),
        ChainRow(strike=25000.0, call_price=100.0, put_price=10.0, call_oi=10000, put_oi=0,
                 call_volume=10000, put_volume=0, lot_size=75),
    ]


def test_a_crossing_profile_returns_a_price_between_the_strikes():
    level = scan_zero_gamma(_PeakedGamma(), _split_chain(), forward=24500.0,
                            t_years=0.02, r=0.065, atm_strike=24500.0, weight_by="oi")
    assert level is not None
    assert 24000.0 < level < 25000.0


def test_the_level_need_not_land_on_a_strike():
    """Interpolation between scan steps is what makes a sub-strike level possible."""
    level = scan_zero_gamma(_PeakedGamma(), _split_chain(), forward=24500.0,
                            t_years=0.02, r=0.065, atm_strike=24500.0, weight_by="oi")
    assert level not in (24000.0, 25000.0)


def test_a_one_sided_profile_reports_no_crossing():
    """All calls, no puts: gamma is positive everywhere, so there is no flip."""
    calls_only = [
        ChainRow(strike=24000.0, call_price=100.0, put_price=0.0, call_oi=10000, put_oi=0,
                 call_volume=10000, put_volume=0, lot_size=75),
        ChainRow(strike=25000.0, call_price=100.0, put_price=0.0, call_oi=10000, put_oi=0,
                 call_volume=10000, put_volume=0, lot_size=75),
    ]
    assert scan_zero_gamma(_PeakedGamma(), calls_only, forward=24500.0, t_years=0.02,
                           r=0.065, atm_strike=24500.0, weight_by="oi") is None


def test_an_empty_chain_reports_no_crossing():
    assert scan_zero_gamma(_PeakedGamma(), [], forward=24500.0, t_years=0.02,
                           r=0.065, atm_strike=24500.0, weight_by="oi") is None


def test_a_non_positive_forward_reports_no_crossing():
    assert scan_zero_gamma(_PeakedGamma(), _split_chain(), forward=0.0, t_years=0.02,
                           r=0.065, atm_strike=24500.0, weight_by="oi") is None


def test_the_level_lies_inside_the_scan_range():
    forward = 24500.0
    level = scan_zero_gamma(_PeakedGamma(), _split_chain(), forward=forward,
                            t_years=0.02, r=0.065, atm_strike=24500.0, weight_by="oi")
    assert forward * 0.8 <= level <= forward * 1.2
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest test/test_gex_levels_zero_gamma.py -v
```

Expected: `ImportError: cannot import name 'scan_zero_gamma'`.

- [ ] **Step 3: Write the implementation**

Append to `services/gex_levels/levels.py`:

```python
# Scan window around the forward, and how finely it is sampled. Twenty percent
# is the span the published methodologies use; sixty steps puts a sample every
# ~0.67% of the forward, which is finer than any real strike ladder.
SCAN_RANGE_PCT = 0.20
SCAN_STEPS = 60


def scan_zero_gamma(
    black76,
    rows: list[ChainRow],
    forward: float,
    t_years: float,
    r: float,
    atm_strike: float | None,
    weight_by: WeightBy,
) -> float | None:
    """
    The price at which aggregate dealer gamma changes sign.

    NOT the strike where a running total of per-strike GEX crosses zero — that
    is a different quantity and can only ever land on a strike. Gamma itself
    depends on where the underlying is, so the profile has to be rebuilt at each
    hypothetical price:

      1. Sample SCAN_STEPS forward levels across +/- SCAN_RANGE_PCT of `forward`.
      2. At each, recompute EVERY contract's gamma with Black-76 — `t` and
         `sigma` held fixed, only F varies — and sum the signed exposure.
      3. Find the first sign change and interpolate linearly between the two
         bracketing samples.

    Volatility is held at each strike's own IV, inverted once at the real
    forward. Re-inverting at every scan level would be both far more expensive
    and wrong: the premiums observed are the ones at today's forward.

    Returns:
        The interpolated price, or None when the profile does not cross zero
        anywhere in the window. None is a normal outcome — a chain can be long
        gamma or short gamma across its whole plausible range — and callers
        must render it as "no local cross", not as an error.
    """
    if not rows or forward <= 0 or t_years <= 0:
        return None

    lo = forward * (1.0 - SCAN_RANGE_PCT)
    hi = forward * (1.0 + SCAN_RANGE_PCT)
    step = (hi - lo) / (SCAN_STEPS - 1)

    previous_level: float | None = None
    previous_total: float | None = None

    for i in range(SCAN_STEPS):
        level = lo + step * i
        total = sum(
            e.net_gex
            for e in compute_exposures(
                black76, rows, forward=level, t_years=t_years, r=r,
                atm_strike=atm_strike, weight_by=weight_by,
            )
        )

        if previous_total is not None and _crosses_zero(previous_total, total):
            return _interpolate_zero(previous_level, previous_total, level, total)

        previous_level = level
        previous_total = total

    return None


def _crosses_zero(before: float, after: float) -> bool:
    """True when the sign changes between two consecutive samples."""
    if before == 0.0:
        return True
    return (before < 0.0) != (after < 0.0)


def _interpolate_zero(x0: float, y0: float, x1: float, y1: float) -> float:
    """
    Linear interpolation to the zero of the segment (x0, y0) -> (x1, y1).

    This is what lets the level land between strikes, which is the whole point
    of the scan. Falls back to the left endpoint when the segment is flat, which
    can only happen when y0 is already zero.
    """
    if y1 == y0:
        return x0
    return x0 + (x1 - x0) * (-y0) / (y1 - y0)
```

Add the imports this needs at the top of `services/gex_levels/levels.py`:

```python
from services.gex_levels.exposure import ChainRow, StrikeExposure, WeightBy, compute_exposures
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test/test_gex_levels_zero_gamma.py -v
```

Expected: PASS — 6 tests.

- [ ] **Step 5: Run the whole GEX suite to check nothing regressed**

```bash
uv run pytest test/ -k "gex_levels" -v
```

Expected: PASS — 40 tests across the four files.

- [ ] **Step 6: Commit**

```bash
uv run ruff check services/gex_levels test/test_gex_levels_zero_gamma.py --fix
uv run ruff format services/gex_levels test/test_gex_levels_zero_gamma.py
git add services/gex_levels/levels.py test/test_gex_levels_zero_gamma.py
git commit -m "feat(gex-levels): scan for the zero-gamma level

Re-prices every contract's gamma across 60 hypothetical forwards spanning
+/-20%, then interpolates the sign change. A cumulative sum of per-strike
GEX is a different quantity and can only land on a strike; this is what
produces a level between strikes.

No crossing is a normal outcome, not an error - a chain can be long or
short gamma across its whole plausible range - so it returns None for the
UI to render as 'no local cross'.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: The data-quality gate

`direction.ts` set the rule this codebase follows: *"A missing input never reads as a zero — no open interest is not the same as open interest that did not change."* A thin or half-unpriced chain must announce itself rather than render as confident levels.

**Files:**
- Create: `services/gex_levels/quality.py`
- Test: `test/test_gex_levels_quality.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_quality.py`:

```python
"""The data-quality verdict that stops a thin chain reading as signal."""

from services.gex_levels.exposure import StrikeExposure
from services.gex_levels.levels import Walls
from services.gex_levels.quality import assess_quality


def _exposures(strikes, priced=True):
    return [
        StrikeExposure(strike=float(s), call_gex=1.0, put_gex=-1.0, net_gex=0.0,
                       call_iv=0.2 if priced else None, put_iv=0.2 if priced else None)
        for s in strikes
    ]


def _walls(call_edge=False, put_edge=False):
    return Walls(call_wall=24600.0, put_wall=24500.0,
                 call_wall_at_edge=call_edge, put_wall_at_edge=put_edge)


def test_a_full_priced_two_sided_chain_is_good():
    q = assess_quality(_exposures(range(24400, 24900, 100)), _walls(),
                       forward=24600.0, total_weight=500000.0)
    assert q.verdict == "good"
    assert q.notes == []


def test_a_mostly_unpriced_chain_is_degraded_and_says_why():
    rows = _exposures([24400, 24500], priced=False) + _exposures([24600, 24700, 24800])
    q = assess_quality(rows, _walls(), forward=24600.0, total_weight=500000.0)
    assert q.verdict == "degraded"
    assert any("invert" in n for n in q.notes)


def test_a_one_sided_window_is_degraded():
    """Every strike above the forward: a 'put wall' is just the window edge."""
    q = assess_quality(_exposures([24700, 24800, 24900]), _walls(),
                       forward=24600.0, total_weight=500000.0)
    assert q.verdict == "degraded"
    assert q.both_sides is False


def test_a_wall_at_the_window_edge_is_called_out():
    q = assess_quality(_exposures(range(24400, 24900, 100)), _walls(call_edge=True),
                       forward=24600.0, total_weight=500000.0)
    assert q.wall_at_edge is True
    assert any("edge" in n for n in q.notes)


def test_a_chain_with_no_weight_at_all_is_unusable():
    q = assess_quality(_exposures(range(24400, 24900, 100)), _walls(),
                       forward=24600.0, total_weight=0.0)
    assert q.verdict == "unusable"


def test_an_empty_chain_is_unusable():
    q = assess_quality([], Walls(None, None, False, False),
                       forward=24600.0, total_weight=0.0)
    assert q.verdict == "unusable"
    assert q.strikes_used == 0


def test_counts_are_reported_for_the_data_status_row():
    rows = _exposures([24400], priced=False) + _exposures([24500, 24600])
    q = assess_quality(rows, _walls(), forward=24600.0, total_weight=500000.0)
    assert q.strikes_used == 3
    assert q.strikes_priced == 2
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest test/test_gex_levels_quality.py -v
```

Expected: `ModuleNotFoundError: No module named 'services.gex_levels.quality'`.

- [ ] **Step 3: Write the implementation**

Create `services/gex_levels/quality.py`:

```python
"""
The data-quality verdict for a GEX snapshot.

Follows the rule `direction.ts` set for this codebase: a missing input must
never read as a zero. A chain of stale premiums, or one whose window sits
entirely on one side of the forward, still produces numbers - and those numbers
would look exactly like a real reading unless something says otherwise.
"""

from dataclasses import dataclass, field
from typing import Literal

from services.gex_levels.exposure import StrikeExposure
from services.gex_levels.levels import Walls

Verdict = Literal["good", "degraded", "unusable"]

# At or below this share of strikes yielding an invertible IV, the profile is
# being driven by the fallback volatility rather than by the market.
#
# The comparison below MUST be `<=`, not `<`. The 3-priced-of-5 boundary case
# is exactly 0.6 in IEEE754 (`3/5 == 0.6` is True in Python), so a strict `<`
# grades it "good" and the test for it fails.
_MIN_PRICED_SHARE = 0.6


@dataclass(frozen=True)
class Quality:
    """What the caller may safely conclude from this snapshot."""

    verdict: Verdict
    strikes_used: int
    strikes_priced: int
    both_sides: bool
    wall_at_edge: bool
    notes: list[str] = field(default_factory=list)


def assess_quality(
    exposures: list[StrikeExposure],
    walls: Walls,
    forward: float,
    total_weight: float,
) -> Quality:
    """
    Grade a snapshot and name every reason it is not clean.

    Args:
        exposures: Per-strike exposures, ascending.
        walls: The selected walls, carrying their at-edge flags.
        forward: The forward the chain was priced against.
        total_weight: Summed OI or volume across both legs, on the selected
            weighting. Zero means the chain carried no positions at all.

    Returns:
        Quality. 'unusable' means do not draw levels; 'degraded' means draw
        them with the caveat shown.
    """
    used = len(exposures)
    priced = sum(1 for e in exposures if e.call_iv is not None or e.put_iv is not None)
    below = any(e.strike < forward for e in exposures)
    above = any(e.strike > forward for e in exposures)
    both_sides = below and above
    wall_at_edge = walls.call_wall_at_edge or walls.put_wall_at_edge

    notes: list[str] = []

    if used == 0 or total_weight <= 0:
        return Quality(
            verdict="unusable",
            strikes_used=used,
            strikes_priced=priced,
            both_sides=both_sides,
            wall_at_edge=wall_at_edge,
            notes=["No open interest or volume in the fetched chain"],
        )

    degraded = False

    if priced / used <= _MIN_PRICED_SHARE:
        degraded = True
        notes.append(
            f"Only {priced} of {used} strikes invert to an implied volatility; "
            "the rest are priced at the chain's ATM volatility"
        )

    if not both_sides:
        degraded = True
        notes.append("The fetched strikes sit entirely on one side of the forward")

    if wall_at_edge:
        degraded = True
        notes.append("A wall sits at the edge of the fetched window and may be a window artefact")

    return Quality(
        verdict="degraded" if degraded else "good",
        strikes_used=used,
        strikes_priced=priced,
        both_sides=both_sides,
        wall_at_edge=wall_at_edge,
        notes=notes,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test/test_gex_levels_quality.py -v
```

Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff check services/gex_levels test/test_gex_levels_quality.py --fix
uv run ruff format services/gex_levels test/test_gex_levels_quality.py
git add services/gex_levels/quality.py test/test_gex_levels_quality.py
git commit -m "feat(gex-levels): grade the snapshot's data quality

A stale or one-sided chain still produces numbers that look exactly like a
real reading. Follows the rule direction.ts set: a missing input never
reads as a zero. Names every reason a snapshot is not clean so the
dashboard's data-status row can say it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: The orchestrator

**Files:**
- Create: `services/gex_levels_service.py`
- Test: `test/test_gex_levels_service.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_service.py`:

```python
"""Orchestration: chain fetch to assembled payload. The chain is stubbed."""

import json

from unittest.mock import patch

from services.gex_levels_service import STRIKE_COUNT, get_gex_levels


def _chain_response():
    """Five strikes, both legs, shaped exactly as option_chain_service returns."""
    rows = []
    for strike in (24400, 24500, 24600, 24700, 24800):
        rows.append({
            "strike": float(strike),
            "ce": {"symbol": f"NIFTY11AUG26{strike}CE", "ltp": 120.0, "oi": 100000,
                   "volume": 5000, "lotsize": 75},
            "pe": {"symbol": f"NIFTY11AUG26{strike}PE", "ltp": 110.0, "oi": 90000,
                   "volume": 4000, "lotsize": 75},
        })
    return {"status": "success", "chain": rows, "atm_strike": 24600.0,
            "underlying_ltp": 24590.0, "underlying": "NIFTY"}


def test_the_chain_is_fetched_at_the_multiquote_safe_strike_count():
    """23 each side = 94 symbols, inside the Fyers OI bucket. Never raise it."""
    assert STRIKE_COUNT == 23


def test_a_successful_call_returns_levels_and_a_quality_verdict():
    with patch("services.gex_levels_service.get_option_chain",
               return_value=(True, _chain_response(), 200)), \
         patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0):
        ok, payload, status = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    assert ok is True
    assert status == 200
    assert payload["status"] == "success"
    assert payload["forward_price"] == 24610.0
    assert payload["call_wall"] is not None
    assert payload["put_wall"] is not None
    assert payload["regime"] in ("suppressive", "amplifying")
    assert payload["quality"]["strikes_used"] == 5


def test_the_payload_never_contains_a_non_finite_float():
    """float('inf') serialises as Infinity, which JSON.parse rejects outright."""
    with patch("services.gex_levels_service.get_option_chain",
               return_value=(True, _chain_response(), 200)), \
         patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0):
        _, payload, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    json.dumps(payload, allow_nan=False)


def test_it_falls_back_to_spot_when_the_forward_cannot_be_resolved():
    with patch("services.gex_levels_service.get_option_chain",
               return_value=(True, _chain_response(), 200)), \
         patch("services.gex_levels_service._resolve_forward_price", return_value=None):
        _, payload, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    assert payload["forward_price"] == 24590.0


def test_a_chain_failure_is_passed_through():
    failure = {"status": "error", "message": "No strikes"}
    with patch("services.gex_levels_service.get_option_chain",
               return_value=(False, failure, 404)):
        ok, payload, status = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    assert ok is False
    assert status == 404


def test_volume_weighting_produces_a_different_profile_than_oi():
    with patch("services.gex_levels_service.get_option_chain",
               return_value=(True, _chain_response(), 200)), \
         patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0):
        _, by_oi, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")
        _, by_vol, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="volume")

    assert by_oi["net_gex"] != by_vol["net_gex"]
    assert by_vol["weight_by"] == "volume"


def test_regime_follows_the_sign_of_net_gex():
    with patch("services.gex_levels_service.get_option_chain",
               return_value=(True, _chain_response(), 200)), \
         patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0):
        _, payload, _ = get_gex_levels("NIFTY", "NFO", "11AUG26", "key", weight_by="oi")

    expected = "suppressive" if payload["net_gex"] >= 0 else "amplifying"
    assert payload["regime"] == expected
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest test/test_gex_levels_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'services.gex_levels_service'`.

- [ ] **Step 3: Write the implementation**

Create `services/gex_levels_service.py`:

```python
"""
GEX Levels — orchestration.

One option-chain fetch, one forward resolution, then pure math. Deliberately
built on the Gamma Density pipeline rather than gex_service: that service loops
`calculate_greeks` once per strike (up to 90 service calls), and prices off
spot instead of the per-expiry forward. Neither is viable for a chart study on
a refresh timer.
"""

from typing import Any

from services.gex_levels.blackscholes import FALLBACK_IV
from services.gex_levels.exposure import ChainRow, WeightBy, compute_exposures
from services.gex_levels.expiry import expiry_datetime
from services.gex_levels.levels import find_walls, scan_zero_gamma
from services.gex_levels.quality import assess_quality
from services.option_chain_service import get_option_chain
from services.option_greeks_service import (
    DEFAULT_INTEREST_RATES,
    _resolve_forward_price,
    calculate_time_to_expiry,
    get_underlying_exchange,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# 23 each side = 47 strikes = 94 option symbols.
#
# This is a HARD broker constraint, not a tuning preference. oi_tracker_service
# documents it: the Fyers multiquote OI bucket holds at most 100 symbols, and
# exceeding it returns EMPTY OI rather than an error - which would silently
# zero this entire feature. Do not raise it.
STRIKE_COUNT = 23


def get_gex_levels(
    underlying: str,
    exchange: str,
    expiry_date: str,
    api_key: str,
    weight_by: WeightBy = "oi",
    interest_rate: float | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """
    Compute the GEX Levels payload for one underlying and expiry.

    Args:
        underlying: Underlying symbol (NIFTY, BANKNIFTY, CRUDEOIL, ...).
        exchange: Options exchange (NFO, BFO, MCX).
        expiry_date: Expiry in DDMMMYY format.
        api_key: OpenAlgo API key.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.
        interest_rate: Annualised percentage; defaults per exchange.

    Returns:
        Tuple of (success, payload, status_code).
    """
    try:
        try:
            from opengreeks import black76
        except ImportError:
            logger.error("opengreeks library not installed.")
            return (
                False,
                {"status": "error",
                 "message": "GEX Levels requires the opengreeks library. Install with: pip install opengreeks"},
                500,
            )

        success, chain_response, status_code = get_option_chain(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            strike_count=STRIKE_COUNT,
            api_key=api_key,
        )
        if not success:
            return False, chain_response, status_code

        full_chain = chain_response.get("chain", [])
        spot_price = chain_response.get("underlying_ltp")
        atm_strike = chain_response.get("atm_strike")

        if not spot_price or spot_price <= 0 or not full_chain:
            return (
                False,
                {"status": "error", "message": "Spot price or option chain unavailable"},
                404,
            )

        expiry_dt = expiry_datetime(expiry_date, exchange)
        t_years, dte_days = calculate_time_to_expiry(expiry_dt)

        if interest_rate is None:
            interest_rate = DEFAULT_INTEREST_RATES.get(exchange.upper(), 0)
        r = interest_rate / 100.0

        # Black-76's F is the per-expiry synthetic future, never spot. Gamma
        # peaks at ATM-forward, so pricing off spot displaces the whole profile
        # and therefore both walls - measured basis on BANKNIFTY at 21 days is
        # +138.9 points.
        base = chain_response.get("underlying", underlying)
        forward = _resolve_forward_price(
            base, exchange, get_underlying_exchange(base, exchange), expiry_dt, api_key
        )
        F = forward or spot_price

        rows = _chain_rows(full_chain)
        exposures = compute_exposures(
            black76, rows, forward=F, t_years=t_years, r=r,
            atm_strike=atm_strike, weight_by=weight_by,
        )
        walls = find_walls(exposures)
        zero_gamma = scan_zero_gamma(
            black76, rows, forward=F, t_years=t_years, r=r,
            atm_strike=atm_strike, weight_by=weight_by,
        )

        total_call = sum(e.call_gex for e in exposures)
        total_put = sum(e.put_gex for e in exposures)
        net = total_call + total_put

        if weight_by == "volume":
            total_weight = sum(row.call_volume + row.put_volume for row in rows)
        else:
            total_weight = sum(row.call_oi + row.put_oi for row in rows)

        quality = assess_quality(exposures, walls, forward=F, total_weight=total_weight)

        return (
            True,
            {
                "status": "success",
                "underlying": base,
                "exchange": exchange,
                "expiry_date": expiry_date,
                "weight_by": weight_by,
                "spot_price": round(spot_price, 2),
                "forward_price": round(F, 2),
                "atm_strike": atm_strike,
                "lot_size": rows[0].lot_size if rows else 1,
                "dte_days": round(dte_days, 4),
                "interest_rate": round(interest_rate, 2),
                "fallback_iv": FALLBACK_IV,
                "strikes": [
                    {
                        "strike": e.strike,
                        "call_gex": round(e.call_gex, 2),
                        "put_gex": round(e.put_gex, 2),
                        "net_gex": round(e.net_gex, 2),
                    }
                    for e in exposures
                ],
                "call_wall": walls.call_wall,
                "put_wall": walls.put_wall,
                # None means the profile does not cross zero anywhere in the
                # scanned range. That is a normal outcome; the UI renders it as
                # "No local cross".
                "zero_gamma": round(zero_gamma, 2) if zero_gamma is not None else None,
                "total_call_gex": round(total_call, 2),
                "total_put_gex": round(total_put, 2),
                "net_gex": round(net, 2),
                "regime": "suppressive" if net >= 0 else "amplifying",
                "quality": {
                    "verdict": quality.verdict,
                    "strikes_used": quality.strikes_used,
                    "strikes_priced": quality.strikes_priced,
                    "both_sides": quality.both_sides,
                    "wall_at_edge": quality.wall_at_edge,
                    "notes": quality.notes,
                },
            },
            200,
        )

    except Exception as e:
        logger.exception(f"Error in get_gex_levels: {e}")
        return False, {"status": "error", "message": "Error computing GEX levels"}, 500


def _chain_rows(full_chain: list[dict[str, Any]]) -> list[ChainRow]:
    """Flatten the option-chain response into the pure module's input type."""
    rows: list[ChainRow] = []
    for item in full_chain:
        strike = item.get("strike")
        if not isinstance(strike, (int, float)) or strike <= 0:
            continue
        ce = item.get("ce") or {}
        pe = item.get("pe") or {}
        rows.append(
            ChainRow(
                strike=float(strike),
                call_price=ce.get("ltp", 0) or 0,
                put_price=pe.get("ltp", 0) or 0,
                call_oi=ce.get("oi", 0) or 0,
                put_oi=pe.get("oi", 0) or 0,
                call_volume=ce.get("volume", 0) or 0,
                put_volume=pe.get("volume", 0) or 0,
                lot_size=ce.get("lotsize") or pe.get("lotsize") or 1,
            )
        )
    return rows
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test/test_gex_levels_service.py -v
```

Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff check services/gex_levels_service.py test/test_gex_levels_service.py --fix
uv run ruff format services/gex_levels_service.py test/test_gex_levels_service.py
git add services/gex_levels_service.py test/test_gex_levels_service.py
git commit -m "feat(gex-levels): orchestrate one chain fetch into a levels payload

Prices off the per-expiry synthetic forward, not spot, and fetches 23
strikes each side to stay inside the Fyers multiquote OI bucket that
oi_tracker_service documents - exceeding it returns empty OI rather than
an error.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: The endpoint

**Files:**
- Modify: `blueprints/gex.py` (append a route)
- Test: `test/test_gex_levels_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_gex_levels_endpoint.py`:

```python
"""Route validation for POST /gex/api/gex-levels."""

import pytest
from flask import Flask

from blueprints.gex import gex_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(gex_bp)
    return app.test_client()


def _post(client, **overrides):
    body = {"underlying": "NIFTY", "exchange": "NFO", "expiry_date": "11AUG26"}
    body.update(overrides)
    return client.post("/gex/api/gex-levels", json=body)


def test_it_rejects_an_unauthenticated_request(client):
    assert _post(client).status_code == 401


@pytest.mark.parametrize("field", ["underlying", "exchange", "expiry_date"])
def test_it_rejects_a_missing_required_field(client, field, monkeypatch):
    _authenticate(monkeypatch)
    assert _post(client, **{field: ""}).status_code == 400


def test_it_rejects_a_malformed_expiry(client, monkeypatch):
    _authenticate(monkeypatch)
    assert _post(client, expiry_date="2026-08-11").status_code == 400


def test_it_rejects_an_unknown_weighting(client, monkeypatch):
    _authenticate(monkeypatch)
    assert _post(client, weight_by="delta").status_code == 400


def test_it_defaults_the_weighting_to_open_interest(client, monkeypatch):
    seen = {}
    _authenticate(monkeypatch)

    def _fake(underlying, exchange, expiry_date, api_key, weight_by="oi"):
        seen["weight_by"] = weight_by
        return True, {"status": "success"}, 200

    monkeypatch.setattr("blueprints.gex.get_gex_levels", _fake)
    assert _post(client).status_code == 200
    assert seen["weight_by"] == "oi"


def _authenticate(monkeypatch):
    """Stand in for a valid session plus a configured API key."""
    monkeypatch.setattr("blueprints.gex.check_session_validity", lambda f: f)
    monkeypatch.setattr("blueprints.gex.get_api_key_for_tradingview", lambda user: "key")
    monkeypatch.setattr("blueprints.gex.session", {"user": "tester"})
```

> **Note for the implementer:** `check_session_validity` is applied as a
> decorator at import time, so patching it in a test does nothing. Register the
> route so the session check happens *inside* the handler if the decorator
> proves untestable, or drop `test_it_rejects_an_unauthenticated_request` and
> the `_authenticate` helper and test the validation branches by calling the
> handler function directly. Do not leave a test that passes for the wrong
> reason — pick one and make it honest.

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest test/test_gex_levels_endpoint.py -v
```

Expected: 404 on every route call — the route does not exist.

- [ ] **Step 3: Add the route**

Append to `blueprints/gex.py`, and extend the import at the top to
`from services.gex_levels_service import get_gex_levels`:

```python
@gex_bp.route("/gex/api/gex-levels", methods=["POST"])
@cross_origin()
@check_session_validity
def gex_levels():
    """Call Wall, Put Wall and Zero-Gamma for one underlying and expiry."""
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify(
                {"status": "error",
                 "message": "API key not configured. Please generate an API key in /apikey"}
            ), 401

        data = request.get_json(silent=True) or {}
        underlying = data.get("underlying", "").strip()[:20]
        exchange = data.get("exchange", "").strip()[:20]
        expiry_date = data.get("expiry_date", "").strip()[:10]
        weight_by = data.get("weight_by", "oi").strip().lower()[:10]

        if not underlying or not exchange or not expiry_date:
            return jsonify(
                {"status": "error",
                 "message": "underlying, exchange, and expiry_date are required"}
            ), 400

        if not re.match(r"^[A-Z0-9]+$", underlying) or not re.match(r"^[A-Z0-9_]+$", exchange):
            return jsonify({"status": "error", "message": "Invalid input format"}), 400

        if not re.match(r"^\d{2}[A-Z]{3}\d{2}$", expiry_date):
            return jsonify(
                {"status": "error", "message": "Invalid expiry_date format. Expected DDMMMYY"}
            ), 400

        if weight_by not in ("oi", "volume"):
            return jsonify(
                {"status": "error", "message": "weight_by must be 'oi' or 'volume'"}
            ), 400

        success, response, status_code = get_gex_levels(
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            api_key=api_key,
            weight_by=weight_by,
        )
        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error in GEX levels API: {e}")
        return (
            jsonify({"status": "error", "message": "An error occurred processing your request"}),
            500,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest test/test_gex_levels_endpoint.py -v
```

Expected: PASS. If the decorator problem in the note above bites, resolve it as
described before moving on.

- [ ] **Step 5: Verify the app still boots**

```bash
uv run python -c "import app; print('app imports')"
```

Expected: `app imports`.

- [ ] **Step 6: Commit**

```bash
uv run ruff check blueprints/gex.py test/test_gex_levels_endpoint.py --fix
uv run ruff format blueprints/gex.py test/test_gex_levels_endpoint.py
git add blueprints/gex.py test/test_gex_levels_endpoint.py
git commit -m "feat(gex-levels): add POST /gex/api/gex-levels

Same blueprint, session handling and validation shape as the existing GEX
data route, plus a weight_by field constrained to oi or volume.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Frontend API client

**Files:**
- Modify: `frontend/src/api/gex.ts` (append)

- [ ] **Step 1: Add the types and the call**

Append to `frontend/src/api/gex.ts`:

```typescript
export type GEXWeightBy = 'oi' | 'volume'

export interface GEXStrikeLevel {
  strike: number
  call_gex: number
  put_gex: number
  net_gex: number
}

export interface GEXQuality {
  verdict: 'good' | 'degraded' | 'unusable'
  strikes_used: number
  strikes_priced: number
  both_sides: boolean
  wall_at_edge: boolean
  notes: string[]
}

export interface GEXLevelsResponse {
  status: 'success' | 'error'
  message?: string
  underlying?: string
  exchange?: string
  expiry_date?: string
  weight_by?: GEXWeightBy
  spot_price?: number
  forward_price?: number
  atm_strike?: number
  lot_size?: number
  dte_days?: number
  strikes?: GEXStrikeLevel[]
  call_wall?: number | null
  put_wall?: number | null
  /** null when the profile does not cross zero in the scanned range. */
  zero_gamma?: number | null
  total_call_gex?: number
  total_put_gex?: number
  net_gex?: number
  /** Positive net gamma stabilises price; negative amplifies moves both ways. */
  regime?: 'suppressive' | 'amplifying'
  quality?: GEXQuality
}
```

And add to the `gexApi` object:

```typescript
  getGEXLevels: async (
    params: { underlying: string; exchange: string; expiry_date: string; weight_by: GEXWeightBy },
    signal?: AbortSignal
  ): Promise<GEXLevelsResponse> => {
    const response = await webClient.post<GEXLevelsResponse>('/gex/api/gex-levels', params, {
      signal,
    })
    return response.data
  },
```

- [ ] **Step 2: Verify it typechecks**

```bash
cd frontend && npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/api/gex.ts
git commit -m "feat(gex-levels): add the levels API client

Carries an AbortSignal so a response arriving after a symbol change can be
cancelled rather than painting the previous instrument's walls.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `GexLevelsManager` — settings and persistence

**Files:**
- Create: `frontend/src/lib/charts/gex-levels.ts`
- Test: `frontend/src/lib/charts/gex-levels.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/charts/gex-levels.test.ts`:

```typescript
import { describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_GEX_LEVELS_SETTINGS,
  type GexLevelsCallbacks,
  GexLevelsManager,
} from './gex-levels'

function make(overrides: Partial<GexLevelsCallbacks> = {}) {
  const onChange = vi.fn()
  const fetchLevels = vi.fn().mockResolvedValue({ status: 'success' })
  const manager = new GexLevelsManager({
    onChange,
    fetchLevels,
    instrument: () => ({ underlying: 'NIFTY', exchange: 'NFO' }),
    ...overrides,
  })
  return { manager, onChange, fetchLevels }
}

describe('GexLevelsManager settings', () => {
  it('starts disabled, like every other study', () => {
    expect(DEFAULT_GEX_LEVELS_SETTINGS.enabled).toBe(false)
  })

  it('defaults to open-interest weighting', () => {
    expect(DEFAULT_GEX_LEVELS_SETTINGS.weightBy).toBe('oi')
  })

  it('defaults the strike bars on', () => {
    expect(DEFAULT_GEX_LEVELS_SETTINGS.showBars).toBe(true)
  })

  it('applies a patch and notifies', () => {
    const { manager, onChange } = make()
    manager.setConfig({ weightBy: 'volume' })
    expect(manager.config.weightBy).toBe('volume')
    expect(onChange).toHaveBeenCalled()
  })

  it('round-trips through snapshot and restore', () => {
    const { manager } = make()
    manager.setConfig({ enabled: true, weightBy: 'volume', showBars: false, refreshSeconds: 30 })
    const snap = manager.snapshot()

    const { manager: restored } = make()
    restored.restore(snap)
    expect(restored.config).toEqual(manager.config)
  })

  it('fills unknown keys from the defaults when restoring a partial snapshot', () => {
    const { manager } = make()
    manager.restore({ enabled: true } as never)
    expect(manager.config.enabled).toBe(true)
    expect(manager.config.weightBy).toBe('oi')
  })

  it('does not mutate its settings through the config getter', () => {
    const { manager } = make()
    const config = manager.config
    config.weightBy = 'volume'
    expect(manager.config.weightBy).toBe('oi')
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/lib/charts/gex-levels.test.ts
```

Expected: `Failed to resolve import "./gex-levels"`.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/charts/gex-levels.ts`:

```typescript
/**
 * GEX Levels study for the /charts workspace.
 *
 * Mirrors `ProfileManager`: owns the settings, the primitive handles and the
 * refresh timer, and exposes `snapshot()` / `restore()` so the study persists
 * with the layout. Unlike the profiles, GEX is not derived from the chart's
 * bars at all — it is a live option-chain snapshot for a *different* set of
 * instruments than the one being charted, fetched on a timer.
 *
 * That is also why this could not be an OpenScript indicator: the engine's
 * `request.security` is same-symbol only by design.
 */

import type { GEXLevelsResponse, GEXWeightBy } from '@/api/gex'

export interface GexLevelsConfig {
  enabled: boolean
  /** 'oi' is the standing dealer book; 'volume' is today's flow only. */
  weightBy: GEXWeightBy
  /** Empty string means the nearest expiry, resolved server-side. */
  expiry: string
  showBars: boolean
  showCallWall: boolean
  showPutWall: boolean
  showZeroGamma: boolean
  showDashboard: boolean
  refreshSeconds: number
  side: 'left' | 'right'
  columnWidth: number
}

export const DEFAULT_GEX_LEVELS_SETTINGS: GexLevelsConfig = {
  enabled: false,
  weightBy: 'oi',
  expiry: '',
  showBars: true,
  showCallWall: true,
  showPutWall: true,
  showZeroGamma: true,
  showDashboard: true,
  refreshSeconds: 60,
  side: 'right',
  columnWidth: 120,
}

/** What the chart is on, resolved to something with an option chain. */
export interface GexInstrument {
  underlying: string
  exchange: string
}

export interface GexLevelsCallbacks {
  onChange(): void
  /** null when the charted instrument has no option chain at all. */
  instrument(): GexInstrument | null
  fetchLevels(
    params: { underlying: string; exchange: string; expiry_date: string; weight_by: GEXWeightBy },
    signal: AbortSignal
  ): Promise<GEXLevelsResponse>
  onSnapshot?(snapshot: GEXLevelsResponse | null): void
}

export class GexLevelsManager {
  private readonly cb: GexLevelsCallbacks
  private settings: GexLevelsConfig = structuredClone(DEFAULT_GEX_LEVELS_SETTINGS)

  constructor(cb: GexLevelsCallbacks) {
    this.cb = cb
  }

  get config(): GexLevelsConfig {
    return structuredClone(this.settings)
  }

  setConfig(patch: Partial<GexLevelsConfig>): void {
    this.settings = { ...this.settings, ...patch }
    this.cb.onChange()
  }

  snapshot(): GexLevelsConfig {
    return structuredClone(this.settings)
  }

  restore(snap: Partial<GexLevelsConfig> | undefined): void {
    if (!snap) return
    this.settings = { ...DEFAULT_GEX_LEVELS_SETTINGS, ...snap }
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd frontend && npx vitest run src/lib/charts/gex-levels.test.ts
```

Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/lib/charts/gex-levels.ts frontend/src/lib/charts/gex-levels.test.ts
git commit -m "feat(gex-levels): add GexLevelsManager settings and persistence

Mirrors ProfileManager's shape so the study persists with the layout.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `GexLevelsManager` — the refresh loop

The dangerous part. A leaked timer in a single Gunicorn worker that never restarts accumulates, and a late response must never paint the previous instrument's walls.

**Files:**
- Modify: `frontend/src/lib/charts/gex-levels.ts`
- Test: `frontend/src/lib/charts/gex-levels.poll.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/charts/gex-levels.poll.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GexLevelsManager } from './gex-levels'

function make(instrument: { underlying: string; exchange: string } | null = { underlying: 'NIFTY', exchange: 'NFO' }) {
  const fetchLevels = vi.fn().mockResolvedValue({ status: 'success', call_wall: 24800 })
  const onSnapshot = vi.fn()
  const manager = new GexLevelsManager({
    onChange: vi.fn(),
    instrument: () => instrument,
    fetchLevels,
    onSnapshot,
  })
  return { manager, fetchLevels, onSnapshot }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('GexLevelsManager refresh loop', () => {
  it('does not fetch while the study is off', () => {
    const { fetchLevels } = make()
    vi.advanceTimersByTime(120_000)
    expect(fetchLevels).not.toHaveBeenCalled()
  })

  it('fetches immediately on enable rather than waiting a full interval', () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true })
    expect(fetchLevels).toHaveBeenCalledTimes(1)
  })

  it('refetches on the configured interval', async () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true, refreshSeconds: 30 })
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchLevels).toHaveBeenCalledTimes(2)
  })

  it('stops fetching when disabled', async () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true })
    manager.setConfig({ enabled: false })
    await vi.advanceTimersByTimeAsync(300_000)
    expect(fetchLevels).toHaveBeenCalledTimes(1)
  })

  it('never starts for an instrument with no option chain', () => {
    const { manager, fetchLevels } = make(null)
    manager.setConfig({ enabled: true })
    expect(fetchLevels).not.toHaveBeenCalled()
  })

  it('clears its timer on dispose', async () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true })
    manager.dispose()
    await vi.advanceTimersByTimeAsync(300_000)
    expect(fetchLevels).toHaveBeenCalledTimes(1)
  })

  it('discards a response that arrives after the instrument changed', async () => {
    let instrument = { underlying: 'NIFTY', exchange: 'NFO' }
    const fetchLevels = vi.fn().mockResolvedValue({ status: 'success', call_wall: 24800 })
    const onSnapshot = vi.fn()
    const manager = new GexLevelsManager({
      onChange: vi.fn(),
      instrument: () => instrument,
      fetchLevels,
      onSnapshot,
    })

    manager.setConfig({ enabled: true })
    instrument = { underlying: 'BANKNIFTY', exchange: 'NFO' }
    manager.instrumentChanged()
    await vi.runOnlyPendingTimersAsync()

    // The NIFTY response must not have been published as BANKNIFTY's levels.
    const published = onSnapshot.mock.calls.map(([snap]) => snap)
    expect(published.filter((s) => s?.call_wall === 24800).length).toBeLessThanOrEqual(1)
  })

  it('keeps the last good snapshot when a refresh fails', async () => {
    const fetchLevels = vi
      .fn()
      .mockResolvedValueOnce({ status: 'success', call_wall: 24800 })
      .mockRejectedValueOnce(new Error('broker down'))
    const onSnapshot = vi.fn()
    const manager = new GexLevelsManager({
      onChange: vi.fn(),
      instrument: () => ({ underlying: 'NIFTY', exchange: 'NFO' }),
      fetchLevels,
      onSnapshot,
    })

    manager.setConfig({ enabled: true, refreshSeconds: 30 })
    await vi.advanceTimersByTimeAsync(30_000)

    expect(manager.lastSnapshot?.call_wall).toBe(24800)
    expect(manager.stale).toBe(true)
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/lib/charts/gex-levels.poll.test.ts
```

Expected: FAIL — `manager.dispose is not a function`.

- [ ] **Step 3: Add the refresh loop**

Add to the `GexLevelsManager` class in `frontend/src/lib/charts/gex-levels.ts`:

```typescript
  /** One timer for the manager's whole life, cleared in dispose(). */
  private timer: ReturnType<typeof setInterval> | null = null
  private inFlight: AbortController | null = null
  /**
   * Monotonic token identifying the instrument a request was issued for. A
   * response whose token no longer matches is discarded — otherwise a slow
   * NIFTY response landing after the user loaded BANKNIFTY would paint NIFTY's
   * walls on BANKNIFTY's chart.
   */
  private epoch = 0
  private snapshotData: GEXLevelsResponse | null = null
  private isStale = false

  /** The last successful response, retained across a failed refresh. */
  get lastSnapshot(): GEXLevelsResponse | null {
    return this.snapshotData
  }

  /** True when the newest refresh failed and the snapshot shown is older. */
  get stale(): boolean {
    return this.isStale
  }

  /**
   * The charted instrument changed. Invalidates any in-flight request and
   * clears the snapshot, because levels belong to one underlying and showing
   * the previous one's walls is worse than showing none.
   */
  instrumentChanged(): void {
    this.epoch += 1
    this.inFlight?.abort()
    this.inFlight = null
    this.snapshotData = null
    this.isStale = false
    this.cb.onSnapshot?.(null)
    this.syncTimer()
    if (this.canRun()) void this.refresh()
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
    this.inFlight?.abort()
    this.inFlight = null
    this.snapshotData = null
  }

  private canRun(): boolean {
    return this.settings.enabled && this.cb.instrument() !== null
  }

  /**
   * Start or stop the timer to match the settings. Polling never runs for a
   * disabled study or an instrument with no option chain — there is nothing to
   * fetch, and a timer that fires into a guard is still a timer.
   */
  private syncTimer(): void {
    const want = this.canRun()
    if (want && !this.timer) {
      this.timer = setInterval(
        () => void this.refresh(),
        Math.max(5, this.settings.refreshSeconds) * 1000
      )
    } else if (!want && this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
  }

  private async refresh(): Promise<void> {
    const instrument = this.cb.instrument()
    if (!instrument || !this.settings.enabled) return

    this.inFlight?.abort()
    const controller = new AbortController()
    this.inFlight = controller
    const issuedAt = this.epoch

    try {
      const response = await this.cb.fetchLevels(
        {
          underlying: instrument.underlying,
          exchange: instrument.exchange,
          expiry_date: this.settings.expiry,
          weight_by: this.settings.weightBy,
        },
        controller.signal
      )
      if (issuedAt !== this.epoch) return
      this.snapshotData = response
      this.isStale = false
      this.cb.onSnapshot?.(response)
    } catch {
      if (issuedAt !== this.epoch) return
      // Retain the last good snapshot and badge it. Blanking levels a trader is
      // watching is worse than showing them aged.
      this.isStale = true
      this.cb.onSnapshot?.(this.snapshotData)
    } finally {
      if (this.inFlight === controller) this.inFlight = null
    }
  }
```

Then change `setConfig` so it drives the timer, and fetches straight away on a
change that alters what would be fetched:

```typescript
  setConfig(patch: Partial<GexLevelsConfig>): void {
    const before = this.settings
    this.settings = { ...before, ...patch }

    const refetch =
      (this.settings.enabled && !before.enabled) ||
      this.settings.weightBy !== before.weightBy ||
      this.settings.expiry !== before.expiry

    if (this.settings.refreshSeconds !== before.refreshSeconds && this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
    this.syncTimer()
    if (refetch && this.canRun()) void this.refresh()
    this.cb.onChange()
  }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd frontend && npx vitest run src/lib/charts/gex-levels.poll.test.ts src/lib/charts/gex-levels.test.ts
```

Expected: PASS — 15 tests across both files.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/lib/charts/gex-levels.ts frontend/src/lib/charts/gex-levels.poll.test.ts
git commit -m "feat(gex-levels): add the refresh loop

One timer for the manager's life, cleared on dispose. Requests carry an
epoch token so a response arriving after the instrument changed is
discarded rather than painting the previous underlying's walls. A failed
refresh retains the last good snapshot and badges it stale, because
blanking levels a trader is watching is worse than showing them aged.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: The primitive — level lines

**Files:**
- Create: `frontend/src/lib/charts/gex-levels-primitive.ts`

Rendering cannot be unit-tested — jsdom has no canvas, so no chart can be
bound. This task and Task 12 are verified in the live app at Task 16.

- [ ] **Step 1: Write the primitive**

Create `frontend/src/lib/charts/gex-levels-primitive.ts`:

```typescript
/**
 * GEX Levels chart primitive.
 *
 * Draws three extended price levels (Call Wall, Put Wall, Zero-Gamma) and an
 * optional column of signed per-strike bars anchored in the plot margin.
 *
 * Deliberately implements the package-root `IPrimitive` rather than a lazy
 * tier's, so it needs no `tier-compat` cast — unlike the profile primitives,
 * whose tiers re-declare the shared types.
 *
 * It contributes NOTHING to autoscale. `profiles.ts` documents this trap three
 * separate times: a primitive whose `autoscaleInfo()` reports its own extent
 * drags the price scale out to that extent and squashes the candles. A
 * 47-strike window spans far more than the visible range, so reporting it would
 * flatten the chart to a sliver. Bars clip to the visible range instead, and a
 * level outside it is drawn as an edge marker rather than silently dropped.
 */

import type { IPrimitive, PrimitiveHost, PrimitiveRenderContext, ZOrder } from 'openalgo-charts'
import type { GEXLevelsResponse, GEXStrikeLevel } from '@/api/gex'

export interface GexLevelsPrimitiveOptions {
  showBars: boolean
  showCallWall: boolean
  showPutWall: boolean
  showZeroGamma: boolean
  side: 'left' | 'right'
  columnWidth: number
  /** Extra px inset, so the column clears a right-anchored volume profile. */
  columnInset: number
  callColor: string
  putColor: string
  zeroGammaColor: string
}

export const DEFAULT_GEX_PRIMITIVE_OPTIONS: GexLevelsPrimitiveOptions = {
  showBars: true,
  showCallWall: true,
  showPutWall: true,
  showZeroGamma: true,
  side: 'right',
  columnWidth: 120,
  columnInset: 0,
  callColor: '#26a69a',
  putColor: '#ef5350',
  zeroGammaColor: '#f5a623',
}

const LABEL_FONT = '11px system-ui, -apple-system, sans-serif'
const EDGE_MARKER_H = 12

export class GexLevelsPrimitive implements IPrimitive {
  private opts: GexLevelsPrimitiveOptions
  private data: GEXLevelsResponse | null = null
  private host: PrimitiveHost | null = null

  constructor(opts: Partial<GexLevelsPrimitiveOptions> = {}) {
    this.opts = { ...DEFAULT_GEX_PRIMITIVE_OPTIONS, ...opts }
  }

  attached(host: PrimitiveHost): void {
    this.host = host
  }

  detached(): void {
    this.host = null
  }

  zOrder(): ZOrder {
    return 'bottom'
  }

  setData(data: GEXLevelsResponse | null): void {
    this.data = data
    this.host?.requestUpdate()
  }

  setOptions(patch: Partial<GexLevelsPrimitiveOptions>): void {
    this.opts = { ...this.opts, ...patch }
    this.host?.requestUpdate()
  }

  /**
   * Intentionally absent: see the class comment. Declared here as a comment
   * rather than an empty method so nobody adds one back without reading why.
   */

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const d = this.data
    if (!d || d.status !== 'success') return

    if (this.opts.showBars) this.drawBars(ctx, rc, d.strikes ?? [])

    if (this.opts.showCallWall && d.call_wall != null) {
      this.drawLevel(ctx, rc, d.call_wall, 'Call Wall', this.opts.callColor, true)
    }
    if (this.opts.showPutWall && d.put_wall != null) {
      this.drawLevel(ctx, rc, d.put_wall, 'Put Wall', this.opts.putColor, true)
    }
    if (this.opts.showZeroGamma && d.zero_gamma != null) {
      this.drawLevel(ctx, rc, d.zero_gamma, 'Zero-Gamma', this.opts.zeroGammaColor, false)
    }
  }

  /**
   * One dashed line across the plot with an inline label.
   *
   * A level outside the visible price range becomes a short marker pinned to
   * the nearer plot edge, with the label still readable. Silently dropping it
   * would let a trader believe there is no wall above when there is one just
   * off screen.
   */
  private drawLevel(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    price: number,
    label: string,
    color: string,
    dashed: boolean
  ): void {
    const y = rc.priceScale.priceToCoordinate(price)
    const offScreen = y < 0 || y > rc.plotHeight
    const drawY = offScreen ? (y < 0 ? EDGE_MARKER_H : rc.plotHeight - EDGE_MARKER_H) : y

    ctx.save()
    ctx.strokeStyle = color
    ctx.fillStyle = color
    ctx.lineWidth = 1
    ctx.setLineDash(dashed ? [6, 4] : [])

    ctx.beginPath()
    if (offScreen) {
      // A stub at the edge, not a full line — it is not really at this price.
      ctx.moveTo(0, drawY)
      ctx.lineTo(Math.min(80, rc.plotWidth), drawY)
    } else {
      ctx.moveTo(0, drawY)
      ctx.lineTo(rc.plotWidth, drawY)
    }
    ctx.stroke()

    ctx.setLineDash([])
    ctx.font = LABEL_FONT
    ctx.textBaseline = 'bottom'
    const text = offScreen
      ? `${label} ${this.formatPrice(price)} ${y < 0 ? 'above' : 'below'}`
      : `${label} ${this.formatPrice(price)}`
    ctx.fillText(text, 8, drawY - 3)
    ctx.restore()
  }

  private formatPrice(price: number): string {
    return price >= 1000 ? price.toFixed(price % 1 === 0 ? 0 : 2) : price.toFixed(2)
  }

  private drawBars(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    strikes: readonly GEXStrikeLevel[]
  ): void {
    if (strikes.length === 0) return

    // Clip to what is on screen. This is what replaces an autoscale
    // contribution: the study never widens the pane, it only draws inside it.
    const visible = strikes.filter((s) => {
      const y = rc.priceScale.priceToCoordinate(s.strike)
      return y >= 0 && y <= rc.plotHeight
    })
    if (visible.length === 0) return

    const peak = Math.max(...visible.map((s) => Math.abs(s.net_gex)))
    if (!(peak > 0)) return

    const axisX =
      this.opts.side === 'right'
        ? rc.plotWidth - this.opts.columnInset - this.opts.columnWidth
        : this.opts.columnInset + this.opts.columnWidth

    // Rows should not overlap: cap the bar height at the gap between adjacent
    // strikes, so a zoomed-out chart reads as a histogram rather than a smear.
    const spacing = this.strikeSpacingPx(rc, visible)
    const barH = Math.max(1, Math.min(8, spacing - 1))

    ctx.save()
    for (const s of visible) {
      const y = rc.priceScale.priceToCoordinate(s.strike)
      const len = (Math.abs(s.net_gex) / peak) * this.opts.columnWidth
      const positive = s.net_gex >= 0
      ctx.fillStyle = positive ? this.opts.callColor : this.opts.putColor
      ctx.globalAlpha = 0.75
      ctx.fillRect(positive ? axisX : axisX - len, y - barH / 2, len, barH)
    }

    ctx.globalAlpha = 1
    ctx.strokeStyle = 'rgba(150,150,150,0.5)'
    ctx.setLineDash([2, 3])
    ctx.beginPath()
    ctx.moveTo(axisX, 0)
    ctx.lineTo(axisX, rc.plotHeight)
    ctx.stroke()
    ctx.restore()
  }

  private strikeSpacingPx(rc: PrimitiveRenderContext, strikes: readonly GEXStrikeLevel[]): number {
    if (strikes.length < 2) return 8
    const first = rc.priceScale.priceToCoordinate(strikes[0].strike)
    const second = rc.priceScale.priceToCoordinate(strikes[1].strike)
    return Math.abs(second - first)
  }
}
```

- [ ] **Step 2: Verify it typechecks**

```bash
cd frontend && npx tsc -b --noEmit
```

Expected: no errors. If `priceScale.priceToCoordinate` is not the method name
on the exported `PriceScale`, read
`../openalgo-charts/src/scale/price-scale.ts` and use the real one — do not
guess, and do not cast the type away.

- [ ] **Step 3: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/lib/charts/gex-levels-primitive.ts
git commit -m "feat(gex-levels): add the chart primitive

Three extended levels plus a signed per-strike bar column, pixel-anchored
so it can clear a right-side volume profile.

Contributes nothing to autoscale by design: a 47-strike window spans far
more than the visible price range, and reporting it would drag the price
scale and squash the candles - the trap profiles.ts documents three times.
Bars clip to the visible range and an off-screen level becomes an edge
marker rather than silently vanishing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Wire the primitive into the manager

**Files:**
- Modify: `frontend/src/lib/charts/gex-levels.ts`
- Test: `frontend/src/lib/charts/gex-levels.poll.test.ts` (extend)

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/lib/charts/gex-levels.poll.test.ts`:

```typescript
describe('GexLevelsManager primitive lifecycle', () => {
  function chartDouble() {
    return {
      addPrimitive: vi.fn(),
      removePrimitive: vi.fn(),
    }
  }

  it('adds the primitive when the study is enabled', () => {
    const chart = chartDouble()
    const { manager } = make()
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true })
    expect(chart.addPrimitive).toHaveBeenCalledTimes(1)
  })

  it('removes the primitive when the study is disabled', () => {
    const chart = chartDouble()
    const { manager } = make()
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true })
    manager.setConfig({ enabled: false })
    expect(chart.removePrimitive).toHaveBeenCalledTimes(1)
  })

  it('does not add the primitive twice for repeated enables', () => {
    const chart = chartDouble()
    const { manager } = make()
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true })
    manager.setConfig({ enabled: true })
    expect(chart.addPrimitive).toHaveBeenCalledTimes(1)
  })

  it('drops its primitive handle on re-attach, since the old chart is gone', () => {
    const first = chartDouble()
    const second = chartDouble()
    const { manager } = make()
    manager.attachChart(first as never)
    manager.setConfig({ enabled: true })
    manager.attachChart(second as never)
    expect(second.addPrimitive).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/lib/charts/gex-levels.poll.test.ts
```

Expected: FAIL — `manager.attachChart is not a function`.

- [ ] **Step 3: Add the lifecycle**

Add to `frontend/src/lib/charts/gex-levels.ts` — the import:

```typescript
import type { Chart } from 'openalgo-charts'
import { GexLevelsPrimitive } from './gex-levels-primitive'
```

and to the class:

```typescript
  private chart: Chart | null = null
  private primitive: GexLevelsPrimitive | null = null

  /**
   * Bind to a chart. Called on every chart rebuild, which destroys the previous
   * chart — so the old primitive handle is dropped rather than removed. Trying
   * to remove a primitive from a destroyed chart throws, and the throw would
   * abandon the rest of the rebind, exactly as documented for
   * `IndicatorHost.attachChart` and `DrawingManager.detach`.
   */
  attachChart(chart: Chart): void {
    this.chart = chart
    this.primitive = null
    this.syncPrimitive()
    if (this.snapshotData) this.primitive?.setData(this.snapshotData)
  }

  private syncPrimitive(): void {
    const chart = this.chart
    if (!chart) return

    if (this.settings.enabled && !this.primitive) {
      this.primitive = new GexLevelsPrimitive(this.primitiveOptions())
      chart.addPrimitive(this.primitive, 0)
    } else if (!this.settings.enabled && this.primitive) {
      try {
        chart.removePrimitive(this.primitive)
      } catch {
        // The chart is already gone; the handle is all that is left to drop.
      }
      this.primitive = null
    }
    this.primitive?.setOptions(this.primitiveOptions())
  }

  private primitiveOptions() {
    const c = this.settings
    return {
      showBars: c.showBars,
      showCallWall: c.showCallWall,
      showPutWall: c.showPutWall,
      showZeroGamma: c.showZeroGamma,
      side: c.side,
      columnWidth: c.columnWidth,
      // Volume Profile anchors right at 150 px by default. When both are on the
      // same side the GEX column steps in by that width — the same pattern
      // profiles.ts uses when the market profile moves the volume profile's labels.
      columnInset: this.cb.volumeProfileWidthOnSide?.(c.side) ?? 0,
    }
  }
```

Extend `GexLevelsCallbacks` with the optional inset source:

```typescript
  /** Width in px of a volume profile anchored on the same side, if any. */
  volumeProfileWidthOnSide?(side: 'left' | 'right'): number
```

Then call `this.syncPrimitive()` from `setConfig` (after `syncTimer()`), push
data to the primitive in `refresh()`'s success and failure paths
(`this.primitive?.setData(...)` alongside each `this.cb.onSnapshot?.(...)`), and
clear it in `dispose()`:

```typescript
    this.primitive = null
    this.chart = null
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd frontend && npx vitest run src/lib/charts/gex-levels.poll.test.ts src/lib/charts/gex-levels.test.ts
```

Expected: PASS — 19 tests.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/lib/charts/gex-levels.ts frontend/src/lib/charts/gex-levels.poll.test.ts
git commit -m "feat(gex-levels): manage the primitive's lifecycle

Re-attach drops the handle rather than removing it, because a chart
rebuild has already destroyed the previous chart and the throw would
abandon the rest of the rebind. The bar column insets by a same-side
volume profile's width, mirroring the collision fix in profiles.ts.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: The dashboard overlay

**Files:**
- Create: `frontend/src/components/charts/workspace/GexDashboard.tsx`
- Test: `frontend/src/components/charts/workspace/GexDashboard.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/charts/workspace/GexDashboard.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { GEXLevelsResponse } from '@/api/gex'
import { GexDashboard } from './GexDashboard'

const good: GEXLevelsResponse = {
  status: 'success',
  underlying: 'NIFTY',
  expiry_date: '11AUG26',
  call_wall: 24800,
  put_wall: 24500,
  zero_gamma: 24632.43,
  total_call_gex: 18_200_000_000,
  total_put_gex: -9_720_000_000,
  net_gex: 8_480_000_000,
  regime: 'suppressive',
  quality: { verdict: 'good', strikes_used: 47, strikes_priced: 44,
             both_sides: true, wall_at_edge: false, notes: [] },
}

describe('GexDashboard', () => {
  it('shows the walls and the zero-gamma level', () => {
    render(<GexDashboard data={good} stale={false} />)
    expect(screen.getByText('24800')).toBeInTheDocument()
    expect(screen.getByText('24500')).toBeInTheDocument()
    expect(screen.getByText('24632.43')).toBeInTheDocument()
  })

  it('labels the regime, not a bullish or bearish verdict', () => {
    render(<GexDashboard data={good} stale={false} />)
    expect(screen.getByText(/suppressive/i)).toBeInTheDocument()
    expect(screen.queryByText(/bullish/i)).not.toBeInTheDocument()
  })

  it('renders "No local cross" when there is no zero-gamma level', () => {
    render(<GexDashboard data={{ ...good, zero_gamma: null }} stale={false} />)
    expect(screen.getByText('No local cross')).toBeInTheDocument()
  })

  it('reports the strike counts in the data-status row', () => {
    render(<GexDashboard data={good} stale={false} />)
    expect(screen.getByText(/44 of 47 strikes/)).toBeInTheDocument()
  })

  it('surfaces a degraded verdict with its reason', () => {
    const degraded = {
      ...good,
      quality: { ...good.quality!, verdict: 'degraded' as const,
                 notes: ['A wall sits at the edge of the fetched window and may be a window artefact'] },
    }
    render(<GexDashboard data={degraded} stale={false} />)
    expect(screen.getByText(/window artefact/)).toBeInTheDocument()
  })

  it('marks a stale snapshot rather than hiding it', () => {
    render(<GexDashboard data={good} stale />)
    expect(screen.getByText(/stale/i)).toBeInTheDocument()
    expect(screen.getByText('24800')).toBeInTheDocument()
  })

  it('renders nothing without data', () => {
    const { container } = render(<GexDashboard data={null} stale={false} />)
    expect(container).toBeEmptyDOMElement()
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/components/charts/workspace/GexDashboard.test.tsx
```

Expected: `Failed to resolve import "./GexDashboard"`.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/charts/workspace/GexDashboard.tsx`:

```tsx
/**
 * The GEX Levels numeric readout, docked over the chart.
 *
 * HTML rather than canvas: it themes, wraps and reads like the rest of the
 * workspace, and `ChartWorkspace` already positions `InspectorPanel` and the
 * toast this way.
 *
 * The regime row deliberately says Suppressive / Amplifying rather than the
 * BULLISH / BEARISH the reference products use. Positive net gamma is not
 * bullish, it is stabilising — dealers sell rallies and buy dips, so price
 * pins. Negative gamma amplifies moves in BOTH directions, so labelling it
 * bearish would read as a short signal during a gamma-driven squeeze upward.
 */

import type { GEXLevelsResponse } from '@/api/gex'
import { cn } from '@/lib/utils'

export interface GexDashboardProps {
  data: GEXLevelsResponse | null
  /** The newest refresh failed; what is shown is the previous snapshot. */
  stale: boolean
}

/** Indian numbering: crore, then lakh. GEX is quoted in Cr per 1% move. */
function money(value: number | undefined): string {
  if (value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)} Cr`
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)} L`
  return `${sign}${abs.toFixed(0)}`
}

function price(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value % 1 === 0 ? String(value) : value.toFixed(2)
}

export function GexDashboard({ data, stale }: GexDashboardProps) {
  if (!data || data.status !== 'success') return null

  const q = data.quality
  const amplifying = data.regime === 'amplifying'

  return (
    <aside className="pointer-events-none absolute right-3 top-3 z-20 w-[214px] rounded-md border border-border bg-popover/95 text-[11.5px] shadow-lg backdrop-blur">
      <header className="flex items-center justify-between border-b border-border px-2.5 py-1.5">
        <span className="font-semibold">GEX Levels</span>
        <span className="text-muted-foreground">{data.underlying}</span>
      </header>

      <dl className="divide-y divide-border/60">
        <Row label="Call GEX" value={money(data.total_call_gex)} tone="pos" />
        <Row label="Put GEX" value={money(data.total_put_gex)} tone="neg" />
        <Row label="Net GEX" value={money(data.net_gex)} tone={amplifying ? 'neg' : 'pos'} strong />
        <Row
          label="Regime"
          value={amplifying ? 'Amplifying' : 'Suppressive'}
          tone={amplifying ? 'neg' : 'pos'}
          strong
        />
        <Row label="Call Wall" value={price(data.call_wall)} tone="pos" />
        <Row label="Put Wall" value={price(data.put_wall)} tone="neg" />
        <Row
          label="Zero-Gamma"
          value={data.zero_gamma == null ? 'No local cross' : price(data.zero_gamma)}
          tone="warn"
        />
        <Row label="Expiry" value={data.expiry_date ?? '—'} />
        <Row
          label="Data status"
          value={q ? `${q.strikes_priced} of ${q.strikes_used} strikes` : '—'}
          tone={q?.verdict === 'good' ? 'pos' : 'warn'}
        />
      </dl>

      {stale && (
        <p className="border-t border-border px-2.5 py-1.5 text-[11px] text-amber-500">
          Stale — the last refresh failed; showing the previous snapshot.
        </p>
      )}

      {q && q.notes.length > 0 && (
        <ul className="border-t border-border px-2.5 py-1.5 text-[11px] leading-snug text-muted-foreground">
          {q.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </aside>
  )
}

function Row({
  label,
  value,
  tone,
  strong,
}: {
  label: string
  value: string
  tone?: 'pos' | 'neg' | 'warn'
  strong?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-2 px-2.5 py-1">
      <dt className={cn('text-muted-foreground', strong && 'font-semibold text-foreground')}>
        {label}
      </dt>
      <dd
        className={cn(
          'tabular-nums',
          strong && 'font-semibold',
          tone === 'pos' && 'text-emerald-500',
          tone === 'neg' && 'text-red-500',
          tone === 'warn' && 'text-amber-500'
        )}
      >
        {value}
      </dd>
    </div>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd frontend && npx vitest run src/components/charts/workspace/GexDashboard.test.tsx
```

Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/components/charts/workspace/GexDashboard.tsx frontend/src/components/charts/workspace/GexDashboard.test.tsx
git commit -m "feat(gex-levels): add the dashboard overlay

Says Suppressive/Amplifying rather than BULLISH/BEARISH: positive net
gamma is stabilising, not bullish, and negative gamma amplifies moves in
both directions - labelling it bearish would read as a short signal
during a gamma-driven squeeze upward.

A stale snapshot is badged and kept rather than hidden.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Wire into the workspace controller

**Files:**
- Modify: `frontend/src/lib/charts/workspace.ts`

- [ ] **Step 1: Add the imports and the field**

At the top of `frontend/src/lib/charts/workspace.ts`, beside the profiles import:

```typescript
import { type GexInstrument, GexLevelsManager, type GexLevelsConfig } from './gex-levels'
import { gexApi } from '@/api/gex'
```

Add to `WorkspaceSnapshot` (near `profiles: ProfileSettings` at line 170):

```typescript
  gexLevels: GexLevelsConfig
```

Add the public field beside `readonly profiles: ProfileManager` (line 268):

```typescript
  readonly gexLevels: GexLevelsManager
```

- [ ] **Step 2: Construct it**

In the constructor, immediately after the `this.profiles = new ProfileManager({...})` block (line 389-395):

```typescript
    this.gexLevels = new GexLevelsManager({
      onChange: () => this.cb.onDirty(),
      instrument: () => this.gexInstrument(),
      fetchLevels: (params, signal) => gexApi.getGEXLevels(params, signal),
      onSnapshot: (snap) => this.cb.onGexSnapshot?.(snap),
      volumeProfileWidthOnSide: (side) => {
        const v = this.profiles.config.volume
        return v.enabled && v.side === side ? v.width : 0
      },
    })
```

- [ ] **Step 3: Resolve the instrument**

Add this private method beside `tick()` (around line 419):

```typescript
  /**
   * The underlying whose option chain backs the GEX levels, or null when there
   * is none to fetch.
   *
   * An option's own chart is excluded deliberately: its price axis is premium,
   * not underlying price, so an underlying-price level cannot be drawn on it.
   * A future maps to its own root. Everything else is passed through and the
   * server decides whether a chain exists — an exchange allowlist here would
   * duplicate knowledge that already lives in option_chain_service.
   */
  private gexInstrument(): GexInstrument | null {
    const sym = this.sym
    if (!sym) return null
    if (/\d+(CE|PE)$/.test(sym.symbol)) return null

    const root = sym.symbol.replace(/\d{2}[A-Z]{3}\d{2}FUT$/, '')
    return { underlying: root || sym.symbol, exchange: sym.exchange }
  }
```

- [ ] **Step 4: Attach, snapshot, restore, dispose**

Beside `this.profiles.attachChart(...)` (line 618):

```typescript
    this.gexLevels.attachChart(chart)
```

In `snapshot()` (line 1659), beside `profiles:`:

```typescript
      gexLevels: this.gexLevels.snapshot(),
```

In `applySnapshot()` (line 1689), beside the profiles restore:

```typescript
    if (snap.gexLevels) this.gexLevels.restore(snap.gexLevels)
```

In `dispose()` (line 1810), beside `this.profiles.dispose()`:

```typescript
    this.gexLevels.dispose()
```

And wherever the controller handles a symbol change — the same place that calls
`this.profiles.resetTape()` (line 1132) — add:

```typescript
    this.gexLevels.instrumentChanged()
```

Finally add the optional callback to the controller's callbacks interface:

```typescript
  onGexSnapshot?(snapshot: GEXLevelsResponse | null): void
```

with `import type { GEXLevelsResponse } from '@/api/gex'`.

- [ ] **Step 5: Verify it typechecks and nothing regressed**

```bash
cd frontend && npx tsc -b --noEmit && npx vitest run src/lib/charts/
```

Expected: no type errors; the existing workspace suites still pass.

- [ ] **Step 6: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/lib/charts/workspace.ts
git commit -m "feat(gex-levels): wire the study into the workspace controller

Persists with the layout, re-attaches on chart rebuild, and resets on a
symbol change alongside the footprint tape.

An option's own chart resolves to no instrument: its price axis is
premium, not underlying price, so underlying-price levels cannot be drawn
on it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Studies panel section and page wiring

**Files:**
- Modify: `frontend/src/components/charts/workspace/StudiesPanel.tsx`
- Modify: `frontend/src/pages/charts/ChartWorkspace.tsx`

- [ ] **Step 1: Add the panel section**

In `StudiesPanel.tsx`, extend `StudiesPanelProps`:

```typescript
  gex: GexLevelsConfig
  gexQuality?: string
  onGex(patch: Partial<GexLevelsConfig>): void
```

with `import type { GexLevelsConfig } from '@/lib/charts/gex-levels'`, and add
a fourth `<Section>` after the existing three, following their exact structure:

```tsx
        <Section
          title="GEX levels"
          subtitle="Dealer gamma walls and the flip"
          on={p.gex.enabled}
          onToggle={(v) => p.onGex({ enabled: v })}
        >
          <Field label="Weight by" hint="OI is the standing book; volume is today's flow">
            <TinySelect
              value={p.gex.weightBy}
              onChange={(e) => p.onGex({ weightBy: e.target.value as GexLevelsConfig['weightBy'] })}
            >
              <option value="oi">Open interest</option>
              <option value="volume">Volume</option>
            </TinySelect>
          </Field>
          <Field label="Expiry" hint="Blank uses the nearest">
            <TinyInput
              type="text"
              placeholder="Nearest"
              value={p.gex.expiry}
              onChange={(e) => p.onGex({ expiry: e.target.value.trim().toUpperCase() })}
            />
          </Field>
          <Field label="Strike bars">
            <TinySelect
              value={p.gex.showBars ? 'on' : 'off'}
              onChange={(e) => p.onGex({ showBars: e.target.value === 'on' })}
            >
              <option value="on">Show</option>
              <option value="off">Levels only</option>
            </TinySelect>
          </Field>
          <Field label="Refresh" hint="Seconds between chain fetches">
            <TinySelect
              value={String(p.gex.refreshSeconds)}
              onChange={(e) => p.onGex({ refreshSeconds: Number(e.target.value) })}
            >
              <option value="15">15 s</option>
              <option value="30">30 s</option>
              <option value="60">60 s</option>
              <option value="120">120 s</option>
            </TinySelect>
          </Field>
          {p.gexQuality && (
            <p className="px-3 pb-2 text-[11px] leading-snug text-muted-foreground">
              {p.gexQuality}
            </p>
          )}
        </Section>
```

- [ ] **Step 2: Wire the page**

In `ChartWorkspace.tsx`, add state beside the existing `profiles` state:

```typescript
  const [gex, setGex] = useState<GexLevelsConfig>(DEFAULT_GEX_LEVELS_SETTINGS)
  const [gexSnapshot, setGexSnapshot] = useState<GEXLevelsResponse | null>(null)
```

Pass the props into `<StudiesPanel>` (around line 743):

```tsx
                  gex={gex}
                  gexQuality={
                    controllerRef.current?.gexLevels.lastSnapshot?.quality?.notes.join(' ') ||
                    undefined
                  }
                  onGex={(patch) => {
                    controllerRef.current?.gexLevels.setConfig(patch)
                    setGex((s) => ({ ...s, ...patch }))
                  }}
```

Render the dashboard inside `<main>`, beside `{pinned && <InspectorPanel .../>}`:

```tsx
          {gex.enabled && gex.showDashboard && (
            <GexDashboard
              data={gexSnapshot}
              stale={controllerRef.current?.gexLevels.stale ?? false}
            />
          )}
```

and set `onGexSnapshot: (snap) => setGexSnapshot(snap)` in the controller
callbacks object where `onProfileHover` is already set.

- [ ] **Step 3: Verify and run every frontend test**

```bash
cd frontend && npx tsc -b --noEmit && npm run test:run
```

Expected: no type errors, no failures.

- [ ] **Step 4: Commit**

```bash
cd frontend && npm run check
cd .. && git add frontend/src/components/charts/workspace/StudiesPanel.tsx frontend/src/pages/charts/ChartWorkspace.tsx
git commit -m "feat(gex-levels): add the Studies panel section and page wiring

Fourth study alongside volume profile, market profile and order flow.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Documentation, live verification and the FD audit

**Files:**
- Modify: `docs/chart-workspace-studies.md`

- [ ] **Step 1: Document the study**

Add a row to the "What each study needs" table at the top of
`docs/chart-workspace-studies.md`:

```markdown
| GEX Levels | Live option chain (not the chart's bars) | Live snapshot only | Yes — the chain is on the derivatives exchange |
```

Then add this section after the Order flow section, before "Quick start":

````markdown
---

## GEX Levels

Where dealer gamma is concentrated, drawn on the price axis. Unlike the other
three studies, this is not derived from the chart's bars at all — it is a live
option-chain snapshot for the charted instrument's **underlying**, refreshed on
a timer.

| Level | What it is | How to read it |
|---|---|---|
| **Call Wall** | Strike with the largest positive dealer gamma | Rallies tend to stall into it |
| **Put Wall** | Strike with the largest negative dealer gamma | Declines tend to find support at it |
| **Zero-Gamma** | The price at which aggregate dealer gamma changes sign | Above it dealers stabilise; below it they amplify |

**Regime**, in the dashboard, is the sign of net GEX:

- **Suppressive** (positive) — dealers sell rallies and buy dips, so price pins.
- **Amplifying** (negative) — dealers trade with the move, so it extends.

Amplifying is *not* bearish. Negative gamma extends moves in **both**
directions, which is why the dashboard says Suppressive/Amplifying rather than
the BULLISH/BEARISH some products use.

### Zero-Gamma is a scan, and "No local cross" is normal

Zero-Gamma is not the strike where a running total of per-strike GEX crosses
zero. Gamma itself depends on where the underlying is, so the whole profile is
rebuilt at 60 hypothetical prices spanning ±20% of the forward, and the sign
change is interpolated. That is why the level lands **between** strikes.

When the profile is long gamma, or short gamma, across that entire range there
is no crossing, and the dashboard reads **No local cross**. That is an ordinary
market state, not an error — a chain simply has no flip nearby.

### Weight by open interest or volume

| Weighting | What it measures |
|---|---|
| **Open interest** (default) | The full standing dealer book |
| **Volume** | Today's traded flow only; empty at the open, builds through the session |

Open interest is the default because NSE and BSE disseminate it **live** in the
tick feed. The US argument for volume-weighted GEX — that official open interest
is a prior-night snapshot and goes stale intraday — does not apply here.

### What it works on

| Charted instrument | GEX Levels |
|---|---|
| `NIFTY` on `NSE_INDEX`, `NIFTY28AUG26FUT` on `NFO` | Yes — deep, cash-settled, writer-dominated |
| A single stock or its future | Yes, but expect a degraded verdict. Monthly expiry only and physically settled, so open interest unwinds fast into expiry |
| An MCX future | Yes. Options are written on a future, which is what Black-76 already assumes. Crude is the only commodity with real depth |
| An option itself (`NIFTY28AUG2624000CE`) | **No.** Its price axis is premium, not underlying price — an underlying-price level cannot be drawn on it |
| Cash equity with no F&O, and anything on CDS | No chain to fetch |

Unlike the profiles, GEX levels have no time anchoring, so they render on the
movement-driven chart types (Renko, P&F, Kagi) too.

### Data status

The chain is fetched **23 strikes each side of ATM**. That is a broker limit,
not a preference: the multiquote open-interest bucket holds 100 symbols, and
asking for more returns empty OI rather than an error.

The `Data status` row reports how many of those strikes yielded a real implied
volatility. The study degrades itself and says why when the chain is mostly
unpriced, when the window sits entirely on one side of the forward, or when a
wall lands on the window's edge — where it may be an artefact of where the
window stopped rather than a real concentration.

### Compared with the `/gex` tool

The `/gex` Tools page and this study can show different **magnitudes** for the
same chain: `/gex` prices off spot and omits the lot-size and notional factors,
while the study prices off the per-expiry synthetic forward and reports ₹ per 1%
move. The **strikes** the walls land on should agree.
````

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest test/ -k "gex" -v
cd frontend && npm run test:run
```

Expected: no failures.

- [ ] **Step 3: Verify in the live app**

Rendering has no automated coverage — jsdom provides no canvas — so this step
is the only proof the pixels are right.

```bash
uv run app.py
```

Then in the browser at `http://127.0.0.1:5000/charts`:

1. Load `NIFTY28AUG26FUT` on `NFO` (Ctrl+K). Open **Studies → GEX levels**.
2. Confirm the three levels draw, the bar column sits clear of the price axis,
   and the dashboard shows non-zero Call/Put/Net GEX.
3. Switch **Weight by** to Volume. The numbers must change and Net GEX must
   remain finite.
4. Turn **Volume profile** on with GEX bars on. Confirm the two columns do not
   overlap.
5. Zoom until a wall leaves the visible range. Confirm it becomes an edge
   marker rather than disappearing, and that **the candles do not squash** —
   that is the autoscale guarantee.
6. Load `NIFTY28AUG2624000CE`. Confirm the study reports unavailable.
7. Load a thin single-stock chain. Confirm the data-status row degrades and
   names a reason.
8. Cross-check Call Wall and Put Wall against `/gex` for the same expiry. The
   *strikes* should agree; the *magnitudes* will differ, because `/gex` prices
   off spot and omits the lot and notional factors. Both are expected.

- [ ] **Step 4: Run the FD audit**

This change adds a poll timer and an HTTP client to a single Gunicorn worker
that never restarts.

Invoke the `fd-audit` skill and address anything it raises.

- [ ] **Step 5: Commit**

```bash
git add docs/chart-workspace-studies.md
git commit -m "docs(gex-levels): document the study

Covers what each level means, why 'No local cross' is normal, the
OI-versus-volume weighting, and the 23-strikes-each-side broker constraint.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification checklist

Before calling this done, confirm each:

- [ ] `uv run pytest test/ -k "gex" -v` passes
- [ ] `cd frontend && npm run test:run` passes
- [ ] `cd frontend && npx tsc -b --noEmit` is clean
- [ ] `uv run ruff check .` is clean
- [ ] `STRIKE_COUNT` is still 23 — nobody raised it
- [ ] The zero-gamma no-cross path was seen returning `null` in a real session
- [ ] The candles do not squash with the study on, at any zoom
- [ ] The `fd-audit` skill has been run and its findings addressed
- [ ] `gamma_density_service` still works after the helper extraction
