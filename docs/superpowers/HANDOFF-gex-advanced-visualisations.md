# Handoff: GEX Levels advanced visualisations

**Session dates:** 2026-08-05 (phases 1-3), 2026-08-06 (phase 4)
**Branch:** `feat/indicator-engine` (long-lived; never merged to main — see the fork model note in memory)
**Read first:** [`specs/2026-08-05-gex-advanced-visualisations-design.md`](specs/2026-08-05-gex-advanced-visualisations-design.md)
**Plans executed:** [`plans/2026-08-05-gex-snapshot-recorder.md`](plans/2026-08-05-gex-snapshot-recorder.md), [`plans/2026-08-06-gex-gamma-bands.md`](plans/2026-08-06-gex-gamma-bands.md)

---

## 1. Where things stand

| Feature | Status |
| --- | --- |
| Gamma Profile | **Shipped**, verified live |
| Delta Exposure (DEX) | **Shipped**, verified live |
| Hover readout, draggable card | **Shipped**, confirmed by the user |
| **Snapshot recorder (phase 3)** | Built, green, fd-audited. **Live verification still owed** — see §2.1 |
| **Gamma Bands (phase 4)** | Built, green, fd-audited. **Seen and verified on a real chart** against seeded history — see §2.2 |
| **GEX Heatmap (phase 5)** | Not started. Unblocked — do this next |

**331 backend tests** across the GEX and option-target suites and **174 frontend
tests** across the seven GEX files, all green. No pure module under
`services/gex_levels/` changed in either phase, which is the regression guard for
the whole change.

---

## 2. Do this before anything else: phase 3's live verification is owed

**Phase 4 has been checked on a real chart** (2026-08-06, against seeded
history — §2.2). **Phase 3 has never touched a broker**: every test stubs the
chain, and the recorder has only ever been observed staying correctly silent out
of hours.

That gap matters more here than anywhere else in this codebase. Three defects on
this exact feature have reached the live chart with a full green suite, and a
fourth was found the same way on 2026-08-06: **the recorded fast path had never
once fired**, because the study sends the charted instrument's exchange
(`NSE_INDEX`) while the watchlist stores the options exchange (`NFO`). Every test
passed the same exchange on both sides. The study kept working by falling back to
a live fetch, so it looked switched off rather than broken. jsdom and stubs can
see none of this.

### 2.0 Start from a clean server, and check you are on one

A stale second instance is a live hazard here, not a hypothetical: on 2026-08-06
port 5000 was serving a build with **neither** phase's routes registered while
two `app.py` process pairs were running. The cheap check, from any shell:

```
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" -X POST \
  -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:5000/gex/api/gex-history
```

`400 application/json` means the route is registered. **`200 text/html` means it
is not** — the `app.py` 404 handler falls through to `serve_react_app()`, so a
missing route looks like a working page. Kill every `app.py` and start one.

A frontend change also needs `cd frontend && npm run build` (Flask serves `dist`,
not `src`); a backend change needs the restart. Keep `dist` out of commits.

### 2.1 Phase 3, the recorder

1. **Idle case first, and it is the one an upgrade hits.** `GET
   /gex/api/gex-series` returns `[]` on a fresh install, and the log shows the
   recorder registering only `gex_prune` — no recording job, no broker call.
2. Add a series. There is now a **button for this** — enable GEX levels, turn
   Gamma bands on, and use "Record this series" in the Studies panel. (The raw
   route is still `POST /gex/api/gex-series {"underlying":"NIFTY",
   "exchange":"NFO","expiry_rule":"nearest"}`.)
3. Within two minutes `db/gex.db` should hold a `gex_snapshot` row with **both**
   `call_wall_oi` and `call_wall_vol` populated, and ~47 `gex_snapshot_strike`
   children.
4. Open the study on `/charts` (`NIFTY28JUL26FUT` — index symbols are quote-only).
   The response must carry `source: "recorded"`, `as_of` must advance each
   minute, and the walls and bar column must look exactly as they did before
   phase 3.
5. **Open a second tab. The broker call count must not double.** That is the
   feature's main claim.
6. **Compare a recorded snapshot against a forced live fetch of the same chain**
   — disable the series, reload, and check the walls and net GEX agree. There is
   a unit test for this, but it cannot see a units bug in the live chain; the
   last one survived 99 green tests and was caught only by a live call.
7. After the close: no new rows, and no rate-limit warnings in `log/errors.jsonl`.
8. Out of hours with a series on the watchlist, the recorder must stay silent —
   `session_is_open(..., default=False)` is what should stop it.

### 2.2 Phase 4, the bands — DONE 2026-08-06, repeat it after any renderer change

The market does not need to be open for this one. Seed a session, aiming it at a
contract and a time range the chart is actually showing:

```
uv run python scripts/seed_gex_history.py --expiry 11AUG26 --hours 6 \
    --end "2026-08-05 15:25" --center 24560
```

**Both of those flags exist because of mistakes made the first time.** Without
`--end` the seeder anchors to `now`, so run after hours every point lands past
the chart's last bar and extrapolates off the right edge of the gapless axis —
drawn perfectly, entirely off-screen. Without `--center` the walls can sit
outside the visible price range, and since Bands contributes nothing to autoscale
(deliberately), they are simply clipped away. Both look identical to "the feature
does not work".

It writes three shapes on purpose, each a rule the renderer must get right.
Verified through the real read path: 346 gaps of 60s, **one of 120s**, **one of
660s**, 30 null zero-gammas and three distinct call-wall steps.

- the **one-minute hole must NOT break** the line (the 150s threshold exists so a
  single dropped tick does not shatter a session into one-point segments);
- the **ten-minute outage MUST break** it — visible as a gap in the shaded
  corridor, not just in the lines;
- the **null zero-gamma stretch must leave a hole**, not drop to the axis;
- walls must **step**, not slope — a diagonal implies the level passed through
  prices no strike ever occupied;
- turning Bands off must remove them and leave the live levels untouched.

All five confirmed on screen on 2026-08-06.

Then `--clear` to remove the fabricated rows. **Do not leave seeded rows in a
database that later records real ones** — the seeder refuses to seed over real
snapshots, but the recorder will happily write real ones alongside fakes, and
nothing on the chart would tell them apart afterwards.

---

## 3. What phase 3 actually built

**One pipeline, two callers.** `services/gex_levels_service.py` now splits into

```
fetch_snapshot_inputs   IO      chain fetch + forward resolution
  -> prepare_snapshot   pure    rows + IVs, weighting-independent
  -> build_snapshot     pure    one weighting's exposures, levels, payload
```

`get_gex_levels` runs all three for the requested weighting; the recorder runs
the first two once and `build_snapshot` twice, for `oi` and `volume`, off a
single chain fetch and a single IV solve (`resolve_ivs` takes no `weight_by`).
Two tests pin the drift this exists to prevent: one asserts the seam's output
equals the live payload, one asserts the recorded rows equal what
`get_gex_levels` returns for the same chain.

**`database/gex_history_db.py`** — `gex.db`, the seventh isolated database, via
`engine_factory` (NullPool). Three tables. Read its module docstring before
changing the schema; the three non-obvious decisions (suffixed weighting
columns, quality stored whole, raw OI/volume retained) each carry their reason.

**`services/gex_recorder_service.py`** — one singleton scheduler on a **memory**
jobstore, one job per enabled series at a staggered offset, plus a daily prune at
03:30 IST. A tick that cannot complete writes **nothing** and leaves a gap, on
every failure path.

**`blueprints/gex.py`** — `/gex/api/gex-series` (GET/POST/PATCH/DELETE), and the
recorded fast path on `/gex/api/gex-levels`: a snapshot under 120 seconds old is
served instead of a broker call, with `source` and `as_of` on the response.

**`services/option_target_sessions.session_is_open`** — the validated session
guard, lifted out of `option_target_service` so both callers share one validator.

**No frontend at all.** Phase 3 is API-only, by agreement — a watchlist control
lands with Bands, when there is something visible to switch on.

---

## 4. What phase 4 built

**`services/gex_history_service.py`** — the read side, separate from the recorder
so a query path can never trigger a fetch (a test pins that by exploding if the
chain service is reached, rather than mocking it into silence). Scoped to one
**resolved** contract: a `nearest` series rolls weekly, and splicing contracts
would draw a wall jump at every roll that is the book changing, not the market
moving. The honest cost is that a weekly series shows only as much history as the
current contract has existed — about five sessions, not the thirty retained.

**`POST /gex/api/gex-history`** with `fields: "levels"`. `fields: "grid"` is an
explicit 400 so phase 5 gets a seam, not a surprise. `MAX_HISTORY_POINTS =
20_000` bounds the **window** rather than truncating the result: returning the
first N points of a wider range would draw a band that simply stops, which reads
as the market going quiet.

**`gex-bands-geometry.ts`** (pure) and **`gex-bands-primitive.ts`** (canvas). One
path per segment, step lines, `timeToIndexFloat` for x, no `autoscaleInfo`.

**The manager** gained `showBands` (off by default) and a history loop that runs
at `refreshSeconds × 5` and waits for the live snapshot to resolve the contract.

**The panel** gained the Bands controls and the **recorder notice** — the piece
that makes phase 3 usable without curl.

### Two things phase 4 decided that the spec left open

**Bands is off by default.** It is the only control in that panel that can be
switched on and legitimately draw nothing, so the notice block explains the state
and offers to start recording rather than looking broken.

**The window ceiling now exists.** The previous handoff flagged that
`get_snapshots_in_range` had no row limit; `MAX_HISTORY_POINTS` is it.

---

## 4a. Pick up here: phase 5, the GEX Heatmap

The grid endpoint, downsampling, and a background layer in the price pane.

**What is already in place:** `fields` is plumbed end to end and `"grid"` returns
a 400 naming itself. Every response already carries `resolution` and
`downsampled`, always `"1m"` / `false`, so downsampling lands as a value change
rather than a shape change. `gex_snapshot_strike` holds the full per-strike
profile for both metrics and both weightings, plus the raw OI and volume.

**What the spec is explicit about (§6, §7) and a heatmap will get wrong:**

1. **The grid must be capped and must say so.** 30 days is ~8,250 columns and
   ~3.5 MB. `MAX_GRID_COLUMNS = 1000`, then `5m`, then `15m` — and the response
   carries `resolution` and `downsampled` because *a heatmap that silently
   thinned itself would look like a market that went quiet*.
2. **Bucketing selects a representative snapshot, never an average.** Averaging
   across a wall that jumped strike invents a concentration at neither strike.
3. **It must share the price pane.** Its y-axis *is* the strike ladder, and its
   whole value is that a band of colour lines up with the candles that did or did
   not break it. In a separate pane the reader eyeballs two y-axes against each
   other — doing by hand the comparison the picture exists to make.
4. **Column-oriented JSON**: one `strikes[]` axis then `columns: [{ts, values[]}]`,
   so a timestamp costs 47 numbers rather than 47 objects.
5. **A gap stays blank**, and a column recorded as `degraded` should be dimmed or
   hatched — `quality_verdict` is already on every point.

**Do not add `autoscaleInfo`.** The heatmap spans the whole strike window, so it
would flatten the candles harder than anything else in this study.

### Solve the per-frame recomputation before the heatmap, not after

`GexBandsPrimitive` re-splits its **entire** history on every `draw()`, and draw
fires on every pan, zoom and tick. Measured cost of one frame's segment work
(fd-audit, 2026-08-06):

| History | Per frame | Share of a 60fps budget |
| --- | --- | --- |
| 375 pts (default 6h lookback) | 0.43 ms | 3% |
| 1,080 pts | 0.56 ms | 3% |
| 4,320 pts (72h — the panel's maximum) | 1.56 ms | 9% |
| 20,000 pts (`MAX_HISTORY_POINTS`, raw API only) | 9.46 ms | **57%** |

**It is not a leak** — the allocations are collected, and descriptors and RSS
were both flat across 1,000 reads. It is latent for Bands too, because the
lookback select tops out at 72 hours, so a user cannot reach worse than 9%.

It will not stay latent for the Heatmap. That draws a 47-strike grid rather than
three lines, over the same column budget, so the same "recompute on every frame"
shape starts from a much larger constant. **Fix it in `GexBandsPrimitive` first
and build the heatmap on the fixed shape**, rather than shipping a second
primitive with the same defect.

The fix is contained: compute the segments in `setData` / `setOptions` and have
`draw()` read a prepared result. **Cache exactly one value per primitive
instance, never a map keyed by anything** — a keyed cache here would be the
unbounded module-level registry the `fd-audit` skill exists to catch, traded in
to fix a cost that was never a leak. Worth a test that a second `draw()` with
unchanged data does no splitting.

---

## 5. Known limitations, deliberately left

**Zero-Gamma is a forward-space level drawn on a spot-price axis.** ~35 points
above the equivalent spot level on a 6-day NIFTY expiry, growing with time to
expiry; exact on a futures chart. The Regime label is unaffected — it makes the
comparison in forward space correctly.

**Bar row height caps at 36px** (`MAX_BAR_ROW_HEIGHT_PX` in
`frontend/src/lib/charts/gex-levels-geometry.ts`). Both extremes were tried and
both were reported wrong by the user: a flat 14px left slivers adrift, and
scaling with the pane produced slabs when the y-axis was stretched. One number,
easy to tune.

**The metric applies to the bar column only.** Walls, Zero-Gamma, Regime and the
card's GEX rows stay gamma whichever metric is selected. Three on-screen labels
exist so this is never ambiguous — the card's `Bars` row, an amber caveat under
delta, and the bar-column caption. **Do not remove these** without replacing the
mitigation: green means "dealers long" under gamma and "dealers short" under
delta, because DEX is the open-interest book's delta and dealers hold the
negation.

---

## 6. Follow-ups worth doing, none blocking

**Bands re-split their history every frame.** The one finding from the 2026-08-06
`fd-audit`; the numbers, the reason it is latent, and the shape of the fix are in
§4a, because it should be fixed *before* the Heatmap rather than after.

**`scan_zero_gamma` re-resolves what the caller already has.** Measured on a
47-strike chain: `resolve_ivs` (0.574 ms) and `weighted_legs` (0.201 ms) run once
in the service, then `scan_zero_gamma` runs both again internally with identical
arguments. It uses `rows` for nothing beyond an emptiness guard, so accepting the
built `legs` would remove it. **The recorder pays this twice per tick**, once per
weighting. Not folded in because it changes `levels.py`'s signature, and
`levels.py`'s untouched tests are the regression guard.

**`/gex` still calls `compute_exposures`.** Correct today — it needs gamma only —
but the two surfaces reach the pricers by different routes. If `/gex` ever gains
the delta metric, move it to the `fetch_snapshot_inputs` -> `build_snapshot`
shape rather than growing a second single-shot path.

**The spec's disk estimate is ~2x low.** Measured 9,435 bytes per snapshot, so
~210 MB/month for two series rather than ~100 MB. Recorded in spec §10b.

**`services/gamma_density_service.py` migration** is a tidy-up for code sharing,
**not a fix** — nothing on that page is wrong.

**Normalising units inside `option_chain_service`** is still the better long-term
shape; larger blast radius, needs its own regression pass across all four options
pages.

---

## 7. Environment gotchas that will cost you an hour each

- **`uv run pytest` fails** on this machine (trampoline error). Use
  `uv run python -m pytest`. Pre-existing collection errors in `test/sandbox/`,
  `test_bot_web.py` and `test_telegram_startup.py` are unrelated — scope to the
  files you care about.
- **Never `npm run build`** unless you intend to; `frontend/dist` is tracked on
  `main` and it rewrites the lot. You *do* need it to see frontend changes in the
  running app — Flask serves `dist`, not `src` — just keep it out of commits.
- **Never `npm run check`** or `biome check --write` across the tree: it
  reformats all ~446 source files. Scope biome to the files you touched, and run
  it **from `frontend/`** or config resolution fails.
- **Vite's dev proxy does not cover `/gex`, `/oitracker` or `/search`** — only
  `/api`, `/socket.io` and `/auth`. Rebuilding `dist` is the path of least
  resistance.
- **Restart the Flask server after backend changes.** Nothing hot-reloads.
- `ruff` on `app.py` will also fix a **pre-existing** un-sorted import block. That
  is expected, not something you introduced.

---

## 8. Process notes worth carrying forward

**Phase 3 was executed inline, single-reviewer, on the user's instruction** —
phases 1-2 used a subagent per task with two-stage review, and every single
"changes needed" verdict there traced to a defect in the *instructions*, not the
implementation. The inline run cost less and found the same class of problem.

**Measure the audit, do not read it.** The `fd-audit` on phase 3 first showed
~5 KB of RSS growth per recorder tick, which looked like a retention leak. It was
`unittest.mock` recording call arguments. Re-running with plain attribute swaps
instead of `patch()` showed RSS plateauing after 400 ticks and **no line from the
new modules** in `tracemalloc`'s retained-growth list. Descriptors were flat
across 200 ticks, 200 read pairs and 600 watchlist syncs. If you audit with
mocks in the loop, you are measuring the mocks. Phase 4's audit was run the same
way from the start: 1,000 reads of a 349-point window, handles flat at 197-198,
no trend in RSS, nothing from the new modules retained.

**Check which server you are talking to before believing anything.** Phase 4
ended with port 5000 serving a build that had neither phase's routes, while two
`app.py` process pairs were alive. Because the 404 handler falls through to the
React app, a missing route answers `200 text/html` and looks like a working page.
§2.0 has the one-line check. This is the second time this trap has cost time on
this feature.

**Two test bugs, no implementation bugs, during phase 3.** Both were arithmetic
in the test's own constants — a timestamp that was not a multiple of the cadence,
so the floor landed a minute early. Worth remembering that a red test is not
automatically a red implementation.

**Live verification caught what no test could, twice now.** Three defects reached
the live chart last session with a full green suite. Phase 3's own live pass is
still outstanding — see §2.
