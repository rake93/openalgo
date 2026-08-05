# Studies: Volume Profile, Market Profile (TPO), Order Flow and GEX Levels

Reference for the four market-structure studies in the `/charts` workspace —
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
| GEX Levels | Live option chain, **not the chart's bars** | Live snapshot only | Yes — the chain is on the derivatives exchange |

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

## GEX Levels

> For **how to read** these verdicts — what makes Regime flip, why Sentiment is
> hard to push bullish on an index, and worked examples from a live chain — see
> [gex-levels-reading.md](gex-levels-reading.md). This section defines what each
> level is.

Where dealer gamma is concentrated, drawn on the price axis. Unlike the other
three studies this is **not derived from the chart's bars at all** — it is a live
option-chain snapshot for the charted instrument's *underlying*, refreshed on a
timer.

| Level | What it is | How it tends to behave |
|---|---|---|
| **Call Wall** | Strike with the largest positive dealer gamma | Rallies stall into it |
| **Put Wall** | Strike with the largest negative dealer gamma | Declines find support at it |
| **Zero-Gamma** | The price at which aggregate dealer gamma changes sign | Above it dealers stabilise, below it they amplify |

**Regime**, in the dashboard, is the sign of net GEX:

- **Suppressive** (positive) — dealers sell rallies and buy dips, so price pins.
- **Amplifying** (negative) — dealers trade with the move, so it extends.

Amplifying is **not bearish**. Negative gamma extends moves in *both* directions,
which is why the dashboard says Suppressive/Amplifying rather than the
bullish/bearish some products use. Reading it as a short signal during a
gamma-driven squeeze upward is exactly the mistake that wording invites.

### Sentiment is a separate read, and answers a different question

Below Regime sits **Sentiment** — Bullish, Bearish or Neutral. It is *not* the
sign of net GEX. It is a composite of three genuinely directional signals, built
the way `direction.ts` builds its market-direction verdict:

| Signal | Weight | Reads |
|---|---|---|
| **Wall position** | 2 | Spot above the call wall is bullish (price broke the largest positive-gamma strike and dealers must chase); below the put wall is bearish; between them is neutral — pinned |
| **Put-call ratio** | 1 | On the selected weighting. Above 1.2 bullish, below 0.8 bearish. High PCR means put writers dominate, which supports the market |
| **IV skew** | 1 | Puts richer than calls by more than 1.5 vol points is bearish (downside protection bid); the reverse is bullish |

Each signal reports **unavailable** independently and drops out of the composite
entirely — a missing input never reads as a zero. The verdict carries how many
signals agreed out of how many participated, shown as `Bullish 2/3`, so a
one-signal read can never display as a unanimous one.

**Regime and Sentiment will often disagree, and that is correct.** A live NIFTY
reading during development showed Regime *suppressive* (net gamma positive, so
price pins) while Sentiment read *neutral* with PCR leaning bearish. One is about
whether moves get damped or extended; the other is about which way. Neither is
derived from the other, and nothing in the code passes `net_gex` to the sentiment
calculation at all.

Deliberately not inferred: **where** between the walls spot sits. "Pinned between
the walls" is the honest read; calling proximity to the put wall bullish is a
weaker claim than a gamma profile supports. The detail line still tells you the
position so you can make that call yourself.

### Zero-Gamma is a scan, and "No local cross" is normal

Zero-Gamma is not the strike where a running total of per-strike GEX crosses
zero. An option's gamma depends on where the underlying *is*, so the whole
profile is rebuilt at 60 hypothetical prices spanning ±20% of the forward, and
the sign change is interpolated. That is why the level lands **between** strikes.

Two consequences worth knowing:

- A profile can cross zero more than once. The level reported is the crossing
  **nearest the forward** — the boundary of the regime price is currently in,
  not the lowest-priced flip in the window.
- When the profile is long gamma, or short gamma, across the entire scanned
  range there is no crossing at all, and the dashboard reads **No local cross**.
  That is an ordinary market state, not an error.

Volatility is inverted **once, at the real forward**, and held fixed across the
scan. Re-inverting at each hypothetical forward would ask what volatility makes
today's premium consistent with a price the market never traded at — a
meaningless quantity that degenerates far from spot.

### Weight by open interest or volume

| Weighting | What it measures |
|---|---|
| **Open interest** (default) | The full standing dealer book |
| **Volume** | Today's traded flow only; empty at the open, builds through the session |

Open interest is the default because NSE and BSE disseminate it **live** in the
tick feed. The US argument for volume-weighted GEX — that official open interest
is a prior-night snapshot that goes stale intraday — does not apply here.

### Metric: gamma or delta

The strike-bar column can be drawn from either of two exposures. Gamma reads
how hard dealers must hedge as the underlying moves — the same profile Call
Wall, Put Wall, Zero-Gamma and Regime are built from. Delta reads which way the
open-interest book already leans — a snapshot of standing position, not of
hedging pressure.

| Metric | What the bar shows | Positive means |
|---|---|---|
| **Gamma (GEX)** (default) | Dealer hedging pressure at that strike | Dealers are long gamma there — stabilising |
| **Delta (DEX)** | The open-interest book's own delta at that strike | Calls dominate the strike; the book is net long delta |

Both are computed from the same option-chain fetch and arrive together — every
strike in the response carries `net_gex` and `net_dex` at once. Switching
`Metric` just re-renders from data already on the client: no refetch, no extra
broker call.

**Read the sign the right way round.** DEX is the open-interest **book's**
delta, not the dealer's. Positive DEX means calls dominate that strike and the
book is net long delta — dealers are the counterparty, so dealer delta is the
negation of what the bar shows (see `services/gex_levels/delta_exposure.py`'s
module docstring). Everything else on this chart, including the walls and
Regime, speaks in the dealer's frame. Delta bars are the one thing on screen
that speaks in the opposite frame, so a reader who learned this study on gamma
will read the delta colours backwards unless they remember the flip.

**Only the bars change.** Call Wall, Put Wall and Zero-Gamma are computed
server-side from gamma alone, regardless of which metric is selected, and
Regime is the sign of net GEX, never net DEX. The readout card's Call GEX, Put
GEX and Net GEX rows stay the same for both settings too. One consequence
follows directly: under gamma, the longest bar in the column lands on one of
the two walls — Call or Put, whichever carries the larger magnitude — whenever
that wall's strike is within the visible price range, because the wall ranks
the full fetched chain while the bar peak scales only over what is currently
on screen. Switch to delta and that coincidence breaks even when both walls
are visible: the walls stay exactly where gamma put them, but the tallest DEX
bar sits wherever the open-interest book is most lopsided, which is generally
a different strike.

Three on-screen labels keep this unambiguous: a `Bars` row in the readout card
reading `Gamma (GEX)` or `Delta (DEX)`, an amber caveat in the card whenever
delta is selected, and a caption under the bar column itself reading
`Gamma · dealer sign` or `Delta · OI-book sign`.

`Metric` sits in the Studies panel directly after `Strike bars`, and disappears
when `Strike bars` is `Levels only` — with no bars drawn, there is nothing for
it to affect.

### What it works on

| Charted instrument | GEX Levels |
|---|---|
| `NIFTY` on `NSE_INDEX`, `NIFTY28AUG26FUT` on `NFO` | Yes — deep, cash-settled, writer-dominated chains |
| A single stock or its future | Yes, but expect a degraded verdict. Monthly expiry only and physically settled, so open interest unwinds fast into expiry |
| An MCX future | Yes. Options are written on a future, which is what Black-76 already assumes. Crude is the only commodity with real depth |
| An option itself (`NIFTY28AUG2624000CE`) | **No.** Its price axis is premium, not underlying price — an underlying-price level cannot be drawn on it, and the study is disabled |
| Cash equity with no F&O, and anything on CDS | No chain to fetch |

A futures **expiry rollover on the same root** does not refetch: the chain is
keyed on the underlying plus the study's own expiry setting, not on the charted
contract's expiry. Changing **timeframe** does not refetch either — GEX does not
depend on it.

Unlike the profiles, GEX levels have no time anchoring, so they also render on
the movement-driven chart types (Renko, P&F, Kagi).

### Prices come off the forward, never spot

Black-76's `F` is the **per-expiry synthetic future**, not the cash index. Gamma
peaks at the at-the-money *forward*, so pricing off spot displaces the entire
profile and therefore both walls. The measured BANKNIFTY basis at 21 days is
**+138.9 points** — far more than one strike.

### Open interest arrives in units, not lots

The textbook GEX formula multiplies by the contract multiplier, because open
interest is conventionally quoted in **contracts**. The broker feed here reports
it in **units** — already multiplied by the lot size. Verified across a live
NIFTY chain: all 188 open-interest and volume values were exact multiples of the
lot size.

So there is deliberately **no lot factor** in the exposure calculation. Adding
one back inflates every figure by the lot size — 65x on NIFTY, which put net GEX
at 547,006 Cr against a true 8,415 Cr. If you are comparing against a published
GEX figure and yours is off by exactly a lot size, this is why.

Worth knowing: this scaling affects **magnitudes only**. A uniform factor cannot
move an argmax or a zero crossing, so Call Wall, Put Wall, Zero-Gamma and the
regime are identical either way.

### Data status

The chain is fetched **23 strikes each side of ATM**. That is a broker limit,
not a preference: the multiquote open-interest bucket holds 100 symbols, and
asking for more returns **empty OI rather than an error**.

The `Data status` row reports how many of those strikes yielded a real implied
volatility. The study degrades itself and says why when the chain is mostly
unpriced, when the window sits entirely on one side of the forward, or when a
wall lands on the window's edge — where it may be an artefact of where the
window stopped rather than a real concentration.

### Settings

| Setting | Default | Why |
|---|---|---|
| Weight by | Open interest | Live in India; volume is the today's-flow read |
| Expiry | Nearest | Front expiry dominates gamma |
| Strike bars | Show | Turn off for a clean levels-and-dashboard view |
| Refresh | 60 s | The cadence the reference products use |

**The study never widens the price scale.** A 47-strike window spans far more
than the visible range, so contributing to autoscale would drag the scale out
and squash the candles. The bars clip to what is on screen instead, and a wall
outside the visible range is drawn as a marker at the plot edge rather than
silently vanishing.

### Compared with the `/gex` tool

The `/gex` Tools page and this study now **agree**. Both run the same maths in
`services/gex_levels/exposure.py` over the same chain: gamma priced off the
per-expiry forward, weighted by raw open interest with no lot factor, reported
as currency per 1% move, with calls positive and puts negative.

The one thing that still differs is the **window**. `/gex` fetches 45 strikes
each side of ATM and the study fetches 23, so the two totals differ by the
contribution of the outer strikes `/gex` sees and the study does not. On a
measured NIFTY chain that was 8,170 Cr against 8,415 Cr — the 44 extra strikes
contributed −246 Cr — while every strike in the 47-strike overlap matched
exactly. Compare like for like by comparing per-strike values, not totals.

They used to disagree, and if you are reading an older screenshot or a
comparison written before 2026-08-05, that is why: `/gex` priced Black-76 off
spot rather than the forward, which displaced the walls by the cash-future
basis, and it multiplied open interest by the lot size, which this broker has
already applied — inflating every figure by 65x on NIFTY.

---

## Quick start on NSE index futures

1. Load `NIFTY28JUL26FUT` (`NFO`) — press `Ctrl+K`.
2. Timeframe `5m`.
3. **Studies → Market profile** on. Leave the defaults: day sessions, India
   hours, 30-minute letters, 2-period initial balance.
4. **Studies → Volume profile** on for visible-range volume at price.
5. **Studies → Order flow** on during market hours and let it build; it starts
   empty by design.
6. **Studies → GEX levels** on. Leave the defaults: open interest, nearest
   expiry, strike bars shown, 60-second refresh. The walls appear as dashed
   lines with the dashboard top-right. If it reads "No local cross", the chain
   simply has no gamma flip within ±20% of the forward — that is a normal state,
   not a failure.

Everything you change is saved with the layout, per the persistence rules in
[chart-workspace.md](chart-workspace.md).
