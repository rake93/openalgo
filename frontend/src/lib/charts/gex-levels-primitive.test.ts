/**
 * Pure geometry/formatting helpers behind the GEX Levels primitive. The
 * primitive's `draw()` itself cannot be unit-tested - jsdom provides no
 * canvas - so this only covers the parts extracted to take plain numbers and
 * callbacks instead of a live chart.
 */

import { describe, expect, it } from 'vitest'
import type { GEXStrikeLevel } from '@/api/gex'
import {
  computeGexBarGeometry,
  computeGexLevelPlacement,
  formatGexPrice,
} from './gex-levels-primitive'

describe('formatGexPrice', () => {
  it('drops decimals for a whole-number price', () => {
    expect(formatGexPrice(24800)).toBe('24800')
  })

  it('keeps two decimals for a fractional strike', () => {
    expect(formatGexPrice(292.5)).toBe('292.50')
  })

  it('treats a float that lands on a whole number as whole', () => {
    expect(formatGexPrice(1500.0)).toBe('1500')
  })

  it('rounds to two decimals rather than truncating', () => {
    expect(formatGexPrice(100.126)).toBe('100.13')
  })
})

describe('computeGexLevelPlacement', () => {
  const plotHeight = 400
  const inset = 12

  it('reports on-screen when y falls inside the plot, including the edges', () => {
    expect(computeGexLevelPlacement(0, plotHeight, inset)).toEqual({
      onScreen: true,
      y: 0,
      direction: null,
    })
    expect(computeGexLevelPlacement(200, plotHeight, inset)).toEqual({
      onScreen: true,
      y: 200,
      direction: null,
    })
    expect(computeGexLevelPlacement(plotHeight, plotHeight, inset)).toEqual({
      onScreen: true,
      y: plotHeight,
      direction: null,
    })
  })

  it('pins a level above the plot to the top edge inset, not the raw y', () => {
    expect(computeGexLevelPlacement(-500, plotHeight, inset)).toEqual({
      onScreen: false,
      y: inset,
      direction: 'above',
    })
  })

  it('pins a level below the plot to the bottom edge inset', () => {
    expect(computeGexLevelPlacement(900, plotHeight, inset)).toEqual({
      onScreen: false,
      y: plotHeight - inset,
      direction: 'below',
    })
  })

  it('never lets the inset push the stub past the opposite edge on a tiny pane', () => {
    // plotHeight smaller than 2*inset: the bottom stub must not go negative.
    const placement = computeGexLevelPlacement(900, 10, inset)
    expect(placement.y).toBeGreaterThanOrEqual(inset)
  })
})

// netDex defaults to netGex so every existing fixture renders identically
// under either metric without being touched. Only the metric-selection
// tests (next task) pass a distinct third argument, since only those need
// to prove gamma and delta actually diverge.
function strike(strikePrice: number, netGex: number, netDex: number = netGex): GEXStrikeLevel {
  return {
    strike: strikePrice,
    call_gex: Math.max(netGex, 0),
    put_gex: Math.max(-netGex, 0),
    net_gex: netGex,
    call_dex: Math.max(netDex, 0),
    put_dex: Math.min(netDex, 0),
    net_dex: netDex,
  }
}

/** A linear price->y map: higher price -> smaller y, like the real (non-inverted) PriceScale. */
function linearPriceToY(price: number): number {
  const min = 24_000
  const max = 25_000
  const plotHeight = 400
  const r = (price - min) / (max - min)
  return plotHeight * (1 - r)
}

describe('computeGexBarGeometry', () => {
  it('returns nothing when every strike falls outside the visible range', () => {
    const strikes = [strike(10_000, 500), strike(10_050, -300)]
    const { bars, rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(bars).toEqual([])
    expect(rowHeight).toBe(0)
  })

  it('clips to the visible strikes only, ignoring off-screen ones for both drawing and peak scaling', () => {
    const strikes = [
      strike(10_000, 9999), // off-screen, must not win the peak or appear in bars
      strike(24_200, 100),
      strike(24_400, -50),
    ]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(bars.map((b) => b.strike)).toEqual([24_200, 24_400])
    // Peak is 100 (the larger of the two visible strikes), so the full-size
    // bar is exactly columnWidth, not scaled down by the off-screen 9999.
    expect(bars.find((b) => b.strike === 24_200)?.length).toBe(120)
  })

  it('does not divide by zero when every visible strike has net_gex 0', () => {
    const strikes = [strike(24_200, 0), strike(24_400, 0)]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(bars).toHaveLength(2)
    for (const b of bars) {
      expect(b.length).toBe(0)
      expect(Number.isNaN(b.length)).toBe(false)
    }
  })

  it('assigns sign correctly: net_gex >= 0 is positive, negative is not', () => {
    const strikes = [strike(24_200, 0), strike(24_400, -1)]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(bars.find((b) => b.strike === 24_200)?.positive).toBe(true)
    expect(bars.find((b) => b.strike === 24_400)?.positive).toBe(false)
  })

  it('falls back to a default row height for a single strike, without NaN', () => {
    const strikes = [strike(24_200, 50)]
    const { bars, rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(bars).toHaveLength(1)
    expect(rowHeight).toBeGreaterThan(0)
    expect(Number.isNaN(rowHeight)).toBe(false)
  })

  it('caps row height so bars cannot grow into an overlapping smear when zoomed in', () => {
    // Strikes 400 price-units apart map to a huge pixel gap on this scale.
    const strikes = [strike(24_200, 10), strike(24_600, -10)]
    const { rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(rowHeight).toBeLessThanOrEqual(14)
  })

  it('reads net_gex under the gamma metric and net_dex under delta', () => {
    // Opposite signs between the two metrics, so a geometry that ignored the
    // metric would be caught by the direction flip alone.
    const strikes = [strike(24_200, 100, -80), strike(24_400, -50, 40)]

    const gamma = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(gamma.bars.map((b) => b.positive)).toEqual([true, false])
    // Gamma peak is 100, so 24_200 is the full column.
    expect(gamma.bars.find((b) => b.strike === 24_200)?.length).toBe(120)

    const delta = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'delta')
    expect(delta.bars.map((b) => b.positive)).toEqual([false, true])
    // Delta peak is 80, so 24_200 is full and 24_400 is half.
    expect(delta.bars.find((b) => b.strike === 24_200)?.length).toBe(120)
    expect(delta.bars.find((b) => b.strike === 24_400)?.length).toBe(60)
  })

  it('scales each metric against its own peak, never the other metric', () => {
    const strikes = [strike(24_200, 1000, 10), strike(24_400, 500, 5)]

    const delta = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'delta')
    // If the gamma peak of 1000 leaked into the delta scaling these would be
    // 1.2px and 0.6px - a column of invisible slivers rather than an
    // obviously wrong chart, which is why it needs pinning.
    expect(delta.bars.find((b) => b.strike === 24_200)?.length).toBe(120)
    expect(delta.bars.find((b) => b.strike === 24_400)?.length).toBe(60)
  })

  it('defaults to the gamma metric when none is passed', () => {
    const strikes = [strike(24_200, 100, -80)]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120)
    expect(bars[0]?.positive).toBe(true)
  })
})
