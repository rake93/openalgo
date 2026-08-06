/**
 * `draw()`-level suite for the GEX Heatmap primitive.
 *
 * jsdom has no canvas, so a plain object records the `fillRect` calls `draw()`
 * makes - the same approach the other two GEX primitive suites take. The cell
 * layout and colour scale are tested with no fake ctx at all in
 * `gex-heatmap-geometry.test.ts`.
 *
 * What is worth pinning here rather than there: that a cell with no reading
 * reaches the canvas as NO RECT. A test that only checks colours cannot tell a
 * blank cell from a faint one.
 */

import { describe, expect, it, vi } from 'vitest'
import * as geometry from './gex-heatmap-geometry'
import type { GexHeatmapData } from './gex-heatmap-primitive'
import { GexHeatmapPrimitive } from './gex-heatmap-primitive'

vi.mock('./gex-heatmap-geometry', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./gex-heatmap-geometry')>()
  return { ...actual, heatmapCellFill: vi.fn(actual.heatmapCellFill) }
})

const M = 60
const T0 = 1_785_000_000

function data(overrides: Partial<GexHeatmapData> = {}): GexHeatmapData {
  return {
    strikes: [24_000, 24_100],
    columns: [
      { ts: T0, values: [10, -20], quality: 'good' },
      { ts: T0 + M, values: [15, -25], quality: 'good' },
    ],
    maxAbsValue: 25,
    resolutionSeconds: 60,
    ...overrides,
  }
}

function fakeCtx() {
  const rects: { x: number; y: number; w: number; h: number; fill: string; alpha: number }[] = []
  return {
    rects,
    save() {},
    restore() {},
    beginPath() {},
    fillRect(x: number, y: number, w: number, h: number) {
      rects.push({ x, y, w, h, fill: this.fillStyle, alpha: this.globalAlpha })
    },
    fillStyle: '',
    globalAlpha: 1,
  }
}

function fakeRc(overrides: Record<string, unknown> = {}) {
  return {
    // Maps the fixture's strike ladder into the plot: 24,300 sits at the top
    // edge and every 0.5 points is a pixel, so 24,000-24,200 lands at y 200-600.
    priceScale: { priceToY: (price: number) => (24_300 - price) * 2 },
    dataLayer: { timeToIndexFloat: (time: number) => (time - T0) / M },
    timeScale: { indexToX: (index: number) => index * 10 },
    plotWidth: 400,
    plotHeight: 1000,
    dpr: 1,
    theme: { axisText: '#888888', axisLine: '#333333', background: '#111111' },
    ...overrides,
  }
}

describe('GexHeatmapPrimitive draw', () => {
  it('paints one rect per recorded cell', () => {
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(data())
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.rects).toHaveLength(4)
  })

  it('draws nothing at all for a cell with no reading', () => {
    // THE test in this file. A null is a strike that minute's chain did not
    // carry, and there is no faint version of that - the background must show.
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(
      data({
        columns: [
          { ts: T0, values: [10, null], quality: 'good' },
          { ts: T0 + M, values: [null, null], quality: 'good' },
        ],
      })
    )
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.rects).toHaveLength(1)
  })

  it('leaves an outage open instead of stretching a column across it', () => {
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(
      data({
        columns: [
          { ts: T0, values: [10, -20], quality: 'good' },
          // Ten minutes missing.
          { ts: T0 + 10 * M, values: [15, -25], quality: 'good' },
        ],
      })
    )
    primitive.draw(ctx as never, fakeRc() as never)

    // One cadence is 10px on this axis, so the first column must stop at x=10
    // rather than run on to the second column at x=100.
    const first = ctx.rects.filter((r) => r.x === 0)
    expect(first).toHaveLength(2)
    expect(first[0].w).toBe(10)
  })

  it('encodes sign as hue', () => {
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(data())
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.rects.some((r) => r.fill.includes('38, 166, 154'))).toBe(true)
    expect(ctx.rects.some((r) => r.fill.includes('239, 83, 80'))).toBe(true)
  })

  it('dims a column recorded as degraded', () => {
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(
      data({
        columns: [
          { ts: T0, values: [25, null], quality: 'good' },
          { ts: T0 + M, values: [25, null], quality: 'degraded' },
        ],
      })
    )
    primitive.draw(ctx as never, fakeRc() as never)

    const alphaOf = (fill: string) => Number(/([\d.]+)\)$/.exec(fill)?.[1])
    expect(alphaOf(ctx.rects[1].fill)).toBeLessThan(alphaOf(ctx.rects[0].fill))
  })

  it('tiles rows with no seam between neighbouring strikes', () => {
    // A fractional edge at 48 rows leaves background showing between cells,
    // which reads as a grid of missing readings rather than a field.
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(data({ strikes: [24_000, 24_100, 24_200] }))
    primitive.setData(
      data({
        strikes: [24_000, 24_100, 24_200],
        columns: [{ ts: T0, values: [1, 2, 3], quality: 'good' }],
      })
    )
    primitive.draw(ctx as never, fakeRc() as never)

    const sorted = [...ctx.rects].sort((a, b) => a.y - b.y)
    for (let i = 1; i < sorted.length; i += 1) {
      expect(sorted[i].y).toBe(sorted[i - 1].y + sorted[i - 1].h)
    }
  })

  it('skips columns that are off-screen', () => {
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(
      data({
        columns: [
          { ts: T0, values: [10, -20], quality: 'good' },
          // x = 1000, well past a 400px plot.
          { ts: T0 + 100 * M, values: [15, -25], quality: 'good' },
        ],
      })
    )
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.rects).toHaveLength(2)
  })

  it('draws nothing when there is no data', () => {
    const ctx = fakeCtx()
    const primitive = new GexHeatmapPrimitive()
    primitive.draw(ctx as never, fakeRc() as never)
    primitive.setData(null)
    primitive.draw(ctx as never, fakeRc() as never)
    primitive.setData(data({ columns: [] }))
    primitive.draw(ctx as never, fakeRc() as never)

    expect(ctx.rects).toEqual([])
  })

  it('contributes nothing to autoscale', () => {
    // This one spans the whole strike window - 2,350 points on a live NIFTY
    // chain - so reporting its extent would flatten the candles hardest of all.
    expect(
      (new GexHeatmapPrimitive() as unknown as { autoscaleInfo?: unknown }).autoscaleInfo
    ).toBeUndefined()
  })

  it('is drawn behind price action', () => {
    expect(new GexHeatmapPrimitive().zOrder()).toBe('bottom')
  })
})

describe('GexHeatmapPrimitive cell colour cache', () => {
  it('builds each cell colour once and reuses it across redraws', () => {
    // 114 columns x 48 strikes is 5,472 rgba strings on a live window. Colour
    // depends on the value, the window maximum and the options - never on the
    // viewport - so rebuilding it per frame is pure waste.
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(data())
    vi.mocked(geometry.heatmapCellFill).mockClear()

    primitive.draw(fakeCtx() as never, fakeRc() as never)
    const first = vi.mocked(geometry.heatmapCellFill).mock.calls.length
    expect(first).toBe(4)

    // A pan and a zoom: new viewport, same data.
    primitive.draw(
      fakeCtx() as never,
      fakeRc({ timeScale: { indexToX: (i: number) => i * 25 } }) as never
    )
    primitive.draw(fakeCtx() as never, fakeRc({ dpr: 2 }) as never)

    expect(vi.mocked(geometry.heatmapCellFill).mock.calls.length).toBe(first)
  })

  it('rebuilds after the data changes', () => {
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(data())
    primitive.draw(fakeCtx() as never, fakeRc() as never)
    vi.mocked(geometry.heatmapCellFill).mockClear()

    primitive.setData(data())
    primitive.draw(fakeCtx() as never, fakeRc() as never)

    expect(vi.mocked(geometry.heatmapCellFill).mock.calls.length).toBeGreaterThan(0)
  })

  it('rebuilds after the colours change', () => {
    const primitive = new GexHeatmapPrimitive()
    primitive.setData(data())
    primitive.draw(fakeCtx() as never, fakeRc() as never)
    vi.mocked(geometry.heatmapCellFill).mockClear()

    primitive.setOptions({ maxAlpha: 0.9 })
    primitive.draw(fakeCtx() as never, fakeRc() as never)

    expect(vi.mocked(geometry.heatmapCellFill).mock.calls.length).toBeGreaterThan(0)
  })
})
