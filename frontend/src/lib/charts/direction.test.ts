/**
 * Direction engine rules. Pure input -> verdict, so every rule is pinned here
 * rather than observed on a live chart.
 */

import { describe, expect, it } from 'vitest'
import { type DirectionInputs, readDirection } from './direction'

const bias = (inp: Partial<DirectionInputs>, key: string) =>
  readDirection(inp).signals.find((s) => s.key === key)?.bias

/** A derivative with every input present and everything pointing up. */
const bullish: DirectionInputs = {
  hasOi: true,
  oi: 11_000_000,
  baselineOi: 10_000_000,
  price: 24_100,
  baselinePrice: 24_000,
  totalBuyQty: 1400,
  totalSellQty: 1000,
  vwap: 24_050,
  tick: 0.1,
  valueArea: { poc: 24_090, vah: 24_120, val: 24_060 },
  prevValueArea: { poc: 23_980, vah: 24_010, val: 23_950 },
  barDelta: 900,
  barVolume: 3000,
  cvdSeries: [0, 200, 500, 900, 1400, 2000],
}

describe('OI buildup', () => {
  const oi = (price: number, openPrice: number, oiNow: number, openOi: number) =>
    bias({ hasOi: true, price, baselinePrice: openPrice, oi: oiNow, baselineOi: openOi }, 'oi')

  it('reads the four cells of the price/OI matrix', () => {
    expect(oi(101, 100, 110, 100)).toBe('bullish') // price up, OI up   -> long buildup
    expect(oi(99, 100, 110, 100)).toBe('bearish') // price down, OI up  -> short buildup
    expect(oi(99, 100, 90, 100)).toBe('bearish') // price down, OI down -> long unwinding
    expect(oi(101, 100, 90, 100)).toBe('bullish') // price up, OI down  -> short covering
  })

  it('names the reading, not just the direction', () => {
    const s = readDirection({
      hasOi: true,
      price: 101,
      baselinePrice: 100,
      oi: 110,
      baselineOi: 100,
    }).signals.find((x) => x.key === 'oi')
    expect(s?.detail.toLowerCase()).toContain('long buildup')
  })

  it('stays neutral inside the dead bands so noise cannot flip it', () => {
    expect(oi(100.01, 100, 110, 100)).toBe('neutral') // price move too small
    expect(oi(101, 100, 100.2, 100)).toBe('neutral') // OI move too small
  })

  it('is unavailable where there is no open interest', () => {
    // Equity: no OI exists at all.
    expect(
      bias({ hasOi: false, price: 101, baselinePrice: 100, oi: 110, baselineOi: 100 }, 'oi')
    ).toBe('unavailable')
    // Derivative, but the broker did not send it.
    expect(bias({ hasOi: true, price: 101, baselinePrice: 100 }, 'oi')).toBe('unavailable')
    // A zero session-open OI would divide by zero.
    expect(
      bias({ hasOi: true, price: 101, baselinePrice: 100, oi: 110, baselineOi: 0 }, 'oi')
    ).toBe('unavailable')
  })
})

describe('book pressure', () => {
  const book = (tbq?: number, tsq?: number) => bias({ totalBuyQty: tbq, totalSellQty: tsq }, 'book')

  it('compares pending buy against pending sell quantity', () => {
    expect(book(1400, 1000)).toBe('bullish')
    expect(book(1000, 1400)).toBe('bearish')
    expect(book(1000, 1000)).toBe('neutral')
  })

  it('is unavailable when the feed omits the totals', () => {
    expect(book(undefined, 1000)).toBe('unavailable')
    expect(book(1000, undefined)).toBe('unavailable')
    expect(book(0, 0)).toBe('unavailable')
  })
})

describe('LTP against VWAP', () => {
  it('needs to clear a tick before it commits', () => {
    expect(bias({ price: 100.5, vwap: 100, tick: 0.1 }, 'vwap')).toBe('bullish')
    expect(bias({ price: 99.5, vwap: 100, tick: 0.1 }, 'vwap')).toBe('bearish')
    expect(bias({ price: 100.05, vwap: 100, tick: 0.1 }, 'vwap')).toBe('neutral')
  })

  it('is unavailable without a VWAP', () => {
    expect(bias({ price: 100.5 }, 'vwap')).toBe('unavailable')
    expect(bias({ price: 100.5, vwap: 0 }, 'vwap')).toBe('unavailable')
  })
})

describe('value-area migration', () => {
  const prev = { poc: 100, vah: 110, val: 90 }

  it('reads value accepted wholly above or below the prior area', () => {
    expect(
      bias({ valueArea: { poc: 125, vah: 130, val: 120 }, prevValueArea: prev }, 'value')
    ).toBe('bullish')
    expect(bias({ valueArea: { poc: 75, vah: 80, val: 70 }, prevValueArea: prev }, 'value')).toBe(
      'bearish'
    )
  })

  it('falls back to POC migration when the areas overlap', () => {
    expect(bias({ valueArea: { poc: 108, vah: 115, val: 95 }, prevValueArea: prev }, 'value')).toBe(
      'bullish'
    )
    expect(bias({ valueArea: { poc: 92, vah: 105, val: 85 }, prevValueArea: prev }, 'value')).toBe(
      'bearish'
    )
    // POC essentially unchanged inside an overlapping area is balance.
    expect(
      bias({ valueArea: { poc: 100.5, vah: 111, val: 91 }, prevValueArea: prev }, 'value')
    ).toBe('neutral')
  })

  it('is unavailable without two sessions to compare', () => {
    expect(bias({ valueArea: { poc: 100, vah: 110, val: 90 } }, 'value')).toBe('unavailable')
  })
})

describe('delta and CVD (inferred)', () => {
  it('scores bar delta as a share of the bar volume', () => {
    expect(bias({ barDelta: 900, barVolume: 3000 }, 'delta')).toBe('bullish')
    expect(bias({ barDelta: -900, barVolume: 3000 }, 'delta')).toBe('bearish')
    expect(bias({ barDelta: 100, barVolume: 3000 }, 'delta')).toBe('neutral')
  })

  it('is unavailable when the order flow is off', () => {
    expect(bias({}, 'delta')).toBe('unavailable')
    expect(bias({ barDelta: 900, barVolume: 0 }, 'delta')).toBe('unavailable')
    expect(bias({}, 'cvd')).toBe('unavailable')
  })

  it('judges CVD by how directional its path was, not its absolute size', () => {
    // Steady climb: nearly all movement is net.
    expect(bias({ cvdSeries: [0, 100, 200, 300, 400] }, 'cvd')).toBe('bullish')
    expect(bias({ cvdSeries: [0, -100, -200, -300, -400] }, 'cvd')).toBe('bearish')
    // Same gross movement, no net progress — chop, not a trend.
    expect(bias({ cvdSeries: [0, 400, 0, 400, 0] }, 'cvd')).toBe('neutral')
    // A large but flat CVD must not read as bullish just for being positive.
    expect(bias({ cvdSeries: [50_000, 50_010, 50_005, 50_010] }, 'cvd')).toBe('neutral')
  })

  it('marks delta and CVD as inferred and the rest as exact', () => {
    const byKey = new Map(readDirection(bullish).signals.map((s) => [s.key, s.exact]))
    expect(byKey.get('oi')).toBe(true)
    expect(byKey.get('book')).toBe(true)
    expect(byKey.get('vwap')).toBe(true)
    expect(byKey.get('value')).toBe(true)
    expect(byKey.get('delta')).toBe(false)
    expect(byKey.get('cvd')).toBe(false)
  })
})

describe('composite', () => {
  it('agrees with six aligned signals', () => {
    const v = readDirection(bullish)
    expect(v.composite).toBe('bullish')
    expect(v.participating).toBe(6)
    expect(v.agreeing).toBe(6)
  })

  it('lets four exact signals outvote two inferred ones', () => {
    // This is the case in the agreed mockup: exact bullish, delta and CVD bearish.
    const v = readDirection({
      ...bullish,
      barDelta: -900,
      cvdSeries: [2000, 1400, 900, 500, 200, 0],
    })
    expect(v.composite).toBe('bullish')
    expect(v.participating).toBe(6)
    expect(v.agreeing).toBe(4)
  })

  it('is neutral when nothing is available rather than guessing', () => {
    const v = readDirection({ hasOi: false })
    expect(v.composite).toBe('neutral')
    expect(v.participating).toBe(0)
    expect(v.score).toBe(0)
  })

  it('reaches a verdict on a single available signal, and says so', () => {
    // An index: quote-only, so only value-area migration participates.
    const v = readDirection({
      hasOi: false,
      valueArea: { poc: 125, vah: 130, val: 120 },
      prevValueArea: { poc: 100, vah: 110, val: 90 },
    })
    expect(v.composite).toBe('bullish')
    expect(v.participating).toBe(1)
    expect(v.agreeing).toBe(1)
  })

  it('never reports a bias stronger than its evidence', () => {
    // Two inferred signals disagreeing with each other, nothing exact.
    const v = readDirection({
      hasOi: false,
      barDelta: 900,
      barVolume: 3000,
      cvdSeries: [0, -100, -200, -300, -400],
    })
    expect(v.composite).toBe('neutral')
    expect(v.participating).toBe(2)
  })

  it('returns every signal even when unavailable, so the panel has stable rows', () => {
    const v = readDirection({})
    expect(v.signals.map((s) => s.key)).toEqual(['oi', 'book', 'vwap', 'value', 'delta', 'cvd'])
    expect(v.signals.every((s) => s.bias === 'unavailable')).toBe(true)
  })
})
