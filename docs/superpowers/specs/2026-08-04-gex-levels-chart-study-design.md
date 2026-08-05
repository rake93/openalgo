# GEX Levels — Chart Study Design

**Date:** 2026-08-04
**Status:** Approved for planning
**Surface:** Charting workspace (`/charts`) — Studies dock
**Branch:** `feat/indicator-engine`

## 1. Problem

An options trader watching a NIFTY or BANKNIFTY chart wants to know where
dealer gamma is concentrated, because that is where price is likely to pin,
stall or accelerate. Today OpenAlgo answers this only in the `/tools` surface —
the GEX Dashboard (`/gex`) and Gamma Density (`/gammadensity`) — as standalone
Plotly pages, disconnected from the chart the decision is actually made on.

The trader has to read a level off one page and eyeball it onto another.

**GEX Levels** puts three numbers on the price axis of the chart itself:

- **Call Wall** — the strike with the largest positive dealer gamma; where
  rallies tend to stall.
- **Put Wall** — the strike with the largest negative dealer gamma; where
  declines tend to find support.
- **Zero-Gamma** — the price at which aggregate dealer gamma changes sign, i.e.
  the boundary between the volatility-suppressing and volatility-amplifying
  regimes.

Plus a per-strike gamma distribution and a numeric dashboard.

### Reference

Three TradingView screenshots supplied by the user (US100 and US500, 0DTE
volume GEX) establish the visual target: a fixed dashboard table top-right, a
column of signed horizontal bars anchored in the chart's right margin, and
three dashed levels extended across the plot with inline labels.

External products surveyed for the level vocabulary and methodology:
DeepCharts DeepGamma, FlashAlpha, GexStream, OptionsFlow, plus the Indian
publishers Vtrender, QuintalMind, optionsflow.in and StockMojo — all of whom
publish GEX for NIFTY / BANKNIFTY / SENSEX in ₹Cr per 1% move.

## 2. Scope

**In scope**

- A new Study in the `/charts` Studies dock, alongside Volume Profile, Market
  Profile and Order Flow.
- Any instrument whose underlying resolves to an option chain: NFO indices and
  stocks, BFO, MCX. Coverage is gated by a data-quality verdict, not by an
  exchange allowlist.
- Both open-interest and volume weighting, OI by default.
- One expiry at a time, defaulting to the nearest.
- A shared, IO-free math module the `/tools` pages can adopt later.

**Out of scope for this build**

- Changes to the `/gex` or `/gammadensity` pages. The shared module is written
  so they *can* adopt it; this build does not migrate them.
- Multi-expiry aggregation. Deferred by decision (§10), not oversight.
- Historical or replayable GEX. GEX is a live snapshot; OpenAlgo stores no
  option-chain history. This is the same honest limitation the footprint study
  states about the tape.
- CDS. This broker's master carries no CDS option expiries at all, so enabling
  it yields an empty chain. Nothing in the math is CDS-specific.

## 3. Why a Study and not an Indicator

The user's opening question was whether GEX belongs in the OpenScript indicator
engine or the Studies section. The engine answers it.

`openalgo-openscript/docs/openscript-phase3-request-security-design.md` scopes
`request.security` to **same-symbol only** — "cross-symbol is out of scope" —
and restricts the source subset to `open`, `high`, `low`, `close`, `volume`,
`hl2`, `hlc3`, `ohlc4`, `time` with a constant history offset. There is no
external-fetch facility and no path to one short of a new engine phase.

GEX is not a function of the chart's OHLCV. It is an external option-chain
snapshot for a *different* set of instruments than the one being charted.

| Option | Verdict |
| --- | --- |
| OpenScript indicator | **Rejected.** Requires building cross-instrument external data into the engine — an XL feature, unrelated to this deliverable. |
| Builtin in `openalgo-charts` | **Rejected.** GEX is India-specific options domain logic, not generic charting. Also forces a cross-repo `npm run build` on every change, since `frontend/node_modules/openalgo-charts` is a symlink to the sibling repo. |
| App-level Study | **Chosen.** Exactly the `ProfileManager` shape already proven by Volume/Market Profile: a settings object driving primitive lifecycle, fed by a Flask service, persisted with the layout. |

`IPrimitive`, `PrimitiveRenderContext` and `PriceLine` are exported from the
`openalgo-charts` package **root** (`src/index.ts:79`, `:85`), not from a lazy
tier. A primitive written in the app therefore needs no `tier-compat` cast —
unlike the profile primitives, which do.

## 4. Reuse audit

Reading the existing services decided the backend shape.

### `services/gamma_density_service.py` — the base

| Capability | Disposition |
| --- | --- |
| Single `get_option_chain` fetch, no per-strike broker calls | Reuse as-is |
| `_expiry_datetime()` — DDMMMYY plus per-exchange close (NFO/BFO 15:30, CDS 12:30, MCX 23:30) | **Extract to shared** |
| `_safe_iv()` — Black-76 inversion; rejects non-finite, `<= 0`, `> 5` | **Extract to shared** |
| `_safe_gamma()` — returns `0.0` on any numerical failure | **Extract to shared** |
| ATM IV, with median-of-valid then `_FALLBACK_IV = 0.15` fallback | **Extract to shared** |
| Forward via `option_greeks_service._resolve_forward_price` | Reuse as-is |
| Peak-strike tracking | **Not reusable** — it tracks unsigned `CE + PE` density. A wall is the *signed* extreme. |

Those four helpers are `_`-prefixed and importing them across service modules
would be a smell. They are lifted into the new shared module and
`gamma_density_service` imports them from there. That is a genuine extraction,
not a copy — there must be exactly one Black-76 IV inversion in the codebase.

### `services/gex_service.py` — not the base

Three properties make it unsuitable for a study on a refresh timer:

1. **Per-strike `calculate_greeks()` loop** — up to 90 service calls per
   request, each re-parsing the symbol and recomputing time-to-expiry.
2. **Prices off spot, not the forward.** It passes `spot_price` as Black-76's
   `F`. Measured basis on BANKNIFTY at 21 days is **+138.9 points**; gamma
   peaks at ATM-forward, so this displaces the entire gamma profile and
   therefore both walls.
3. **`strike_count=45`** → 91 strikes → 182 symbols, well past the broker
   multiquote bucket described in §5.1.

`iv_smile_service.py` shares the per-strike loop. `gamma_density_service.py` is
the only options service that calls `black76` directly after one fetch.

**Follow-up, not part of this build:** point 3 is worth verifying against a
live Fyers session, since it may mean the existing `/gex` page silently
receives zero OI for part of its chain. It is recorded here as an observation,
not a claim that the page is broken — the generic multiquote path may absorb it.

## 5. Backend design

### 5.1 The strike-count constraint

`oi_tracker_service.py:142` states it plainly: *"Sized to fit the fyers
multiquote OI bucket (<=100 symbols) so OI is populated."* Both OI Tracker and
Gamma Density therefore request `strike_count=23` — 47 strikes, 94 symbols.
`option_chain_service.py:479` reinforces it: the Fyers fast path engages only
at `strike_count <= 50`.

**GEX Levels uses `strike_count=23`.** Exceeding the bucket does not error — it
returns empty OI, which would silently zero the entire study. This is a hard
constraint, not a tuning preference.

Consequence to state in the UI: with 23 strikes each side, a "wall" at the edge
of the window may be a window artefact rather than a real concentration. The
quality gate (§5.5) detects and reports this.

### 5.2 Module layout

Mirrors `services/option_target/`, which established the pattern of pure math
separated from broker-touching orchestration.

```
services/gex_levels/
    __init__.py
    blackscholes.py   _safe_iv, _safe_gamma, atm_iv        (extracted, shared)
    expiry.py         _expiry_datetime                      (extracted, shared)
    exposure.py       per-strike signed GEX
    levels.py         call wall, put wall, zero-gamma scan
    quality.py        data-quality verdict
services/gex_levels_service.py                              (orchestration, IO)
```

Everything under `services/gex_levels/` is pure: plain inputs to plain outputs,
no network, no database, no clock beyond what is passed in. That is what makes
the level math unit-testable without a broker session.

### 5.3 Exposure math

For each strike `k`, with `w` being open interest or volume per the selected
weighting:

```
GEX_k = gamma_k(call) * w_k(call) * lot * F^2 * 0.01
      - gamma_k(put)  * w_k(put)  * lot * F^2 * 0.01
```

**Sign convention:** calls positive, puts negative. This is the standard
convention used by every product surveyed, and encodes the approximation that
dealers are long calls and short puts at the index level. No toggle is exposed;
if the convention is later disputed for Indian market structure, it is a single
constant in `exposure.py`.

**Units:** currency delta change per 1% move in the underlying — displayed in
₹ Cr, matching what the Indian publishers quote.

**On the `F^2 * 0.01` factor:** `F` is constant across strikes, so introducing
it does **not** move any wall or the zero-gamma level relative to today's
`/gex` output. It is a units correction, not a levels change. Stated explicitly
so nobody later reads a numeric difference as a regression.

**Pricing input is the forward, never spot** — the per-expiry synthetic future
from `_resolve_forward_price`, with spot as the documented fallback when the
ATM CE/PE quotes needed for put-call parity are missing.

**Lot size** is taken from the chain rows. Gamma Density deliberately omits it
because Γ×OI is a density; GEX is a notional and requires it.

### 5.4 Walls and the zero-gamma scan

**Call Wall** = strike with maximum `GEX_k`. **Put Wall** = strike with minimum
`GEX_k`. Both may legitimately be the same strike — one of the reference
screenshots shows Call Wall and Put Wall both at 29500 — so nothing may assume
they differ.

**Zero-Gamma is a profile scan, not a cumulative sum.** Summing per-strike GEX
and finding where the running total crosses zero is a different quantity and
cannot produce a price between strikes.

The algorithm, following the standard method:

1. Generate `N = 60` hypothetical forward levels spanning `±20%` around `F`.
2. At each level, **recompute every contract's gamma** with Black-76 — `t` and
   `sigma` held fixed, only `F` varies — and sum the signed exposure.
3. Detect the sign change and linearly interpolate between the bracketing
   levels.

This is why a reference screenshot reads `7532.43`, a price no strike sits on.
When the profile does not cross zero anywhere in the range, the result is
`null` and the UI reads **"No local cross"** — which two of the three reference
screenshots show, so it is a normal outcome and not an error path.

**Regime** is the sign of net GEX: positive → **Suppressive**, negative →
**Amplifying**.

> The reference screenshots label this BULLISH / BEARISH. That is a misreading
> of the metric and is deliberately not reproduced. Positive net gamma is not
> bullish, it is *stabilising* — dealers sell rallies and buy dips, so price
> pins. Negative gamma amplifies moves in **both** directions, so labelling it
> "BEARISH" would read as a short signal during a gamma-driven squeeze upward.

### 5.5 Quality gate

`direction.ts` established the governing rule for this codebase: *"A missing
input never reads as a zero — no open interest is not the same as open interest
that did not change."* The quality gate applies it here.

Reported per response:

| Check | Why it matters |
| --- | --- |
| Strikes with invertible IV, against strikes fetched | A chain of stale premiums yields gamma from the fallback IV, not the market |
| Total chain OI / volume on the selected weighting | Distinguishes a thin chain from a real one |
| Strikes present on **both** sides of the forward | Without both sides, a "wall" may be the window edge |
| Whether either wall sits at the window boundary | Same — flags a likely artefact |
| Age of the snapshot | Surfaces a stalled feed rather than showing frozen levels as live |

The verdict drives the dashboard's `Data status` row — the analogue of the
reference screenshots' "25 contracts".

**Instrument coverage** follows from the gate rather than an allowlist:

| Charted instrument | Outcome |
| --- | --- |
| `NIFTY` (NSE_INDEX), `NIFTY28AUG26FUT` (NFO) | Available — chain on NFO. Deep, cash-settled, writer-dominated. |
| `RELIANCE` (NSE or NFO future) | Available, quality-flagged. Monthly expiry only and **physically settled**, so OI unwinds faster into expiry than a cash-settled index. |
| `CRUDEOIL19AUG26FUT` (MCX) | Available. Options are written on a future with a different expiry; `services/pricing_underlying.py` already resolves this platform-wide. Crude is 60%+ of MCX option volume; other commodities are thin and the gate will say so. |
| `NIFTY28AUG2624000CE` | **Unavailable.** The price axis is premium, not underlying price. GEX levels are underlying prices and cannot be drawn on it. |
| Cash equity with no F&O; anything on CDS | Unavailable |

### 5.6 Endpoint

`POST /gex/api/gex-levels` on the existing `gex_bp` blueprint — same domain,
same session and API-key handling, same input validation shape (the existing
route's regex validation of `underlying`, `exchange` and the `DDMMMYY` expiry
is the template).

Response:

```jsonc
{
  "status": "success",
  "underlying": "NIFTY", "exchange": "NFO", "expiry_date": "11AUG26",
  "spot_price": 24610.5, "forward_price": 24632.1, "atm_strike": 24600,
  "lot_size": 75, "weight_by": "oi",
  "strikes": [{ "strike": 24600, "call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0 }],
  "call_wall": 24800, "put_wall": 24500,
  "zero_gamma": 24632.43,          // null when there is no crossing
  "total_call_gex": 0.0, "total_put_gex": 0.0, "net_gex": 0.0,
  "regime": "suppressive",         // "suppressive" | "amplifying"
  "quality": { "verdict": "good", "strikes_used": 47, "strikes_priced": 44,
               "both_sides": true, "wall_at_edge": false, "notes": [] }
}
```

**No non-finite floats, ever.** `float("-inf")` used as a sort sentinel
serialises as `-Infinity`, which `JSON.parse` rejects, silently destroying an
otherwise correct response. This exact bug already hit the Option Target
Calculator. Guarded by a test asserting `json.dumps(payload, allow_nan=False)`
succeeds.

## 6. Frontend design

```
frontend/src/api/gex.ts                          + getGEXLevels()
frontend/src/lib/charts/gex-levels.ts            GexLevelsManager
frontend/src/lib/charts/gex-levels-primitive.ts  implements IPrimitive
frontend/src/components/charts/workspace/GexDashboard.tsx   HTML overlay
frontend/src/components/charts/workspace/StudiesPanel.tsx   + a fourth section
frontend/src/lib/charts/workspace.ts             owns the manager, snapshot/restore
frontend/src/pages/charts/ChartWorkspace.tsx     wires panel props and the overlay
```

`GexLevelsManager` mirrors `ProfileManager` exactly: it owns the settings, the
primitive handles, the poll timer, and `snapshot()` / `restore()`. It is
constructed in the controller alongside `this.profiles`, re-attached on chart
rebuild, and disposed with it.

### 6.1 Settings

| Setting | Default | Rationale |
| --- | --- | --- |
| `enabled` | `false` | Consistent with every other study |
| `weightBy` | `'oi'` | OI is live in India — the exchange disseminates it in the tick feed, so the US rationale for volume-GEX (stale prior-night OI snapshot) does not apply. `'volume'` gives the today's-flow read the reference screenshots show. |
| `expiry` | nearest | Switchable. Front expiry dominates gamma. |
| `showBars` | `true` | Turning it off gives the clean levels-plus-dashboard view |
| `showCallWall` / `showPutWall` / `showZeroGamma` | `true` each | Independently switchable |
| `showDashboard` | `true` | |
| `refreshSeconds` | `60` | The cadence the reference products use |
| `side`, `columnWidth` | `'right'`, 120 px | Pixel-anchored, mirroring `VolumeProfilePrimitiveOptions` |

The zero-gamma scan range (`±20%`, 60 steps) is a module constant, not a knob.

### 6.2 Two rendering decisions

**The primitive contributes nothing to autoscale.** `profiles.ts` documents
this trap three separate times: a primitive whose `autoscaleInfo()` reports its
full extent drags the price scale out and squashes the candles. A 47-strike
window spans far more than the visible price range, so reporting it would
flatten the chart to a sliver.

Instead the bars **clip to the visible price range**, and a wall outside that
range is drawn as a small arrow at the top or bottom plot edge — visible rather
than silently absent.

**Column placement mirrors the existing collision fix.** The bar column is
pixel-anchored by `side` plus an offset, exactly as `VolumeProfile` is. Volume
Profile also anchors right at 150 px by default, so when both are enabled on
the same side the GEX column offsets by Volume Profile's width. This is the
pattern already implemented in `profiles.ts:volumeOptions()`, where the volume
profile relocates its own labels when Market Profile is switched on.

**Side effect worth stating:** GEX levels are price-axis only with no time
anchoring, so unlike the profiles they render correctly on movement-driven
chart types (Renko, P&F, Kagi). `ProfileManager.available` gates those off;
`GexLevelsManager` does not need to.

### 6.3 Dashboard

An HTML overlay component, not canvas — it themes, wraps and reads like the
rest of the workspace, and `ChartWorkspace.tsx` already positions
`InspectorPanel` and the toast this way. Docked top-right of the chart main
area, below the top bar. Rows: Call GEX, Put GEX, Net GEX, Regime, Call Wall,
Put Wall, Zero-Gamma, Expiry, Data status.

## 7. Failure modes

Every condition gets a stated behaviour. None may render as a silent zero.

| Condition | Behaviour |
| --- | --- |
| Underlying resolves to no option chain | Panel states unavailable, nothing drawn, **no polling started** |
| Charted symbol is itself an option | Unavailable — premium axis, per §5.5 |
| Broker or API error mid-session | **Retain the last good snapshot**, badge it stale with its age, back off and retry. Blanking levels a trader is watching is worse than showing them aged. |
| No zero crossing within ±20% | `zero_gamma: null` → "No local cross" |
| Expiry day, past settlement | Roll to the next expiry; unavailable if none |
| Thin or partly unpriced chain | Quality gate downgrades the verdict and names the reason |
| Symbol or expiry switched mid-flight | `AbortController` cancels the in-flight request. A late response must never paint the previous instrument's walls. |
| Chart destroyed during rebuild | Primitive disposal is wrapped in try/catch — the old series are already gone and a throw would abandon the rest of the rebind, exactly as documented for `IndicatorHost.attachChart` and `DrawingManager.detach` |

### Resource hygiene

One shared poll timer per manager, cleared in `dispose()` — the
`ProfileManager.rangeTimer` pattern. Polling is suspended when the study is
off, the tab is hidden, or the instrument has no chain. HTTP goes through the
shared `utils/httpx_client.get_httpx_client()` with an explicit timeout.

Production is a single Gunicorn worker that never restarts, so the **`fd-audit`
skill runs before this is called done**.

## 8. Testing

**Python — pure modules, no broker session**

- Per-strike signed exposure, including the sign convention and lot-size factor.
- Wall selection, including the case where both walls land on one strike.
- Zero-gamma scan: a synthetic chain with a known crossing, verified against the
  interpolated price; a chain with **no** crossing, verified to return `null`.
- Quality gate thresholds: unpriced strikes, one-sided windows, wall-at-edge.
- `json.dumps(payload, allow_nan=False)` succeeds on every response shape.

**Python — endpoint**

Route validation and error mapping, alongside the existing tests in `test/`.

**Frontend — vitest**

- `GexLevelsManager` settings round-trip through `snapshot()` / `restore()`.
- Poll lifecycle: starts on enable, stops on disable and on dispose, does not
  start for an instrument with no chain.
- Stale-response rejection: a response arriving after a symbol change is discarded.
- Level math helpers tested directly.

**Not covered, and stated rather than implied:** primitive rendering. jsdom
provides no canvas, so no chart can be bound. This is a proof of computation and
wiring, not of pixels — the same limitation `product-chain.test.ts` states about
itself. Visual verification is part of the live demonstration.

## 9. Documentation

`docs/chart-workspace-studies.md` gains a GEX Levels section, and its opening
"What each study needs" table gains a row. That file is the single source of
truth for the studies; nothing is restated elsewhere.

## 10. Deferred by decision

These are choices, not oversights. Do not "fix" them on sight.

- **Multi-expiry aggregation.** Gamma is dominated by the front expiry, and each
  additional expiry is a full chain fetch plus IV inversion — roughly
  multiplying refresh latency for a second-order change to the walls. The
  response shape does not preclude adding it.
- **Migrating `/gex` and `/gammadensity` onto the shared module.** See the
  backlog entry below — this is the one deferred item with a known correctness
  consequence, not merely a missing feature.
- **A dealer sign-convention toggle.** One constant in `exposure.py`; no UI
  until there is evidence Indian market structure warrants inverting it.
- **CDS.** Blocked on data, not code — this broker's master has no CDS option
  expiries.
- **Historical GEX.** No option-chain history is stored anywhere in OpenAlgo.

---

## 11. Migrating the `/tools` options pages onto the shared module

**Status:** `/gex` **done** 2026-08-05. Max Pain and OI Tracker **reviewed and
cleared** 2026-08-05 — see "Max Pain and OI Tracker" below. Gamma Density and
the central `option_chain_service` normalisation are still open — see "What is
left".

### What was wrong

`services/gex_service.py`, which backs the `/gex` Tools page, carried three
defects the chart study fixed by construction. They were found while building
it and confirmed against a live chain:

1. **Open interest was multiplied by the lot size.** The broker reports OI and
   volume in **units**, already lot-multiplied — verified across 188 live NIFTY
   values, every one an exact multiple of 65. The extra factor inflated every
   figure by the lot size. On NIFTY that is 65x. Independent corroboration:
   `GEXDashboard.tsx` *divides* OI by the lot size to display lots, so the page
   author already knew the units.
2. **Black-76 was priced off spot, not the per-expiry forward.** Gamma peaks at
   the ATM-*forward* strike. The measured BANKNIFTY 21-day basis is +138.9
   points, which is more than one strike — so the walls landed in the wrong
   place, not merely at the wrong scale.
3. **`calculate_greeks` was called once per strike** — up to 182 service calls
   for a 45-strike chain, each one re-parsing the option symbol and recomputing
   time to expiry.

### What was changed

- `StrikeExposure` gained `call_gamma` and `put_gamma`, populated in
  `price_exposures`. Additive, defaulted to `0.0` so the level, quality and
  sentiment tests that construct exposures by hand are untouched. `/gex`
  displays gamma per strike and needed it surfaced rather than discarded.
- `services/gex_service.py` was rewritten around the shared pipeline: one
  `get_option_chain` call, `expiry_datetime` + `calculate_time_to_expiry`,
  `_resolve_forward_price` (falling back to spot), then `compute_exposures`
  with `weight_by="oi"` and a direct `black76` pass. `lot_size` survives in the
  response as display data only.
- **Put GEX became signed.** `pe_gex` and `total_pe_gex` are now negative and
  `total_net_gex = total_ce_gex + total_pe_gex`, matching `price_exposures` and
  the chart study's dashboard. Per-strike `net_gex` and `total_net_gex` are
  numerically unchanged by the flip — `ce - pe` with a positive put equals
  `ce + pe` with a signed one — so only the put columns change sign. Every
  other response key is unchanged; `test/test_gex_service.py` pins the whole
  contract, since the page had no test at all before this.

### `strike_count` stays at 45 — measured, not assumed

The original write-up flagged 45 strikes (91 strikes, 182 symbols) as past the
100-symbol fyers multiquote OI bucket that `oi_tracker_service.py` documents,
and proposed dropping it to 23. **That was measured on this broker and is not a
problem here.** Of the 94 legs common to a 23-strike and a 45-strike request,
**zero** lose their open interest, and all 28 empty legs at 45 are genuinely
dead deep-OTM strikes in the outer ring. Narrowing the window would drop real
strikes from the page to fix a problem this broker does not have — a product
regression. It stays at 45, with a comment at the constant saying so.

**The fyers case remains unverified**: it could not be tested without a fyers
session. The chart study keeps its own `STRIKE_COUNT = 23` regardless, because
a study that refreshes on a timer has a different cost profile than a page load
and does not need the outer ring.

### Verified live

Same underlying and expiry, `weight_by='oi'`, against a live chain:

| | net GEX | window |
| --- | --- | --- |
| `/gex` Tools page | 8,170 Cr | 91 strikes |
| GEX Levels study | 8,415 Cr | 47 strikes |

Every strike in the 47-strike overlap matched to 0.0000%; the whole 245 Cr gap
is the 44 outer strikes `/gex` sees and the study does not (−246 Cr). That is
the migration being faithful, and it is now recorded in
`docs/chart-workspace-studies.md` in place of the old note saying the two
surfaces disagree.

### Max Pain and OI Tracker — reviewed 2026-08-05, no units defect

Both pages are served by `services/oi_tracker_service.py`, which reads the same
`get_option_chain` response as `/gex`. **Neither carried the lot-size defect.**

`calculate_max_pain` computes

```
pain(K) = Σ_{S<K} (K−S)·ce_oi(S) + Σ_{S>K} (S−K)·pe_oi(S)
```

and never multiplies by `lot_size`. Rupee distance times OI-in-units is already
rupees, and `total_pain_cr` divides by 1e7 correctly. `lot_size` rides along in
the response as a display badge only (`MaxPain.tsx:407`) and never enters the
arithmetic; the frontend plots `total_pain_cr` straight through.

The other two `/gex` defects do not apply either. Max Pain has no option
pricing, so there is no spot-versus-forward question — it is settlement
intrinsic value, which is a cash-spot quantity by definition. And it calls no
Greeks, so there is no per-strike `calculate_greeks` fan-out.

**Worth recording for whoever reviews the next page:** max pain would have
survived the lot-size bug intact anyway. Lot size is constant across strikes for
one underlying/expiry, so multiplying every OI by it scales the whole pain curve
uniformly and **argmin is unchanged** — the max pain *strike* would still have
been right, and only the y-axis magnitudes would have read 65x high. PCR is a
ratio and is scale-invariant for the same reason. A units bug is only
*observable* where the output is an absolute rupee figure, which is exactly why
it went unnoticed in `/gex` until the chart study computed the same number a
different way.

What was wrong on OI Tracker was **labelling, not arithmetic**: the chart
divides OI by the lot size and is labelled "(lots)", while the "Total CE OI" /
"Total PE OI" badges rendered the raw units figure under the same word "OI" —
the two disagreeing by the lot size on one screen. The badges now divide through
a shared `formatLots` helper and are labelled "(lots)", the y-axis title gained
"(lots)" to match `GEXDashboard.tsx`, and the hover readouts name the unit. One
unit for OI across the whole page.

**Still untested.** `get_oi_data` and `calculate_max_pain` have no test at all —
the same state `/gex` was in before this work. The units convention above is
currently pinned by nothing.

### What is left

- `services/gamma_density_service.py` is largely unaffected: it already
  resolves the forward (`_resolve_forward_price`, line 131) and deliberately
  omits the lot factor because Γ×OI is a density rather than a notional
  (line 199). Re-verified 2026-08-05. Its migration is a tidy-up, not a fix.
- A contract test over `get_oi_data` and `calculate_max_pain`, pinning the
  units convention and the pain formula.
- Still the better long-term shape: normalising units-versus-lots inside
  `option_chain_service` would fix every consumer at once and stop the next
  tool repeating it. Larger blast radius, so it needs its own regression pass
  across all four pages.
