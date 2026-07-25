# Studies: Volume Profile, Market Profile (TPO) and Order Flow

Reference for the three market-structure studies in the `/charts` workspace —
what each one is, what data it can and cannot be built from, and the default
values, which are tuned for **NSE index futures** and scale to everything else.

See [chart-workspace.md](chart-workspace.md) for the workspace as a whole.

---

## What each study needs

| Study | Built from | Coverage | Works on an index? |
|---|---|---|---|
| Volume Profile | OHLCV bars | All loaded history | No — an index has no volume |
| Market Profile (TPO) | OHLC bars (time at price) | All loaded history | Yes, but volume-weighted extras are empty |
| Footprint / CVD | Live classified ticks | **Current session only** | No — an index has no order book |

**Use the futures, not the index.** `NIFTY`/`BANKNIFTY` on `NSE_INDEX` are
quote-only: no traded volume, no order book, no trading. For volume profile,
order flow, and the volume side of TPO, load the future — `NIFTY28JUL26FUT`,
`BANKNIFTY28JUL26FUT` on `NFO`. TPO's *time*-at-price works on the index itself,
since it counts periods rather than contracts.

### Why footprint is session-only

A footprint needs every print classified as having hit the bid or the ask.
OpenAlgo streams **depth** and **last price**, not a classified tape, and does
not store historical trades. The workspace therefore classifies each live tick
against the best bid/ask from the depth stream — the standard **quote rule**,
falling back to the tick rule (compare with the previous print) for a trade
inside the spread — and accumulates from the moment the chart connected.

That is an approximation of a real tape, and it cannot be reconstructed for past
bars. The panel states this and shows how many bars it has built. A true
historical footprint needs a tick-recording backend.

---

## Row size: the one setting that must change per instrument

Row height is what makes or breaks all three studies. One row per tick sounds
neutral and is almost always wrong: NIFTY futures tick in **0.10**, so a
one-tick profile over a 300-point session is 3,000 rows of noise.

`Row size: 0` means **auto** — the row is scaled to the instrument's price and
snapped to its tick (`autoRowSize` in `frontend/src/lib/charts/profiles.ts`):

- profile (volume + TPO) row ≈ **0.02%** of price
- footprint row ≈ **0.008%** of price
- never finer than one tick

| Instrument | Tick | Price | Profile row | Footprint row |
|---|---|---|---|---|
| NIFTY future | 0.10 | ~23,800 | **5** | **2** |
| BANKNIFTY future | 0.20 | ~52,000 | **10** | **4** |
| RELIANCE | 0.10 | ~1,500 | 0.3 | 0.1 |
| BHEL | 0.05 | ~415 | 0.10 | 0.05 |

Those NSE index-future numbers are the ones desks actually use:

- **5-point NIFTY TPO rows** over a typical 150–300 point session give roughly
  30–60 rows — the density a market profile is meant to be read at. Finer and
  the letters stop fitting; coarser and the value area loses resolution.
- **2-point NIFTY footprint bricks** are the figure openalgo-charts' own
  `rowTicks` documentation uses (`2 / 0.1 = 20` ticks per row). At NIFTY's
  typical spread this puts meaningful volume in every cell instead of scattering
  it across empty ticks.
- **BANKNIFTY is roughly twice as wide** in both tick and daily range, and the
  same formula lands on 10 / 4 — which is what its range warrants.

Type a number to override; the field is in **price units** (points), not ticks,
because that is what a trader thinks in.

---

## Volume Profile

Volume traded at each price, with the point of control and the value area.

| Setting | Default | Why |
|---|---|---|
| Built over | **Visible range** | What "volume profile" means to most traders (VPVR). It follows the viewport and recomputes as you pan or zoom. |
| Display | Buy vs sell | The split is a bar-direction approximation (a bar's whole volume goes to buyers when it closed up). `Total` avoids the approximation; true bid/ask split needs the footprint. |
| Row size | Auto | See above — 5 points on NIFTY futures. |
| Value area | 0.70 | The universal standard: the band around the POC holding 70% of volume. |
| Anchor | Right edge | Keeps the profile off the price action you are reading. |
| Width | 150 px | Longest bar. |

**Visible range is also what keeps the pane honest.** `VolumeProfile` reports
its own extent for autoscale, so a profile over *all* loaded bars drags the
price scale out to that whole history's range and squashes the bars on screen.
A visible-range profile spans exactly what is already displayed. Choose
`All loaded bars` deliberately, knowing it will rescale the pane.

Other modes — per day / week / month — draw one profile under each session's own
bars.

---

## Market Profile (TPO)

The session split into fixed time periods, one letter each, stacked at every
price that period traded. The fat middle is acceptance, the thin edges are
rejection.

| Setting | Default | Why |
|---|---|---|
| Sessions | Per day | One profile per trading day. |
| Trading hours | **India 09:15–15:30** | Anchors period `A` to the session open. Without a window, period A starts at whatever bar arrived first and **every letter shifts**. |
| Minutes per letter | **30** | The classic half-hour period. NSE's 375-minute session gives 12.5 periods — letters A to M. |
| Only the sessions on screen | On | Same reason as the volume profile's visible range: the primitive reports its own extent for autoscale, so profiling weeks of history stretches the price scale across that whole range and squashes today's bars. Turn it off to carry naked levels forward from further back. |
| Row size | Auto | 5 points on NIFTY futures. |
| Initial balance | **2 periods** | The first hour, 09:15–10:15. The range that sets the day's balance on the NSE. |
| Merge sessions | 1 | Raise it to roll N consecutive days into one composite. |
| Blocks | Auto | Letters crossfade to bricks as rows get too short to hold a glyph, so zooming reads as one continuous change. |
| Colour by | Period | One hue per TPO period, so you can see when price traded where. |
| Single prints | On | Lone TPOs away from the extremes — where price moved too fast to build. |
| Naked levels, developing POC, day/open type | Off | Useful, but noisy by default. |

Derived automatically: POC, value area (VAH/VAL), initial balance bracket,
single prints, poor high/low, buying and selling tails, day type
(normal / normal-variation / trend / double-distribution / neutral) and open
type (drive / test-drive / rejection-reverse / auction).

**On the index vs the future:** TPO counts *time* at price, so it is meaningful
on `NIFTY` `NSE_INDEX` itself. The volume sub-profile and volume colour mode
need the future.

---

## Order flow (Footprint, delta, imbalances)

Per-price bid against ask volume for each bar, with delta, cumulative delta and
diagonal imbalances.

| Setting | Default | Why |
|---|---|---|
| Bars built from | **Time, 300 s (5m)** | Matches the chart's default interval. On NIFTY futures a 5-minute footprint gives ~75 bars a session — readable. 1-minute gives 375 and turns into a wall. |
| Row size | Auto | 2 points on NIFTY futures. |
| Cells show | Bid against ask | The classic footprint, where diagonal imbalances are visible. `Delta` and `Volume` collapse to one column. |
| Imbalance ratio | **3** | The industry-standard threshold: ask volume at least 3× the bid one tick below (or the reverse). |
| Stacked run | **3** | Three consecutive same-side imbalances mark a zone worth bracketing. |
| Stats rows | volume, delta, delta %, CVD | The four a trader reads per bar. Add `trades` for print count. |
| Candle behind cells | On | Keeps the bar's range and body visible under the ladder. |

Bars can also be built by **tick count** (250 is a reasonable NIFTY start) or by
**traded volume**. Changing the timeframe or the row size restarts the tape
rather than mixing two bucketings, and the live tape is capped at 400 bars so a
long session cannot grow without bound.

Also available as pure functions for custom overlays or alerts:
`diagonalImbalances`, `stackedImbalances`, `cumulativeDelta`.

---

## Quick start on NSE index futures

1. Load `NIFTY28JUL26FUT` (`NFO`) — press `Ctrl+K`.
2. Timeframe `5m`.
3. **Studies → Market profile** on. Leave the defaults: day sessions, India
   hours, 30-minute letters, 2-period initial balance.
4. **Studies → Volume profile** on for visible-range volume at price.
5. **Studies → Order flow** on during market hours and let it build; it starts
   empty by design.

Everything you change is saved with the layout, per the persistence rules in
[chart-workspace.md](chart-workspace.md).
