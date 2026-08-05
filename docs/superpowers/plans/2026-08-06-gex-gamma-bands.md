# Gamma Bands (phase 4) Implementation Plan

**Goal:** Plot the three GEX levels — Call Wall, Put Wall, Zero-Gamma — through
time as step lines over price, reading the history the phase 3 recorder writes.

**Architecture:** A read-only service over `gex_history_db.get_snapshots_in_range`,
a `fields: "levels"` route, and a new chart primitive. The read side is a separate
module from the recorder so a query path can never trigger a fetch.

**Tech Stack:** Flask, SQLAlchemy, React 19 + TypeScript, `openalgo-charts`
primitives, pytest + vitest.

---

## Context

Phase 3 records a snapshot per watchlisted series per minute but nothing reads it.
Bands is the first consumer and the smallest query shape: snapshot rows only, no
strike children.

**Two constraints shape this plan and neither is negotiable:**

1. **The market is shut and the recorder has never recorded.** Bands has nothing
   to draw. Phase 3's live verification is already outstanding; shipping a second
   unverified visual on top would compound it. So this plan includes a **seed
   script** that fabricates a session of history into `gex.db`, and the renderer
   gets looked at against that before it is called done. Last session three
   defects reached the live chart with a full green suite — jsdom calls handlers
   with no chart underneath and cannot see any of them.

2. **`frontend/dist` is tracked on this branch and already dirty.** Build to look
   at the result; **never `git add frontend/dist`**. Never `npm run check` — it
   reformats all ~446 source files.

### The one design question the spec already answers

Bands shows **one contract**, not a spliced series. Spec §6 puts `expiry_date` in
the request body, the same scoping `get_latest_snapshot` uses. The client takes it
from the live snapshot it already holds (`GEXLevelsResponse.expiry_date`), so a
study set to "nearest" resolves correctly without knowing the rule.

The honest cost, which the UI must not hide: on a weekly `nearest` series the
visible history is only as long as the current contract has existed — about five
sessions, not the thirty days retained. That is the right trade. Splicing
contracts would draw wall jumps at each roll that are the book changing, not the
market moving — the same class of error as labelling a synthetic forward
"Futures".

### Out of scope

The `fields: "grid"` response, `MAX_GRID_COLUMNS` downsampling and the heatmap
renderer are phase 5. This plan adds `fields` to the request and implements
`"levels"` only, rejecting `"grid"` with a 400 so phase 5 has a seam rather than a
surprise.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `services/gex_history_service.py` (create) | Read side. Resolves the series, ranges the snapshots, projects one weighting, returns band points. No fetch, ever. |
| `database/gex_history_db.py` (modify) | `get_series_by_contract` — find the series that recorded a given (underlying, exchange, expiry). |
| `blueprints/gex.py` (modify) | `POST /gex/api/gex-history`. |
| `frontend/src/api/gex.ts` (modify) | `GEXHistoryResponse`, `GEXBandPoint`, `getGEXHistory`. |
| `frontend/src/lib/charts/gex-bands-geometry.ts` (create) | Pure: split points into drawable segments at gaps. No canvas, no chart. |
| `frontend/src/lib/charts/gex-bands-primitive.ts` (create) | The `IPrimitive` that paints three step lines. Canvas calls only. |
| `frontend/src/lib/charts/gex-levels.ts` (modify) | `showBands` config, the history fetch, primitive lifecycle. |
| `frontend/src/components/charts/workspace/StudiesPanel.tsx` (modify) | Bands toggle, and the recorder watchlist control. |
| `scripts/seed_gex_history.py` (create) | Dev-only. Fabricates a session so the renderer can be seen with the market shut. |

---

## Task 1: The read side

**Files:** create `services/gex_history_service.py`; modify
`database/gex_history_db.py`; test `test/test_gex_history_service.py`.

`get_series_by_contract(underlying, exchange, expiry_date)` returns the series
that has recorded snapshots for that resolved contract — the same join
`get_latest_snapshot` does, without the row.

`get_gex_history(underlying, exchange, expiry_date, weight_by, from_ts, to_ts,
fields="levels") -> (ok, payload, status)`:

- Validates `weight_by` and `fields` before touching the database.
- `fields: "grid"` returns 400 "not implemented until phase 5" — an explicit
  seam, not a silent empty response.
- No recorded series → **200 with an empty `points` list**, not a 404. A series
  nobody chose to record is an ordinary state, and the study must render exactly
  as it did before phase 4.
- Projects one weighting into `points: [{ts, call_wall, put_wall, zero_gamma,
  net_gex, regime, quality_verdict}]`.
- Carries `resolution: "1m"` and `downsampled: false` so phase 5's downsampling
  is additive rather than a shape change.

**Tests:** the range is inclusive; the weighting selects the right columns; a
null `zero_gamma` stays null rather than becoming 0; an unrecorded contract is an
empty 200; `fields: "grid"` is a 400; a gap survives as a gap.

**Commit:** `feat(gex-history): add the read side for recorded levels`

---

## Task 2: The route

**Files:** modify `blueprints/gex.py`; test `test/test_gex_history_endpoint.py`.

`POST /gex/api/gex-history`, session-gated, validated with the same regexes as
`gex_levels`. `from_ts`/`to_ts` must be positive integers with `from_ts <= to_ts`.

**One ceiling the spec does not have and the fd-audit flagged:**
`get_snapshots_in_range` has no row limit. Thirty days is ~11,000 rows. Add
`MAX_HISTORY_POINTS = 20_000` and reject a window that would exceed it with a
400 naming the limit, rather than streaming an unbounded response to a browser.

**Commit:** `feat(gex-history): add the history route`

---

## Task 3: Geometry, pure and tested first

**Files:** create `frontend/src/lib/charts/gex-bands-geometry.ts` and its test.

```ts
export interface GexBandPoint { ts: number; value: number | null }
export function splitBandSegments(
  points: readonly GexBandPoint[],
  maxGapSeconds: number,
): GexBandPoint[][]
```

Splits on two things, and both matter:

- **A time gap wider than `maxGapSeconds`.** A failed tick has no row. Joining
  across it would draw a level that was never read — the error `quality.py` and
  `direction.ts` already forbid. An overnight session break exceeds the same
  threshold and breaks for free, which is correct: the chart's gapless axis would
  otherwise connect yesterday's close to today's open with a straight line
  through a level nobody observed.
- **A `null` value.** `zero_gamma: null` is "no local cross", a real reading, not
  a missing number. The line stops and restarts; it does not interpolate and it
  does not drop to zero.

`maxGapSeconds` defaults to **150** — two and a half cadence intervals, so one
missed tick does not fragment the line but a real outage does.

**Tests:** a clean run is one segment; one missing minute splits it in two; a
null run splits and does not emit a zero; a single isolated point still yields a
segment (so a lone reading is visible as a dot rather than silently dropped);
an empty input yields no segments.

**Commit:** `feat(gex-bands): add the pure segment geometry`

---

## Task 4: The primitive

**Files:** create `frontend/src/lib/charts/gex-bands-primitive.ts` and its test.

`zOrder: 'bottom'`, like `GexLevelsPrimitive`. **No `autoscaleInfo`** — the same
trap `profiles.ts` documents three times and `gex-levels-primitive.ts` documents
again: a primitive that reports its own extent drags the price scale and squashes
the candles.

Time-to-x is `rc.dataLayer.timeToIndexFloat(ts)` then
`rc.timeScale.indexToX(index)`. `timeToIndexFloat` rather than `timeToIndex`
because a snapshot is minute-floored and the chart may be on any timeframe — an
exact-match lookup would drop four of every five points on a 5-minute chart.

Step lines, not straight ones: a wall sits at a strike until it moves to another
strike, so the honest shape is a horizontal hold and a vertical jump. Drawn with
the same three colours the levels already use, thinner, so a band and its live
level read as the same object.

**Tests (vitest, with a stub render context):** each visible band draws; a hidden
band draws nothing; a segmented series issues a separate path per segment (a
single path would connect across the gap); nothing throws when the history is
empty.

**Commit:** `feat(gex-bands): draw the three levels through time`

---

## Task 5: Manager wiring

**Files:** modify `frontend/src/lib/charts/gex-levels.ts`, `api/gex.ts`, tests.

`GexLevelsConfig` gains `showBands: boolean` (default `false` — Bands shows
nothing until a series is recorded, and a control that does nothing by default is
worse than one the user turns on) and `bandsLookbackHours: number` (default 6).

The history fetch is separate from the levels poll and much lazier: on enable, on
instrument change, and every `refreshSeconds * 5`. History is append-only and a
band that is one minute behind is invisible; re-fetching a 6-hour window every
minute is pure waste.

It uses `lastSnapshot.expiry_date`, so it does not run until the first live
snapshot has resolved the contract. Same epoch guard as `fetchNow` — a slow
history response for NIFTY must never paint on BANKNIFTY.

**Commit:** `feat(gex-bands): fetch and hold recorded history in the manager`

---

## Task 6: The panel, and the watchlist control

**Files:** modify `StudiesPanel.tsx`, `ChartWorkspace.tsx`, tests.

A **Bands** field in the existing GEX section (Show / Hide) plus a lookback
select.

And the recorder control, which is what makes phase 3 reachable without curl:
when Bands is on and the current contract is **not** recorded, the panel shows a
one-line explanation and a **Record this series** button that POSTs to
`/gex/api/gex-series`. When it is recorded, it shows how many points are held and
a way to stop. Without this, Bands is a toggle that draws nothing for every user
who has not read the handoff.

**Commit:** `feat(gex-bands): add the Bands and recorder controls`

---

## Task 7: Seed, then actually look at it

**Files:** create `scripts/seed_gex_history.py`.

Fabricates one session of snapshots for a chosen series: walls that hold and
then step to a new strike, a zero-gamma that wanders and goes null for a stretch,
and **two deliberate holes** — one single missing minute and one ten-minute
outage — so the gap rule is visible rather than assumed.

Then: `cd frontend && npm run build`, restart Flask, open `/charts` on
`NIFTY28JUL26FUT`, and confirm

- the bands sit on the price axis aligned with the candles;
- the single-minute hole does **not** break the line, the ten-minute one does;
- the null zero-gamma stretch leaves a hole rather than dropping to the bottom;
- turning Bands off removes them and leaves the live levels untouched;
- the readout card and the bar column are unchanged.

**Do not skip this.** Everything above this line is jsdom and stubs.

**Commit:** `chore(gex-bands): add a history seeder for local verification`

---

## Task 8: fd-audit and docs

Read-only paths and no new threads, so the audit is narrow — but it adds a route
that returns an unbounded-ish result set and a frontend timer, both of which the
skill covers. Then update spec §10 and the handoff for phase 5.

**Commit:** `docs(gex): record Gamma Bands and hand off the Heatmap`

---

## Verification

```bash
uv run python -m pytest test/test_gex_history_service.py test/test_gex_history_endpoint.py \
  test/test_gex_history_db.py test/test_gex_recorder_service.py \
  test/test_gex_levels_service.py test/test_gex_levels_endpoint.py \
  test/test_gex_series_endpoint.py -v
cd frontend && npx vitest run src/lib/charts/gex-bands-geometry.test.ts \
  src/lib/charts/gex-bands-primitive.test.ts src/lib/charts/gex-levels.test.ts
```

Plus Task 7's visual pass, and — once the market opens — phase 3's outstanding
live verification, which Bands now also depends on.
