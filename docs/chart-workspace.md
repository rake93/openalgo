# Chart Workspace (`/charts`)

The chart workspace is OpenAlgo's full charting surface, built on
[openalgo-charts](https://github.com/marketcalls/openalgo-charts) — a
dependency-free HTML5-canvas engine whose feature tiers are loaded lazily. This
page is the map of what is wired, where the code lives, and which behaviours are
deliberate.

Related surfaces: `/charts/editor` (the OpenScript indicator editor, which drives
the same controller with a smaller callback bag) and `/trading` (the multi-pane
trading terminal, a separate implementation).

---

## Layout

```
┌──────────────────────────────────────────────────────────────┐
│ toolbar   symbol · LTP · timeframe · chart type · indicators │ 40px
│           · studies · trade · view · screenshot · save        │
├────┬────────────────────────────────────────────┬────────────┤
│    │                                            │            │
│ r  │           chart canvas                     │   dock     │ 288px
│ a  │   pane legends drawn on the canvas         │  studies   │ (only when
│ i  │   drawing properties bar floats on top     │  or trade  │  opened)
│ l  │                                            │            │
├────┴────────────────────────────────────────────┴────────────┤
│ status  ·  O H L C V under the crosshair  ·  tool  ·  ws      │ 24px
└──────────────────────────────────────────────────────────────┘
```

The crosshair readout lives in the **status strip**, not in a floating card, so
the hovered bar's O/H/L/C never covers price action. The TradingView-style
floating Data Window is still available — toggle it from the status strip.

---

## Code map

| File | Owns |
|---|---|
| `frontend/src/lib/charts/workspace.ts` | `ChartWorkspaceController` — chart lifecycle, feeds, panes, legends, theme, snapshot/restore |
| `frontend/src/lib/charts/chart-types.tsx` | The 18 chart types, transform construction, box sizing, icons |
| `frontend/src/lib/charts/indicator-host.ts` | OpenScript (WASM worker) indicator instances — shared with `/trading` |
| `frontend/src/lib/charts/library-indicators.ts` | The `openalgo-charts/indicators` tier |
| `frontend/src/lib/charts/drawing.ts` | `DrawingManager` over the `draw` tier |
| `frontend/src/lib/charts/profiles.ts` | Volume profile, market profile (TPO), footprint, CVD |
| `frontend/src/lib/charts/trading-layer.ts` | Order engine, order/position/bracket lines, DOM ladder |
| `frontend/src/lib/charts/tier-compat.ts` | The one place cross-tier type casts live — see below |
| `frontend/src/pages/charts/ChartWorkspace.tsx` | The page: presentation and orchestration only |
| `frontend/src/components/charts/workspace/` | Toolbar, rail, properties bar, pickers, dock panels, context menu |

The controller is **imperative on purpose**. Ticks arrive many times a second
and canvas repaints must not go through React's render path; React receives
coarse updates through a callback bag and unmount is a single `destroy()`.

---

## Chart types

Eleven time-indexed types from the base tier — candles, hollow candles, volume
candles, OHLC bars, high-low, line, line+markers, step, area, HLC area,
baseline, columns — plus six movement-driven families from the transform tier:
**Heikin Ashi, Renko, Range bars, Line Break, Point & Figure, Kagi**.

Box sizing for the movement-driven families is exposed in the toolbar:

| Mode | Meaning |
|---|---|
| `auto` | ~0.15% of price, snapped to the instrument tick — one setting that suits a ₹20 stock and a ₹75,000 index |
| `fixed` | An absolute box size |
| `percent` | A percentage of price, re-resolved as each column opens |
| `atr` | ATR(n) × multiplier (Wilder), re-resolved as each column opens |

**Companion series are re-bucketed.** A transform emits fewer elements than the
raw bars, so the volume histogram is summed onto the *transformed* times. Feeding
it raw bars would scatter every brick back onto the raw timestamps.

---

## Timeframes

Three kinds, all in one menu:

| Kind | Source | Notes |
|---|---|---|
| **Native** | The broker's own resolutions, from `/api/v1/intervals` | Fetched directly |
| **Derived** | Re-bucketed from the coarsest native interval that divides it | 3m, 4m, 10m, 2h … |
| **Live bars** | Built from the tick stream | Tick-count and volume bars; start empty |

**Derived resolutions** exist because brokers serve very few. Dhan, for
instance, offers only 1m, 5m, 15m, 25m, 1h and D — so 3m comes from 1m, 10m
from 5m, and 2h from 1h. Choosing the *coarsest* divisor keeps the request
small: a 2h chart costs a 1h fetch, not a 1m one.

Buckets anchor to the **exchange's market open**, not to an epoch floor —
otherwise a 4-minute bar starts at an arbitrary offset inside the session and
matches no other chart. The rule deliberately mirrors Historify's server-side
aggregation (`database/historify_db.py::_get_aggregated_ohlcv`), including the
per-exchange open (NSE/NFO 09:15, MCX/CDS 09:00), so a 25-minute candle here and
one exported from Historify describe the same bucket. Historify aggregates its
own DuckDB store of 1-minute bars, which the chart does not read — it fetches
from the broker — so the rule is shared even though the code is not. The live
`CandleBuilder` takes the same anchor, so the forming bar lands on the same
boundary as history.

Weeks and months are never re-bucketed: a calendar month is not an arithmetic
one. `frontend/src/lib/charts/resample.ts` is covered by `resample.test.ts`.

**Tick and volume bars** need individual prints, which history does not carry.
They start empty and build from the moment you selected them — the same
constraint as the footprint.

## Indicators

Two runtimes coexist, and the picker labels each row with the one that computes
it:

- **Engine** — the OpenScript WASM worker (`@openalgo/openscript`). 21 built-ins
  plus anything written in the `/charts/editor`. Full Inputs / Style / Visibility
  dialog, alerts, and per-output style overrides applied without a recompute.
- **Library** — the `openalgo-charts/indicators` tier's 18 built-ins, computed by
  the chart itself, with generated settings and native pane chrome.

**Panes come from one allocator.** Pane 0 is price (plus the volume overlay and
any on-chart indicator); sub-pane indicators take 1..n in creation order. On
every rebuild the engine host claims its panes first and the library tier
continues from where it stopped, so the two runtimes never collide.

Removing a pane-owning indicator renumbers everything above it, so the chart is
rebuilt from the surviving instances rather than patched. Moving a pane reorders
the *instances* (`IndicatorHost.movePane`) and then rebuilds — moving the chart's
pane alone would be undone by the next rebuild.

---

## Drawing tools

All 18 tools from the `draw` tier: trend line, ray, extended line, arrow,
horizontal line/ray, vertical line, cross line, rectangle, ellipse, parallel
channel, fib retracement/extension, long/short position, measure, text, path.

The rail has **one button per family** — clicking again cycles the family, so 18
tools fit in 9 buttons with no flyout to chase. The armed tool is named in the
tooltip and echoed in the status strip.

Anchors are `{ time, price }`, never pixels, so drawings survive zoom, collapsed
session gaps, and a chart-type switch. A drag is one undo step. The model lives
in `DrawingManager`, not on the canvas, so it survives every chart rebuild.

Keyboard: `Esc` deselect and disarm, `Del` delete the selection, `Ctrl+Z` /
`Ctrl+Shift+Z` undo and redo, `Ctrl+K` symbol search, `Ctrl+S` save the layout.

### Long / short position sizing

The two position tools answer "how big can this trade be?", so their maths has
to match how the instrument actually trades. Selecting one opens a sizing panel
in the properties bar.

```
riskPoints   = |entry − stop|
rewardPoints = |target − entry|
R:R          = rewardPoints / riskPoints
budget       = capital × risk%
lots         = floor(budget / (riskPoints × lotSize))
qty          = lots × lotSize          capped by the exchange order limit
```

Only **capital** and **risk per trade** are yours to set. Lot size and the order
cap come from the master contract when a symbol loads and are shown read-only —
they are the exchange's decision.

| Segment | Lot size | Sizing |
|---|---|---|
| Cash (NSE, BSE) | 1 | Whole shares |
| F&O (NFO, BFO) | Contract lot | Whole lots, capped by the freeze quantity |
| Currency (CDS, BCD) | Contract lot | Whole lots |
| Commodity (MCX, NCDEX) | Contract lot | Whole lots |

**A budget that cannot afford one lot reports zero**, which is the honest
answer: the stop is too wide for the account. Earlier builds divided the budget
by the point risk alone and reported sizes like "qty 24" on NIFTY futures — not
an order any exchange would accept, since NIFTY trades in lots of 65.

The chart reads back, on each level it belongs to:

```
              +165.48 pts  ₹21,513      ← on the target band
LONG  R:R 4.84                          ← on the entry
qty 130 · 2 lots                        ← what you would send
              -34.22 pts  ₹4,448        ← on the stop band
```

Each band states the distance **and** what it is worth: the distance is what you
check against the chart, the amount is what you check against the account.
Labels sit inside their own band on a long and a short alike. When no size is
affordable the amounts drop out — they would all be zero — and the distances
remain. Sizing is
`sizePosition()` in `openalgo-charts/src/draw/tools.ts`, covered by
`tests/position-sizing.test.ts`, and is exported so a host panel can show the
same numbers without re-deriving them.

---

## Studies and order flow

| Study | Data source | Coverage |
|---|---|---|
| Volume profile | OHLCV history | Everything loaded |
| Market profile (TPO) | OHLCV history | Everything loaded |
| Footprint / CVD | Live classified ticks | **This session only** |

**Footprint is honest about its data.** A footprint needs every print classified
as hitting the bid or the ask. OpenAlgo streams depth and last price, not a
classified tape, so the workspace classifies each live tick against the best
bid/ask from the depth stream (the standard quote rule, falling back to the tick
rule for prints inside the spread) and accumulates from the moment the chart
connected. Historical bars cannot be reconstructed into it, and the panel says
so rather than implying otherwise.

**Profiles are gated on time-indexed chart types.** They anchor to bar times, so
on a movement-driven type (Renko, P&F, Kagi, ...) they stay off and the panel
explains why, rather than drawing at the wrong x.

Market profile exposes sessions (day/week/month/composite), trading-hour windows
(`india` 09:15–15:30 and friends), minutes per letter, row size in price units,
initial balance, merged composites, letters/blocks crossfade, colour modes,
single prints, naked levels, developing POC/value area, and day/open type.

---

## Trading

Two write paths, deliberately:

- **New orders go through `OrderEngine`** (the `trade` tier): tick snapping,
  price-band and freeze-quantity validation, client-token idempotency, OCO
  linking for brackets, and analyzer (sandbox) routing.
- **Existing orders are reconciled from the broker book** and modified or
  cancelled by broker id. A working order may have been placed from `/trading`,
  TradingView, or a Python strategy, so it has no engine client id — driving it
  by broker id is the only correct option.

On the chart: draggable order lines with a cancel ✕, a position line with live
P&L, an OCO bracket whose entry carries its legs, `BuySellButtons` inside the
plot, a `DomLadder` fed from the depth stream (a row click places a limit at that
price), and a right-click menu at the price under the cursor.

**Confirmation is on by default.** "Skip confirmation" is off until the trader
turns it on, and the confirm dialog names live versus analyzer mode. If a host
supplies no confirm handler at all, the controller *declines* rather than firing.

---

## Persistence

`ChartLayoutState.workspace` (in the free-form `layout_json` column, so no
migration) carries chart type, transform settings, volume placement, grid, both
indicator runtimes, drawings, study settings, and trading preferences. It is
auto-saved 1.2 s after any change, and `Ctrl+S` saves immediately. Layouts
written before this field existed still restore their indicator list.

---

## A packaging wart worth knowing

`openalgo-charts` builds each lazy tier as its own bundle whose generated
`.d.ts` **re-declares** `Chart`, `TimeScale`, `PriceScale` and
`PrimitiveRenderContext` rather than importing them from the package root. At
runtime there is exactly one of each — that is what makes the tiers' registries
work — but TypeScript compares those re-declarations nominally because they carry
private fields, so passing a `Chart` into `DrawingController`, or a tier
primitive into `chart.addPrimitive`, is rejected at type level though it is
correct.

`frontend/src/lib/charts/tier-compat.ts` is the single place that bridge is made.
Delete it when a future openalgo-charts release marks the root external for type
generation.
