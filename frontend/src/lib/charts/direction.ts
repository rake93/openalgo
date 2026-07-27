/**
 * Market direction readout for the /charts studies.
 *
 * Turns the studies' raw numbers into a stated bias: who is in control, per
 * signal and overall. Pure — plain inputs to a plain verdict, no chart, no feed,
 * no DOM — so every rule below is pinned by unit tests instead of being judged on
 * a live session.
 *
 * Exact against inferred
 *   Four signals come straight from the exchange and need no interpretation of a
 *   trade tape: open-interest buildup, pending book pressure, price against the
 *   day's VWAP, and value-area migration (which market profile derives from OHLC
 *   by counting time at price). Two — bar delta and cumulative delta — are
 *   reconstructed with the quote rule at depth-packet granularity, because no
 *   OpenAlgo broker feed publishes an aggressor-classified tape. The inferred pair
 *   carry half the weight and the panel labels them, so a reader can tell which
 *   part of the verdict is measurement and which part is reconstruction.
 *
 * Availability is a first-class outcome
 *   Field coverage varies sharply across the broker adapters, and instrument
 *   classes differ more: equity has no open interest at all, and an index is
 *   quote-only, so it has no book, no VWAP and nothing to build a footprint from.
 *   Every signal therefore reports `unavailable` independently and is excluded
 *   from the composite. A missing input never reads as a zero — no open interest
 *   is not the same as open interest that did not change.
 */

export type Bias = 'bullish' | 'bearish' | 'neutral' | 'unavailable'

export interface DirectionInputs {
  /** Whether the instrument has open interest at all (derivatives only). */
  hasOi?: boolean
  oi?: number
  price?: number
  /**
   * What `oi` and `price` are compared against — normally the **previous
   * session's close**, which is what "change in OI" means on the Indian
   * exchanges. Both must come from the same instant or the co-movement read is
   * meaningless, so the host takes them off one bar.
   */
  baselineOi?: number
  baselinePrice?: number
  /** Total *pending* order quantity each side, as the exchange reports it. */
  totalBuyQty?: number
  totalSellQty?: number
  /** The day's average traded price. */
  vwap?: number
  tick?: number
  valueArea?: ValueArea
  prevValueArea?: ValueArea
  /** Current bar's footprint delta and volume. */
  barDelta?: number
  barVolume?: number
  /** Cumulative delta, oldest first. */
  cvdSeries?: readonly number[]
}

export interface ValueArea {
  poc: number
  vah: number
  val: number
}

export interface DirectionSignal {
  key: 'oi' | 'book' | 'vwap' | 'value' | 'delta' | 'cvd'
  label: string
  /** The reading in words — "long buildup", "TBQ 1.4x TSQ". */
  detail: string
  bias: Bias
  /** False for the two quote-rule signals; they weigh half and are labelled. */
  exact: boolean
}

export interface DirectionVerdict {
  composite: 'bullish' | 'bearish' | 'neutral'
  /** Weighted net, normalised to the participating weight: -1..1. */
  score: number
  /** Signals matching the composite, and signals with any reading at all. */
  agreeing: number
  participating: number
  signals: DirectionSignal[]
}

/* ── thresholds ──────────────────────────────────────────────────────────── */

/** Price move below this is noise, not a trend leg (0.05%). */
const OI_PRICE_BAND = 0.0005
/** OI change below this is churn (0.5%). */
const OI_BAND = 0.005
/** Pending-quantity imbalance needed to call a side. */
const BOOK_RATIO = 1.25
/** Bar delta as a share of bar volume needed to call a side. */
const DELTA_SHARE = 0.15
/** Share of gross CVD movement that must be net before it counts as a trend. */
const CVD_EFFICIENCY = 0.3
/** Bars of cumulative delta the CVD trend is measured over. */
const CVD_WINDOW = 10
/** Normalised score needed for a directional composite — a third net agreement. */
const COMPOSITE_BAND = 0.34

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

const off = (key: DirectionSignal['key'], label: string, exact: boolean): DirectionSignal => ({
  key,
  label,
  detail: '—',
  bias: 'unavailable',
  exact,
})

/* ── exact signals ───────────────────────────────────────────────────────── */

/**
 * The price/open-interest matrix, over the window since the baseline.
 *
 * | price | OI   | reading        | bias    |
 * | up    | up   | long buildup   | bullish |
 * | down  | up   | short buildup  | bearish |
 * | down  | down | long unwinding | bearish |
 * | up    | down | short covering | bullish |
 *
 * For a charted *option* this describes that contract, not the underlying: longs
 * building in a put is bullish for the put and bearish for the index beneath it.
 * The panel notes that rather than silently inverting the sign, which would make
 * the verdict disagree with the chart the reader is looking at.
 */
function oiSignal(i: DirectionInputs): DirectionSignal {
  const label = 'OI buildup'
  if (i.hasOi !== true) return off('oi', label, true)
  if (!num(i.oi) || !num(i.baselineOi) || i.baselineOi <= 0) return off('oi', label, true)
  if (!num(i.price) || !num(i.baselinePrice) || i.baselinePrice <= 0) {
    return off('oi', label, true)
  }

  const priceChg = (i.price - i.baselinePrice) / i.baselinePrice
  const oiChg = (i.oi - i.baselineOi) / i.baselineOi
  if (Math.abs(priceChg) < OI_PRICE_BAND || Math.abs(oiChg) < OI_BAND) {
    return { key: 'oi', label, detail: 'flat', bias: 'neutral', exact: true }
  }

  const up = priceChg > 0
  const oiUp = oiChg > 0
  const reading = up
    ? oiUp
      ? 'Long buildup'
      : 'Short covering'
    : oiUp
      ? 'Short buildup'
      : 'Long unwinding'
  return {
    key: 'oi',
    label,
    detail: `${reading} (OI ${oiUp ? '+' : ''}${(oiChg * 100).toFixed(1)}%)`,
    bias: up ? 'bullish' : 'bearish',
    exact: true,
  }
}

/** Pending buy quantity against pending sell quantity — demand against supply. */
function bookSignal(i: DirectionInputs): DirectionSignal {
  const label = 'Book pressure'
  const { totalBuyQty: b, totalSellQty: s } = i
  if (!num(b) || !num(s) || b <= 0 || s <= 0) return off('book', label, true)

  if (b / s >= BOOK_RATIO) {
    return {
      key: 'book',
      label,
      detail: `Buy ${(b / s).toFixed(2)}x sell`,
      bias: 'bullish',
      exact: true,
    }
  }
  if (s / b >= BOOK_RATIO) {
    return {
      key: 'book',
      label,
      detail: `Sell ${(s / b).toFixed(2)}x buy`,
      bias: 'bearish',
      exact: true,
    }
  }
  return { key: 'book', label, detail: 'Balanced', bias: 'neutral', exact: true }
}

/** Trading above or below the day's average traded price. */
function vwapSignal(i: DirectionInputs): DirectionSignal {
  const label = 'LTP vs VWAP'
  if (!num(i.price) || !num(i.vwap) || i.vwap <= 0) return off('vwap', label, true)

  const diff = i.price - i.vwap
  const band = num(i.tick) && i.tick > 0 ? i.tick : 0
  if (Math.abs(diff) <= band) {
    return { key: 'vwap', label, detail: 'At VWAP', bias: 'neutral', exact: true }
  }
  return {
    key: 'vwap',
    label,
    detail: `${diff > 0 ? '+' : ''}${diff.toFixed(2)}`,
    bias: diff > 0 ? 'bullish' : 'bearish',
    exact: true,
  }
}

/**
 * Where value moved between the last two sessions.
 *
 * A value area entirely clear of the previous one is acceptance at a new level —
 * the strong form. Overlapping areas are balance, so the POC decides, with a dead
 * band of a tenth of the prior area's width to keep a one-row shift from counting.
 */
function valueSignal(i: DirectionInputs): DirectionSignal {
  const label = 'Value area'
  const va = i.valueArea
  const prev = i.prevValueArea
  if (!va || !prev || !num(va.poc) || !num(prev.poc)) return off('value', label, true)

  if (va.val > prev.vah) {
    return { key: 'value', label, detail: 'Accepting higher', bias: 'bullish', exact: true }
  }
  if (va.vah < prev.val) {
    return { key: 'value', label, detail: 'Accepting lower', bias: 'bearish', exact: true }
  }

  const band = Math.max(0, (prev.vah - prev.val) * 0.1)
  const shift = va.poc - prev.poc
  if (Math.abs(shift) <= band) {
    return { key: 'value', label, detail: 'Balanced', bias: 'neutral', exact: true }
  }
  return {
    key: 'value',
    label,
    detail: `POC ${shift > 0 ? 'higher' : 'lower'}`,
    bias: shift > 0 ? 'bullish' : 'bearish',
    exact: true,
  }
}

/* ── inferred signals ────────────────────────────────────────────────────── */

/** This bar's delta as a share of its volume, so it compares across bar sizes. */
function deltaSignal(i: DirectionInputs): DirectionSignal {
  const label = 'Delta'
  if (!num(i.barDelta) || !num(i.barVolume) || i.barVolume <= 0) return off('delta', label, false)

  const share = i.barDelta / i.barVolume
  const detail = `${share > 0 ? '+' : ''}${(share * 100).toFixed(1)}% of bar`
  if (share >= DELTA_SHARE) return { key: 'delta', label, detail, bias: 'bullish', exact: false }
  if (share <= -DELTA_SHARE) return { key: 'delta', label, detail, bias: 'bearish', exact: false }
  return { key: 'delta', label, detail, bias: 'neutral', exact: false }
}

/**
 * How directional the cumulative-delta path was over the recent window.
 *
 * Two conditions, because either alone misreads a common case.
 *
 * *Efficiency* — net movement over gross movement — makes the answer scale-free:
 * a CVD climbing steadily from zero is a trend, while one that zigzags to the same
 * place is chop. Judging the level or the raw change instead would call any large
 * positive CVD bullish forever.
 *
 * *Materiality* — the net must exceed the largest single step in the window.
 * Efficiency is a ratio, so a CVD parked at 50,000 that drifts ten contracts scores
 * a confident 50% on nothing at all. Requiring the net to beat the biggest single
 * bar means a trend has to be more than one bar wearing a trend's clothes.
 */
function cvdSignal(i: DirectionInputs): DirectionSignal {
  const label = 'CVD'
  const series = i.cvdSeries
  if (!series || series.length < 3) return off('cvd', label, false)

  const w = series.slice(-CVD_WINDOW)
  let gross = 0
  let maxStep = 0
  for (let n = 1; n < w.length; n++) {
    const step = Math.abs(w[n] - w[n - 1])
    gross += step
    maxStep = Math.max(maxStep, step)
  }
  if (gross <= 0) return { key: 'cvd', label, detail: 'Flat', bias: 'neutral', exact: false }

  const net = w[w.length - 1] - w[0]
  const eff = net / gross
  const material = Math.abs(net) > maxStep
  const detail = `${net > 0 ? 'Rising' : 'Falling'} ${Math.abs(eff * 100).toFixed(0)}% net`
  if (material && eff >= CVD_EFFICIENCY) {
    return { key: 'cvd', label, detail, bias: 'bullish', exact: false }
  }
  if (material && eff <= -CVD_EFFICIENCY) {
    return { key: 'cvd', label, detail, bias: 'bearish', exact: false }
  }
  return { key: 'cvd', label, detail: material ? 'Choppy' : 'Flat', bias: 'neutral', exact: false }
}

/* ── composite ───────────────────────────────────────────────────────────── */

/**
 * Read every signal and combine them.
 *
 * The net is normalised by the weight that actually participated, so a verdict
 * resting on one signal is reachable (an index has only value-area migration) yet
 * still reported as resting on one — `participating` travels with the verdict so
 * the panel can never imply six signals agreed when one did.
 */
export function readDirection(i: DirectionInputs): DirectionVerdict {
  const signals = [
    oiSignal(i),
    bookSignal(i),
    vwapSignal(i),
    valueSignal(i),
    deltaSignal(i),
    cvdSignal(i),
  ]

  let net = 0
  let weight = 0
  let participating = 0
  for (const s of signals) {
    if (s.bias === 'unavailable') continue
    participating += 1
    const w = s.exact ? 1 : 0.5
    weight += w
    if (s.bias === 'bullish') net += w
    else if (s.bias === 'bearish') net -= w
  }

  const score = weight > 0 ? net / weight : 0
  const composite =
    score >= COMPOSITE_BAND ? 'bullish' : score <= -COMPOSITE_BAND ? 'bearish' : 'neutral'
  const agreeing = signals.filter((s) => s.bias === composite).length

  return { composite, score, agreeing, participating, signals }
}
