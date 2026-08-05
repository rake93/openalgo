# Reading GEX Levels: Regime, Sentiment and why they move

[`chart-workspace-studies.md`](chart-workspace-studies.md#gex-levels) defines
what each level **is**. This document is about **reading** them: what makes a
verdict flip, which flips are meaningful, and which are an artefact of how the
number is built.

Every figure below is a live NIFTY 11AUG26 snapshot taken on 2026-08-05 at
around 11:20 IST, both weightings captured seconds apart, so the two columns are
genuinely comparable.

---

## 1. The two verdicts answer different questions

| | Question | Answer values | Derived from |
|---|---|---|---|
| **Regime** | Will moves be damped or extended? | Suppressive / Amplifying | Sign of net GEX |
| **Sentiment** | Which way? | Bullish / Bearish / Neutral | Three directional signals, never net GEX |

They are independent by design, and disagreement between them is normal rather
than a contradiction. The one reading that is **never** available is direction
from Regime: Amplifying means moves extend in *both* directions, so it is not a
short signal.

Both verdicts are gamma-only, always — that holds even if the chart's strike
bars are currently set to delta. Switching the bar column to delta does not
switch what is underneath it: Regime, the walls and Sentiment keep reading the
gamma profile described in this document. See
[Metric: gamma or delta](chart-workspace-studies.md#metric-gamma-or-delta) for
what the bar-column setting does and does not change, and for the sign flip
between the open-interest book's delta and the dealer's.

---

## 2. Why Regime flips back and forth — the number is a near-cancellation

This is the single most important thing to understand about Regime, and it
explains the Amplifying-then-Suppressive flip within minutes.

Net GEX is not a measured quantity. It is the **difference between two very
large numbers of opposite sign**, and how stable it is depends entirely on how
close those two numbers are.

| | Weight by OI | Weight by Volume |
|---|---:|---:|
| Total call GEX | +42,552.8 Cr | +330,960.6 Cr |
| Total put GEX | −36,989.6 Cr | −329,077.5 Cr |
| **Net GEX** | **+5,563.2 Cr** | **+1,883.1 Cr** |
| Net as a share of gross | **6.99%** | **0.29%** |
| Regime | Suppressive | Suppressive |

On volume weighting the two sides cancel to within **0.29%**. A 0.3% shift in
either leg — a few hundred lots printing at one busy strike — is enough to
change the sign of the result and flip the label. That is exactly what you saw:

> Amplifying, net −10,137.53 Cr → minutes later Suppressive, net +10,008.30 Cr

Those two readings are 20,145 Cr apart on a gross figure of roughly 600,000 Cr.
The *regime* did not reverse. A 3% wobble in a near-perfect cancellation crossed
zero.

**Practical rules:**

- **Judge Regime by margin, not by label.** Net GEX at 0.3% of gross is a coin
  flip that will keep flipping. At 7% it is a reading.
- **Volume weighting flips far more often than OI**, and the table shows why:
  intraday flow is two-sided churn that nets out to almost nothing, while open
  interest is the standing book that has already netted. Note also that volume
  GEX is ~8x *larger* in absolute terms and yet ~24x *tighter* as a net.
- **If you want a stable regime read, use OI.** Use volume to see what is being
  traded *today*, not to decide what regime you are in.
- **Cross-check against the Zero-Gamma distance.** When the forward sits a few
  points from the flip, expect the label to oscillate. Read section 3 first —
  the distance you can see on the chart is not the one Regime uses.

---

## 3. Zero-Gamma is a forward-price level, drawn on a spot-price axis

Worth knowing before you compare the line to the candles, because on a spot
index chart the two can disagree.

Regime is the sign of net GEX evaluated **at the forward**. Zero-Gamma is the
forward at which that sign changes — the scan varies the forward and finds the
crossing. Both live in forward space and are perfectly consistent with each
other. But the chart's y-axis on `NIFTY NSE_INDEX` is the **spot** index, and
the forward sits above spot by the basis.

From the same snapshot:

| | Weight by OI | Weight by Volume |
|---|---:|---:|
| Spot | 24,625.70 | 24,620.25 |
| Forward | 24,659.20 | 24,656.50 |
| Zero-Gamma | 24,597.52 | 24,652.85 |
| Forward − Zero-Gamma | **+61.68** | **+3.65** |
| Spot − Zero-Gamma | +28.18 | **−32.60** |

Look at the volume column. The forward is 3.65 points **above** the flip, so
Regime correctly reads Suppressive. But the line is drawn at 24,652.85 while
spot is 24,620.25 — so on screen the level sits **32 points above price**, which
reads as "we are below the flip, so Amplifying". Same data, opposite conclusion,
purely from which price the comparison is made against.

This is precisely the case in the screenshot showing Zero-Gamma 24,667.98 above
spot 24,647.80 while the panel reads Suppressive. Nothing is miscomputed.

- **On a futures chart the axis is the forward, so the line lines up exactly.**
  This is the cleanest way to use it.
- **On a spot index chart, mentally add the basis** (about +35 points on this
  6-day expiry) to price before comparing, or just trust the Regime label, which
  already makes the comparison correctly.
- The offset scales with time to expiry — negligible on expiry day, wider on a
  monthly.

---

## 4. How Sentiment scores, exactly

Three signals vote. Each is independently Bullish (+1), Bearish (−1), Neutral
(0) or Unavailable (drops out entirely).

| Signal | Weight | Bullish when | Bearish when |
|---|---:|---|---|
| **Wall position** | 2 | Spot **above** the Call Wall | Spot **below** the Put Wall |
| **Put-call ratio** | 1 | PCR ≥ **1.20** | PCR ≤ **0.80** |
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
| Neutral | one bearish, one neutral | −0.25 | Neutral |
| Neutral | both bearish | −0.50 | **Bearish** |
| Bearish | both bullish | 0.00 | Neutral |
| Bearish | one bullish, one neutral | −0.25 | Neutral |
| Bearish | both neutral, or one of each | −0.50 | **Bearish** |
| Bearish | one bearish, one neutral | −0.75 | **Bearish** |
| Bearish | both bearish | −1.00 | **Bearish** |

**The structural consequence: while spot is pinned between the walls — which is
most of the time — the two minor signals must BOTH point the same way to move
the verdict off Neutral.** One of them alone reaches only ±0.25, inside the
±0.34 band.

The `agreeing / participating` count on the panel (`2 of 3`) is how many signals
share the final verdict. A Neutral with a low count means the signals cancelled;
a Neutral with a high count means they genuinely all read flat. Those are
different market states wearing the same label.

---

## 5. Your Bearish-to-Neutral flip, decoded

The OI-weighted panel showed:

```
Sentiment - Neutral,  score -0.25,  2 of 3 agree
  Wall position  neutral  w2   Pinned between put wall 24400 and call wall 24800 (63% of the way up)
  Put-call ratio neutral  w1   PCR 0.85 by open interest
                               "0.85 clears the 0.80 bearish threshold by 0.05"
  IV skew        bearish  w1   puts 13.6% vs calls 11.8%, richer by 1.8 vol points
```

Score = (2×0 + 0 + −1) / 4 = **−0.25** → inside the band → Neutral.

For it to have read **Bearish** earlier, with walls neutral, the table above says
PCR must also have been bearish: (2×0 + −1 + −1)/4 = **−0.50**. So the entire
flip was **PCR crossing 0.80**. Nothing else changed.

And the panel told you the margin: *"0.85 clears the 0.80 bearish threshold by
0.05"*. That `why` line exists for exactly this — it is the only place the
distance to the next verdict is visible. When it reads a small number, expect
the verdict to flip.

---

## 6. Why you have never seen Bullish

Not a bug. From the table, Bullish requires **either**:

1. **Spot above the Call Wall** — and the other two not both bearish; or
2. **Walls neutral, with PCR ≥ 1.20 AND calls richer than puts by > 1.5 vol
   points.**

Route 2 is close to unreachable on a NIFTY index chain, because Indian index
options carry a **structural put skew** — downside protection is permanently
bid. From today's snapshot:

| | Put IV | Call IV | Skew |
|---|---:|---:|---|
| Weight by OI | 13.3% | 11.9% | puts richer by 1.4 |
| Weight by Volume | 13.5% | 11.8% | puts richer by 1.7 |

For the skew signal to turn *bullish* the sign has to invert **and** clear the
band — roughly a 3 vol point swing from the normal state. That happens in a
violent upside squeeze, or on single stocks with event-driven call demand. It
does not happen on a normal NIFTY session.

So the asymmetry you have noticed is real, and it decomposes as:

- **Bearish is one small step away.** Skew is usually already bearish, so only
  PCR has to slip under 0.80. Today's OI PCR was 0.85–0.89 — that close.
- **Bullish is two large steps away.** It needs the skew to invert *and* PCR to
  clear 1.20, or a genuine break above the Call Wall.

**The realistic route to Bullish on an index chart is spot breaking above the
Call Wall** — 24,800 in every snapshot here. That is the signal worth waiting
for, and it carries weight 2, so it alone produces +0.50.

The thresholds are symmetric; the *market* is not. The composite is reporting a
real feature of index option pricing, not a bias in the code.

---

## 7. Reading recipes

**"Is this Regime reading trustworthy?"**
Compare net GEX against the gross of the two legs shown above it. Under ~1%,
treat the label as noise and switch to OI weighting. Confirm with the
forward-to-Zero-Gamma distance (section 3) rather than the on-screen gap.

**"Regime says Suppressive but price is running."**
Check where spot is relative to the walls. Suppressive means dealers damp moves
*within* the gamma structure; a break above the Call Wall leaves that structure,
which is why wall position carries double weight in Sentiment.

**"Sentiment is Neutral — is that flat, or cancelled?"**
Read the count. `Neutral 3 of 3` means all three signals genuinely read flat.
`Neutral 2 of 3` with a score of ±0.25 means one signal is already pointing and
lacks a partner. The second is one threshold crossing from a directional verdict.

**"Which weighting should I use?"**
OI for structure and regime stability. Volume for what is being traded today —
and it is empty at the open and builds through the session, so early-session
volume readings are thin by construction. Both PCR and skew are computed on the
weighting you select, so the whole panel stays internally consistent.

**"The walls have not moved all day."**
Normal, and a feature. Walls are strikes, so they only move when the largest
gamma concentration relocates to a different strike. 24,800 and 24,500 held
across every snapshot here. A wall that *does* jump is a real event.

---

## 8. Known limitation

Zero-Gamma is drawn in forward space on a spot-price axis (section 3). On a
6-day NIFTY expiry that is about 35 points, and it grows with time to expiry.
The Regime label is unaffected — it makes the comparison in forward space
correctly — but the visual gap between the line and the candles is overstated by
the basis on spot index charts. It is exact on a futures chart.
