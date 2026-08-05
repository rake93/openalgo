# Handoff: GEX Levels advanced visualisations

**Session date:** 2026-08-05
**Branch:** `feat/indicator-engine` (long-lived; never merged to main — see the fork model note in memory)
**Commit range:** `a25b8110d..6e0ea0b1c` — 39 commits
**Read first:** [`specs/2026-08-05-gex-advanced-visualisations-design.md`](specs/2026-08-05-gex-advanced-visualisations-design.md)

---

## 1. Where things stand

Four features were requested from the deepcharts DeepGamma references, plus two
added mid-session. **Two of the four and both extras shipped. Two remain, and
both are blocked on the same missing infrastructure.**

| Feature | Status |
| --- | --- |
| Gamma Profile | **Shipped.** Was already built; only the metric toggle was missing |
| Delta Exposure (DEX) | **Shipped.** Verified against a live chain and on the running chart |
| Hover readout (added mid-session) | **Shipped.** Both metrics at once |
| Draggable readout card (added mid-session) | **Shipped.** Confirmed by the user |
| **Gamma Bands** | **Not started.** Needs the recorder |
| **GEX Heatmap** | **Not started.** Needs the recorder |

Test counts moved from 125 backend / ~240 frontend to **160 backend / 323
frontend**, all green.

Two `/tools` defects were also found and fixed en route (see §5).

---

## 2. What to pick up next — phase 3, the recorder

**The design is complete and approved. Nothing needs re-deciding.** Spec
sections 4 through 9 specify the architecture, the schema, the API and the
failure modes in full. The decisions already made, with their rejected
alternatives recorded:

| Decision | Chosen |
| --- | --- |
| Who records | Backend recorder, persisted multi-day |
| Coverage | Configured watchlist, **ships empty**, recorder idle until a series is added |
| Cadence / retention | 1 minute, 30 days |
| What is stored | Computed exposures, **plus raw OI and volume** |
| Storage | New `gex.db`, the seventh isolated database, via `engine_factory` |
| Scheduling | `utils/scheduler.ResilientBackgroundScheduler`, **memory jobstore** |

Then phase 4 (Gamma Bands — the smallest query shape, do it first) and phase 5
(GEX Heatmap — the grid endpoint plus downsampling).

### The one thing phase 3 gets for free that the spec predates

Phase 2 extracted `weighted_legs(rows, ivs, weight_by)` into
`services/gex_levels/exposure.py`, and both pricers now take the resulting legs
list. `gex_levels_service` already resolves IVs once, builds legs once, and
prices gamma and delta from the same list object. **That is most of the
`build_snapshot` extraction the spec's phase 3 calls for** — the recorder should
consume that seam rather than reimplementing the pipeline. The spec's warning
about the recorder drifting from the live path is already half-answered.

### Mandatory before phase 3 is called done

CLAUDE.md requires the **`fd-audit` skill** after any change touching databases,
threads, schedulers or subprocesses. Phase 3 touches all four. Phases 1-2 did
not, which is why it has not been run yet.

---

## 3. Known limitations, deliberately left

**Zero-Gamma is a forward-space level drawn on a spot-price axis.** On a cash
index chart the line sits above the equivalent spot level by the basis (~35
points on a 6-day NIFTY expiry, growing with time to expiry). It is exact on a
futures chart. Documented in
[`../gex-levels-reading.md`](../gex-levels-reading.md) §8. The Regime label is
unaffected — it makes the comparison in forward space correctly.

**Bar row height caps at 36px** (`MAX_BAR_ROW_HEIGHT_PX` in
`frontend/src/lib/charts/gex-levels-geometry.ts`). There is a real trade-off with
no value that gets both halves: bars tile into a continuous profile only when a
row is as tall as the gap to the next strike, and a 50-point NIFTY spacing is
routinely 100-200px once the price scale is stretched. Both extremes were tried
and both were reported wrong by the user — a flat 14px left slivers adrift, and
scaling with the pane produced slabs when the y-axis was stretched. 36px tiles
while the gap is small and stops at a readable ribbon. One number, easy to tune.

**The metric applies to the bar column only.** Walls, Zero-Gamma, Regime and the
card's GEX rows stay gamma whichever metric is selected — they are gamma-derived
server-side. Three on-screen labels exist so this is never ambiguous: the card's
`Bars` row, an amber caveat under delta, and the bar-column caption
(`Gamma · dealer sign` / `Delta · OI-book sign`). **Do not remove these** without
replacing the mitigation: green means "dealers long" under gamma and "dealers
short" under delta, because DEX is the open-interest book's delta and dealers
hold the negation.

---

## 4. Follow-ups worth doing, none blocking

**`scan_zero_gamma` re-resolves what the caller already has.** Measured on a
47-strike chain: `resolve_ivs` (0.574 ms) and `weighted_legs` (0.201 ms) run once
in `gex_levels_service`, then `scan_zero_gamma` runs both again internally with
identical arguments. That duplicated 0.775 ms per request is larger than the
entire delta pricing pass added this session (0.300 ms). It uses `rows` for
nothing beyond an emptiness guard, so accepting the built `legs` would remove it.
Changes `levels.py`'s signature, which is why it was not folded in.

**`/gex` still calls `compute_exposures`.** Correct today — it needs gamma only —
but the two surfaces now reach the pricers by different routes. If `/gex` ever
gains the delta metric, move it to the `resolve_ivs` -> `weighted_legs` ->
two-pricer shape rather than growing a second single-shot path.

**`services/gamma_density_service.py` migration.** Re-verified this session: it
already resolves the forward and correctly omits the lot factor. Its migration is
a tidy-up for code sharing, **not a fix** — nothing on that page is wrong.

**Normalising units inside `option_chain_service`.** Still the better long-term
shape: it would fix every consumer at once and stop the next tool repeating the
units-versus-lots mistake. Larger blast radius; needs its own regression pass
across all four options pages.

---

## 5. Fixed this session, outside the original scope

**The Futures badge was dead on four pages** (`/gex`, OI Tracker, Max Pain, OI
Range). `_get_nearest_futures_price` filtered `fno_search_symbols(underlying=)`,
which matches on `SymToken.name` — NULL for this broker's NFO rows — so both the
primary lookup and its nearest-month fallback returned nothing. Weeklies have no
listed future either, so the primary could never match. Replaced with the shared
`_resolve_forward_price` (put-call parity synthetic), renamed the response key to
`forward_price`, and relabelled the badge "Forward:" since on a weekly there is no
future and the figure is a synthetic.

**OI Tracker showed two units for OI on one screen.** The chart divides by lot
size and says "(lots)"; the total badges showed raw units under the same word
"OI" — 65x apart on NIFTY with nothing saying which was which.

**Max Pain and OI Tracker were reviewed for the units bug and cleared.** Neither
multiplies OI by the lot size. Recorded with the reasoning in
[`specs/2026-08-04-gex-levels-chart-study-design.md`](specs/2026-08-04-gex-levels-chart-study-design.md)
§11, including the point that max pain would have survived the bug anyway: lot
size is constant across strikes, so it scales the whole pain curve uniformly and
argmin is unchanged.

---

## 6. Environment gotchas that will cost you an hour each

- **`uv run pytest` fails** on this machine (trampoline error). Use
  `uv run python -m pytest`. Some pre-existing collection errors in
  `test/sandbox/`, `test_bot_web.py` and `test_telegram_startup.py` are unrelated
  — scope to the files you care about.
- **Never `npm run build`** unless you intend to; `frontend/dist` is tracked on
  `main` and it rewrites the lot. You *do* need it to see frontend changes in the
  running app — the Flask server serves `dist`, not `src` — just keep it out of
  commits.
- **Never `npm run check`** or `biome check --write` across the tree: it
  reformats all ~446 source files. Scope biome to the files you touched, and run
  it **from `frontend/`** or config resolution fails.
- **Vite's dev proxy does not cover `/gex`, `/oitracker` or `/search`** — only
  `/api`, `/socket.io` and `/auth`. So `npm run dev` cannot load these pages'
  data without a proxy entry. Rebuilding `dist` is the path of least resistance.
- **Restart the Flask server after backend changes.** Nothing hot-reloads. A
  frontend change needs a `dist` rebuild; a backend change needs a restart. Both
  caught me this session.
- **The `.superpowers/brainstorm/` server auto-exits after 30 minutes.** Check
  `$STATE_DIR/server-info` exists before pushing a screen.

---

## 7. Process notes worth carrying forward

The two-stage review (spec compliance, then code quality) was run on every task.
**Every single "Changes needed" verdict was a defect in the instructions, not in
the implementation.** The subagents built what was specified, faithfully, each
time. Examples worth knowing about because they are the shape of what this
codebase gets wrong:

- The spec's DEX sign rule was wrong — mirroring GEX's dealer constants makes
  every strike positive and the total meaningless. Caught by working the algebra
  during planning, not by a test.
- The duplicated preamble between the two pricers carried an *unwritten*
  position-alignment contract that the service's `zip` depended on. Extracting
  `weighted_legs` made it structural — and hoisting it out of the zero-gamma scan
  took that scan from 24.46 ms to 12.21 ms, faster than before the work started.
- One claim — "the longest bar lands on the Call Wall" — was wrong three separate
  ways before it was right. The walls rank the full 47-strike chain while the bar
  peak scales over visible strikes only, so the coincidence holds only while the
  dominant wall is on screen. A test pinning that clipping already existed;
  nobody had connected it to the claim.

**Live verification caught what no test could.** Three defects reached the live
chart with a full green suite: the dead futures badge (invisible because the
badge is conditionally rendered), the metric caption drawn underneath the readout
card, and the card drag panning the chart because the press bubbled to a
container listener. jsdom calls handlers directly with no chart underneath, so
none of these were reachable from a unit test.

**Build the frontend and look at it.** Twice this session a feature was
"complete", fully typechecked and fully tested, and visibly wrong on screen.
