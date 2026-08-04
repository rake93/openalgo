# Option Target Calculator — Design

**Date:** 2026-08-04
**Status:** Approved for planning
**Surface:** Options & Portfolio Suite (`/tools`)

## 1. Problem

A directional intraday or swing trader watches the index future, forms a view
("NIFTY future is at 24635, I think it reaches 24700"), and then has to guess
two things:

1. **Which strike to buy.** ATM? One strike ITM? Three OTM?
2. **What that option will be worth** when the future actually reaches target.

Today this is done by eyeballing delta, which is wrong in three separate ways:
it ignores gamma over the move, it ignores the time the move takes, and it
ignores that implied volatility changes as the forward moves. The errors
compound and all point the same direction on a winning trade.

The Option Target Calculator answers both questions from live market data.

### Reference trade

From the originating chart (NIFTY 3-minute, 2026-08-04):

| Leg | Entry | Exit |
| --- | --- | --- |
| 24700 PE | future at 24690 | future at 24625 |
| 24600 CE | future at 24625 | future at 24670 |

Single-leg, slightly ITM, held 30–60 minutes. The tool must make this exact
decision better, while also supporting multi-day holds.

## 2. Scope

**In scope**

- Single-leg CE or PE buying, intraday (minutes) and multi-day (days).
- All F&O underlyings: indices and stocks, NFO / BFO / MCX / CDS.
- Strike ranking across a configurable window around ATM.
- Cross-expiry comparison for the chosen strike.
- Live auto-refresh with a freeze toggle.
- Actions on the chosen strike: copy symbol, place a buy order, send to
  Strategy Builder as a leg.

**Out of scope**

- Multi-leg spread construction — Strategy Builder already owns this.
- Option selling / margin analysis.
- Backtesting the strike-selection rule against history.
- Automated entry or exit.

## 3. Validation against live data

The algorithm below was prototyped end-to-end against a live NIFTY chain
snapshot (2026-08-04 12:11 IST, expiry 11AUG26, 7.14 days to expiry, 25
strikes) before this spec was written. Findings that shaped the design:

| Check | Result | Consequence |
| --- | --- | --- |
| Vega-weighted quadratic smile fit | RMS residual **0.053** vol points, max **0.169** across 25 strikes | Fit is good enough to project from; no need for SVI or a spline |
| Synthetic forward vs index spot | 24514.62 vs 24507.10, basis **+7.53 pts** | Pricing off spot is wrong from the first line; forward is mandatory |
| Full reprice vs delta-only, 65-pt move | Gap of **Rs 9 to Rs 274 per lot**, widening with move size | Full reprice is mandatory, not a refinement |
| Smile-slide vs sticky-strike, CE up-move | Slide higher by up to **Rs 173/lot** on ITM strikes | Vol model choice is material; expose both |
| Smile-slide vs sticky-strike, PE down-move | Slide **lower** by Rs 21–112/lot | See 3.1 — drove the vol-response addition |

### 3.1 Why a vol-response term exists

The fitted NIFTY smile (`iv(x) = 0.1095 - 0.2434x + 10.7931x^2`, where
`x = log(K/F)`) has its minimum at `x* = -b/2c = +0.0113`, i.e. just above ATM.
A downward move raises `x` for every strike, walking near-ATM strikes *toward*
that minimum — so pure smile-slide **lowers** near-ATM put IV on a fall.

That is mechanically correct for the smile *shape*, and it is also incomplete.
The familiar "index drops, volatility spikes" effect is a change in the vol
**level**, which sliding a fixed shape cannot represent. A model with only
smile-slide systematically underprices puts on sharp falls. Section 3.2
measures this directly.

The design therefore separates the two effects:

- **Smile slide** — shape travels with the forward. Derived from live data.
- **Vol response (beta)** — level shifts with the move. Estimated from the
  session (Section 3.3), overridable, because it is a regime-dependent
  quantity that no single snapshot can determine.

`sigma_target(K) = smile(log(K / F_target)) - beta * (dF / F) * 100 + manual_shift`

where `beta` is in vol points per 1% move, signed so that a fall raises vol.

Default is **auto-estimated from the session** (Section 3.3), falling back to
the Normal preset when the fit is weak. The applied vol delta and the beta
actually used are always displayed, so the assumption is never hidden.

### 3.2 Backtest against a real trade (BANKNIFTY, 2026-08-04)

A second validation replayed an actual trade: 57800 PE, entered at spot 57793
(10:25), exited at spot 57505 (12:01), expiry 25AUG26 (21 DTE), 96-minute hold,
a -0.502% index move. The smile was reconstructed **at entry only**, projected
to the exit spot, and compared against the premium each strike **actually
traded at** on exit. 37 series (CE and PE), 1-minute history.

| Model | Mean err | MAE | Worst |
| --- | --- | --- | --- |
| Delta only | -6.84% | 6.84% | 17.38% |
| Smile slide alone | -6.71% | 6.77% | 14.75% |
| Sticky strike, full reprice | -5.52% | 5.55% | 14.45% |
| Slide + vol-beta 0.8 | -3.42% | 3.60% | 8.35% |
| **Slide + vol-beta 1.5** | **-0.53%** | **1.26%** | **3.37%** |

Conclusions that changed the design:

1. **The full model cuts error 5.4x versus delta-only** (1.26% vs 6.84% MAE).
   On the traded 57800 PE it projected 791.2 against an actual 792.5.
2. **Every model under-predicts, and calls and puts err in the same
   direction.** A forward-price error would move calls and puts in *opposite*
   directions. Same-signed error across both is the signature of a **vol level
   rise**, confirming the vol-response term is a real effect and not a fitted
   fudge. It is the single largest error term in the model.
3. **Realised vol-beta was ~1.4**, well above the 0.8 assumed as "Normal".
   A guessed constant is not good enough — see Section 3.3.

### 3.3 Vol-beta must be measured, not guessed

Because vol response dominates the residual error, the engine **estimates beta
from the session's own data** rather than relying on a preset: sample ATM IV
and index level at 1-minute intervals over a trailing window (default 90 min,
via the existing history API), regress `d(ATM IV in vol pts)` on
`(percent index return)`, and take the negated slope as beta.

- Estimated beta is displayed with its R-squared and sample count.
- A weak fit (R-squared below 0.3, or fewer than 20 samples) falls back to the
  Normal preset and says so.
- The user can override with presets Calm (0.3) / Normal (0.8) / Panic (2.0) /
  Off (0) or a manual value.

This replaces the largest remaining error term with a measurement. It costs one
extra history call per underlying, cached for the life of the snapshot.

### 3.4 Basis: measured, and why reference choice follows from it

| Underlying | Expiry | Basis | Drift over hold |
| --- | --- | --- | --- |
| NIFTY | 11AUG26 (7 DTE weekly) | +7.53 pts | not measured |
| BANKNIFTY | 25AUG26 (21 DTE monthly) | **+138.9 pts** at entry, **+156.2** at exit | **+17.3 pts over 96 min** |

The BANKNIFTY basis is 0.24% of spot. A 57800 strike that looks ATM against
spot 57794 is 134 points OTM against the forward 57934 — a materially different
option. Pricing off spot is not an approximation, it is the wrong instrument.

The basis also **drifts during the hold** (+17.3 pts here). Both candidate
forward models missed the exit forward by 17-18 points, almost entirely basis
drift, and were within 0.7 points of each other — so the ratio-versus-parallel
choice is immaterial next to the basis question itself.

This yields the reference rule in Step 3.

## 4. Algorithm

All steps operate on one chain snapshot. Pure functions, no broker calls except
in the snapshot layer.

### Step 1 — Snapshot

Fetch via `option_chain_service.get_option_chain(underlying, exchange,
expiry_date, strike_count)`. Per strike this yields CE and PE `ltp`, `bid`,
`ask`, `oi`, `volume`, `lotsize`, `tick_size`, plus `underlying_ltp` and
`atm_strike`. Also fetch the near-month futures quote when the reference is
`FUT`.

Note: `expiry_date` must be **DDMMMYY** (`11AUG26`). The `/api/v1/expiry`
endpoint returns **DD-MMM-YY** (`11-AUG-26`). The service converts; the caller
must not pass the dashed form.

### Step 2 — Anchor the forward

`F_now = K_atm + mid(CE_atm) - mid(PE_atm)` (put-call parity), via
`synthetic_future_service`.

`mid = (bid + ask) / 2` when both sides are present and `ask >= bid`, else
`ltp`. Mid is preferred over LTP because LTP goes stale and one-sided on thin
strikes.

Fallback: if ATM CE/PE quotes are missing, use spot and emit a warning. The
projection stays usable; the user is told it is less precise.

### Step 3 — Map the target onto the forward

Two modes, selected automatically by whether a future of the **same expiry as
the option** exists.

**Exact mode — matched future.** When a same-expiry future is tradeable (all
stock options, and index monthlies such as BANKNIFTY 25AUG26), and the user
references that future:

`F_target = F_now + (R_target - R_now)`

A 1:1 shift with **no basis assumption at all**, because the future and the
synthetic forward are the same economic instrument. This removes the basis
drift error entirely (17.3 pts on the measured BANKNIFTY trade).

**Basis-modelled mode.** When no same-expiry future exists (NIFTY weeklies) or
the user references spot:

`F_target = F_now * (R_target / R_now)`

Proportional, because basis is a cost-of-carry ratio. Section 3.4 measured
ratio and parallel as within 0.7 pts of each other, so the choice is
immaterial; proportional is used as the textbook relationship.

The response labels which mode was used — `exact` or `basis_modelled` with the
current basis and an uncertainty estimate — and the UI displays it. A number
carrying a basis assumption must never look identical to one that does not.

**Guidance surfaced in the UI:** stocks and index monthlies default to the
matched future; NIFTY weeklies accept spot, where the measured basis is only
~7.5 pts.

**Moneyness labelling.** Labels are computed against the **forward**, since
that is what determines option value, but the spot-relative ATM is displayed
alongside. On the BANKNIFTY trade the 57800 PE is ATM against spot and OTM1
against the forward; showing only one of those misleads.

### Step 4 — Calibrate IV per strike

For each strike, back out IV from the live **mid** using
`opengreeks.black76.implied_volatility(price, F_now, K, r, T_now, flag)`.

Rejection rules, each producing a per-strike reason string:

- `mid <= 0` — no market.
- `mid <= intrinsic + 0.05` — no time value; IV is not recoverable.
- solver raises, or `iv` outside `(0.01, 3.0)` — numerically unusable.

Calibrating from live prices rather than assuming a vol is what makes the model
reproduce today's actual market. The projection is then a consistent
perturbation of reality rather than a free-standing theoretical price.

### Step 5 — Fit the smile

Points are `(x = log(K / F_now), iv)` using the **OTM wing** on each side:
puts for `K < F`, calls for `K > F`. ITM implied vols are discarded because the
premium is nearly all intrinsic, so IV is numerically ill-conditioned there.

Weighted least squares on `[1, x, x^2]` with weights `= vega`, so ATM strikes
dominate and far wings cannot lever the fit. Returns `(a, b, c)` plus the
observed range `[x_lo, x_hi]` and the RMS residual.

Outside `[x_lo, x_hi]` the fit is **clamped flat**, never extrapolated — an
unconstrained parabola produces absurd vols on far strikes.

If fewer than 5 points calibrate, skip the fit, use flat ATM IV, and warn.

### Step 6 — Advance the clock

`T_target = T_now - dt`, `dt` from the hold input (minutes or days).

Day count is **calendar 365** by default, matching
`option_greeks_service.calculate_time_to_expiry`, so numbers reconcile with the
Option Greeks and Option Chain pages. A "skip non-trading days" toggle uses
`utils/trading_calendar.py` for multi-day holds spanning weekends or holidays,
where calendar decay materially overstates the bleed.

If `T_target <= 0` the hold runs past expiry: price is intrinsic at
`F_target`, and the UI warns prominently rather than quietly returning a
number.

### Step 7 — Project IV

Per Section 3.1:

- `smile_slide` (default): `sigma = fit(clamp(log(K / F_target)))`
- `sticky_strike`: `sigma = iv_now(K)`

then apply vol response and manual shift, floored at 0.001.

Both models are computed and returned regardless of selection, so the UI can
show the spread between them as an honest uncertainty band.

### Step 8 — Reprice

`P_target = black76.black(flag, F_target, K, T_target, r, sigma_target)`

Full reprice. Greeks at target come from the same call set. The chain is
repriced with the `*_array` vectorised variants in one pass.

### Step 9 — Execution-adjusted P&L

- Entry cost = `ask_now` — you pay the offer.
- Exit value = `P_target - half_spread_now`, floored at 0.

The current **rupee** half-spread is assumed to persist. Assuming a constant
*percentage* spread would triple the modelled spread on a strike whose premium
triples, over-penalising exactly the winners. Constant rupee is the better
approximation and errs conservative.

`pnl_per_lot = (exit_value - entry_cost) * lot_size`

Mid-based P&L is returned alongside so the spread cost is explicit rather than
silently baked in.

### Step 10 — Adverse mirror

Reprice at `R_adverse = R_now - (R_target - R_now)` over the same elapsed time.
`reward_risk = projected_gain / abs(adverse_loss)`.

This is the number most such tools never compute, and it is what separates a
strike that makes 30% on a win from one that also loses 60% on a miss.

### Step 11 — P&L attribution

For the waterfall display, decompose the total projected change:

| Term | Method |
| --- | --- |
| Delta | `delta_now * dF` |
| Gamma | `0.5 * gamma_now * dF^2` |
| Theta | `P(F_now, T_target, sigma_now) - P(F_now, T_now, sigma_now)` — exact |
| Vega | `P(F_target, T_target, sigma_target) - P(F_target, T_target, sigma_now)` — exact |
| Spread | `-(entry_ask - mid_now) - half_spread` |
| Residual | total minus the above |

Theta and vega are computed exactly by re-pricing rather than via the Greek,
because over a 65-point move and 45 minutes the linear approximations drift.
The residual is displayed, not hidden — a large residual is a signal that the
move is big enough that the attribution itself is only indicative.

### Step 12 — Ranking

Per-strike metrics: moneyness label, bid/mid/ask, spread %, IV now and at
target, Greeks now, projected premium, P&L per lot and total, return %,
**effective delta** (`(P_target - P_now) / dF`, the realised figure over the
whole move rather than the instantaneous Greek), theta cost in rupees, adverse
P&L, reward:risk, breakeven reference level, OI, volume.

Objectives, user-selectable: `max_pnl`, `max_return`, `max_rr`, `balanced`.

`balanced` normalises return %, reward:risk and effective delta to [0,1] across
candidates, weights them 0.4 / 0.4 / 0.2, then subtracts a liquidity penalty
scaled by spread % and an OI floor. One row is flagged Recommended with a
one-line reason naming the dominant factor.

Strikes failing hard filters (zero bid, OI below floor, spread % above ceiling)
are **greyed with the reason shown**, never silently dropped. A hidden
exclusion looks identical to a strike that does not exist.

### Step 13 — Cross-expiry compare

Run Steps 1–11 for the selected strike against the next 2–3 expiries. Returns
expiry, DTE, entry cost, projected premium, P&L, return %, theta cost — making
the weekly-versus-monthly trade-off explicit for multi-day holds.

### Step 14 — Premium ladder

For the selected strike, project across a range of reference levels
(`R_now +/- 1.5x` the target distance, ~15 steps) at the chosen elapsed time.
This maps directly onto a price chart: "future at 24670, my 24600 CE is worth
X."

## 5. API contract

`POST /api/v1/optiontarget`

**Request**

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `apikey` | string | — | required |
| `underlying` | string | — | e.g. `NIFTY` |
| `exchange` | string | `NFO` | options exchange |
| `expiry_date` | string | nearest | DDMMMYY |
| `reference` | enum | `FUT` | `FUT` or `SPOT` |
| `reference_price` | float | live | override for what-if |
| `target_price` | float | — | required |
| `hold_minutes` | int | 45 | mutually exclusive with `hold_days` |
| `hold_days` | float | — | multi-day holds |
| `iv_model` | enum | `smile_slide` | or `sticky_strike` |
| `vol_beta` | float \| `"auto"` | `"auto"` | vol points per 1% move; `auto` estimates from session (S 3.3) |
| `vol_shift` | float | 0 | absolute vol points |
| `day_count` | enum | `calendar` | or `trading` |
| `strike_count` | int | 12 | strikes each side of ATM |
| `side` | enum | `AUTO` | `AUTO` derives from target direction |
| `lots` | int | 1 | |
| `interest_rate` | float | 0 | matches platform default |
| `objective` | enum | `balanced` | ranking metric |
| `compare_expiries` | string[] | [] | DDMMMYY list |

**Response** — `snapshot`, `smile` (coefficients, RMS, point count, clamp
range), `scenario` (echo of resolved inputs including derived `forward_target`
and applied vol delta), `candidates[]`, `recommended`, `ladder[]`,
`expiry_compare[]`, `warnings[]`.

Every assumption the engine made appears in the response. Nothing is applied
invisibly.

## 6. UI

Route `/optiontarget`, title "Option Target Calculator", registered in
`lib/tools.ts` (which auto-updates the `/tools` count and home page).

- **Header** — underlying picker, expiry dropdown, live spot / futures /
  expiry-forward with basis, DTE, ATM strike, ATM IV, freeze toggle, quote age.
- **Scenario panel** — reference toggle, current price (auto, overridable),
  target price with quick-set buttons (+/-0.25%, 0.5%, 1%), hold time
  (minutes/days), IV model toggle, vol response (auto-estimated value shown
  with its R-squared, overridable by preset or manual), manual vol shift, lots,
  day-count toggle.
- **Strike table** — ranked, objective dropdown, Recommended row highlighted
  with reason, excluded rows greyed with cause.
- **Detail card** for the selected strike — premium ladder chart, Greeks now vs
  at target, P&L waterfall, breakeven, sticky-vs-slide spread as an uncertainty
  band.
- **Cross-expiry panel**.
- **Actions** — copy symbol, Buy (behind a confirm dialog showing symbol,
  quantity, product and estimated cost), Send to Strategy Builder.

Auto-refresh polls on an interval; freeze pins the snapshot so the table does
not move while rows are being compared.

## 7. Files

**Backend**

- `services/option_target_service.py` — engine. Pure functions
  (`compute_forward`, `project_forward`, `calibrate_ivs`, `fit_smile`,
  `project_strike`, `attribute_pnl`, `rank_strikes`, `build_ladder`) plus one
  snapshot function that touches the broker. This split is what makes the math
  testable without a live session.
- `restx_api/option_target.py` + schema; namespace registered in
  `restx_api/__init__.py` at `/optiontarget`.

**Frontend**

- `frontend/src/pages/OptionTargetCalculator.tsx`
- `frontend/src/api/option-target.ts`
- Registrations: `App.tsx` route, `usePageTitle.ts` title,
  `blueprints/react_app.py` route (so unauthenticated hits do not count toward
  `Error404Tracker`), `lib/tools.ts` registry entry.

**Docs** — page under `docs/`, linked from `docs/INDEX.md`.

## 8. Testing

Unit tests (`test/test_option_target_service.py`) run against a **recorded live
chain fixture**, so they need no broker session:

- Forward equals put-call parity to within a tick.
- Smile fit RMS residual stays under 0.25 vol points on the fixture.
- Smile clamps flat outside the observed moneyness range.
- Sticky-strike and smile-slide agree when `b = c = 0`.
- CE projected premium is monotonically increasing in `F_target`; PE decreasing.
- `T_target <= 0` returns intrinsic exactly.
- Adverse mirror is symmetric in reference distance.
- Attribution terms sum to the total within the reported residual.
- Excluded strikes carry a non-empty reason.
- Ranking is stable under a permutation of the input strike order.

Fixture capture script under `scripts/` so the snapshot can be refreshed.

## 9. Edge cases

| Case | Handling |
| --- | --- |
| Hold runs past expiry | Intrinsic value, prominent warning |
| ATM CE/PE missing | Forward falls back to spot, warning |
| Fewer than 5 calibrated strikes | Flat ATM IV, no fit, warning |
| Zero-bid or illiquid strike | Excluded with visible reason |
| Market closed / stale quotes | Quote age shown, staleness warning |
| MCX / CDS expiry times | Reuse `option_greeks_service` conventions (23:30 / 12:30) |
| Non-index underlyings | Lot size and strike step derived from the chain, not hardcoded |
| Target equals current price | Zero-move projection showing pure theta cost |

## 10. Resource hygiene

No new engines, threads, executors or sockets. The engine reuses existing
services, which already use the shared httpx client. The snapshot cache is a
**bounded `TTLCache`** (following the `verified_api_key_cache` pattern in
`database/auth_db.py`), not an unbounded dict — an unbounded cache keyed by
`(symbol, expiry)` would grow without limit across a long-lived Gunicorn
worker.

Run the `fd-audit` skill before considering the work done.

## 11. Open decisions for review

1. Route and tool name — `/optiontarget`, "Option Target Calculator".
2. Default strike window — 12 each side of ATM.
3. ~~Vol-beta trailing window — spec assumes 90 minutes.~~ **Resolved:
   120 minutes**, measured back from the last bar, as a maximum lookback rather
   than a minimum wait. 120 is the shortest window where the first-differenced
   regression also clears the R-squared 0.30 gate (1.99 on 2026-08-04 NIFTY);
   at 30/60/90 minutes differencing returns 0.22/0.22/0.29 and falls back, so
   a shorter window's higher levels estimate cannot be distinguished from a
   shared time-of-day trend.
4. ~~Whether the vol-beta estimator should also run on prior sessions' data.~~
   **Resolved: no.** Measured on 2026-08-04 NIFTY, the existing gates already
   degrade correctly on their own — "Only 11 samples" at 09:25, a weak fit
   until ~09:45, and clean estimates from 10:00 (beta 1.60, R-squared 0.60).
   That is a 45-minute window, not a gap worth new plumbing, and an overnight
   return is not a 1-minute return.

Added during implementation, not in the original spec: an estimate beyond the
Panic preset is **clamped to 2.0** with the raw value reported. Both NIFTY and
BANKNIFTY clamped on 2026-08-04 (raw 2.89 and 2.10), so the clamp binds on an
ordinary day — which is the point. The sample range that produced 2.89 was
0.18 percent wide.

## 12. Validation assets

The two prototypes that produced Sections 3.2–3.4 should be promoted into
`scripts/` so the findings can be re-derived after any change:

- Chain-snapshot smile calibration and projection check.
- Historical replay of a completed trade, measuring projection error per model
  across all strikes.

The replay harness doubles as the regression test for model changes: any edit
to the projection math must not worsen MAE on the recorded fixture.
