/**
 * On-chart event markers.
 *
 * Charting libraries usually mean earnings, dividends and splits by "events".
 * OpenAlgo carries none of those — but it does carry two things a trader
 * actually wants on the chart, and both are already in the platform:
 *
 *  - **Your own fills** for the loaded symbol, from the tradebook, drawn as
 *    buy/sell arrows at the traded price. This is the "where did I get in"
 *    question the chart is usually opened to answer.
 *  - **The contract's expiry**, from the master contract, for futures and
 *    options.
 *
 * Fills are drawn with `SeriesMarkers` (anchored to the price series) and the
 * expiry with `EventMarkers` (a time-axis flag), which is what each primitive
 * is for.
 */

import {
  type EventMarkers,
  istStringToUtcSeconds,
  type SeriesApi,
  type SeriesMarker,
  type SeriesMarkers,
} from 'openalgo-charts'

const BUY = '#26a69a'
const SELL = '#ef5350'

/** One executed trade, as the OpenAlgo tradebook reports it. */
export interface TradeRow {
  symbol?: string
  exchange?: string
  action?: string
  quantity?: number | string
  average_price?: number | string
  timestamp?: string
  orderid?: string
  [k: string]: unknown
}

/**
 * Parse a broker timestamp to UTC seconds.
 *
 * Brokers are not consistent here: some send `YYYY-MM-DD HH:MM:SS`, some an ISO
 * string, some epoch seconds or milliseconds, some a format we cannot read at
 * all. An unreadable stamp returns null and the marker is skipped rather than
 * landing at the epoch and dragging the whole time axis back to 1970.
 */
export function parseTradeTime(raw: unknown): number | null {
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return raw > 1e11 ? Math.floor(raw / 1000) : Math.floor(raw)
  }
  if (typeof raw !== 'string' || !raw.trim()) return null
  const text = raw.trim()

  if (/^\d+$/.test(text)) {
    const n = Number(text)
    return n > 1e11 ? Math.floor(n / 1000) : n
  }
  // The engine's own IST parser handles `YYYY-MM-DD[ T]HH:MM[:SS]` explicitly,
  // without depending on the host machine's timezone.
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) {
    const s = istStringToUtcSeconds(text)
    if (Number.isFinite(s) && s > 0) return s
  }
  const parsed = Date.parse(text)
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null
}

/** Build fill markers for one symbol from tradebook rows. */
export function tradeMarkers(
  rows: readonly TradeRow[],
  symbol: string,
  exchange: string,
  fmt: (n: number) => string
): SeriesMarker[] {
  const out: SeriesMarker[] = []
  for (const row of rows) {
    if (row.symbol !== symbol) continue
    // Some brokers report a segment code rather than the OpenAlgo exchange, so
    // a mismatch is only disqualifying when the row actually carries one.
    if (row.exchange && row.exchange !== exchange) continue
    const time = parseTradeTime(row.timestamp)
    if (time == null) continue
    const price = Number(row.average_price)
    if (!Number.isFinite(price) || price <= 0) continue
    const qty = Number(row.quantity) || 0
    const buy = String(row.action || '').toUpperCase() === 'BUY'
    out.push({
      time,
      position: buy ? 'belowBar' : 'aboveBar',
      shape: buy ? 'arrowUp' : 'arrowDown',
      size: 'small',
      color: buy ? BUY : SELL,
      text: `${buy ? 'B' : 'S'} ${qty} @ ${fmt(price)}`,
      ...(row.orderid ? { id: String(row.orderid) } : {}),
    })
  }
  return out.sort((a, b) => a.time - b.time)
}

/** Contract expiry as a time-axis event, or null for a cash instrument. */
export function expiryEvent(expiry: unknown): { time: number; type: string; label: string }[] {
  if (typeof expiry !== 'string' || !expiry.trim()) return []
  const time = parseExpiry(expiry)
  return time == null ? [] : [{ time, type: 'expiry', label: `Expiry ${expiry}` }]
}

/**
 * Master-contract expiry dates come as `DD-MMM-YY` (`28-JUL-26`) on most
 * brokers, sometimes as an ISO date. Anything else is skipped.
 */
function parseExpiry(text: string): number | null {
  const t = text.trim().toUpperCase()
  const dmy = /^(\d{1,2})-([A-Z]{3})-(\d{2,4})$/.exec(t)
  if (dmy) {
    const months = [
      'JAN',
      'FEB',
      'MAR',
      'APR',
      'MAY',
      'JUN',
      'JUL',
      'AUG',
      'SEP',
      'OCT',
      'NOV',
      'DEC',
    ]
    const month = months.indexOf(dmy[2])
    if (month < 0) return null
    const year = dmy[3].length === 2 ? 2000 + Number(dmy[3]) : Number(dmy[3])
    const iso = `${year}-${String(month + 1).padStart(2, '0')}-${dmy[1].padStart(2, '0')}`
    const s = istStringToUtcSeconds(`${iso} 15:30`)
    return Number.isFinite(s) && s > 0 ? s : null
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) {
    const s = istStringToUtcSeconds(`${t} 15:30`)
    return Number.isFinite(s) && s > 0 ? s : null
  }
  return null
}

/**
 * Owns the two marker layers for one chart. Rebuilt on every chart rebuild,
 * like every other primitive holder.
 */
export class EventMarkerLayer {
  private markers: SeriesMarkers | null = null
  private events: EventMarkers | null = null
  private fills: SeriesMarker[] = []
  private expiries: { time: number; type: string; label: string }[] = []
  private enabled = true

  attach(series: SeriesApi, events: EventMarkers | null): void {
    this.markers = series.createMarkers()
    this.events = events
    this.push()
  }

  setEnabled(on: boolean): void {
    this.enabled = on
    this.push()
  }

  get showing(): boolean {
    return this.enabled
  }

  get fillCount(): number {
    return this.fills.length
  }

  setFills(fills: SeriesMarker[]): void {
    this.fills = fills
    this.push()
  }

  setExpiries(expiries: { time: number; type: string; label: string }[]): void {
    this.expiries = expiries
    this.push()
  }

  private push(): void {
    this.markers?.setMarkers(this.enabled ? this.fills : [])
    this.events?.setEvents(this.enabled ? this.expiries : [])
  }

  detach(): void {
    this.markers = null
    this.events = null
  }
}
