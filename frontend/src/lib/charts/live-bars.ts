/**
 * Tick and volume bar timeframes.
 *
 * These are the two bar types that **cannot** come from history. A 1-minute
 * OHLCV series cannot be re-bucketed into 250-tick or 5,000-contract bars —
 * that needs the individual prints. OpenAlgo streams live ticks but does not
 * store them, so a tick or volume chart starts empty and builds from the moment
 * you connected, exactly like the footprint.
 *
 * They are encoded in the interval slot as `250T` / `5000V` so they travel
 * through the same symbol/interval plumbing (and the same saved layout) as the
 * broker's own resolutions, and are told apart by {@link parseLiveBar}.
 */

import type { TickTimeframe } from 'openalgo-charts'

export interface LiveBarSpec {
  mode: 'ticks' | 'volume'
  /** Ticks per bar, or contracts/shares per bar. */
  size: number
}

/** Parse `250T` / `5000V`; null for a normal broker interval. */
export function parseLiveBar(interval: string): LiveBarSpec | null {
  const m = /^(\d+)([TV])$/.exec(interval.trim().toUpperCase())
  if (!m) return null
  const size = Number(m[1])
  if (!(size > 0)) return null
  return { mode: m[2] === 'T' ? 'ticks' : 'volume', size }
}

export const formatLiveBar = (spec: LiveBarSpec): string =>
  `${spec.size}${spec.mode === 'ticks' ? 'T' : 'V'}`

/** The aggregator timeframe for a spec. */
export function liveBarTimeframe(spec: LiveBarSpec): TickTimeframe {
  return spec.mode === 'ticks'
    ? { mode: 'ticks', count: spec.size }
    : { mode: 'volume', perBar: spec.size }
}

/** Human label for the toolbar and status strip. */
export function liveBarLabel(spec: LiveBarSpec): string {
  return spec.mode === 'ticks' ? `${spec.size} ticks per bar` : `${spec.size} traded per bar`
}

/**
 * Offered in the timeframe menu. Tick counts suit the NSE index futures, where
 * a busy 5-minute bar carries a few hundred prints; volume presets are in
 * contracts, so they want to be read against the instrument's lot size.
 */
export const LIVE_BAR_PRESETS: { value: string; label: string; group: 'ticks' | 'volume' }[] = [
  { value: '100T', label: '100T', group: 'ticks' },
  { value: '250T', label: '250T', group: 'ticks' },
  { value: '500T', label: '500T', group: 'ticks' },
  { value: '1000T', label: '1000T', group: 'ticks' },
  { value: '1000V', label: '1KV', group: 'volume' },
  { value: '5000V', label: '5KV', group: 'volume' },
  { value: '10000V', label: '10KV', group: 'volume' },
]
