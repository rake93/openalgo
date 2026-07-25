/**
 * Derived timeframes.
 *
 * Brokers expose only a handful of native resolutions — Dhan, for instance,
 * serves 1m, 5m, 15m, 25m, 1h and D. A trader still expects 3m, 4m, 10m, 2h and
 * the rest, so anything the broker does not serve is built here by re-bucketing
 * the coarsest native interval that divides it evenly. That is what TradingView
 * does for its non-native resolutions, and it keeps the request small: 2h comes
 * from 1h rather than from 1m.
 *
 * Buckets anchor to the **exchange's market open**, not to an epoch floor.
 * Without that, a 4-minute bar would start at an arbitrary offset inside the
 * session and never line up with what any other chart shows.
 *
 * The alignment deliberately mirrors Historify's server-side aggregation
 * (`database/historify_db.py::_get_aggregated_ohlcv`), down to the per-exchange
 * open — NSE and NFO at 09:15, MCX and the currency segments at 09:00 — so a
 * 25-minute candle drawn here and one exported from Historify describe the same
 * bucket. Historify aggregates its own DuckDB store of 1-minute bars, which the
 * chart does not read (it fetches from the broker), so the rule is shared even
 * though the code cannot be.
 */

import type { Bar } from 'openalgo-charts'
import { intervalSeconds } from '@/lib/trading/intervals'

/** IST is a fixed UTC+5:30; India observes no DST. */
const IST_OFFSET = 19_800

/**
 * Market open in seconds from IST midnight, mirroring
 * `EXCHANGE_MARKET_OPEN_SECONDS` in `database/historify_db.py`.
 */
const MARKET_OPEN_SECONDS: Record<string, number> = {
  NSE: 33_300,
  BSE: 33_300,
  NFO: 33_300,
  BFO: 33_300,
  CDS: 32_400,
  BCD: 32_400,
  MCX: 32_400,
  NCDEX: 32_400,
  NCO: 33_300,
  NSE_INDEX: 33_300,
  BSE_INDEX: 33_300,
}

const DEFAULT_OPEN_SECONDS = 33_300

export const marketOpenSeconds = (exchange: string): number =>
  MARKET_OPEN_SECONDS[exchange?.toUpperCase()] ?? DEFAULT_OPEN_SECONDS

/**
 * The resolutions offered in the timeframe menu regardless of broker support.
 * Anything the broker serves natively is used as-is; the rest are derived.
 */
export const DERIVED_INTERVALS: { label: string; items: string[] }[] = [
  {
    label: 'minutes',
    items: ['1m', '2m', '3m', '4m', '5m', '9m', '10m', '15m', '20m', '30m', '45m'],
  },
  { label: 'hours', items: ['1h', '2h', '3h', '4h'] },
  { label: 'days', items: ['D', 'W', 'M'] },
]

/** UTC seconds of the market open on the IST day containing `t`. */
export function sessionAnchor(t: number, exchange = 'NSE'): number {
  const istMidnight = Math.floor((t + IST_OFFSET) / 86_400) * 86_400 - IST_OFFSET
  return istMidnight + marketOpenSeconds(exchange)
}

/**
 * The bucket a timestamp belongs to, anchored to the market open — the same
 * arithmetic Historify's SQL performs.
 */
export function bucketStart(t: number, targetSec: number, exchange = 'NSE'): number {
  const anchor = sessionAnchor(t, exchange)
  // Math.floor rounds towards -Infinity, so a pre-open print lands in the
  // bucket *below* the anchor rather than being pulled onto it.
  return anchor + Math.floor((t - anchor) / targetSec) * targetSec
}

export interface ResamplePlan {
  /** Broker interval to request. */
  source: string
  /** Bucket size of the result, in seconds. */
  targetSec: number
}

/**
 * How to build `target`, given what the broker serves.
 *
 * Returns null when the broker already has it (fetch directly) or when it
 * cannot be derived at all — daily and above are never re-bucketed, because
 * a week or a month is a calendar question, not an arithmetic one.
 */
export function resamplePlan(
  target: string,
  brokerIntervals: readonly string[]
): ResamplePlan | null {
  if (brokerIntervals.includes(target)) return null
  const targetSec = intervalSeconds(target)
  if (!targetSec) return null

  let best: { token: string; sec: number } | null = null
  for (const token of brokerIntervals) {
    const sec = intervalSeconds(token)
    if (!sec || sec >= targetSec || targetSec % sec !== 0) continue
    // Coarsest divisor wins — fewest bars to fetch and to fold.
    if (!best || sec > best.sec) best = { token, sec }
  }
  return best ? { source: best.token, targetSec } : null
}

/**
 * Fold finer bars into `targetSec` buckets. Input must be sorted ascending;
 * the output carries each bucket's open, extremes, last close and summed volume.
 */
export function resampleBars(bars: readonly Bar[], targetSec: number, exchange = 'NSE'): Bar[] {
  if (targetSec <= 0 || bars.length === 0) return [...bars]
  const out: Bar[] = []
  let current: Bar | null = null
  let currentStart = Number.NaN

  for (const bar of bars) {
    const start = bucketStart(bar.time, targetSec, exchange)
    if (!current || start !== currentStart) {
      if (current) out.push(current)
      currentStart = start
      current = {
        time: start,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume ?? 0,
      }
      continue
    }
    current.high = Math.max(current.high, bar.high)
    current.low = Math.min(current.low, bar.low)
    current.close = bar.close
    current.volume = (current.volume ?? 0) + (bar.volume ?? 0)
  }
  if (current) out.push(current)
  return out
}

/**
 * Merge the broker's native resolutions with the derived ones, so the menu
 * shows a full TradingView-style ladder. Native tokens the broker offers but
 * the ladder omits (Dhan's 25m, say) are kept — they are real data.
 */
export function mergedIntervalGroups(
  brokerGroups: readonly { label: string; items: string[] }[]
): { label: string; items: string[] }[] {
  const native = new Map<string, string[]>()
  for (const g of brokerGroups) native.set(g.label, [...g.items])

  const out: { label: string; items: string[] }[] = []
  for (const derived of DERIVED_INTERVALS) {
    const extra = (native.get(derived.label) ?? []).filter((t) => !derived.items.includes(t))
    native.delete(derived.label)
    out.push({ label: derived.label, items: sortIntervals([...derived.items, ...extra]) })
  }
  // Seconds (and anything else the broker groups separately) pass through: they
  // are native-only, since sub-minute bars cannot be folded from minute bars.
  for (const [label, items] of native) {
    if (items.length) out.unshift({ label, items })
  }
  return out
}

/** Ascending by duration, with D/W/M last. */
function sortIntervals(tokens: string[]): string[] {
  const rank = (t: string) => intervalSeconds(t) ?? { D: 1e7, W: 1e8, M: 1e9 }[t] ?? 1e10
  return [...new Set(tokens)].sort((a, b) => rank(a) - rank(b))
}
