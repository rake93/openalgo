/**
 * `draw()`/`hitTest()`-level suite for the two GEX Levels canvas primitives.
 * jsdom provides no canvas, but `draw()` only ever calls a handful of
 * `CanvasRenderingContext2D` methods, so a plain object recording those calls
 * is enough to pin what actually reaches the screen - see `fakeCtx` / `fakeRc`
 * below. The pure geometry/formatting these primitives are built on has its
 * own test file, `gex-levels-geometry.test.ts`, with no fake ctx at all.
 */

import { describe, expect, it } from 'vitest'
import type { GEXStrikeLevel } from '@/api/gex'
import { formatGexPrice, gexMetricCaption, gexStrikeExternalId } from './gex-levels-geometry'
import {
  DEFAULT_GEX_PRIMITIVE_OPTIONS,
  GexLevelsPrimitive,
  GexOverlayPrimitive,
} from './gex-levels-primitive'

// netDex defaults to netGex so every existing fixture renders identically
// under either metric without being touched. Only the metric-selection
// tests pass a distinct third argument, since only those need to prove
// gamma and delta actually diverge. Duplicated from gex-levels-geometry.test.ts
// rather than imported across sibling test files - both halves stay
// self-contained.
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
