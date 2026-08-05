/**
 * `draw()`-level suite for the Gamma Bands primitive.
 *
 * jsdom has no canvas, so a plain object records the handful of
 * `CanvasRenderingContext2D` calls `draw()` actually makes - the same approach
 * `gex-levels-primitive.test.ts` takes. The segment logic these paths are built
 * on is tested with no fake ctx at all in `gex-bands-geometry.test.ts`.
 *
 * What is worth pinning here rather than there: that each band ends up on the
 * canvas as SEPARATE paths per segment. A single path spanning a gap is exactly
 * the bug the geometry exists to prevent, and it is invisible to a test that
 * only counts points.
 */

import { describe, expect, it } from 'vitest'
import type { GexBandSeries } from './gex-bands-primitive'
import { DEFAULT_GEX_BANDS_OPTIONS, GexBandsPrimitive } from './gex-bands-primitive'

const M = 60
const T0 = 1_754_000_040

function series(overrides: Partial<GexBandSeries> = {}): GexBandSeries {
  return {
    points: [
      { ts: T0, call_wall: 24800, put_wall: 24400, zero_gamma: 24600 },
      { ts: T0 + M, call_wall: 24800, put_wall: 24400, zero_gamma: 24610 },
      { ts: T0 + 2 * M, call_wall: 24850, put_wall: 24400, zero_gamma: 24620 },
    ],
    ...overrides,
  }
}

/**
 * Records path construction so a test can tell one continuous line from several.
 * `beginPath` starts a new run; every `moveTo`/`lineTo` appends to it.
 */
function fakeCtx() {
  const paths: { points: [number, number][]; stroke: string; width: number }[] = []
  let current: { points: [number, number][]; stroke: string; width: number } | null = null
  const arcs: { x: number; y: number; fill: string }[] = []
  const fills: { points: [number, number][]; fill: string; alpha: number }[] = []

  const ctx = {
    paths,
    arcs,
    fills,
    save() {},
    restore() {},
    setLineDash() {},
    closePath() {},
    rect() {},
    clip() {},
    fill() {
      // A fill with a real outline is a corridor; the lone-point dot also
      // fills, so only outlines with vertices count.
      if (current && current.points.length > 2) {
        fills.push({ points: current.points, fill: ctx.fillStyle, alpha: ctx.globalAlpha })
      }
    },
    beginPath() {
      current = { points: [], stroke: '', width: 0 }
      paths.push(current)
    },
    moveTo(x: number, y: number) {
      current?.points.push([x, y])
    },
    lineTo(x: number, y: number) {
      current?.points.push([x, y])
    },
    arc(x: number, y: number) {
      arcs.push({ x, y, fill: ctx.fillStyle })
    },
    stroke() {
      if (current) {
        current.stroke = ctx.strokeStyle
        current.width = ctx.lineWidth
      }
    },
    strokeStyle: '',
    fillStyle: '',
    lineWidth: 0,
    globalAlpha: 1,
  }
  return ctx
}

/** Only the `PrimitiveRenderContext` fields `draw()` reads. */
function fakeRc(overrides: Record<string, unknown> = {}) {
  return {
    priceScale: { priceToY: (price: number) => 400 * (1 - (price - 24_000) / 1_000) },
    // A minute per bar starting at T0, so ts -> index is (ts - T0) / 60.
    dataLayer: { timeToIndexFloat: (time: number) => (time - T0) / M },
    timeScale: { indexToX: (index: number) => 100 + index * 10 },
    plotWidth: 400,
    plotHeight: 400,
    dpr: 1,
    theme: { axisText: '#888888', axisLine: '#333333', background: '#111111' },
    ...overrides,
  }
}

/** Paths that actually got stroked in a given colour. */
function pathsFor(ctx: ReturnType<typeof fakeCtx>, colour: string) {
  return ctx.paths.filter((p) => p.stroke === colour)
}

describe('GexBandsPrimitive draw', () => {
  it('draws all three bands when all three are enabled', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.setData(series())
    primitive.draw(ctx as never, fakeRc() as never)

    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.callColor)).toHaveLength(1)
    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.putColor)).toHaveLength(1)
    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.zeroGammaColor)).toHaveLength(1)
  })

  it('draws nothing for a band that is switched off', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive({ showPutWall: false })
    primitive.setData(series())
    primitive.draw(ctx as never, fakeRc() as never)

    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.callColor)).toHaveLength(1)
    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.putColor)).toHaveLength(0)
  })

  it('issues a separate path per segment so the line never joins across a gap', () => {
    // THE test in this file. One path spanning the outage would draw a level
    // nobody observed, and no assertion about point counts would catch it.
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.setData({
      points: [
        { ts: T0, call_wall: 24800, put_wall: null, zero_gamma: null },
        { ts: T0 + M, call_wall: 24800, put_wall: null, zero_gamma: null },
        // Ten minutes missing.
        { ts: T0 + 11 * M, call_wall: 24900, put_wall: null, zero_gamma: null },
        { ts: T0 + 12 * M, call_wall: 24900, put_wall: null, zero_gamma: null },
      ],
    })
    primitive.draw(ctx as never, fakeRc() as never)

    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.callColor)).toHaveLength(2)
  })

  it('steps rather than sloping when a wall moves strike', () => {
    // A wall sits at a strike until it moves to another strike. A straight
    // diagonal would imply the level passed through prices between them, which
    // no strike ever occupied.
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive({ showPutWall: false, showZeroGamma: false })
    primitive.setData({
      points: [
        { ts: T0, call_wall: 24800, put_wall: null, zero_gamma: null },
        { ts: T0 + M, call_wall: 24900, put_wall: null, zero_gamma: null },
      ],
    })
    primitive.draw(ctx as never, fakeRc() as never)

    const path = pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.callColor)[0]
    const yFor = (price: number) => 400 * (1 - (price - 24_000) / 1_000)
    // Three vertices: hold at the old level to the new x, then jump.
    expect(path.points).toEqual([
      [100, yFor(24800)],
      [110, yFor(24800)],
      [110, yFor(24900)],
    ])
  })

  it('maps a timestamp through the data layer rather than assuming bar spacing', () => {
    // timeToIndexFloat, not timeToIndex: snapshots are minute-floored and the
    // chart may be on any timeframe, so an exact-match lookup would silently
    // drop four of every five points on a 5-minute chart.
    const seen: number[] = []
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive({ showPutWall: false, showZeroGamma: false })
    primitive.setData(series())
    primitive.draw(
      ctx as never,
      fakeRc({
        dataLayer: {
          timeToIndexFloat: (time: number) => {
            seen.push(time)
            return (time - T0) / M
          },
        },
      }) as never
    )

    expect(seen).toEqual([T0, T0 + M, T0 + 2 * M])
  })

  it('marks a lone reading with a dot, since a single point cannot be a line', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive({ showPutWall: false, showZeroGamma: false })
    primitive.setData({
      points: [
        { ts: T0, call_wall: 24800, put_wall: null, zero_gamma: null },
        { ts: T0 + 60 * M, call_wall: 24900, put_wall: null, zero_gamma: null },
      ],
    })
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.arcs).toHaveLength(2)
  })

  it('draws nothing at all when there is no history', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.setData({ points: [] })
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.paths.filter((p) => p.points.length > 0)).toHaveLength(0)
  })

  it('draws nothing before any history has arrived', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.paths).toHaveLength(0)
  })

  it('skips a band whose every reading is null without touching the others', () => {
    // zero_gamma is null for a whole session whenever the profile never crosses
    // zero near the forward. That is an ordinary state, not a reason to stop
    // drawing the walls.
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.setData({
      points: [
        { ts: T0, call_wall: 24800, put_wall: 24400, zero_gamma: null },
        { ts: T0 + M, call_wall: 24800, put_wall: 24400, zero_gamma: null },
      ],
    })
    primitive.draw(ctx as never, fakeRc() as never)

    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.zeroGammaColor)).toHaveLength(0)
    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.callColor)).toHaveLength(1)
  })

  it('sits behind price action', () => {
    expect(new GexBandsPrimitive().zOrder()).toBe('bottom')
  })

  it('contributes nothing to autoscale', () => {
    // The trap profiles.ts documents three times and gex-levels-primitive.ts
    // documents again: a primitive reporting its own extent drags the price
    // scale out and squashes the candles into a sliver. Walls sit far from
    // price, so this one would be especially bad.
    expect(
      (new GexBandsPrimitive() as unknown as { autoscaleInfo?: unknown }).autoscaleInfo
    ).toBeUndefined()
  })
})

describe('GexBandsPrimitive corridor', () => {
  it('shades between the two walls', () => {
    // The corridor is what makes this read as BANDS rather than as three more
    // lines on a chart that already carries a VWAP, three dashed live levels
    // and the candles.
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.setData(series())
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.fills).toHaveLength(1)
    expect(ctx.fills[0].fill).toBe(DEFAULT_GEX_BANDS_OPTIONS.corridorColor)
    expect(ctx.fills[0].alpha).toBe(DEFAULT_GEX_BANDS_OPTIONS.corridorOpacity)
  })

  it('breaks the shading wherever the walls break', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.setData({
      points: [
        { ts: T0, call_wall: 24800, put_wall: 24400, zero_gamma: null },
        { ts: T0 + M, call_wall: 24800, put_wall: 24400, zero_gamma: null },
        { ts: T0 + 11 * M, call_wall: 24900, put_wall: 24450, zero_gamma: null },
        { ts: T0 + 12 * M, call_wall: 24900, put_wall: 24450, zero_gamma: null },
      ],
    })
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.fills).toHaveLength(2)
  })

  it('does not shade up to a wall the reader cannot see', () => {
    // Hiding one edge but keeping the fill would assert a boundary with
    // nothing on screen to justify it.
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive({ showPutWall: false })
    primitive.setData(series())
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.fills).toHaveLength(0)
  })

  it('can be switched off without losing the wall lines', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive({ showCorridor: false })
    primitive.setData(series())
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.fills).toHaveLength(0)
    expect(pathsFor(ctx, DEFAULT_GEX_BANDS_OPTIONS.callColor)).toHaveLength(1)
  })

  it('closes the fill along the same step outline the edges stroke', () => {
    // A fill that sloped where its edge stepped would leave a visible sliver
    // at every wall move.
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive({ showZeroGamma: false })
    primitive.setData({
      points: [
        { ts: T0, call_wall: 24800, put_wall: 24400, zero_gamma: null },
        { ts: T0 + M, call_wall: 24900, put_wall: 24400, zero_gamma: null },
      ],
    })
    primitive.draw(ctx as never, fakeRc() as never)

    const yFor = (price: number) => 400 * (1 - (price - 24_000) / 1_000)
    // Upper edge forward with its step, then the lower edge back.
    expect(ctx.fills[0].points).toEqual([
      [100, yFor(24800)],
      [110, yFor(24800)],
      [110, yFor(24900)],
      [110, yFor(24400)],
      [100, yFor(24400)],
    ])
  })

  it('skips a corridor with only one minute in it', () => {
    const ctx = fakeCtx()
    const primitive = new GexBandsPrimitive()
    primitive.setData({
      points: [{ ts: T0, call_wall: 24800, put_wall: 24400, zero_gamma: null }],
    })
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.fills).toHaveLength(0)
  })
})
