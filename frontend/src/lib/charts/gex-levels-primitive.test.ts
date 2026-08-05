/**
 * Pure geometry/formatting helpers behind the GEX Levels primitives, plus a
 * `draw()`-level suite for the metric caption. jsdom provides no canvas, but
 * `draw()` only ever calls a handful of `CanvasRenderingContext2D` methods, so
 * a plain object recording those calls is enough to pin what actually reaches
 * the screen - see `fakeCtx` / `fakeRc` below.
 */

import { describe, expect, it } from 'vitest'
import type { GEXStrikeLevel } from '@/api/gex'
import {
  computeGexBarGeometry,
  computeGexLevelPlacement,
  formatGexPrice,
  GexLevelsPrimitive,
  GexMetricCaptionPrimitive,
  gexMetricCaption,
} from './gex-levels-primitive'

describe('gexMetricCaption', () => {
  it('labels gamma with the dealer-sign frame', () => {
    expect(gexMetricCaption('gamma')).toBe('Gamma · dealer sign')
  })

  it('labels delta with the OI-book frame, not the dealer frame', () => {
    // DEX is the open-interest book's delta, not the dealer's - dealers hold
    // the negation (services/gex_levels/delta_exposure.py). Getting this
    // label backwards would make the caption actively mislead rather than
    // just being absent. "OI-book", not a bare "book" - this workspace also
    // has a real order book (depth, pollBook(), the trade panel).
    expect(gexMetricCaption('delta')).toBe('Delta · OI-book sign')
  })
})

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
    const { bars, rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(bars).toEqual([])
    expect(rowHeight).toBe(0)
  })

  it('clips to the visible strikes only, ignoring off-screen ones for both drawing and peak scaling', () => {
    const strikes = [
      strike(10_000, 9999), // off-screen, must not win the peak or appear in bars
      strike(24_200, 100),
      strike(24_400, -50),
    ]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(bars.map((b) => b.strike)).toEqual([24_200, 24_400])
    // Peak is 100 (the larger of the two visible strikes), so the full-size
    // bar is exactly columnWidth, not scaled down by the off-screen 9999.
    expect(bars.find((b) => b.strike === 24_200)?.length).toBe(120)
  })

  it('does not divide by zero when every visible strike has net_gex 0', () => {
    const strikes = [strike(24_200, 0), strike(24_400, 0)]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(bars).toHaveLength(2)
    for (const b of bars) {
      expect(b.length).toBe(0)
      expect(Number.isNaN(b.length)).toBe(false)
    }
  })

  it('assigns sign correctly: net_gex >= 0 is positive, negative is not', () => {
    const strikes = [strike(24_200, 0), strike(24_400, -1)]
    const { bars } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(bars.find((b) => b.strike === 24_200)?.positive).toBe(true)
    expect(bars.find((b) => b.strike === 24_400)?.positive).toBe(false)
  })

  it('falls back to a default row height for a single strike, without NaN', () => {
    const strikes = [strike(24_200, 50)]
    const { bars, rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(bars).toHaveLength(1)
    expect(rowHeight).toBeGreaterThan(0)
    expect(Number.isNaN(rowHeight)).toBe(false)
  })

  it('tiles bars with only a hairline gap at a wide but not extreme strike spacing', () => {
    // 150 price-units apart on this scale (400px / 1000pt range) is 60px - a
    // routine on-screen gap once zoomed in past the default view, and well
    // under the plotHeight/4 = 100px ceiling below. Row height should track
    // the gap almost exactly (gap - 1, the same -1 hairline the floor case
    // always had), not be clamped down to some fixed sliver regardless of
    // how much room the gap actually has.
    const strikes = [strike(24_200, 10), strike(24_350, -10)]
    const { rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(rowHeight).toBe(59)
  })

  it('caps row height at a proportion of the pane height, not a fixed px value, when only two strikes are visible', () => {
    // 900 price-units apart - near the full width of this fixture's 1000pt
    // domain, so both strikes land on screen (y=380 and y=20) rather than
    // one panning off it - maps to a 360px gap, most of the 400px pane.
    // Without a ceiling this would make one strike's bar swallow nearly the
    // whole column; with the plotHeight/4 = 100px ceiling, it is capped
    // instead of filling the pane.
    const strikes = [strike(24_050, 10), strike(24_950, -10)]
    const { rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'gamma')
    expect(rowHeight).toBe(100)
  })

  it('scales the ceiling with plotHeight rather than a value fixed at one pane size', () => {
    // Same strikes and 360px gap as above, but a taller pane: the cap should
    // grow with it (200px, not still 100px), proving the bound is genuinely
    // proportional and not just a second fixed constant that happens to
    // equal 400/4.
    const strikes = [strike(24_050, 10), strike(24_950, -10)]
    const { rowHeight } = computeGexBarGeometry(strikes, linearPriceToY, 800, 120, 'gamma')
    expect(rowHeight).toBe(200)
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
    // The gamma/delta test above already proves per-metric peak scaling (a
    // leaked gamma peak of 100 would turn delta's 24_400 assertion of 60 into
    // 48). This fixture exists on top of that for a starker regression
    // signal: a leaked peak here would shrink both bars to slivers (1.2px and
    // 0.6px) rather than a merely-wrong number, which is easier to miss.
    const strikes = [strike(24_200, 1000, 10), strike(24_400, 500, 5)]

    const delta = computeGexBarGeometry(strikes, linearPriceToY, 400, 120, 'delta')
    expect(delta.bars.find((b) => b.strike === 24_200)?.length).toBe(120)
    expect(delta.bars.find((b) => b.strike === 24_400)?.length).toBe(60)
  })
})

/**
 * `draw()` only ever calls a handful of `CanvasRenderingContext2D` methods -
 * this records the ones that matter (`fillText`) and no-ops the rest, which
 * is enough to stand in for a real canvas without jsdom needing one.
 */
function fakeCtx() {
  const texts: { text: string; x: number; y: number }[] = []
  return {
    texts,
    save() {},
    restore() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {},
    setLineDash() {},
    fillRect() {},
    fillText(text: string, x: number, y: number) {
      texts.push({ text, x, y })
    },
    strokeStyle: '',
    lineWidth: 0,
    fillStyle: '',
    font: '',
    textBaseline: '',
    textAlign: '',
    globalAlpha: 1,
  }
}

/** Only the `PrimitiveRenderContext` fields `draw()` actually reads. */
function fakeRc(overrides: { plotWidth?: number; plotHeight?: number; dpr?: number } = {}) {
  return {
    priceScale: { priceToY: (_price: number) => 200 },
    plotWidth: 400,
    plotHeight: 300,
    dpr: 1,
    theme: { axisText: '#888888', axisLine: '#333333' },
    ...overrides,
  }
}

describe('GexMetricCaptionPrimitive draw', () => {
  it('emits the caption text for the configured metric', () => {
    const ctx = fakeCtx()
    const primitive = new GexMetricCaptionPrimitive({
      showBars: true,
      hasBars: true,
      metric: 'delta',
    })
    primitive.draw(ctx as never, fakeRc() as never)
    expect(ctx.texts.map((t) => t.text)).toContain(gexMetricCaption('delta'))
  })

  it('positions the caption at the bar column axis, bottom-anchored and centred - side, columnInset, columnWidth and plotHeight all move it', () => {
    const ctx = fakeCtx()
    // side 'right', columnInset 0, columnWidth 120, plotWidth 400, dpr 1:
    // axisX = (400 - 0 - 120) * 1 = 280, matching gexColumnAxisX exactly -
    // this is the same formula GexLevelsPrimitive.drawBars uses for the bars
    // themselves, so a caption drifting from this value would sit over the
    // wrong part of the column. y = plotHeight*dpr - BAR_CAPTION_BOTTOM_PX*dpr
    // = 300 - 12 = 288: this is the axis the Critical review bug lived on
    // (the caption used to sit at the TOP of the plot, under the readout
    // card) - a regression back to the top must fail this assertion.
    const primitive = new GexMetricCaptionPrimitive({
      showBars: true,
      hasBars: true,
      metric: 'gamma',
      side: 'right',
      columnInset: 0,
      columnWidth: 120,
    })
    primitive.draw(ctx as never, fakeRc({ plotWidth: 400, plotHeight: 300, dpr: 1 }) as never)
    expect(ctx.texts[0]?.x).toBe(280)
    expect(ctx.texts[0]?.y).toBe(288)
    // The x/y assertions above only mean what they say if the text is
    // actually centred on x and anchored to its bottom at y - otherwise
    // "positioned at (280, 288)" could still render anywhere around that
    // point.
    expect(ctx.textAlign).toBe('center')
    expect(ctx.textBaseline).toBe('bottom')
  })

  it('draws nothing when the bar column is switched off, even with bar data present', () => {
    const ctx = fakeCtx()
    const primitive = new GexMetricCaptionPrimitive({
      showBars: false,
      hasBars: true,
      metric: 'gamma',
    })
    primitive.draw(ctx as never, fakeRc() as never)
    expect(ctx.texts).toEqual([])
  })

  it('draws nothing when there is no bar data, even with the bar column switched on', () => {
    // The showBars-off case above and this one are independent gates - see
    // GexMetricCaptionOptions.hasBars. This is the instrument-with-no-option-
    // chain case: showBars stays whatever the user last set it to, but there
    // is nothing on the chart for the caption to be captioning.
    const ctx = fakeCtx()
    const primitive = new GexMetricCaptionPrimitive({
      showBars: true,
      hasBars: false,
      metric: 'gamma',
    })
    primitive.draw(ctx as never, fakeRc() as never)
    expect(ctx.texts).toEqual([])
  })

  it('is always top zOrder, regardless of options', () => {
    const primitive = new GexMetricCaptionPrimitive()
    expect(primitive.zOrder()).toBe('top')
  })
})

describe('GexLevelsPrimitive draw does not draw the caption itself', () => {
  // GexLevelsPrimitive is zOrder 'bottom' (see its own zOrder()); if the
  // caption were emitted from inside it too - a regression back to how this
  // primitive worked before the caption moved to its own zOrder: 'top'
  // primitive - it would once again be paintable-over by the candles that
  // render between the two. This pins that the split actually happened,
  // not just that GexMetricCaptionPrimitive independently works. Reuses the
  // module-level `strike` helper defined above for `computeGexBarGeometry`.

  it('never emits caption text, even with bars showing and data loaded', () => {
    const ctx = fakeCtx()
    const primitive = new GexLevelsPrimitive({
      showBars: true,
      showCallWall: false,
      showPutWall: false,
      showZeroGamma: false,
      metric: 'delta',
    })
    primitive.setData({
      status: 'success',
      strikes: [strike(24_200, 100), strike(24_400, -50)],
    } as never)
    primitive.draw(ctx as never, fakeRc() as never)
    expect(ctx.texts.map((t) => t.text)).not.toContain(gexMetricCaption('delta'))
    expect(ctx.texts.map((t) => t.text)).not.toContain(gexMetricCaption('gamma'))
  })
})
