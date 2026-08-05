# Handoff: GEX Levels advanced visualisations

**Session date:** 2026-08-05 (phase 3; phases 1-2 the same day)
**Branch:** `feat/indicator-engine` (long-lived; never merged to main — see the fork model note in memory)
**Read first:** [`specs/2026-08-05-gex-advanced-visualisations-design.md`](specs/2026-08-05-gex-advanced-visualisations-design.md)
**Plan just executed:** [`plans/2026-08-05-gex-snapshot-recorder.md`](plans/2026-08-05-gex-snapshot-recorder.md)

---

## 1. Where things stand

| Feature | Status |
| --- | --- |
| Gamma Profile | **Shipped**, verified live |
| Delta Exposure (DEX) | **Shipped**, verified live |
| Hover readout, draggable card | **Shipped**, confirmed by the user |
| **Snapshot recorder (phase 3)** | **Built and green. NOT yet verified live** — see §2 |
| **Gamma Bands (phase 4)** | Not started. Unblocked — do this next |
| **GEX Heatmap (phase 5)** | Not started |

Backend tests went from 160 to **301 across the GEX and option-target suites**,
all green. No pure module under `services/gex_levels/` changed, which is the
regression guard for the whole change.

---

## 2. Do this before anything else: verify phase 3 live

**Phase 3 has never touched a broker.** Every test stubs the chain. That matters
more here than usual, because last session three defects reached the live chart
with a full green suite — a dead futures badge, a caption drawn under the readout
card, and a card drag that panned the chart. jsdom and stubs cannot see any of
that.

The server must be restarted first; nothing hot-reloads.

1. **Idle case first, and it is the one an upgrade hits.** `GET
   /gex/api/gex-series` returns `[]` on a fresh install, and the log shows the
   recorder registering only `gex_prune` — no recording job, no broker call.
2. Add a series (session-gated, so drive it from the browser console on an
   authenticated tab, or export the session cookie):
   ```
   POST /gex/api/gex-series {"underlying":"NIFTY","exchange":"NFO","expiry_rule":"nearest"}
   ```
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
   a unit test for this, but the unit test cannot see a units bug in the live
   chain; the last one survived 99 green tests and was caught only by a live call.
7. After the close: no new rows, and no rate-limit warnings in `log/errors.jsonl`.

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

## 4. Pick up here: phase 4, Gamma Bands

The smallest query shape, and the first consumer of the history.

**The query already exists and is already boundary-tested:**
`gex_history_db.get_snapshots_in_range(series_id, from_ts, to_ts)` — inclusive
both ends, ordered ascending, snapshot rows only. What remains:

- `services/gex_history_service.py` — the read side, deliberately separate from
  the recorder so a query path can never trigger a fetch.
- `POST /gex/api/gex-history` with `fields: "levels"` (spec §6).
- Step-line renderers for Call Wall, Put Wall and Zero-Gamma over price.
- A watchlist control in the GEX Levels settings panel, wired to the routes
  phase 3 shipped.

**Three things the spec is explicit about and a renderer will get wrong:**

1. **A gap must look like a gap.** A failed tick has no row. The Bands break the
   line; they do not interpolate. Flat gamma where there was *no reading* is the
   error `quality.py` and `direction.ts` already forbid.
2. **Mark the roll.** Every snapshot stores its resolved `expiry_date`. On a
   `nearest` series, 30 days is four or five different books, and the walls jump
   at each roll because the book changed, not because the market moved. Filter to
   one contract or mark the boundary — drawing across it unmarked is the same
   class of error as labelling a synthetic forward "Futures".
3. **Zero-Gamma stays in forward space** and inherits the existing limitation:
   on a cash-index chart the band sits above the equivalent spot level by the
   basis. Documented in [`../gex-levels-reading.md`](../gex-levels-reading.md) §8.

**One thing to decide rather than inherit:** `get_snapshots_in_range` has no row
ceiling. The spec caps only the phase 5 grid endpoint and calls the Bands query
"a few thousand small objects" — true for a month, but nothing enforces it.

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
mocks in the loop, you are measuring the mocks.

**Two test bugs, no implementation bugs, during phase 3.** Both were arithmetic
in the test's own constants — a timestamp that was not a multiple of the cadence,
so the floor landed a minute early. Worth remembering that a red test is not
automatically a red implementation.

**Live verification caught what no test could, twice now.** Three defects reached
the live chart last session with a full green suite. Phase 3's own live pass is
still outstanding — see §2.
