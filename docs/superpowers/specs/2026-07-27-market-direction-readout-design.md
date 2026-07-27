# Market direction readout for the /charts studies

Date: 2026-07-27
Status: approved (placement option B), pending implementation review

## Purpose

The studies in `/charts` each show data but none of them state a conclusion. A
trader reading a footprint, a volume profile and a TPO profile has to hold six
things in their head and combine them. This adds a standing panel that says who
is in control, per study and overall.

The panel is read-only. It introduces no new market data requests: every input
either already arrives on the existing Depth subscription or is already computed
by the profile tier.

## The honesty constraint

Delta and cumulative delta cannot be exact here, and the design must say so
rather than imply otherwise.

A footprint needs each trade classified as hitting the bid or lifting the ask.
Dhan's API publishes no aggressor classification anywhere — neither the live
market feed (`https://dhanhq.co/docs/v2/live-market-feed/`, packet types Ticker /
Quote / Full / OI / Prev Close) nor the 20-level full depth feed
(`https://dhanhq.co/docs/v2/full-market-depth/`, which carries only Price,
Quantity and No. of Orders and has no last traded price at all). Every OpenAlgo
broker feed is a *snapshot* feed, not a tape.

So the panel splits its signals into two groups and labels them:

- **Exact** — read straight from the exchange, no inference. OI buildup, book
  pressure, LTP vs VWAP, value-area migration.
- **Inferred** — reconstructed with the quote rule against a lagged best
  bid/ask, at depth-packet granularity rather than trade granularity. Delta, CVD.

Inferred signals carry half the weight of exact ones in the composite, and the
panel states the caveat inline.

## Signals

### Exact

**1. OI buildup** (F&O only). The classic price/OI matrix, comparing the live OI
on the depth payload against the **previous session's close** — which is what
"change in OI" means on the Indian exchanges, so the reading matches every OI
table the user would check it against.

That baseline is available because OpenAlgo's history API returns an `oi` column
per bar (`services/history_service.py` guarantees it, zero-filling instruments
that have none). The library's `Bar` and `mapHistoryResponse` were dropping it at
the parse boundary — the same class of gap as `ltq` — so `Bar` gains an optional
`oi`, and the resampler carries it as a *level* (last value in a bucket) rather
than summing it the way it sums volume. The baseline is then the last loaded bar
before today's session anchor, with its close as the matching reference price:
both sides of the comparison come off one bar.

Two fallbacks, in order: today's first bar when only today is loaded, then the
first live observation when the feed reports no historical OI at all. The panel
states which of the two it used, because "since the previous close" and "since
you connected" are different claims.

| Price | OI | Reading | Bias |
| --- | --- | --- | --- |
| up | up | long buildup | bullish |
| down | up | short buildup | bearish |
| down | down | long unwinding | bearish |
| up | down | short covering | bullish |

Neutral when either change is within a dead band (0.05% price, 0.5% OI) —
otherwise noise flips the verdict every tick.

**2. Book pressure.** Total pending buy quantity against total pending sell
quantity. `TBQ / TSQ >= ratio` is bullish, `<= 1/ratio` bearish, else neutral.
Default ratio 1.25.

**3. LTP vs VWAP.** The day's average traded price is on the depth payload.
Trading above it is bullish, below bearish, with a dead band of one tick.

**4. Value-area migration.** From the market profile the studies tier already
computes: compare the latest session's POC and value area against the previous
session's. Higher value accepted is bullish, lower bearish, overlapping neutral.
Derived from OHLCV, so exact.

### Inferred

**5. Delta.** The current bar's delta from the live footprint tape, as a share of
the bar's volume. Bullish above +15%, bearish below -15%.

**6. CVD.** The slope of cumulative delta across the last 10 footprint bars.
Rising is bullish, falling bearish, flat neutral.

## Composite

Each signal returns `bullish | bearish | neutral | unavailable`. Score is a
weighted sum with exact signals at 1.0 and inferred at 0.5; bullish adds, bearish
subtracts, neutral and unavailable add nothing. The verdict is bullish above
+1.0, bearish below -1.0, and neutral between — reported alongside the vote count
("BULLISH, 4 of 6") and the participating weight so a verdict resting on two
signals cannot masquerade as one resting on six.

## Graceful degradation is mandatory, not a nicety

Field availability in *depth* mode varies sharply across the 34 adapters. Checked
directly against the adapter sources:

| Broker | OI | TBQ/TSQ | VWAP | LTQ |
| --- | --- | --- | --- | --- |
| Dhan | yes | yes | yes | yes |
| Zerodha | yes | yes | yes | yes |
| Angel | yes | quote mode only | quote mode only | yes |
| Flattrade | no | yes | yes | yes |
| Upstox | no | yes | yes | no |
| Fyers | no | no | no | no |

Every signal therefore reports `unavailable` independently when its input is
absent, renders as `—`, and is excluded from the composite. No signal may assume
another's input exists, and a missing field must never read as a zero — a zero OI
change is not the same as no OI.

## Instrument coverage

The panel works on every instrument the workspace can load, but how much of it
lights up depends on what the instrument *has* — not on special-casing. Two
existing sets in `workspace.ts` already draw the lines: `DERIVATIVE_EXCHANGES`
(NFO, BFO, CDS, BCD, MCX, NCO, NCDEX) and `QUOTE_ONLY` (NSE_INDEX, BSE_INDEX,
MCX_INDEX, GLOBAL_INDEX).

| Instrument | Feed | OI | Book | VWAP | Value area | Delta / CVD | Live signals |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Equity (NSE, BSE) | Depth | no OI exists | yes | yes | yes | yes | 5 of 6 |
| Futures (NFO, BFO, MCX, CDS) | Depth | yes | yes | yes | yes | yes | 6 of 6 |
| Options (NFO, BFO) | Depth | yes | yes | yes | yes | yes | 6 of 6 |
| Index (NSE_INDEX, ...) | LTP only | no | no book | no | **yes** | no tape | 1 of 6 |

Equity has no OI at all, so that signal is unavailable there by definition rather
than by feed gap — and a missing OI must not be read as an OI change of zero.

Indices are the interesting case. They subscribe LTP-only (`quoteOnly`), so there
is no book, no VWAP, no OI, and no traded quantity to build a footprint from —
five of the six signals are unavailable. Value-area migration still works,
because `pocAndValueArea` ranks price levels by **TPO period count**, not by
volume: market profile measures time at price and needs only OHLC. (Volume
profile, by contrast, genuinely has nothing to work with there.) A one-signal
composite is honest as long as the panel shows the participating count, which is
why that count is part of the verdict rather than a decoration.

**Options read in their own premium.** For a charted option the OI matrix, book
pressure, VWAP and delta all describe that contract, so a "long buildup" verdict
means longs building *in that option*. For a call that coincides with bullishness
in the underlying; for a put it is the opposite. Rather than silently invert the
sign — which would make the panel disagree with the chart the user is looking at —
the bias always describes the charted instrument, and the panel adds a note on
`PE` symbols that a rising put implies a falling underlying. Symbol suffix `CE` /
`PE` per OpenAlgo's option format is enough to detect this.

## Architecture

Three units with narrow interfaces, so each is testable alone.

**`lib/charts/direction.ts` — `DirectionEngine`.** Pure computation. Takes a
plain input snapshot, returns a plain verdict object. No chart, no DOM, no
network, no timers. This is where every rule above lives, and where the tests
concentrate.

```ts
interface DirectionInputs {
  hasOi: boolean
  oi?: number; sessionOpenOi?: number
  price?: number; sessionOpenPrice?: number
  totalBuyQty?: number; totalSellQty?: number
  vwap?: number; tick?: number
  valueArea?: { poc: number; vah: number; val: number }
  prevValueArea?: { poc: number; vah: number; val: number }
  barDelta?: number; barVolume?: number
  cvdSeries?: readonly number[]
}

interface DirectionSignal {
  key: string; label: string; detail: string
  bias: 'bullish' | 'bearish' | 'neutral' | 'unavailable'
  exact: boolean
}

interface DirectionVerdict {
  composite: 'bullish' | 'bearish' | 'neutral'
  score: number; agreeing: number; participating: number
  signals: DirectionSignal[]
}
```

**`lib/charts/workspace.ts` — collection only.** Accumulates the session's
opening OI and price, holds the latest depth-derived fields, reads the value area
off `ProfileManager` and the delta/CVD off the footprint tape, and exposes a
`direction` getter that calls the engine. It stores no verdict logic.

**`components/charts/workspace/DirectionPanel.tsx` — presentation only.** Renders
a `DirectionVerdict`. No computation, no data access.

### Plumbing

`MarketDepth` in the library gains `oi`, `totalBuyQty`, `totalSellQty` and `atp`,
and `parseMessage` extracts them, accepting the spelling variants the adapters
use (`oi` / `open_interest`, `average_price` / `atp`). `Bar` gains an optional
`oi` so the history baseline survives too. This mirrors the `ltq` and `volume`
fields already added, and is the same class of gap throughout: the data reaches
the proxy and is dropped at the parse boundary.

On the platform side, Dhan's depth-mode payload was missing `last_quantity`,
`average_price`, `total_buy_quantity` and `total_sell_quantity` even though
`_parse_full_packet` already read all four off the wire. Added, using the key
names Zerodha, Angel and the Noren brokers already emit so consumers stay
broker-agnostic.

### Data flow

```
depth packet -> parseMessage -> MarketDepth{oi, tbq, tsq, atp, ltq, volume}
             -> workspace.onDepth  (store fields, seed session opens)
             -> workspace.onTick   (feed footprint, per the existing fix)

ProfileManager.footprintTape  --\
market profile sessions       ---> workspace.direction -> DirectionEngine
depth-derived fields          --/                            |
                                                    DirectionVerdict
                                                             |
                                          DirectionPanel (dock tab)
```

### UI

A third dock tab beside Studies and Trade, mutually exclusive with them exactly
as those two already are with each other (`dock: 'studies' | 'trade' |
'direction' | 'none'`). Layout: composite verdict at the top, then the exact
group, then the inferred group under its caveat. Bias is conveyed by label and
sign as well as colour, never colour alone.

## Error handling

The engine is total: every input is optional and every branch returns a bias, so
malformed or partial input yields `unavailable` rather than throwing. The panel
renders whatever it is given. A missing footprint tape (order flow switched off)
makes the two inferred signals unavailable, which is correct — not an error.

## Testing

The market is closed during implementation, so correctness rests on tests rather
than observation.

1. **Engine unit tests** — every cell of the OI matrix, each dead band from both
   sides, each signal's unavailable path, and the composite's weighting including
   the case where only inferred signals participate.
2. **Parser tests** — the new depth fields, each spelling variant, and absent
   fields staying `undefined` rather than becoming `0`.
3. **Replay harness** (`orderflow-replay.test.ts`) — a deterministic sequence of
   synthetic depth packets fed through the real path (`parseMessage` →
   `ProfileManager` → `readDirection`) asserting the resulting footprint cells,
   per-price bid/ask split, CVD across bars, and the verdict. This substitutes for
   a live session, and pins three things that are otherwise only visible during
   market hours: prints classified against the pre-trade quote, traded quantity
   differenced out of cumulative volume rather than summed from the sticky
   last-traded quantity, and graceful degradation on a feed that omits fields.

**Status:** 538 library tests and 133 workspace tests pass, including 21 engine
unit tests and 7 replay cases. One pre-existing failure elsewhere in the app
(`navigation.test.ts` expects 9 nav items against 10) is unrelated and untouched.

## Out of scope

Alerts and notifications on flips; persisting the panel's thresholds to the saved
layout; historical backfill of delta or CVD (impossible without a tape);
20-level depth (NSE-only, 50 instruments, and it carries no traded quantity).
