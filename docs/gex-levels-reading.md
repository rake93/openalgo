# Reading the GEX study: Levels, Profile, Bands and Heatmap

A complete guide to the dealer-positioning study on `/charts`.
[`chart-workspace-studies.md`](chart-workspace-studies.md#gex-levels) defines
what each control **does**. This document is about **reading** the output: what
the numbers mean, what makes a verdict flip, which flips are meaningful, and
which are an artefact of how the number is built.

This is decision *support*, not advice. It describes what the study measures and
how the measurement behaves. What you do with it is yours.

Figures marked *(05 Aug)* are a live NIFTY 11AUG26 snapshot from 2026-08-05
around 11:20 IST, both weightings captured seconds apart so the columns are
genuinely comparable. Figures marked *(06 Aug)* come from the 2026-08-06 session.

---

## Contents

1. [The four views, and what each answers](#1-the-four-views-and-what-each-answers)
2. [GEX and DEX: what is actually being computed](#2-gex-and-dex-what-is-actually-being-computed)
3. [Weighting: open interest or volume](#3-weighting-open-interest-or-volume)
4. [The three levels: Call Wall, Put Wall, Zero-Gamma](#4-the-three-levels-call-wall-put-wall-zero-gamma)
5. [The two verdicts answer different questions](#5-the-two-verdicts-answer-different-questions)
6. [Why Regime flips back and forth](#6-why-regime-flips-back-and-forth)
7. [Zero-Gamma is a forward-price level on a spot axis](#7-zero-gamma-is-a-forward-price-level-on-a-spot-axis)
8. [How Sentiment scores, exactly](#8-how-sentiment-scores-exactly)
9. [Why you rarely see Bullish](#9-why-you-rarely-see-bullish)
10. [The strike bars (Profile)](#10-the-strike-bars-profile)
11. [Gamma Bands: the levels through time](#11-gamma-bands-the-levels-through-time)
12. [The Heatmap: the whole profile through time](#12-the-heatmap-the-whole-profile-through-time)
13. [What happens if I stop the recording](#13-what-happens-if-i-stop-the-recording)
14. [Reading recipes](#14-reading-recipes)
15. [Data quality and known limitations](#15-data-quality-and-known-limitations)

---

## 1. The four views, and what each answers

One study, four ways of looking at the same option chain.

| View | Shows | Answers |
|---|---|---|
| **Levels** | Three horizontal lines at their current prices | Where are the structural prices *right now*? |
| **Profile** (strike bars) | A signed bar per strike, in the plot margin | How is exposure distributed across strikes right now? |
| **Bands** | The three levels drawn at every recorded minute | Did those levels move, and did price respect them? |
| **Heatmap** | Every strike of every recorded minute as colour | Where did the exposure sit as the session developed? |

Levels and Profile are computed from a **live** option-chain snapshot each
refresh. Bands and Heatmap are drawn from **recorded** history and show nothing
until the contract is on the recorder's watchlist — see
[section 13](#13-what-happens-if-i-stop-the-recording).

The Heatmap replaces the bar column while it is on. They are the same quantity —
the column is this minute's profile, the field is every recorded minute of it —
so drawing both would encode the same numbers twice in one pane.

---

## 2. GEX and DEX: what is actually being computed

Two different questions about the same option chain.

| | GEX (gamma exposure) | DEX (delta exposure) |
|---|---|---|
| Question | How hard must dealers hedge? | Which way does the book already lean? |
| Sign convention | Dealer's | The open-interest **book's** |
| Units | Currency delta change per **1%** move | Currency notional |
| Drives | Walls, Zero-Gamma, Regime, Sentiment | The bar column only |

### GEX, per strike

```
GEX_k = gamma_k(call) * w_k(call) * F^2 * 0.01
      - gamma_k(put)  * w_k(put)  * F^2 * 0.01
```

Calls positive, puts negative. That encodes the standard assumption that dealers
are long calls and short puts at the index level. It is the study's only source
of sign, because **gamma itself is positive for both legs** — a call and a put at
the same strike have identical gamma.

`F` is the forward, `w` is the weight (open interest or volume). The `F^2 * 0.01`
factor converts a per-unit-move sensitivity into "currency per 1% move". It is
constant across strikes, so **it moves neither the walls nor Zero-Gamma** — it
only changes the units the number is quoted in.

**There is no lot-size multiplication.** The textbook formula carries a contract
multiplier because open interest is conventionally quoted in lots. This broker's
chain already reports OI and volume multiplied by the lot size — verified across
a live NIFTY chain where every one of 188 OI and volume values was an exact
multiple of 65. Multiplying again would inflate every figure by 65x. The
`lot_size` shown on the panel is a **display value only**.

### DEX, per strike

```
DEX_k = (delta_k(call) * w_k(call) + delta_k(put) * w_k(put)) * F
```

**Whose delta this is matters, and it is the single most common misreading.**
This is the *open-interest book's* delta, not the dealer's. Positive DEX means
calls dominate that strike and the book is net long delta. **Dealers stand on the
other side, so dealer delta is the negation of this number.**

There is no dealer sign flip in the DEX formula, and that is deliberate rather
than an oversight. Delta already carries its own sign — a call's delta is
positive, a put's is negative. Applying the dealer constants on top would give:

```
+1 * delta_call * w   ->  positive
-1 * delta_put  * w   ->  negative x negative  ->  ALSO positive
```

Every strike would contribute positively, the total would always be positive, and
the number would carry no direction at all. That is why no published DEX is
defined that way.

Note also the single factor of `F`, against GEX's `F^2 * 0.01`: delta is already
expressed per unit of the underlying, so one multiplication turns it into
notional. Gamma needs the extra conversion first.

### The consequence you must hold on to

**Green means opposite things under the two metrics.**

| Bar colour | Under Gamma | Under Delta |
|---|---|---|
| Green (positive) | Dealers **long** gamma at that strike | The book is long delta, so dealers are **short** |
| Red (negative) | Dealers **short** gamma at that strike | The book is short delta, so dealers are **long** |

Three on-screen labels exist so this is never ambiguous: the readout card's
`Bars` row, an amber caveat under delta, and the bar-column caption. If you are
reading the colours, read those too.

### A worked example

From the 06 Aug session, NIFTY 11AUG26, weighted by open interest:

```
Call GEX   +270,358.67 Cr      (sum of every strike's call leg)
Put GEX    -216,151.75 Cr      (sum of every strike's put leg)
Net GEX     +54,206.92 Cr      Regime: Suppressive
Call Wall        24,800
Put Wall         24,400
Zero-Gamma    24,615.72
Spot          24,658.70
Forward       24,692.00        basis +33.30
lot size             65        (display only - not in the maths)
```

Reading it: the aggregate is positive, so dealers are net long gamma and hedging
**damps** moves. The largest positive concentration sits at 24,800 and the
largest negative at 24,400. Spot is between them, which is the ordinary state.

---

## 3. Weighting: open interest or volume

Both weightings run the same formulas. Only `w_k` changes.

| | Weight by OI | Weight by Volume |
|---|---|---|
| What it is | The **standing book** — every open contract | **Today's flow** — contracts traded this session |
| Stability | High; the book has already netted | Low; intraday flow is two-sided churn |
| At the open | Full | Empty, and builds through the session |
| Best for | Structure, walls, regime | Seeing what is being traded *today* |

A note specific to India: NSE and BSE disseminate open interest **live** in the
tick feed. The US rationale for preferring volume-weighted GEX — that official OI
is a stale prior-night snapshot — does not apply here. Volume weighting gives a
*different read*, not a fix for staleness, which is why OI is the default.

Everything on the panel follows the weighting you select, including PCR and IV
skew, so the whole readout stays internally consistent. The walls, Zero-Gamma and
net GEX will all differ between the two — that is not an inconsistency, it is two
different questions.

*(06 Aug, same minute)*: OI put wall 24,400 against volume put wall 24,500;
OI Zero-Gamma 24,615.72 against volume 24,557.86. The call wall agreed at 24,800.

---

## 4. The three levels: Call Wall, Put Wall, Zero-Gamma

### The walls are strikes

**Call Wall** is the strike with the greatest net GEX. **Put Wall** is the strike
with the least. They are the two largest gamma concentrations in the chain.

- **They can be the same strike.** A single dominant expiry-day strike routinely
  holds the largest call gamma and the largest put gamma at once.
- **They move in steps, never gradually.** A wall sits at a strike until the
  concentration relocates to a *different* strike. Walls that do not move all day
  are normal and are the common case; a wall that jumps is a real event worth
  noticing.
- **A wall on the edge of the fetched window is flagged.** It may be a genuine
  concentration, or it may simply be where the 47-strike window stopped. The
  quality gate turns that flag into a visible caveat.

How they are commonly read: the Call Wall acts as resistance and the Put Wall as
support *while dealers are long gamma*, because hedging into a concentration
sells strength and buys weakness. That relationship is a consequence of the
regime, not a property of the strike — under a negative-gamma regime the same
levels can accelerate moves instead.

### Zero-Gamma is not a strike at all

It is the **price at which aggregate dealer gamma changes sign**. That is a very
different object from "the strike where a running total crosses zero", which can
only ever land on a strike and is a different quantity.

Gamma depends on where the underlying is, so the whole profile has to be rebuilt
at each hypothetical price. The scan:

1. Samples 60 forward levels across +/-20% of the current forward.
2. At each level, recomputes **every** contract's gamma with Black-76.
3. Finds where the aggregate crosses zero, and returns the crossing **nearest the
   forward**.

Two consequences:

- **The nearest crossing, not the first one walking up from the bottom.** A
  profile can flip sign several times; the regime boundary is the adjacent
  crossing, and any other choice would report a boundary you are not standing next
  to.
- **"No local cross" is an ordinary market state**, not an error. If the profile
  does not change sign anywhere near the forward, there is no line to draw. The
  readout says so, and the Band leaves a hole rather than dropping to the axis.

Above Zero-Gamma, dealers are typically long gamma and hedging damps moves. Below
it, short gamma, and hedging extends them. This is why it is worth watching as a
boundary rather than a target.

---

## 5. The two verdicts answer different questions

| | Question | Answer values | Derived from |
|---|---|---|---|
| **Regime** | Will moves be damped or extended? | Suppressive / Amplifying | Sign of net GEX |
| **Sentiment** | Which way? | Bullish / Bearish / Neutral | Three directional signals, never net GEX |

They are independent by design, and disagreement between them is normal rather
than a contradiction. The one reading that is **never** available is direction
from Regime: Amplifying means moves extend in *both* directions, so it is not a
short signal. A widely-copied reading treats negative gamma as bearish; it prints
bearish during an upside squeeze, which is exactly when it is most wrong.

Both verdicts are gamma-only, always — that holds even if the strike bars are
currently set to delta. Switching the bar column to delta does not switch what is
underneath it: Regime, the walls and Sentiment keep reading the gamma profile.

---

## 6. Why Regime flips back and forth

This is the single most important thing to understand about Regime, and it
explains the Amplifying-then-Suppressive flip within minutes.

Net GEX is not a measured quantity. It is the **difference between two very large
numbers of opposite sign**, and how stable it is depends entirely on how close
those two numbers are.

*(05 Aug)*

| | Weight by OI | Weight by Volume |
|---|---:|---:|
| Total call GEX | +42,552.8 Cr | +330,960.6 Cr |
| Total put GEX | -36,989.6 Cr | -329,077.5 Cr |
| **Net GEX** | **+5,563.2 Cr** | **+1,883.1 Cr** |
| Net as a share of gross | **6.99%** | **0.29%** |
| Regime | Suppressive | Suppressive |

On volume weighting the two sides cancel to within **0.29%**. A 0.3% shift in
either leg — a few hundred lots printing at one busy strike — is enough to change
the sign of the result and flip the label:

> Amplifying, net -10,137.53 Cr -> minutes later Suppressive, net +10,008.30 Cr

Those two readings are 20,145 Cr apart on a gross of roughly 600,000 Cr. The
*regime* did not reverse. A 3% wobble in a near-perfect cancellation crossed zero.

**Practical rules:**

- **Judge Regime by margin, not by label.** Net GEX at 0.3% of gross is a coin
  flip that will keep flipping. At 7% it is a reading.
- **Volume weighting flips far more often than OI**, and the table shows why:
  intraday flow is two-sided churn that nets out to almost nothing, while open
  interest is the standing book that has already netted. Volume GEX is ~8x
  *larger* in absolute terms and yet ~24x *tighter* as a net.
- **If you want a stable regime read, use OI.**
- **Cross-check against the Zero-Gamma distance** — but read section 7 first,
  because the distance you can see on screen is not the one Regime uses.

---

## 7. Zero-Gamma is a forward-price level on a spot axis

Worth knowing before you compare the line to the candles, because on a spot index
chart the two can disagree.

Regime is the sign of net GEX evaluated **at the forward**. Zero-Gamma is the
forward at which that sign changes. Both live in forward space and are perfectly
consistent with each other. But the y-axis on `NIFTY NSE_INDEX` is the **spot**
index, and the forward sits above spot by the basis.

*(05 Aug)*

| | Weight by OI | Weight by Volume |
|---|---:|---:|
| Spot | 24,625.70 | 24,620.25 |
| Forward | 24,659.20 | 24,656.50 |
| Zero-Gamma | 24,597.52 | 24,652.85 |
| Forward - Zero-Gamma | **+61.68** | **+3.65** |
| Spot - Zero-Gamma | +28.18 | **-32.60** |

Look at the volume column. The forward is 3.65 points **above** the flip, so
Regime correctly reads Suppressive. But the line is drawn at 24,652.85 while spot
is 24,620.25 — so on screen the level sits **32 points above price**, which reads
as "we are below the flip, so Amplifying". Same data, opposite conclusion, purely
from which price the comparison is made against.

- **On a futures chart the axis is the forward, so the line lines up exactly.**
  This is the cleanest way to use it.
- **On a spot index chart, mentally add the basis** (about +33 points on a 6-day
  expiry; measured +33.30 on 06 Aug) before comparing, or just trust the Regime
  label, which already makes the comparison correctly.
- The offset scales with time to expiry — negligible on expiry day, wider on a
  monthly.

---

## 8. How Sentiment scores, exactly

Three signals vote. Each is independently Bullish (+1), Bearish (-1), Neutral (0)
or Unavailable (drops out entirely).

| Signal | Weight | Bullish when | Bearish when |
|---|---:|---|---|
| **Wall position** | 2 | Spot **above** the Call Wall | Spot **below** the Put Wall |
| **Put-call ratio** | 1 | PCR >= **1.20** | PCR <= **0.80** |
| **IV skew** | 1 | Calls richer by > **1.5** vol points | Puts richer by > **1.5** vol points |

```
score = (2 x walls + pcr + skew) / (total participating weight)

Bullish if score >=  0.34
Bearish if score <= -0.34
Neutral otherwise
```

With all three participating the denominator is 4, so every possible outcome is:

| Wall position | PCR + skew combined | Score | Verdict |
|---|---:|---:|---|
| Bullish | both bullish | +1.00 | **Bullish** |
| Bullish | one bullish, one neutral | +0.75 | **Bullish** |
| Bullish | both neutral, or one of each | +0.50 | **Bullish** |
| Bullish | one bearish, one neutral | +0.25 | Neutral |
| Bullish | both bearish | 0.00 | Neutral |
| Neutral | both bullish | +0.50 | **Bullish** |
| Neutral | one bullish, one neutral | +0.25 | Neutral |
| Neutral | both neutral, or one of each | 0.00 | Neutral |
| Neutral | one bearish, one neutral | -0.25 | Neutral |
| Neutral | both bearish | -0.50 | **Bearish** |
| Bearish | both bullish | 0.00 | Neutral |
| Bearish | one bullish, one neutral | -0.25 | Neutral |
| Bearish | both neutral, or one of each | -0.50 | **Bearish** |
| Bearish | one bearish, one neutral | -0.75 | **Bearish** |
| Bearish | both bearish | -1.00 | **Bearish** |

**The structural consequence: while spot is pinned between the walls — which is
most of the time — the two minor signals must BOTH point the same way to move the
verdict off Neutral.** One of them alone reaches only +/-0.25, inside the +/-0.34
band.

The `agreeing / participating` count on the panel (`2 of 3`) is how many signals
share the final verdict. A Neutral with a low count means the signals cancelled; a
Neutral with a high count means they genuinely all read flat. Those are different
market states wearing the same label.

### A worked flip

*(05 Aug, OI weighting)*

```
Sentiment - Neutral,  score -0.25,  2 of 3 agree
  Wall position  neutral  w2   Pinned between put wall 24400 and call wall 24800 (63% of the way up)
  Put-call ratio neutral  w1   PCR 0.85 by open interest
                               "0.85 clears the 0.80 bearish threshold by 0.05"
  IV skew        bearish  w1   puts 13.6% vs calls 11.8%, richer by 1.8 vol points
```

Score = (2x0 + 0 + -1) / 4 = **-0.25** -> inside the band -> Neutral.

For it to have read **Bearish** minutes earlier, with walls neutral, PCR must also
have been bearish: (2x0 + -1 + -1)/4 = **-0.50**. So the entire flip was **PCR
crossing 0.80**. Nothing else changed.

The panel told you the margin: *"0.85 clears the 0.80 bearish threshold by 0.05"*.
Every signal carries a `why` line generated beside the threshold it describes, and
it is the only place the distance to the next verdict is visible. **When it reads
a small number, expect the verdict to flip.** Hover the Sentiment row to see all
three.

---

## 9. Why you rarely see Bullish

Not a bug. From the table, Bullish requires **either**:

1. **Spot above the Call Wall** — and the other two not both bearish; or
2. **Walls neutral, with PCR >= 1.20 AND calls richer than puts by > 1.5 vol
   points.**

Route 2 is close to unreachable on a NIFTY index chain, because Indian index
options carry a **structural put skew** — downside protection is permanently bid.

*(05 Aug)*

| | Put IV | Call IV | Skew |
|---|---:|---:|---|
| Weight by OI | 13.3% | 11.9% | puts richer by 1.4 |
| Weight by Volume | 13.5% | 11.8% | puts richer by 1.7 |

For the skew signal to turn *bullish* the sign has to invert **and** clear the
band — roughly a 3 vol point swing from the normal state. That happens in a
violent upside squeeze, or on single stocks with event-driven call demand. It does
not happen on a normal NIFTY session.

So the asymmetry is real, and it decomposes as:

- **Bearish is one small step away.** Skew is usually already bearish, so only PCR
  has to slip under 0.80.
- **Bullish is two large steps away.** It needs the skew to invert *and* PCR to
  clear 1.20, or a genuine break above the Call Wall.

**The realistic route to Bullish on an index chart is spot breaking above the Call
Wall.** That is the event worth waiting for, and it carries weight 2, so it alone
produces +0.50.

The thresholds are symmetric; the *market* is not. The composite is reporting a
real feature of index option pricing, not a bias in the code.

---

## 10. The strike bars (Profile)

A signed horizontal bar per strike, drawn in the plot margin against the price
axis, so each bar sits at its own strike.

**What the length means:** the magnitude of that strike's exposure, relative to
the largest bar in the window. **What the direction and colour mean:** the sign —
and the sign means different things under the two metrics, per
[section 2](#2-gex-and-dex-what-is-actually-being-computed).

How to use it:

- **Find the concentrations.** The longest green and longest red bars *are* the
  Call Wall and Put Wall. The bar column is where you see how dominant they are
  compared with their neighbours — a wall that towers over the chain is a much
  harder level than one that barely leads.
- **Look at the shape, not just the extremes.** A profile with one huge spike
  behaves differently from one with exposure smeared across ten strikes. The
  spike gives a sharp pin; the smear gives a broad zone.
- **Hover any bar** to read that strike with **both** metrics at once, plus its
  wall status. This is the fastest way to answer "is this strike big on gamma, on
  delta, or both?"
- **Switch the metric to compare** the same chain's hedging pressure (gamma)
  against its directional lean (delta). They frequently disagree, and the
  disagreement is the information.

Two things the bar column deliberately does **not** do:

- **It contributes nothing to the price scale.** A 47-strike window spans far more
  than the visible price range, so if it reported its own extent it would squash
  the candles into a sliver. Bars clip to the visible range instead, and an
  off-screen level becomes an edge marker.
- **The metric applies to the bar column only.** Walls, Zero-Gamma, Regime and the
  card's GEX rows stay gamma whichever metric is selected.

Bar row height caps at 36px, so on a stretched price axis the bars stay readable
rather than becoming slabs.

---

## 11. Gamma Bands: the levels through time

The same three levels, drawn at every recorded minute instead of only now.

This answers the question a single snapshot cannot: **did the wall move, and did
price respect it before it did?** A Call Wall that has held 24,800 for three hours
while price tested it twice is a different object from one that jumped there
five minutes ago.

How to read them:

- **Solid line = recorded history. Dashed = the current value.** Each level is one
  continuous line: solid where the recorder observed it, dashed beyond that at the
  live value. The dashed portion is clipped away wherever the band covers it, so
  the pair never overprints.
- **Walls step; Zero-Gamma slopes.** A wall holds a strike until it moves to
  another strike, so a diagonal would imply it passed through prices no strike
  ever occupied. Zero-Gamma is an interpolated crossing price that genuinely does
  move continuously, so it is drawn as a curve.
- **A break in the line is a break in the data.** The recorder writes nothing for
  a minute it could not complete, and the band breaks rather than drawing across
  it. A single missed tick does not break the line (the join tolerance is 150
  seconds); two consecutive misses do. An overnight session break exceeds it by
  hours, so yesterday never joins to today.
- **A hole in Zero-Gamma is "no local cross"**, not a dropout — the profile did
  not change sign near the forward during those minutes.

**Wall corridor** (optional, off by default) shades the region between the two
wall bands. That region is the range dealers are hedging inside, and its width
through the session is the thing worth watching — a corridor that narrows is a
tightening pin.

---

## 12. The Heatmap: the whole profile through time

Time across, the strike ladder down, signed exposure as colour. It shares the
price pane deliberately: its y-axis **is** the strike ladder, so a band of colour
lines up with the candles that did or did not break it. In a separate pane you
would be eyeballing two y-axes against each other — doing by hand the comparison
the picture exists to make.

**Colour is diverging**, one hue per sign with the neutral in the middle: teal for
positive, red for negative, the same hues the walls use so colour follows the
entity across the whole study. Intensity is the magnitude relative to the largest
value in the **window** — not per column, which would paint every column's own
maximum at full saturation and erase the change through time that the heatmap
exists to show.

Intensity is square-root compressed on purpose. Net GEX is heavily tailed — a
handful of strikes near the money carry most of the exposure — and on a linear
ramp everything else collapses to invisible, leaving two bright rows and a blank
field.

How to read it:

- **Horizontal bands are persistent structure.** A strike that stays saturated all
  session is a wall that never moved. A band that migrates is exposure relocating.
- **The teal/red boundary is the gamma flip through time.** Watch where it sits
  relative to the candles.
- **A blank column is a minute the recorder missed**, and blank means blank — a
  column is drawn one cadence wide rather than stretched to its neighbour, so an
  outage stays open instead of being painted across. On 06 Aug both of the day's
  outages (a manual stop and a DNS failure) rendered as clean white columns.
- **A blank cell inside a column** is a strike that minute's chain did not carry.
  A recorded near-zero cell keeps a faint floor tint, so "recorded and near zero"
  never looks like "not recorded".
- **A dimmed column was recorded as degraded** — the chain that minute failed a
  quality check, and the picture says so rather than hiding it.

**Downsampling says so.** Above 1,000 columns the grid buckets to 5-minute, and
above 5,000 to 15-minute, selecting a *representative* snapshot per bucket rather
than averaging — averaging across a wall that jumped strike would invent a
concentration at neither strike. The response always carries its resolution,
because a heatmap that quietly thinned itself would look like a market that went
quiet. An ordinary intraday window (about 2.7 sessions) is never thinned.

**Look back** controls how far both recorded overlays reach: 1 hour to 3 days.

---

## 13. What happens if I stop the recording

There are two different actions, and only one of them is reversible.

### Stop recording (keeps history)

The **"Stop recording"** control, or `PATCH /gex/api/gex-series/<id>
{"enabled": false}`.

| What | Effect |
|---|---|
| New snapshots | Stop from the next minute. Nothing further is written. |
| Existing history | **Kept in full.** Bands and Heatmap keep drawing every minute already recorded. |
| Bands and Heatmap | Keep working, but stop advancing. The gap between the last recorded point and now grows, and the band shows it as a break. |
| Live Levels, Profile, walls, Zero-Gamma, Regime, Sentiment | **Unaffected.** These never needed the recorder — they are computed from a live chain fetch. |
| Broker load | Goes **up**, not down. See below. |
| Restarting | Just enable it again. Recording resumes from that minute; the old history is still there, with a gap where it stopped. |

**The broker-load point is the one worth understanding.** While a contract is
being recorded, the study answers from the newest snapshot if it is under 120
seconds old, so five open tabs cost **one** broker call a minute. Stop recording
and every tab goes back to fetching its own chain — so N tabs cost N calls. The
recorder reduces broker load; it does not add to it.

One quirk: for up to 120 seconds after stopping, the study may still answer from
the last recorded snapshot, because the fast path checks the snapshot's age rather
than whether the series is still enabled. It falls back to live fetches after
that.

### Remove the series (destroys history)

The **delete** action, or `DELETE /gex/api/gex-series/<id>`.

This deletes the series **and every snapshot recorded for it**, irreversibly.
**There is no source to rebuild from** — the option chain API returns only
*current* open interest and volume, so recorded history that is deleted is gone
for good. Use "stop recording" unless you actually want the history destroyed.

### Things that are not "stopped"

- **Out of hours the recorder is silent by design.** It only runs during session
  hours, validated against the exchange calendar. An empty overnight is correct
  behaviour, not a stopped recorder.
- **History ages out on its own.** A daily prune at 03:30 IST removes snapshots
  older than `GEX_RECORDER_RETENTION_DAYS` (default 30). Bands and Heatmap will
  never show more than the retention window.
- **A weekly series shows less history than you might expect.** History is scoped
  to one *resolved* contract, because a `nearest` series rolls weekly and splicing
  across a roll would draw a wall jump at every roll that is the book changing,
  not the market moving. So a weekly contract shows only as much history as that
  contract has existed — about five sessions, not thirty.
- **A failed refresh does not clear what is drawn.** Bands and Heatmap keep
  showing the recorded past; it does not become wrong because one request failed.

---

## 14. Reading recipes

**"Is this Regime reading trustworthy?"**
Compare net GEX against the gross of the two legs shown above it. Under ~1%, treat
the label as noise and switch to OI weighting. Confirm with the
forward-to-Zero-Gamma distance (section 7) rather than the on-screen gap.

**"Regime says Suppressive but price is running."**
Check where spot is relative to the walls. Suppressive means dealers damp moves
*within* the gamma structure; a break above the Call Wall leaves that structure,
which is why wall position carries double weight in Sentiment.

**"Sentiment is Neutral — is that flat, or cancelled?"**
Read the count. `Neutral 3 of 3` means all three signals genuinely read flat.
`Neutral 2 of 3` with a score of +/-0.25 means one signal is already pointing and
lacks a partner. The second is one threshold crossing from a directional verdict.

**"Which weighting should I use?"**
OI for structure and regime stability. Volume for what is being traded today — and
it is empty at the open and builds through the session, so early-session volume
readings are thin by construction.

**"The walls have not moved all day."**
Normal, and a feature. Walls are strikes, so they only move when the largest gamma
concentration relocates. A wall that *does* jump is a real event — and Bands is
where you see exactly when it jumped.

**"Is this wall real or a window edge?"**
Check the Data status row for the edge caveat. A wall on the first or last strike
of the fetched window may just be where the window stopped.

**"Gamma and delta disagree about a strike."**
That is normal and informative: gamma says how hard dealers must hedge there,
delta says which way the book leans. Remember the dealer negation on delta.

**"Did price respect this level, or am I pattern-matching after the fact?"**
This is what Bands and the Heatmap exist for. A level you can only see at its
current price tells you nothing about whether it held an hour ago. Turn Bands on
and look at where the line was when price got there.

**"The heatmap has a white stripe through it."**
That is a recorded outage, not a quiet market. Cross-check `log/errors.jsonl` for
that minute if you want to know why.

---

## 15. Data quality and known limitations

**Quality verdicts.** Every snapshot carries one, per weighting — a chain can be
good on open interest and degraded on volume.

| Verdict | Meaning |
|---|---|
| `good` | Draw normally. |
| `degraded` | Draw, with the caveat shown. The heatmap dims these columns. |
| `unusable` | Do not draw levels at all. |

**Zero-Gamma is drawn in forward space on a spot-price axis** (section 7). On a
6-day NIFTY expiry that is about 33 points, growing with time to expiry. The
Regime label is unaffected — it makes the comparison in forward space correctly —
but the visual gap between the line and the candles is overstated by the basis on
spot index charts. It is exact on a futures chart.

**The strike window is 47 strikes** — 23 either side of ATM, plus ATM, which is
94 option symbols. That is a hard broker limit rather than a preference: it is
sized to fit the multiquote open-interest bucket of 100 symbols. Exceeding it does
not raise an error — it silently returns **empty** open interest, which would zero
every exposure in the study with nothing anywhere to say so. This is why the study
does not offer a wider window, and why the number must never be raised.

**The Heatmap's strike axis can show 48 rows, not 47.** The window is centred on
ATM, so when spot moves far enough the chain shifts by a strike mid-session. The
axis is the union of every strike seen in the window, so it grows by one while
each individual minute still carries 47 — which is exactly why one cell per column
is blank. Measured on 06 Aug: a 48-strike axis over 114 columns with precisely one
blank per column.

**Index symbols are quote-only.** Use a futures contract (`NIFTY28JUL26FUT`) when
you want the Zero-Gamma line to align exactly.

**Bands and Heatmap show nothing until recording is on.** That is not a broken
control — the Studies panel says whether the contract is being recorded and offers
to start. See [section 13](#13-what-happens-if-i-stop-the-recording).
