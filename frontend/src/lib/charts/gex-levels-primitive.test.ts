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
  computeGexReadoutBoxGeometry,
  DEFAULT_GEX_PRIMITIVE_OPTIONS,
  formatGexMoney,
  formatGexPrice,
  GexLevelsPrimitive,
  GexOverlayPrimitive,
  gexHitTestStrike,
  gexMetricCaption,
  gexReadoutLines,
  gexStrikeExternalId,
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

describe('formatGexMoney', () => {
  it('formats crore-scale values with the Cr suffix', () => {
    expect(formatGexMoney(13_181_000_000)).toBe('1318.10 Cr')
  })

  it('keeps the sign on a negative crore-scale value', () => {
    expect(formatGexMoney(-13_181_000_000)).toBe('-1318.10 Cr')
  })

  it('formats lakh-scale values with the L suffix', () => {
    expect(formatGexMoney(6_500_000)).toBe('65.00 L')
  })

  it('formats anything smaller as a grouped plain integer', () => {
    expect(formatGexMoney(12_345)).toBe('12,345')
  })

  it('is an em dash for null, undefined and non-finite values', () => {
    expect(formatGexMoney(null)).toBe('—')
    expect(formatGexMoney(undefined)).toBe('—')
    expect(formatGexMoney(Number.NaN)).toBe('—')
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

describe('gexHitTestStrike', () => {
  const bars = [
    { strike: 24_500, y: 100, length: 60, positive: true },
    { strike: 24_600, y: 200, length: 120, positive: true },
  ]
  // Fixed geometry shared by every case below; each `it` only overrides x/y
  // (and occasionally `bars`), keeping the object-literal call sites focused
  // on what actually varies per scenario.
  const base = {
    rowHeight: 40,
    plotWidth: 300,
    columnWidth: 120,
    side: 'right' as const,
    columnInset: 0,
  }

  it('returns the strike whose row band contains the point', () => {
    // rowHeight 40 means each band is +/-20px around its y.
    expect(gexHitTestStrike({ ...base, bars, x: 290, y: 195 })?.strike).toBe(24_600)
  })

  it('returns null above and below every band', () => {
    // A point strictly between the two bands...
    expect(gexHitTestStrike({ ...base, bars, x: 290, y: 150 })).toBeNull()
    // ...and points past the topmost band's outer edge (80..120) and the
    // bottommost band's outer edge (180..220) - "above and below every
    // band" as the test name claims, not just the gap between them.
    expect(gexHitTestStrike({ ...base, bars, x: 290, y: 50 })).toBeNull()
    expect(gexHitTestStrike({ ...base, bars, x: 290, y: 260 })).toBeNull()
  })

  it('returns null outside the column horizontally', () => {
    // axisX for plotWidth 300, columnWidth 120, right side is 180; the column
    // spans 60..300. A point at x=20 is in the chart body, not the column.
    expect(gexHitTestStrike({ ...base, bars, x: 20, y: 195 })).toBeNull()
  })

  it('picks the nearer row when two bands touch', () => {
    const touching = [
      { strike: 24_500, y: 100, length: 60, positive: true },
      { strike: 24_600, y: 140, length: 60, positive: true },
    ]
    expect(gexHitTestStrike({ ...base, bars: touching, x: 290, y: 121 })?.strike).toBe(24_600)
    expect(gexHitTestStrike({ ...base, bars: touching, x: 290, y: 119 })?.strike).toBe(24_500)
  })

  it('breaks an exact tie by array order, not by picking neither', () => {
    // y=120 is the shared boundary of touching's two bands (24_500: 80..120,
    // 24_600: 120..160) - equidistant from both centres (100 and 140, 20px
    // either way). The docstring pins this as "earlier bar in `bars` wins",
    // not "first band scanned" or an arbitrary pick; a change from strict
    // `<` to `<=` when updating the best match would flip this silently.
    const touching = [
      { strike: 24_500, y: 100, length: 60, positive: true },
      { strike: 24_600, y: 140, length: 60, positive: true },
    ]
    expect(gexHitTestStrike({ ...base, bars: touching, x: 290, y: 120 })?.strike).toBe(24_500)
  })

  it('returns null for an empty bar list', () => {
    expect(gexHitTestStrike({ ...base, bars: [], x: 290, y: 195 })).toBeNull()
  })

  it('hit-tests the left-anchored column too', () => {
    // side 'left' puts the axis at columnInset + columnWidth = 120, so the
    // column spans 0..240 and a point at x=290 is now OUTSIDE it.
    expect(gexHitTestStrike({ ...base, bars, side: 'left', x: 290, y: 195 })).toBeNull()
    expect(gexHitTestStrike({ ...base, bars, side: 'left', x: 200, y: 195 })?.strike).toBe(24_600)
  })
})

describe('gexStrikeExternalId', () => {
  it('encodes a whole-number strike', () => {
    expect(gexStrikeExternalId(24_000)).toBe('gex-strike-24000')
  })

  it('encodes a fractional strike exactly, not rounded or truncated - routine on stock options like VEDL25APR24292.5CE', () => {
    expect(gexStrikeExternalId(292.5)).toBe('gex-strike-292.5')
  })

  it('does not collide two different strikes', () => {
    expect(gexStrikeExternalId(24_000)).not.toBe(gexStrikeExternalId(24_600))
  })
})

describe('gexReadoutLines', () => {
  const colors = { headerColor: '#eeeeee', callColor: '#26a69a', putColor: '#ef5350' }

  it('shows the strike header and both metric rows, with the active metric emphasised - the whole point is comparing them', () => {
    // Live-measurement fixture from the plan: strike 24000 is -1318 Cr gamma
    // but +679 Cr delta - put-dominant under one metric, call-dominant under
    // the other, and being able to see that without toggling is the feature.
    const lines = gexReadoutLines({
      strike: 24_000,
      netGex: -13_181_000_000,
      netDex: 6_794_000_000,
      metric: 'gamma',
      isCallWall: false,
      isPutWall: false,
      ...colors,
    })
    expect(lines).toEqual([
      { text: '24000', emphasis: true, color: colors.headerColor },
      { text: 'GEX  -1318.10 Cr', emphasis: true, color: colors.putColor },
      { text: 'DEX  +679.40 Cr', emphasis: false, color: colors.callColor },
    ])
  })

  it('flips which row is emphasised when the active metric is delta, not gamma', () => {
    const lines = gexReadoutLines({
      strike: 24_000,
      netGex: -13_181_000_000,
      netDex: 6_794_000_000,
      metric: 'delta',
      isCallWall: false,
      isPutWall: false,
      ...colors,
    })
    expect(lines[1]?.emphasis).toBe(false) // GEX
    expect(lines[2]?.emphasis).toBe(true) // DEX
  })

  it('signs a non-negative reading with an explicit +, unlike formatGexMoney alone', () => {
    const lines = gexReadoutLines({
      strike: 24_000,
      netGex: 0,
      netDex: 0,
      metric: 'gamma',
      isCallWall: false,
      isPutWall: false,
      ...colors,
    })
    expect(lines[1]?.text).toBe(`GEX  +${formatGexMoney(0)}`)
    expect(lines[2]?.text).toBe(`DEX  +${formatGexMoney(0)}`)
  })

  it('has no wall line for a strike that is neither the call wall nor the put wall', () => {
    const lines = gexReadoutLines({
      strike: 24_100,
      netGex: 10,
      netDex: -10,
      metric: 'gamma',
      isCallWall: false,
      isPutWall: false,
      ...colors,
    })
    expect(lines).toHaveLength(3)
  })

  it('appends a Call wall line only for the call-wall strike', () => {
    const lines = gexReadoutLines({
      strike: 24_800,
      netGex: 100,
      netDex: 100,
      metric: 'gamma',
      isCallWall: true,
      isPutWall: false,
      ...colors,
    })
    expect(lines).toHaveLength(4)
    expect(lines[3]).toEqual({ text: 'Call wall', emphasis: false, color: colors.callColor })
  })

  it('appends a Put wall line only for the put-wall strike', () => {
    const lines = gexReadoutLines({
      strike: 23_500,
      netGex: -100,
      netDex: -100,
      metric: 'gamma',
      isCallWall: false,
      isPutWall: true,
      ...colors,
    })
    expect(lines).toHaveLength(4)
    expect(lines[3]).toEqual({ text: 'Put wall', emphasis: false, color: colors.putColor })
  })
})

describe('computeGexReadoutBoxGeometry', () => {
  const base = {
    boxWidth: 100,
    boxHeight: 80,
    plotWidth: 400,
    plotHeight: 300,
    columnInset: 0,
    columnWidth: 120,
    gap: 10,
  }

  it('places the box on the side with more room, vertically centred on a mid-plot row', () => {
    // side 'right': axisX = 400-120 = 280, roomLeft = 160, roomRight = 0, so
    // the box goes left of the column: x = 280-120-10-100 = 50.
    // rowY 150 centred: y = 150 - 80/2 = 110 - well inside [0, 300-80=220].
    expect(computeGexReadoutBoxGeometry({ ...base, rowY: 150, side: 'right' })).toEqual({
      x: 50,
      y: 110,
      width: 100,
      height: 80,
    })
  })

  it('chooses the other side when the column is left-anchored, without needing a separate code path', () => {
    // side 'left': axisX = 0+120 = 120, roomLeft = 0, roomRight = 400-240 =
    // 160, so the box goes right of the column: x = 120+120+10 = 250.
    const box = computeGexReadoutBoxGeometry({ ...base, rowY: 150, side: 'left' })
    expect(box.x).toBe(250)
    expect(box.x).toBeGreaterThan(120) // right of the left-anchored axis
  })

  it('clamps to the plot top for the topmost visible strike, never a negative y', () => {
    const box = computeGexReadoutBoxGeometry({ ...base, rowY: 5, side: 'right' })
    expect(box.y).toBe(0)
  })

  it('clamps to the plot bottom for the bottommost visible strike, never past plotHeight', () => {
    const box = computeGexReadoutBoxGeometry({ ...base, rowY: 295, side: 'right' })
    expect(box.y).toBe(base.plotHeight - base.boxHeight) // 220
  })

  it('clamps horizontally too, when the box is wider than the room on its chosen side', () => {
    // Same 'right' setup as the first case, but a box wide enough (250) that
    // the natural left-of-column position (280-120-10-250 = -100) would run
    // off the left edge of the plot entirely.
    const box = computeGexReadoutBoxGeometry({ ...base, rowY: 150, side: 'right', boxWidth: 250 })
    expect(box.x).toBe(0)
  })
})

/**
 * `draw()` only ever calls a handful of `CanvasRenderingContext2D` methods -
 * this records the ones that matter and no-ops the rest, which is enough to
 * stand in for a real canvas without jsdom needing one.
 *
 * `fillText`/`fillRect`/`strokeRect` capture the mutable style state
 * (`globalAlpha`, `font`, `fillStyle`, `strokeStyle`) at the moment they are
 * called, not just their own arguments - a real canvas applies whatever the
 * context's current state is at draw time, and a primitive that sets
 * `globalAlpha` then draws is only correctly tested if the fake records what
 * was actually in effect. A version of this fake that dropped that state let
 * a flipped emphasis ternary (dimming the active row and brightening the
 * inactive one) pass all 308 tests - only the text content was ever checked.
 */
function fakeCtx() {
  const texts: { text: string; x: number; y: number; alpha: number; font: string; fill: string }[] =
    []
  const fillRects: { x: number; y: number; w: number; h: number; alpha: number; fill: string }[] =
    []
  const strokeRects: {
    x: number
    y: number
    w: number
    h: number
    stroke: string
    dash: readonly number[]
  }[] = []
  let dash: readonly number[] = []
  return {
    texts,
    fillRects,
    strokeRects,
    save() {},
    restore() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() {},
    rect() {},
    clip() {},
    setLineDash(segments: readonly number[]) {
      dash = segments
    },
    fillRect(x: number, y: number, w: number, h: number) {
      fillRects.push({ x, y, w, h, alpha: this.globalAlpha, fill: this.fillStyle })
    },
    strokeRect(x: number, y: number, w: number, h: number) {
      strokeRects.push({ x, y, w, h, stroke: this.strokeStyle, dash })
    },
    fillText(text: string, x: number, y: number) {
      texts.push({ text, x, y, alpha: this.globalAlpha, font: this.font, fill: this.fillStyle })
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

/** Only the `PrimitiveRenderContext` fields `draw()`/`hitTest()` actually read. */
function fakeRc(
  overrides: {
    plotWidth?: number
    plotHeight?: number
    dpr?: number
    priceScale?: { priceToY: (price: number) => number }
    hoverId?: string | null
  } = {}
) {
  return {
    priceScale: { priceToY: (_price: number) => 200 },
    plotWidth: 400,
    plotHeight: 300,
    dpr: 1,
    theme: { axisText: '#888888', axisLine: '#333333', background: '#111111' },
    ...overrides,
  }
}

describe('GexOverlayPrimitive draw', () => {
  it('emits the caption text for the configured metric', () => {
    const ctx = fakeCtx()
    const primitive = new GexOverlayPrimitive({
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
    const primitive = new GexOverlayPrimitive({
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
    const primitive = new GexOverlayPrimitive({
      showBars: false,
      hasBars: true,
      metric: 'gamma',
    })
    primitive.draw(ctx as never, fakeRc() as never)
    expect(ctx.texts).toEqual([])
  })

  it('draws nothing when there is no bar data, even with the bar column switched on', () => {
    // The showBars-off case above and this one are independent gates - see
    // GexOverlayOptions.hasBars. This is the instrument-with-no-option-
    // chain case: showBars stays whatever the user last set it to, but there
    // is nothing on the chart for the caption to be captioning.
    const ctx = fakeCtx()
    const primitive = new GexOverlayPrimitive({
      showBars: true,
      hasBars: false,
      metric: 'gamma',
    })
    primitive.draw(ctx as never, fakeRc() as never)
    expect(ctx.texts).toEqual([])
  })

  it('is always top zOrder, regardless of options', () => {
    const primitive = new GexOverlayPrimitive()
    expect(primitive.zOrder()).toBe('top')
  })
})

describe('GexLevelsPrimitive draw does not draw the caption itself', () => {
  // GexLevelsPrimitive is zOrder 'bottom' (see its own zOrder()); if the
  // caption were emitted from inside it too - a regression back to how this
  // primitive worked before the caption moved to its own zOrder: 'top'
  // primitive - it would once again be paintable-over by the candles that
  // render between the two. This pins that the split actually happened,
  // not just that GexOverlayPrimitive independently works. Reuses the
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

describe('GexOverlayPrimitive hitTest', () => {
  function makePrimitive(overrides: Record<string, unknown> = {}) {
    return new GexOverlayPrimitive({
      showBars: true,
      hasBars: true,
      side: 'right',
      columnWidth: 120,
      columnInset: 0,
      metric: 'gamma',
      strikes: [strike(24_200, 100), strike(24_400, -50)],
      callWall: null,
      putWall: null,
      ...overrides,
    } as never)
  }

  // linearPriceToY(24_200) = 320, linearPriceToY(24_400) = 240 (see the
  // fixture above computeGexBarGeometry) - both on-screen for a 400px plot.
  const rc = fakeRc({ plotWidth: 300, plotHeight: 400, priceScale: { priceToY: linearPriceToY } })

  it('returns a hit with the strike-encoded externalId over a bar', () => {
    const primitive = makePrimitive()
    const hit = primitive.hitTest(290, 320, rc as never)
    expect(hit?.externalId).toBe(gexStrikeExternalId(24_200))
    expect(hit?.zOrder).toBe('top')
  })

  it('never arms a drag - no draggable:true, no cursor:ns-resize - so pressing the column still pans', () => {
    // See chart.ts's _onPointerDown: hit.draggable === true, or cursor ===
    // 'ns-resize' with a drag callback registered, arms a drag instead of a
    // pan. A plain hover hit must set neither.
    const primitive = makePrimitive()
    const hit = primitive.hitTest(290, 320, rc as never)
    expect(hit?.draggable).toBeUndefined()
    expect(hit?.cursor).not.toBe('ns-resize')
  })

  it('returns null off the column horizontally', () => {
    const primitive = makePrimitive()
    expect(primitive.hitTest(20, 320, rc as never)).toBeNull()
  })

  it('returns null when hasBars is false, even with strikes present', () => {
    const primitive = makePrimitive({ hasBars: false })
    expect(primitive.hitTest(290, 320, rc as never)).toBeNull()
  })

  it('returns null when showBars is false', () => {
    const primitive = makePrimitive({ showBars: false })
    expect(primitive.hitTest(290, 320, rc as never)).toBeNull()
  })

  it('round-trips a fractional strike, like VEDL25APR24292.5CE', () => {
    // A dedicated priceToY calibrated for a stock-option strike range, not
    // the 24_000..25_000 index range linearPriceToY assumes.
    function stockPriceToY(price: number): number {
      const min = 280
      const max = 300
      const plotHeight = 400
      return plotHeight * (1 - (price - min) / (max - min))
    }
    const primitive = makePrimitive({ strikes: [strike(292.5, 40)] })
    const fractionalRc = fakeRc({
      plotWidth: 300,
      plotHeight: 400,
      priceScale: { priceToY: stockPriceToY },
    })
    // stockPriceToY(292.5) = 150 - dead centre of the single strike's row.
    const hit = primitive.hitTest(290, 150, fractionalRc as never)
    expect(hit?.externalId).toBe(gexStrikeExternalId(292.5))
  })
})

describe('GexOverlayPrimitive draw hover readout', () => {
  function makePrimitive(overrides: Record<string, unknown> = {}) {
    return new GexOverlayPrimitive({
      showBars: true,
      hasBars: true,
      side: 'right',
      columnWidth: 120,
      columnInset: 0,
      metric: 'gamma',
      strikes: [strike(24_200, 100), strike(24_400, -50)],
      callWall: 24_200,
      putWall: null,
      ...overrides,
    } as never)
  }

  function rc(hoverId: string | null) {
    return fakeRc({
      plotWidth: 300,
      plotHeight: 400,
      priceScale: { priceToY: linearPriceToY },
      hoverId,
    })
  }

  it('draws the readout for the hovered strike - both metric rows and the wall line - when hoverId matches', () => {
    const ctx = fakeCtx()
    const primitive = makePrimitive()
    primitive.draw(ctx as never, rc(gexStrikeExternalId(24_200)) as never)
    const texts = ctx.texts.map((t) => t.text)
    expect(texts).toContain(formatGexPrice(24_200))
    expect(texts.some((t) => t.startsWith('GEX'))).toBe(true)
    expect(texts.some((t) => t.startsWith('DEX'))).toBe(true)
    expect(texts).toContain('Call wall')
  })

  it('draws nothing beyond the metric caption when no strike is hovered', () => {
    const ctx = fakeCtx()
    const primitive = makePrimitive()
    primitive.draw(ctx as never, rc(null) as never)
    expect(ctx.texts.map((t) => t.text)).toEqual([gexMetricCaption('gamma')])
  })

  it('draws nothing beyond the metric caption when the hovered id does not resolve to a bar this frame', () => {
    // A stale id from the previous frame's data, momentarily, while a fresh
    // snapshot is still in flight - must fail closed, not throw or draw a
    // readout for data that no longer exists.
    const ctx = fakeCtx()
    const primitive = makePrimitive()
    primitive.draw(ctx as never, rc(gexStrikeExternalId(99_999)) as never)
    expect(ctx.texts.map((t) => t.text)).toEqual([gexMetricCaption('gamma')])
  })

  it('draws nothing at all - not even the caption - when hasBars is false, regardless of hoverId', () => {
    const ctx = fakeCtx()
    const primitive = makePrimitive({ hasBars: false })
    primitive.draw(ctx as never, rc(gexStrikeExternalId(24_200)) as never)
    expect(ctx.texts).toEqual([])
  })

  it('draws nothing at all when showBars is false, regardless of hoverId', () => {
    const ctx = fakeCtx()
    const primitive = makePrimitive({ showBars: false })
    primitive.draw(ctx as never, rc(gexStrikeExternalId(24_200)) as never)
    expect(ctx.texts).toEqual([])
  })

  it('paints the active metric row at full alpha and the inactive metric row dimmed, and flips which is which when metric flips', () => {
    // The text-content assertions above pass even if the emphasis->alpha
    // mapping is inverted, as long as gexReadoutLines' own emphasis boolean
    // is right - this is the assertion that would actually catch that.
    const ctxGamma = fakeCtx()
    makePrimitive({ metric: 'gamma' }).draw(
      ctxGamma as never,
      rc(gexStrikeExternalId(24_200)) as never
    )
    const [, header, gexRow, dexRow] = ctxGamma.texts
    expect(header?.alpha).toBe(1)
    expect(gexRow?.alpha).toBe(1) // GEX is the active row under gamma
    expect(dexRow?.alpha).toBe(0.6) // DEX is dimmed

    const ctxDelta = fakeCtx()
    makePrimitive({ metric: 'delta' }).draw(
      ctxDelta as never,
      rc(gexStrikeExternalId(24_200)) as never
    )
    const [, , gexRow2, dexRow2] = ctxDelta.texts
    expect(gexRow2?.alpha).toBe(0.6) // now GEX is dimmed
    expect(dexRow2?.alpha).toBe(1) // DEX is the active row under delta
  })

  it('sign-codes the GEX and DEX rows independently - the whole point of the feature is that they can point opposite ways for the same strike', () => {
    const ctx = fakeCtx()
    const primitive = makePrimitive({
      strikes: [strike(24_200, -50, 80)], // negative gamma, positive delta
      callWall: null,
    })
    primitive.draw(ctx as never, rc(gexStrikeExternalId(24_200)) as never)
    const [, , gexRow, dexRow] = ctx.texts
    expect(gexRow?.fill).toBe(DEFAULT_GEX_PRIMITIVE_OPTIONS.putColor)
    expect(dexRow?.fill).toBe(DEFAULT_GEX_PRIMITIVE_OPTIONS.callColor)
  })

  it('pins the readout box rect at dpr 2 - background fill and a pixel-snapped, undashed border', () => {
    const ctx = fakeCtx()
    const primitive = makePrimitive()
    const twoXRc = fakeRc({
      plotWidth: 300,
      plotHeight: 400,
      dpr: 2,
      priceScale: { priceToY: linearPriceToY },
      hoverId: gexStrikeExternalId(24_200),
    })
    primitive.draw(ctx as never, twoXRc as never)

    // box (CSS px) = {x:0, y:280, width:150, height:80} - see
    // computeGexReadoutBoxGeometry's own tests for that derivation; here it
    // is only the *dpr and pixel-snapping* on top of it under scrutiny.
    expect(ctx.fillRects).toContainEqual({
      x: 0,
      y: 560,
      w: 300,
      h: 160,
      alpha: 0.92,
      fill: '#111111',
    })
    expect(ctx.strokeRects).toContainEqual({
      x: 0.5,
      y: 560.5,
      w: 300,
      h: 160,
      stroke: '#333333',
      dash: [],
    })
  })
})
