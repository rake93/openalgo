/**
 * End-to-end replay of the order-flow path, without a live feed.
 *
 * Drives synthetic depth packets — shaped exactly as the WebSocket proxy forwards
 * them — through the *real* chain: the library's `parseMessage`, then
 * `ProfileManager`'s classification and aggregation, then the direction engine.
 * Nothing here is mocked except the packets themselves.
 *
 * This exists because the order flow can only otherwise be judged during market
 * hours, which makes regressions in it invisible for most of the day. It pins, in
 * particular:
 *
 *  - prints classified against the quote prevailing *before* they traded, which is
 *    the bug that had every delta inverted;
 *  - traded quantity differenced out of cumulative volume rather than summed from
 *    the sticky last-traded quantity;
 *  - the fields a direction verdict needs surviving the parse boundary.
 */

import { parseMessage } from 'openalgo-charts'
import { describe, expect, it } from 'vitest'
import { readDirection } from './direction'
import { ProfileManager } from './profiles'

interface Packet {
  ltp: number
  bid: number
  ask: number
  /** Cumulative day volume — the traded quantity is its increment. */
  volume: number
  oi?: number
  tbq?: number
  tsq?: number
  atp?: number
}

/** A depth-mode `market_data` frame as the proxy sends it, per broker field names. */
const frame = (p: Packet) => ({
  type: 'market_data',
  mode: 3,
  data: {
    symbol: 'NIFTY28JUL26FUT',
    exchange: 'NFO',
    ltp: p.ltp,
    volume: p.volume,
    last_quantity: 65,
    ...(p.oi === undefined ? {} : { oi: p.oi }),
    ...(p.tbq === undefined ? {} : { total_buy_quantity: p.tbq }),
    ...(p.tsq === undefined ? {} : { total_sell_quantity: p.tsq }),
    ...(p.atp === undefined ? {} : { average_price: p.atp }),
    depth: {
      buy: [{ price: p.bid, quantity: 750, orders: 5 }],
      sell: [{ price: p.ask, quantity: 800, orders: 6 }],
    },
  },
})

/**
 * Replay a packet sequence the way `workspace.connectLive` does: absorb the book,
 * then classify the print against the lagged quote, with every print stamped with
 * the chart bar it landed in.
 */
function replay(packets: Packet[], barTime = 60) {
  const m = new ProfileManager({
    onChange: () => {},
    refPrice: () => 24_000,
    tickSize: () => 0.1,
    visibleRange: () => null,
  })
  m.setFootprintConfig({ enabled: true, rowSize: 0.1 })

  const book: { oi?: number; tbq?: number; tsq?: number; vwap?: number } = {}
  let baseOi: number | undefined
  let basePrice: number | undefined
  let cumVolume = -1
  let lastLtp = 0

  for (const p of packets) {
    const parsed = parseMessage(frame(p))
    expect(parsed?.kind).toBe('depth')
    if (parsed?.kind !== 'depth') continue
    const d = parsed.depth

    m.onDepth(d)
    if (typeof d.oi === 'number') book.oi = d.oi
    if (typeof d.totalBuyQty === 'number') book.tbq = d.totalBuyQty
    if (typeof d.totalSellQty === 'number') book.tsq = d.totalSellQty
    if (typeof d.atp === 'number') book.vwap = d.atp
    if (baseOi === undefined && book.oi !== undefined) {
      baseOi = book.oi
      basePrice = d.ltp
    }

    // Traded quantity is the cumulative-volume increment; the first packet only
    // establishes the baseline and contributes nothing.
    const cum = d.volume ?? 0
    const prev = cumVolume
    cumVolume = cum
    const qty = prev < 0 || cum < prev ? 0 : cum - prev

    lastLtp = d.ltp
    m.onTrade({ time: barTime, price: d.ltp, qty })
  }

  return { m, book, baseOi, basePrice, lastLtp }
}

/**
 * A rally: two buys lifting the ask, then one smaller sell hitting the bid.
 *
 * VWAP trails well below price, as it does on a day that has trended up — a few
 * paise would sit inside the one-tick dead band and read (correctly) as neutral.
 */
const RALLY: Packet[] = [
  { ltp: 100.0, bid: 100.0, ask: 100.1, volume: 1000, oi: 10_000, tbq: 1400, tsq: 1000, atp: 99.7 },
  {
    ltp: 100.1,
    bid: 100.1,
    ask: 100.2,
    volume: 1130,
    oi: 10_050,
    tbq: 1400,
    tsq: 1000,
    atp: 99.75,
  },
  { ltp: 100.2, bid: 100.2, ask: 100.3, volume: 1260, oi: 10_120, tbq: 1500, tsq: 1000, atp: 99.8 },
  {
    ltp: 100.1,
    bid: 100.1,
    ask: 100.2,
    volume: 1325,
    oi: 10_130,
    tbq: 1500,
    tsq: 1000,
    atp: 99.85,
  },
]

describe('order-flow replay', () => {
  it('builds one column on the chart bar and totals the traded volume exactly', () => {
    const { m } = replay(RALLY)

    expect(m.footprintTape.length).toBe(1)
    const bar = m.footprintTape[0]
    expect(bar.time).toBe(60)

    // 130 + 130 + 65. The first packet only set the volume baseline.
    const volume = bar.cells.reduce((a, c) => a + c.bidVol + c.askVol, 0)
    expect(volume).toBe(325)
  })

  it('classifies the two ask-lifts as buys and the bid-hit as a sell', () => {
    const { m } = replay(RALLY)
    const bar = m.footprintTape[0]

    // Net: +130 +130 -65.
    expect(bar.delta).toBe(195)

    const at = (price: number) => bar.cells.find((c) => Math.abs(c.price - price) < 0.001)
    expect(at(100.1)?.askVol).toBe(130) // bought at the prevailing ask
    expect(at(100.2)?.askVol).toBe(130)
    expect(at(100.1)?.bidVol).toBe(65) // sold into the prevailing bid
  })

  it('never sums the sticky last-traded quantity', () => {
    // Every frame carries last_quantity 65. Summing it over four packets would
    // give 260 and, worse, would keep counting on packets where nothing traded.
    const flat: Packet[] = [
      { ltp: 100.0, bid: 100.0, ask: 100.1, volume: 5000 },
      { ltp: 100.0, bid: 100.0, ask: 100.1, volume: 5000 },
      { ltp: 100.0, bid: 100.0, ask: 100.1, volume: 5000 },
    ]
    const { m } = replay(flat)
    expect(m.footprintTape.length).toBe(0) // no volume moved, so nothing traded
  })

  it('opens a new column when the chart bar advances, and carries CVD across', () => {
    const m = replay(RALLY).m
    // Second bar: a sell-off.
    const fall: Packet[] = [
      { ltp: 100.1, bid: 100.1, ask: 100.2, volume: 1325 },
      { ltp: 100.0, bid: 100.0, ask: 100.1, volume: 1585 },
    ]
    let cum = 1325
    for (const p of fall) {
      const parsed = parseMessage(frame(p))
      if (parsed?.kind !== 'depth') continue
      m.onDepth(parsed.depth)
      const qty = Math.max(0, (parsed.depth.volume ?? 0) - cum)
      cum = parsed.depth.volume ?? cum
      m.onTrade({ time: 120, price: parsed.depth.ltp, qty })
    }

    expect(m.footprintTape.length).toBe(2)
    expect(m.footprintTape[1].time).toBe(120)
    expect(m.footprintTape[1].delta).toBe(-260) // hit the bid at 100.00

    let running = 0
    const cvd = m.footprintTape.map((b) => (running += b.delta))
    expect(cvd).toEqual([195, -65])
  })

  it('reaches a bullish verdict from the replayed rally', () => {
    const { m, book, baseOi, basePrice, lastLtp } = replay(RALLY)
    const bar = m.footprintTape[0]

    const v = readDirection({
      hasOi: true,
      oi: book.oi,
      baselineOi: baseOi,
      price: lastLtp,
      baselinePrice: basePrice,
      totalBuyQty: book.tbq,
      totalSellQty: book.tsq,
      vwap: book.vwap,
      tick: 0.1,
      barDelta: bar.delta,
      barVolume: bar.cells.reduce((a, c) => a + c.bidVol + c.askVol, 0),
    })

    const byKey = new Map(v.signals.map((s) => [s.key, s.bias]))
    // Price up 0.1%, OI up 1.3% -> longs building.
    expect(byKey.get('oi')).toBe('bullish')
    expect(byKey.get('book')).toBe('bullish') // 1500 pending buy vs 1000 sell
    expect(byKey.get('vwap')).toBe('bullish') // 100.10 against a 99.85 VWAP
    expect(byKey.get('delta')).toBe('bullish') // +195 of 325
    // No second session replayed, and CVD needs three bars.
    expect(byKey.get('value')).toBe('unavailable')
    expect(byKey.get('cvd')).toBe('unavailable')

    expect(v.composite).toBe('bullish')
    expect(v.participating).toBe(4)
  })

  it('degrades to unavailable rather than zero when a broker omits fields', () => {
    // Fyers-shaped: depth and LTP only, no OI, no book totals, no VWAP.
    const bare: Packet[] = [
      { ltp: 100.0, bid: 100.0, ask: 100.1, volume: 1000 },
      { ltp: 100.1, bid: 100.1, ask: 100.2, volume: 1130 },
    ]
    const { m, book, baseOi } = replay(bare)
    expect(book.oi).toBeUndefined()
    expect(baseOi).toBeUndefined()

    const bar = m.footprintTape[0]
    const v = readDirection({
      hasOi: true, // a derivative, but the feed says nothing about OI
      oi: book.oi,
      baselineOi: baseOi,
      totalBuyQty: book.tbq,
      totalSellQty: book.tsq,
      vwap: book.vwap,
      barDelta: bar.delta,
      barVolume: bar.cells.reduce((a, c) => a + c.bidVol + c.askVol, 0),
    })

    const byKey = new Map(v.signals.map((s) => [s.key, s.bias]))
    expect(byKey.get('oi')).toBe('unavailable')
    expect(byKey.get('book')).toBe('unavailable')
    expect(byKey.get('vwap')).toBe('unavailable')
    // The order flow still works, so delta alone carries the verdict.
    expect(byKey.get('delta')).toBe('bullish')
    expect(v.participating).toBe(1)
  })
})

describe('value-area migration over replayed sessions', () => {
  /** 1-minute bars for one UTC day, centred on `mid`. */
  const day = (dayIndex: number, mid: number) => {
    const base = dayIndex * 86_400 + 4 * 3600
    return Array.from({ length: 60 }, (_, n) => ({
      time: base + n * 60,
      open: mid,
      high: mid + 5,
      low: mid - 5,
      close: mid,
      volume: 1000,
    }))
  }

  it('reads value accepted higher across two sessions, with no volume needed', () => {
    const m = new ProfileManager({
      onChange: () => {},
      refPrice: () => 100,
      tickSize: () => 0.1,
      visibleRange: () => null,
    })
    // Unfiltered window so the synthetic times are not excluded by market hours.
    m.setMarketConfig({ window: 'all-hours', rowSize: 1 })
    // Volume left at zero throughout: market profile ranks levels by TPO period
    // count, which is why this works on an index that reports no volume at all.
    const bars = [
      ...day(20_290, 100).map((b) => ({ ...b, volume: 0 })),
      ...day(20_293, 130).map((b) => ({ ...b, volume: 0 })),
    ]
    m.setBars(bars)

    const vas = m.valueAreas()
    expect(vas.current).toBeDefined()
    expect(vas.previous).toBeDefined()
    expect(vas.current?.poc).toBeGreaterThan(vas.previous?.poc as number)

    const v = readDirection({ valueArea: vas.current, prevValueArea: vas.previous })
    expect(v.signals.find((s) => s.key === 'value')?.bias).toBe('bullish')
  })
})
