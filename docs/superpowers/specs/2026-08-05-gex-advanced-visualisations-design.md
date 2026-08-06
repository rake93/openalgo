# GEX Levels: Gamma Profile, Bands, Heatmap and Delta Exposure

**Date:** 2026-08-05
**Status:** **Phases 1-4 built. Phase 5 not started** — see
[§10 Delivery order](#10-delivery-order) for the split and
[HANDOFF-gex-advanced-visualisations.md](../HANDOFF-gex-advanced-visualisations.md)
to pick it up. Phases 1-2 are verified live; **phases 3 and 4 are green and
audited but have NOT been seen on a live chart** — see the handoff §2.
**Builds on:** [2026-08-04-gex-levels-chart-study-design.md](2026-08-04-gex-levels-chart-study-design.md)
**Reader's guide to the existing study:** [../../gex-levels-reading.md](../../gex-levels-reading.md)

## 1. Problem

The GEX Levels study draws three levels and a readout card from a **single live
snapshot**. `GexLevelsManager` polls on a client-side timer, keeps one snapshot in
memory, and discards the previous one on every refresh. Nothing on the server
knows a snapshot ever happened.

That makes a whole class of question unanswerable:

- When did the Call Wall move, and did price respect it before it moved?
- Is the put wall building or decaying?
- Did the gamma structure migrate ahead of the breakout, or after it?

It also means every open tab fetches the chain independently. This deployment
shares one broker session across up to five devices, so three tabs on the study
is three identical chain fetches a minute.

Four features were requested, modelled on deepcharts' DeepGamma: **Gamma
Profile**, **Gamma Bands**, **GEX Heatmap**, and **Delta Exposure**.

## 2. What each feature actually needs

| Feature | Needs | Why |
| --- | --- | --- |
| **Gamma Profile** | Almost nothing | **Already built.** `gex-levels-primitive.ts:drawBars` already renders the two-sided histogram: bars diverging from a zero axis line, positive right in the call colour, negative left in the put colour, peak-scaled, with a configurable column width. Only the metric toggle is missing. |
| **Delta Exposure (DEX)** | One pure module | Same pipeline, delta instead of gamma, reusing IVs already inverted. No extra broker call. |
| **Gamma Bands** | Snapshot history | The three levels plotted through time. Cannot be drawn from one snapshot at any price. |
| **GEX Heatmap** | Snapshot history | The whole per-strike profile as a time x strike grid. Same. |

Two of the four are cheap; two require infrastructure that does not exist.

## 3. Decisions taken

| Question | Decision | Rejected |
| --- | --- | --- |
| Who records? | **Backend recorder, persisted multi-day** | Client-side ring buffer (gappy, per-device, resets on reload) |
| What is covered? | **Configured watchlist** | Auto-follow (unbounded growth); watchlist + auto-add (needs cap and eviction) |
| Cadence and retention | **1 minute, 30 days** | 30s/14d (2x cost for detail a 3-minute chart cannot show); 5m/90d (forces two fetch paths) |
| Storage shape | **Record computed exposures** | Record raw and compute on read: a heatmap load would re-invert IVs ~17,600 times |
| Chart layering | **Mode switch** | Four independent toggles (reaches an illegible state in two clicks); heatmap sub-pane (loses price alignment) |

### 3.1 The recorder reduces broker load, it does not add to it

Because the chart reads the newest recorded row instead of calling the broker, N
tabs cost one poll rather than N. The recorder's cadence becomes the study's live
refresh rate: **1 minute**, against the current default of 60s in code and the
30s some users have set. Open interest does not change tick-by-tick, so a faster
poll largely re-fetches identical data.

### 3.2 Disk

Sixteen float columns plus the raw inputs is **~6 KB per snapshot**. At 375
snapshots per session and two series: **~4.5 MB/day, ~100 MB per rolling month.**

(An earlier estimate of 78 MB/month predated the raw-input columns. This figure
supersedes it.)

## 4. Architecture

The governing rule is the one `services/gex_levels/` already follows:
**everything that computes stays pure and offline; only two components touch IO.**
That is why the module has 125 tests that need no broker, and the design spends
none of it.

### New, pure

**`services/gex_levels/delta_exposure.py`** — per-strike DEX. Reuses `ChainRow`,
`ResolvedIVs`, and the existing `DEALER_CALL_SIGN` / `DEALER_PUT_SIGN` from
`exposure.py` rather than restating the dealer-position convention. A separate
file because `exposure.py` is already 316 lines and gamma is its one job.

### New, IO

**`database/gex_history_db.py`** — the seventh isolated database, `gex.db`,
created through `engine_factory.create_db_engine()` (mandatory `NullPool`).
Schema, init, write, range query, retention prune.

**`services/gex_recorder_service.py`** — module-level singleton on
`utils/scheduler.ResilientBackgroundScheduler`, following
`historify_scheduler_service` in structure but with a **memory jobstore**: the
schedule is fixed rather than user-defined, so nothing needs persisting. That
avoids both the write-lock hazard the resilient scheduler exists for and the
APScheduler jobstore import-path trap (a persisted job stores its module path, so
renaming the job's module errors on startup).

**`services/gex_history_service.py`** — the read side, deliberately separate from
the recorder so a query path can never trigger a fetch.

### Changed

**`services/gex_levels_service.py`** — extract the compute core,
`build_snapshot(chain_response, ...)`, from the IO wrapper so the recorder and the
live path run **one** pipeline. The recorder reimplementing the maths is the
failure this design most wants to prevent: `/gex` drifted from the study exactly
that way and shipped three defects (see §11 of the prior design).

**`blueprints/gex.py`** — history and watchlist routes alongside the existing
`/gex/api/gex-levels`.

**Frontend** — `gex-levels.ts` gains a history fetch; new renderers;
`GexDashboard.tsx` extended.

### Deliberately untouched

`exposure.py`, `levels.py`, `quality.py`, `sentiment.py`, `blackscholes.py`,
`expiry.py`. Their 125 tests staying green is the regression guard for the whole
change.

### Two boundary decisions

**The watchlist lives in `gex.db`, not `settings_db`.** `settings_db.Settings` is
a typed-column table (analyze mode, SMTP, security), not a general key-value
store. The recorder owning its configuration next to the data it produces keeps
the feature in one store that can be dropped and rebuilt.

**The live path keeps a direct-fetch fallback.** Reading the newest stored row is
the fast path, but a series not on the watchlist, or a recorder that is down, must
still render — falling back to a direct service call exactly as today. Unifying
the fetch must not make the study fail closed on instruments nobody chose to
record.

## 5. Data model

### `gex_series` — the watchlist

`id`, `underlying`, `exchange`, `expiry_rule`, `enabled`, `created_at`,
`updated_at`. Unique on `(underlying, exchange, expiry_rule)`.

`expiry_rule` is either `nearest` or a pinned `DDMMMYY`. `nearest` resolves
through `services/expiry_service.get_expiry_dates(underlying, exchange,
"options", api_key)` and takes the first entry, which is already filtered to
live expiries and sorted ascending — the same resolution the Tools pages use.
Note it returns `DD-MMM-YY` and the chain service wants `DDMMMYY`, so the
recorder normalises exactly as `OITracker.tsx:formatExpiry` does.

**The watchlist ships empty and the recorder stays idle until a series is
added.** An upgrade must not silently start making broker calls on a schedule
nobody asked for.

**The roll is a first-class concern.** On `nearest`, the resolved contract rolls
weekly, so 30 days of "NIFTY nearest weekly" is a sequence of four or five
different chains. Walls jump at each roll because the book changed, not because
the market moved. Therefore every snapshot stores its **resolved** `expiry_date`,
and readers either filter to one contract or mark the roll boundary. Drawing
across it unmarked would be the same class of error as labelling a synthetic
forward "Futures".

### `gex_snapshot` — one row per series per minute

`id`, `series_id`, `ts` (epoch seconds — directly usable by the chart library and
immune to timezone drift; India has no DST), resolved `expiry_date`,
`spot_price`, `forward_price`, `atm_strike`, `dte_days`, `interest_rate`.

Per-weighting results as suffixed columns: `call_wall_oi` / `call_wall_vol`,
`put_wall_*`, `zero_gamma_*`, `net_gex_*`, `regime_*`, `sentiment_*` (JSON), plus
`quality_verdict` and `quality_notes` (JSON) and `strikes_used`.

Unique and indexed on `(series_id, ts)` — that index is the range scan both Bands
and Heatmap live on.

**Suffixed columns rather than a `weighting` discriminator row**, because
`WeightBy` is a closed set of exactly two values (`"oi" | "volume"`). It halves
the strike table (47 rows per snapshot, not 94) and lets the Bands query be a
plain select with no pivot. The cost is explicit: a third weighting later is a
migration.

### `gex_snapshot_strike`

Primary key `(snapshot_id, strike)`. Then GEX and DEX for both weightings —
`call_gex_oi`, `put_gex_oi`, `net_gex_oi`, `call_gex_vol`, `put_gex_vol`,
`net_gex_vol`, `call_dex_oi`, `put_dex_oi`, `net_dex_oi`, `call_dex_vol`,
`put_dex_vol`, `net_dex_vol` — **plus the raw `call_oi`, `put_oi`, `call_volume`,
`put_volume`**.

Keeping raw inputs is the one place this design argues against normalisation, and
the argument is recent history: a lot-size units bug in this very pipeline
survived 99 green tests and was caught only by a live call. With raw inputs, a
maths error means history can be **repaired**. Without them it must be
**discarded**. That is worth roughly 25% more disk.

### Retention

A daily prune deletes snapshots older than the retention window. **It deletes
strike rows explicitly rather than relying on a cascade** — SQLite does not
enforce foreign keys unless `PRAGMA foreign_keys=ON` is set per connection, and
with `NullPool` handing out a fresh connection per operation that pragma cannot be
assumed armed.

### Both weightings cost one IV solve

`resolve_ivs` does not take `weight_by` — it inverts at the real forward and is
weighting-independent. So each tick solves IVs **once** and runs only
`compute_exposures` and `scan_zero_gamma` twice. The expensive part is paid once.

## 6. API

### `POST /gex/api/gex-history`

Body: `underlying`, `exchange`, `expiry_date`, `weight_by`, `from_ts`, `to_ts`,
`fields`.

**`fields: "levels"`** backs Gamma Bands. Snapshot rows only; a month is a few
thousand small objects.

**`fields: "grid"`** backs the Heatmap. Column-oriented — one `strikes[]` axis,
then `columns: [{ts, values[]}]` — so a timestamp costs 47 numbers rather than 47
objects.

**The grid endpoint must be capped.** One session is 375 columns x 47 values,
about 150 KB of JSON: fine. Thirty days is ~8,250 columns, about 3.5 MB: not
fine. The server downsamples above a fixed column budget **and says so**:

| Requested columns | Returned `resolution` | Bucketing |
| --- | --- | --- |
| <= 1000 | `1m` | none, raw snapshots |
| 1001 - 5000 | `5m` | every 5th snapshot |
| > 5000 | `15m` | every 15th snapshot |

`MAX_GRID_COLUMNS = 1000` is roughly 2.7 sessions, so a normal intraday view is
never downsampled. Bucketing **selects a representative snapshot rather than
averaging** — averaging across a wall that jumped strike would invent a
concentration at neither strike.

The response always carries `resolution` and `downsampled: bool`. A heatmap that
silently thinned itself would look like a market that went quiet.

### `/gex/api/gex-series`

List, add, remove watchlist entries.

### No new live endpoint

The existing `/gex/api/gex-levels` gains a recorded fast path: if the series is on
the watchlist and the newest snapshot is **less than 120 seconds old** (two
cadence intervals, so a single missed tick does not force a live fetch), return
it; otherwise fetch live. The response carries `source: "recorded" |
"live"` and `as_of`. The study already renders a `stale` flag and an aged-snapshot
convention, so this reuses an existing affordance rather than inventing one.

## 7. Frontend

Four features, four controls — not four switches:

| Control | Values | Notes |
| --- | --- | --- |
| **View** | Profile / Heatmap / Off | Mutually exclusive: they are the same data, now versus through time |
| **Bands** | on / off | Independent overlay, legible over either view |
| **Metric** | Gamma / Delta | Re-renders whichever view is active |
| **Weight by** | OI / Volume | Existing control, unchanged |

Making Delta a **metric toggle** rather than a fifth study means DEX arrives in
both the Profile and the Heatmap for the price of one control, and composes with
the weighting toggle users already understand.

**Gamma Profile** is a two-sided signed histogram anchored on a zero line at the
chosen chart edge, replacing the current thin strike-bar column, with the walls
and Zero-Gamma marked on it. It reuses the existing `side` and `columnWidth`
config.

**GEX Heatmap** is a background layer in the price pane, time on x, strike on y,
signed GEX as colour. It must share the price pane: its y-axis *is* the strike
ladder, and its whole value is that a band of colour lines up with the candles
that did or did not break it. In a separate pane a reader would eyeball two
y-axes against each other — doing by hand the comparison the picture exists to
make.

**Gamma Bands** are step-line series over price: Call Wall, Put Wall, Zero-Gamma.

**Zero-Gamma stays in forward space.** The existing known limitation (documented
in `gex-levels-reading.md` §8) applies to the band too: the level is a forward
price plotted on a spot axis, offset by the basis. This design does not change
that, and the band inherits it. Fixing it is out of scope here.

## 8. Failure modes

**A gap must look like a gap.** When a tick fails, that minute has no snapshot:
the Heatmap leaves it blank and the Bands break the line. Interpolating would draw
flat gamma where there was *no reading* — the same error `quality.py` and
`direction.ts` already forbid, that a missing input must never render as a zero.

**Do not trust the market calendar blindly.** The recorder runs only during
session hours, but `get_timings` has known-suspect seeded data (MCX evening
sessions with ~895-minute windows spanning two dates). The recorder validates the
window it receives — plausible length, single date — and falls back to a plain IST
time guard rather than either running 24/7 or silently recording nothing. Verify
the current state of that data during implementation.

**Rate limiting is live, not hypothetical.** A single manual call during design
hit `Rate limit hit (805)`. Three mitigations: stagger each series' job by a
per-series offset so they do not all fire on the minute; `max_instances: 1` so a
slow tick cannot overlap itself; `coalesce: True` so a backlog collapses to one
run.

**A dead scheduler thread must not be silent.** Job exceptions are caught and
logged with `logger.exception`; the schedule survives. This is what
`ResilientBackgroundScheduler` exists for.

**Quality travels with history.** The per-snapshot `good` / `degraded` /
`unusable` verdict is stored, so the Heatmap can dim or hatch columns that were
degraded when recorded.

**Prune failure is silent disk growth**, so the prune logs rows deleted and rows
remaining.

**FD hygiene.** Scheduler is a module-level singleton; engine via
`engine_factory` with `NullPool`; sessions through the teardown or a `with`
block. This change touches DB, threads and a scheduler, so the **`fd-audit` skill
runs before it is called done**, per CLAUDE.md.

## 9. Testing

The existing 125 pure tests staying green is the regression guard, and they will,
because no pure module changes.

New tests follow the same offline pattern, with the chain stubbed as
`test_gex_service.py` does with `_patched`:

- **`delta_exposure`** — sign convention, the put-delta-is-negative trap,
  non-finite guards, weighting selection. Mirrors `test_gex_levels_exposure.py`.
- **`gex_history_db`** — round-trip; `(series_id, ts)` uniqueness; prune actually
  deleting strike children given the cascade cannot be assumed; range-query
  boundary inclusivity.
- **`gex_recorder_service`** — one stubbed fetch produces one snapshot plus N
  strike rows populated for *both* weightings; a failed fetch writes nothing and
  does not raise; an expiry roll writes the new resolved expiry.
- **`gex_history_service`** — a range over the column budget returns the coarser
  `resolution` *and* declares it; gaps survive as gaps.
- **Frontend vitest** — mode exclusivity (Profile and Heatmap never drawn
  together); the metric toggle swapping data source; gap rendering.

**One test matters more than the rest:** that the recorder and the live path
produce identical output for the same chain. That is the drift this design exists
to prevent.

### The one piece of maths that is not a mechanical port

**Corrected 2026-08-05 during planning.** This section originally said DEX should
reuse `DEALER_CALL_SIGN` / `DEALER_PUT_SIGN`. Working the algebra shows that
degenerates, so the rule below replaces it.

Gamma is positive for both legs, so GEX's sign comes entirely from the
dealer-position constants. Delta already carries its own sign — measured against
the live chain, `black76.delta` returns `+0.551` for a call and `-0.448` for the
matching put. Mirroring the GEX formula therefore gives

```
+1 * delta_call * w_call   ->  positive
-1 * delta_put  * w_put    ->  negative x negative -> ALSO positive
```

so every strike contributes positively and the total is **always positive**,
carrying no directional information at all. That is why no published DEX is
defined that way: the dealer-long-calls/short-puts approximation exists to make
*gamma's* sign meaningful and does not transfer.

DEX is therefore defined as the net delta of the open-interest book, with each
leg's natural delta sign and **no dealer flip**:

```
DEX_k = (delta_call_k * w_call_k + delta_put_k * w_put_k) * F
```

Positive means calls dominate at that strike and the book is net long delta;
negative means puts dominate. Dealers are the counterparty, so dealer delta is
the negation — which the module docstring must state, because "delta exposure"
alone does not say whose.

The implementation must (a) use the rule above rather than the exposure module's
dealer constants, (b) pin the sign with a **worked numeric example** in the test
rather than a property assertion, and (c) state in the docstring whose delta is
being reported. Getting this wrong produces a plausible-looking chart that is
directionally inverted — the exact failure mode that made Regime deliberately say
Suppressive/Amplifying instead of bullish/bearish.

## 10. Delivery order

Phased so value lands before the infrastructure is finished.

| # | Step | Status |
| --- | --- | --- |
| 1 | **Gamma Profile** — already built; folded into step 2 | **DONE** 2026-08-05 |
| 2 | **Delta Exposure** — `delta_exposure.py`, the live path, the Metric toggle | **DONE** 2026-08-05 |
| 3 | **Storage and recorder** — `gex_history_db`, `gex_recorder_service`, `build_snapshot` extraction, watchlist routes, the recorded fast path | **DONE** 2026-08-05; verified live 2026-08-06 (after-close pass still open) |
| 4 | **Gamma Bands** — the first consumer of history; smallest query shape | **DONE** 2026-08-06; verified on a real chart 2026-08-06 |
| 5 | **GEX Heatmap** — the grid endpoint, downsampling, the background renderer | **DONE** 2026-08-06; seen on a live chart the same day |

Steps 1 and 2 shipped two of the four features with no recorder at all, exactly
as the phasing intended. **Everything from step 3 onward still needs the
recorder**, and nothing in steps 1-2 pre-empts any of its design decisions — the
`weighted_legs` seam added during step 2 is in fact what step 3's
`build_snapshot` extraction will build on.

Two features NOT in the original four were added on top, both requested after
the delta work landed and both shipped:

- **Hover readout** — hovering a strike bar shows that strike with both metrics
  at once. Plan: [`2026-08-05-gex-hover-and-draggable-card.md`](../plans/2026-08-05-gex-hover-and-draggable-card.md).
- **Draggable readout card** — header-drag, clamped, double-click to reset,
  persisted with the layout.

## 10b. Where phase 3 read this spec differently

Three detail-level readings taken during implementation. None touches the six
decisions in §3; each is recorded here so a later reader does not mistake it for
undocumented drift.

**Quality is stored per weighting, and stored whole.** §5 lists the suffixed
columns and then says "plus `quality_verdict` and `quality_notes` (JSON) and
`strikes_used`", which reads as unsuffixed. But `assess_quality` takes the
*priced* exposures, so a chain can be good on open interest and degraded on
volume — and §8 wants the Heatmap to dim the columns that were degraded, which
on a volume-weighted heatmap means the volume verdict. So there are
`quality_verdict_oi` / `_vol` (a queryable string, so a reader can filter
without parsing JSON) plus `quality_oi` / `quality_vol` holding the **whole**
quality payload rather than verdict-and-notes. That last part is not cosmetic:
`may_draw` is a `@property`, not a dataclass field, and an absent key reads as
`undefined` -> falsy in TypeScript, which would render every good recorded
snapshot as "do not draw". `strikes_used` stays single — it is a strike count
and genuinely weighting-independent.

**The watchlist is capped at ten series** (`MAX_SERIES` in `blueprints/gex.py`).
Not in this spec. §3 rejected auto-follow *because it grows without bound*, and a
manually curated list with no ceiling reaches the same place, just more slowly.
Ten series is 940 chain symbols a minute against a broker that rate-limited a
single manual call during design (§8). The cap counts disabled rows, so
re-enabling one cannot push past it.

**The §8 session guard reuses `services/option_target_sessions.py`.** That module
already validates the calendar window exactly as §8 asks — it rejects the seeded
MCX special sessions, which decode to 895-minute windows spanning two calendar
dates, and falls back to a static per-exchange table. `_market_is_open` moved out
of `option_target_service` into it as `session_is_open`, gaining a `default`
argument because the two callers want opposite behaviour when the lookup
*raises*: a price projection must never be blocked by a calendar error
(`default=True`), while a recorder that fails open polls the broker around the
clock (`default=False`). A merely suspect window reaches neither default — the
provider has already fallen back.

### Two figures this spec got wrong, measured during phase 3

**Disk is roughly double.** §3.2 estimates ~6 KB per snapshot and ~100 MB per
rolling month for two series. Measured over 2,100 real snapshots at 48 strikes:
**9,435 bytes per snapshot**, so ~7 MB/day and **~210 MB/month** for two series.
Still small, and `GEX_RECORDER_RETENTION_DAYS` is the knob, but the §3.2 figure
should not be quoted as-is.

**`get_snapshots_in_range` is unbounded by row count.** §6 caps the *grid*
endpoint at `MAX_GRID_COLUMNS` and calls the Bands query "a few thousand small
objects", which it is for a month — but nothing in the query itself enforces
that. Phase 4 should decide whether Bands needs its own ceiling rather than
inheriting one only the grid has.

## 10a. Follow-ups found during implementation

Recorded rather than actioned, because each is pre-existing rather than
introduced by this work.

**`scan_zero_gamma` re-resolves what the caller already has.** Measured on a
47-strike chain: `resolve_ivs` 0.574 ms and `weighted_legs` 0.201 ms run once in
`gex_levels_service`, then `scan_zero_gamma` runs both again internally with
identical arguments — same `rows`, forward, `t_years`, `r`, `atm_strike` and
weighting. That duplicated 0.775 ms per request is larger than the entire delta
pricing pass this work added (0.300 ms). `scan_zero_gamma` uses `rows` for
nothing beyond its `if not rows` guard, so accepting the already-built `legs`
would remove it. It changes `levels.py`'s signature, which is why it was not
folded into the delta work.

**The `/gex` Tools page still calls `compute_exposures`.** That is correct today
— it needs gamma only — but it means the two surfaces now reach the pricers by
different routes. If `/gex` ever gains the delta metric, it should move to the
`resolve_ivs` -> `weighted_legs` -> two-pricer shape rather than growing a second
single-shot path.

## 11. Out of scope

- Converting Zero-Gamma from forward space to spot space on cash-index charts
  (existing limitation, inherited, tracked in `gex-levels-reading.md` §8).
- Charm, vanna, or any third-order Greek surface.
- Backfilling history from before the recorder is switched on. There is no source
  for it: the option chain API returns only current OI and volume.
- Two-tier storage (full grid recent, levels-only older). At ~100 MB/month it buys
  nothing yet, and the schema does not prevent adding it later as a roll-up job
  over rows that already exist.
