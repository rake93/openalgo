# Option Target Calculator (`/optiontarget`)

The Option Target Calculator answers the question a directional trader always
has to guess at: "if [the future / the index] reaches X, what will each strike
be worth, and which one should I buy?"

It is for a trader who already has a directional view — from a chart, a news
event, a level — and needs to turn that view into a strike, not for someone
asking how likely the move is. Give it a futures or spot target, a hold time,
and it projects every strike in the chain to that target and ranks them.

Backend: `services/option_target/*.py` (pure math, no broker or database
dependency), `services/option_target_service.py` (orchestration — the only
module in the feature that touches the broker), `services/option_target_sessions.py`
(live session-hours provider), `restx_api/option_target.py`
(`POST /api/v1/optiontarget`). Frontend: `frontend/src/pages/OptionTargetCalculator.tsx`,
registered in `frontend/src/lib/tools.ts`.

---

## Why the forward, not spot

Options are priced off the forward for their own expiry, not the index spot.
Measured on 2026-08-04:

| Underlying | Expiry | Basis |
| --- | --- | --- |
| NIFTY | 11AUG26 (7 DTE weekly) | +7.5 pts |
| BANKNIFTY | 25AUG26 (21 DTE monthly) | +138.9 pts |

At a +139 point basis, a strike that looks at-the-money against spot is 134
points out-of-the-money against the forward. Those are priced as different
options. Anchoring the projection on spot is not an approximation of the
forward — it is the wrong instrument, by the size of the basis.

The forward is reconstructed from live put-call parity at the ATM strike:
`F = K_atm + mid(CE_atm) - mid(PE_atm)`. If either ATM leg has no usable quote,
the forward falls back to spot and the response carries a warning — the
projection stays usable, but the basis is now carried as error rather than
removed.

## Spot vs futures guidance

When a same-expiry future exists — stock options, and index monthlies such as
BANKNIFTY's monthly contract — the calculator references that future.
`reference: "FUT"` and `forward_mode` resolves to `exact`: the shift from the
current future price to the target is 1:1, with **no basis assumption at
all**, because the future and the synthetic forward are the same economic
instrument.

NIFTY weeklies have no matching future, so they run `forward_mode:
"basis_modelled"`: the forward is shifted proportionally to the spot move
(`F_target = F_now * (target / now)`). The measured basis for a NIFTY weekly
is small (about +7.5 pts), so the residual error from modelling rather than
matching is minor. The response always states which mode was used, along with
the basis actually observed, so a number carrying a basis assumption never
looks identical to one that does not.

## The three modelled effects

In measured order of importance:

1. **Vol-level response** — the largest single correction. "The index fell
   and volatility spiked" is a change in the vol *level*, not a change in
   moneyness, and only the vol-beta term captures it.
2. **Gamma** — captured by doing a full Black-76 reprice at the target forward
   and time, never a delta or delta-gamma Taylor approximation.
3. **Smile slide** — the fitted vega-weighted quadratic smile is evaluated at
   the strike's *new* moneyness against the target forward, rather than held
   fixed at its current-moneyness value.

Backtest against a completed BANKNIFTY trade (2026-08-04, 57800 PE, entry spot
57793 at 10:25, exit spot 57505 at 12:01, 25AUG26 expiry, 96-minute hold, 37
strike series):

| Model | Mean absolute error |
| --- | --- |
| Delta only | 6.84% |
| Smile slide alone | 6.77% |
| Sticky strike, full reprice | 5.55% |
| Smile slide + vol-beta 1.5 | 1.26% |

### Why smile slide alone is worse than sticky strike

This is the counter-intuitive result that shaped the design. Sliding a fixed
smile shape to the strike's new moneyness models the strike's changed position
relative to the forward, but it cannot represent a change in the vol *level*.
"The index dropped and volatility spiked" is exactly that: a level change, not
a moneyness change. A model with slide but no vol-beta term systematically
under-prices the move, which is why slide alone (6.77%) barely beats delta
only (6.84%) and is measurably worse than simply holding each strike's own IV
fixed and doing a full reprice (5.55%). The vol-beta term is what closes the
gap from there down to 1.26%, which is why it is treated as the largest
correction in the model rather than a refinement.

## Why probability weighting was rejected

An earlier design considered weighting each strike's projected P&L by the
probability of the target actually being reached. It was rejected: over the
45-90 minute horizons this tool targets, a 1 percent index move is 3 to 9
standard deviations. Every strike then scores a near-zero probability, and
ranking on probability-weighted P&L degenerates into noise rather than signal.

Probability also answers a question the user did not ask. The user is
asserting a directional view ("if NIFTY reaches X") — not asking how likely
that view is. Instead, every candidate is evaluated at 50%, 75% and 100% of
the target move (`MOVE_SCENARIOS` in `services/option_target/ranking.py`), and
`robust_pnl_per_lot` is the mean P&L across those three partial-move
scenarios. A strike that only pays once the move fully completes is penalised
relative to one that pays across the range, without ever pretending to know
the odds of the move happening.

## Zero-DTE behaviour

Measured on expiry day versus a 7-day expiry, same underlying:

| Measure | Expiry day (0 DTE) | 7 DTE |
| --- | --- | --- |
| ATM implied vol | 38.59% | 11.34% |
| Smile RMS residual | 0.625 vol points | 0.024 vol points |

Below one day to expiry, theta dominates hard enough that a far out-of-the-money
strike can *lose* value even on a favourable underlying move, because time
decay outruns the delta gain over the hold. The response sets
`snapshot.is_zero_dte` and adds a warning naming the hours remaining whenever
`days_to_expiry` is under 1.0, so the UI can flag it rather than let the
projection look as reliable as it does on a normal day.

## Ranking objectives

Every candidate strike gets the full metric set regardless of objective —
premium, Greeks, projected P&L, return %, reward:risk, robust P&L, theta
cost, attribution — and the user picks how they are ranked:

| Objective | Ranks on |
| --- | --- |
| `balanced` (default) | Weighted blend of robust P&L (0.5), reward:risk (0.3) and effective delta (0.2), minus a spread penalty |
| `max_pnl` | Highest rupee P&L per lot |
| `max_return` | Highest percentage return on premium |
| `max_rr` | Best reward-to-risk ratio |
| `max_robust` | Best average P&L across the 50/75/100% partial-move scenarios |

Deep ITM strikes maximise rupee P&L; far OTM strikes maximise percentage
return and reward:risk (a small premium caps the modelled loss while the
payoff on a completed move is still large in relative terms). Those are never
the same strike, which is why every metric is computed and surfaced rather
than the tool silently picking one definition of "best".

Strikes that fail a liquidity or spread filter (zero bid, OI and volume both
below the floor, or spread over 25% of mid) are **retained in the response and
shown with their exclusion reason**, sorted to the bottom, rather than
dropped. A hidden exclusion is indistinguishable from a strike that does not
exist, which is exactly the confusion this tool exists to remove.

## API contract

`POST /api/v1/optiontarget`

### Request

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `apikey` | string | yes | - | |
| `underlying` | string | yes | - | e.g. `NIFTY`, `BANKNIFTY` |
| `exchange` | string | yes | - | one of the platform's valid exchanges |
| `expiry_date` | string | no | nearest live expiry | **DDMMMYY**, e.g. `11AUG26`. `/api/v1/expiry` returns the dashed form `11-AUG-26`, which this endpoint does **not** accept — convert before calling |
| `reference` | enum | no | `FUT` | `FUT` or `SPOT` |
| `reference_price` | float | no | live spot/forward | override, for a what-if scenario against a price other than the current one |
| `target_price` | float | yes | - | must be > 0.01 |
| `hold_minutes` | float | no | 45.0 | 0-525600; ignored when `hold_days` is supplied |
| `hold_days` | float | no | - | 0-365; wins over `hold_minutes` when both are given |
| `iv_model` | enum | no | `smile_slide` | `smile_slide` or `sticky_strike` |
| `vol_beta` | float or string | no | `"auto"` | `"auto"` (estimated from session data), a preset (`"off"` 0.0, `"calm"` 0.3, `"normal"` 0.8, `"panic"` 2.0), or a manual float in vol points per 1% move |
| `vol_shift` | float | no | 0.0 | manual additional shift, in vol points, -50 to 50 |
| `day_count` | enum | no | `calendar` | `calendar` (365-day) or `trading` (market-session minutes) |
| `strike_count` | int | no | 12 | 1-50; strikes fetched each side of ATM |
| `side` | enum | no | `AUTO` | `AUTO` derives CE/PE from target direction, or force `CE`/`PE` |
| `lots` | int | no | 1 | 1-10,000 |
| `interest_rate` | float | no | 0.0 | percent, -10 to 50 |
| `objective` | enum | no | `balanced` | `balanced`, `max_pnl`, `max_return`, `max_rr`, `max_robust` |

### Response

```json
{
  "status": "success",
  "snapshot": { "...": "forward, basis, ATM strike/IV, DTE, is_zero_dte, matched_future, lot_size" },
  "smile": { "...": "fitted a/b/c, clamp range, RMS, point count, degenerate flag, rejected strikes" },
  "scenario": { "...": "resolved reference/target, forward_target, forward_mode, move_pct, vol_beta detail" },
  "candidates": [ "...one row per strike, see below" ],
  "recommended_strike": 57800.0,
  "ladder": [ "...premium across a bracket of reference levels, for charting" ],
  "warnings": [ "...every assumption the engine made, in plain text" ]
}
```

- **`snapshot`** — `underlying`, `exchange`, `expiry_date`, `spot`, `forward`,
  `basis`, `forward_source` (`parity` or `spot_fallback`), `atm_strike`,
  `strike_step`, `atm_iv_pct`, `days_to_expiry`, `is_zero_dte`, `t_years`,
  `matched_future` (the matched futures symbol, or `null`), `lot_size`.
- **`smile`** — `a`, `b`, `c` (the quadratic coefficients), `x_lo`/`x_hi` (the
  clamp range), `rms_vol_pts`, `n_points`, `degenerate`, `rejected` (per-strike
  rejection reasons from calibration).
- **`scenario`** — echoes every resolved input (`reference`, `reference_now`,
  `reference_target`, `hold_minutes`, `day_count`, `iv_model`, `vol_shift`,
  `side`, `objective`, `lots`) plus derived values: `forward_target`,
  `forward_mode`, `move_pct`, `t_target_years`, and `vol_beta` as an object
  (`beta`, `r_squared`, `samples`, `source`, `reason`).
- **`candidates[]`** — one row per strike on the chosen side: `strike`,
  `option_type`, `symbol`, `label` (moneyness, e.g. `ATM`, `OTM2`), quote
  fields (`bid`, `ask`, `mid_now`, `spread_pct`, `entry_cost`), `iv_now_pct`,
  `iv_target_pct`, `greeks_now` (delta/gamma/theta/vega), `projected_premium`,
  `exit_value`, `pnl_per_lot`, `pnl_total`, `return_pct`, `effective_delta`,
  `theta_cost_per_lot`, `adverse_premium`, `adverse_pnl_per_lot`,
  `reward_risk`, `scenario_pnl` (P&L at 50/75/100% of the move),
  `robust_pnl_per_lot`, `attribution` (delta/gamma/theta/vega/spread/residual/total),
  `oi`, `volume`, `excluded`, `exclude_reason`, `recommended`,
  `recommend_reason`, `score`.
- **`ladder[]`** — `reference_level`, `premium`, `pnl_per_lot` for the
  recommended strike across a bracket spanning 1.5x the target distance either
  side of the current reference, for plotting a premium-vs-price chart.
- **`warnings[]`** — plain-text strings for every assumption the engine made:
  expiry defaulted, forward fell back to spot, basis is modelled rather than
  exact, smile is degenerate or fits poorly, vol-beta fell back to a preset,
  hold runs past expiry, close to zero DTE, or the hold consumes most of the
  remaining time to expiry.

## Known limitations

- **`vol_beta: "auto"` does not actually estimate from session history yet.**
  `services.option_target_service._vol_beta_samples` is a stub that always
  returns an empty list, so `estimate_vol_beta` always falls back to the
  Normal preset (0.8) and reports the fallback in `warnings` and in
  `scenario.vol_beta.source`. Since vol-beta is the single largest error term
  in the model (see the backtest table above) and the realised beta measured
  on the backtested BANKNIFTY day was close to 1.4 — well above the 0.8
  fallback — wiring actual session history (ATM IV and index level sampled at
  1-minute intervals over a trailing window, regressed to a slope) into
  `_vol_beta_samples` is the highest-value follow-up to this feature.
- **Cross-expiry comparison and order placement are not implemented.** Both
  were discussed during design; neither exists in the current API or UI.
- **The `trading` day-count session provider falls back to a static table
  more often than it should**, because the seeded special-session data in
  `database/market_calendar_db.py` is currently corrupt: every seeded
  special-session row spans two calendar dates (decoding to an 895-minute
  window) where the seed comment intends a same-day 415-minute evening
  session. `services/option_target_sessions.py` detects this — a window is
  only trusted when both its start and end fall on the requested calendar
  date — and falls back to the static per-exchange table
  (`services/option_target/daycount.py:SESSIONS`) rather than propagate a bad
  window. This is a pre-existing platform defect in the seed data, not
  something this feature introduced, and it only affects `day_count:
  "trading"` on the specific dates that carry a special session (evening-only
  MCX sessions on some equity holidays, for example) — `calendar` day count is
  unaffected.

## The replay harness

`test/test_option_target_replay.py` turns the backtest above into a regression
gate. It replays the same completed BANKNIFTY trade from a captured fixture:
the forward and smile are reconstructed once, at entry, and every strike is
projected to the exit spot and compared against what it actually traded at on
exit. Three assertions: the full model beats the delta-only baseline by at
least 3x, the full model's MAE stays under a 2.5% ceiling (recorded 1.26%),
and the delta-only baseline stays above 4% (recorded 6.84%) — confirming the
fixture and harness still reproduce the original measurement rather than
having quietly drifted.

The module is skipped automatically when the fixture is absent. Recapture it
with:

```bash
uv run python scripts/capture_option_target_fixture.py \
  --underlying BANKNIFTY --expiry 25AUG26 --date 2026-08-04 \
  --low 56800 --high 58800 --step 100 \
  --out test/fixtures/option_target/banknifty_2026-08-04.json
```

This records 1-minute history for the underlying's spot/index and for every
CE/PE at each strike in the range, via `POST /api/v1/history`, using the first
active broker session's API key. It requires the OpenAlgo server to be running
locally and at least one non-revoked broker session — 1-minute history is only
available for the current or recent trading days, so the `--date` argument
should stay close to today when recapturing.
