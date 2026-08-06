import { describe, expect, it } from 'vitest'
import {
  computeColumnSpans,
  computeStrikeRows,
  DEFAULT_HEATMAP_COLORS,
  heatmapAlpha,
  heatmapCellFill,
} from './gex-heatmap-geometry'

const M = 60
const T0 = 1_785_000_000

/** A linear price->y map: higher price yields smaller y, like the real price scale. */
const yFor = (price: number) => 1000 - price

describe('heatmapAlpha', () => {
  it('gives no alpha at all to a cell with no reading', () => {
    // The gap rule. A null must be skipped, not drawn faintly - the reader is
    // being asked to tell "not recorded" from "recorded near zero".
    expect(heatmapAlpha(null, 100)).toBe(0)
    expect(heatmapAlpha(Number.NaN, 100)).toBe(0)
  })

  it('paints the window maximum at the ceiling', () => {
    expect(heatmapAlpha(100, 100)).toBeCloseTo(DEFAULT_HEATMAP_COLORS.maxAlpha, 6)
    expect(heatmapAlpha(-100, 100)).toBeCloseTo(DEFAULT_HEATMAP_COLORS.maxAlpha, 6)
  })

  it('holds a recorded near-zero cell on the floor rather than letting it vanish', () => {
    const alpha = heatmapAlpha(0, 100)

    expect(alpha).toBe(DEFAULT_HEATMAP_COLORS.minAlpha)
    expect(alpha).toBeGreaterThan(0)
  })

  it('compresses the tail so a heavy distribution is not two rows and a blank field', () => {
    // A tenth of the maximum reads as a third of the way up the ramp under the
    // square root, against a tenth on a linear one.
    const { minAlpha, maxAlpha } = DEFAULT_HEATMAP_COLORS
    const tenth = heatmapAlpha(10, 100)
    const linear = minAlpha + (maxAlpha - minAlpha) * 0.1

    expect(tenth).toBeGreaterThan(linear)
    expect(tenth).toBeCloseTo(minAlpha + (maxAlpha - minAlpha) * Math.sqrt(0.1), 6)
  })

  it('stays monotonic in magnitude', () => {
    expect(heatmapAlpha(5, 100)).toBeLessThan(heatmapAlpha(50, 100))
    expect(heatmapAlpha(50, 100)).toBeLessThan(heatmapAlpha(100, 100))
  })

  it('treats a value beyond the window maximum as the maximum', () => {
    expect(heatmapAlpha(500, 100)).toBeCloseTo(DEFAULT_HEATMAP_COLORS.maxAlpha, 6)
  })

  it('puts every recorded cell on the floor when the whole window is zero', () => {
    // A chain with no open interest at all is a real state, not a division to
    // guard against.
    expect(heatmapAlpha(0, 0)).toBe(DEFAULT_HEATMAP_COLORS.minAlpha)
    expect(heatmapAlpha(null, 0)).toBe(0)
  })
})

describe('heatmapCellFill', () => {
  it('returns null for a cell that must stay blank', () => {
    expect(heatmapCellFill(null, 100)).toBeNull()
  })

  it('encodes the sign as the hue, diverging about zero', () => {
    // Positive shares the Call Wall's colour and negative the Put Wall's, so
    // colour follows the entity across the bars, the walls and the heatmap.
    expect(heatmapCellFill(50, 100)).toContain('38, 166, 154')
    expect(heatmapCellFill(-50, 100)).toContain('239, 83, 80')
  })

  it('reads zero as the positive hue at floor alpha rather than as a gap', () => {
    const fill = heatmapCellFill(0, 100)

    expect(fill).not.toBeNull()
    expect(fill).toContain(String(DEFAULT_HEATMAP_COLORS.minAlpha))
  })

  it('dims a column recorded as degraded', () => {
    const good = heatmapCellFill(100, 100, false)
    const degraded = heatmapCellFill(100, 100, true)
    const alphaOf = (s: string | null) => Number(/([\d.]+)\)$/.exec(s ?? '')?.[1])

    expect(alphaOf(degraded)).toBeLessThan(alphaOf(good))
    expect(alphaOf(degraded)).toBeCloseTo(
      alphaOf(good) * DEFAULT_HEATMAP_COLORS.degradedAlphaScale,
      3
    )
  })

  it('never approaches opaque, since this is a backdrop for candles', () => {
    const alphaOf = (s: string | null) => Number(/([\d.]+)\)$/.exec(s ?? '')?.[1])

    expect(alphaOf(heatmapCellFill(100, 100))).toBeLessThan(0.7)
  })
})

describe('computeColumnSpans', () => {
  const xFor = (ts: number) => (ts - T0) / M // one pixel per minute

  it('draws each column one cadence wide', () => {
    const spans = computeColumnSpans([T0, T0 + M, T0 + 2 * M], 60, xFor)

    expect(spans).toEqual([
      { x0: 0, x1: 1 },
      { x0: 1, x1: 2 },
      { x0: 2, x1: 3 },
    ])
  })

  it('leaves an outage open instead of stretching across it', () => {
    // THE rule in this file. The column before a ten-minute hole stays one
    // minute wide; painting to the next column would assert gamma nobody
    // measured for the whole outage.
    const spans = computeColumnSpans([T0, T0 + 10 * M], 60, xFor)

    expect(spans[0]).toEqual({ x0: 0, x1: 1 })
    expect(spans[1]).toEqual({ x0: 10, x1: 11 })
  })

  it('widens with the resolution when the grid was thinned', () => {
    const spans = computeColumnSpans([T0, T0 + 5 * M], 300, xFor)

    expect(spans).toEqual([
      { x0: 0, x1: 5 },
      { x0: 5, x1: 10 },
    ])
  })

  it('never overruns the next column on a collapsed axis', () => {
    // A gapless time axis puts a session break's two sides next to each other,
    // so one cadence of x can be wider than the distance to the next column.
    const collapsed = (ts: number) => (ts <= T0 ? 0 : 0.25)
    const spans = computeColumnSpans([T0, T0 + M], 60, collapsed)

    expect(spans[0].x1).toBeLessThanOrEqual(spans[1].x0 + 1)
  })

  it('gives a degenerate axis a visible sliver rather than a zero-width rect', () => {
    const spans = computeColumnSpans([T0], 60, () => 42)

    expect(spans[0].x1).toBeGreaterThan(spans[0].x0)
  })

  it('handles an empty column list', () => {
    expect(computeColumnSpans([], 60, xFor)).toEqual([])
  })
})

describe('computeStrikeRows', () => {
  it('splits rows at the midpoints between strikes', () => {
    const rows = computeStrikeRows([24_000, 24_100, 24_200], yFor)

    // Midpoints at 24,050 and 24,150; y inverts price.
    expect(rows[1]).toEqual({ y0: yFor(24_150), y1: yFor(24_050) })
  })

  it('mirrors the neighbouring half-spacing at both outer edges', () => {
    const rows = computeStrikeRows([24_000, 24_100], yFor)

    expect(rows[0]).toEqual({ y0: yFor(24_050), y1: yFor(23_950) })
    expect(rows[1]).toEqual({ y0: yFor(24_150), y1: yFor(24_050) })
  })

  it('follows an uneven strike ladder rather than assuming one spacing', () => {
    // 50-point strikes near the money, 100-point in the wings. A fixed row
    // height would leave gaps in the wide stretch and overlap in the tight one.
    const rows = computeStrikeRows([24_000, 24_050, 24_150], yFor)

    // Rows must tile: each row's bottom is the next row's top.
    expect(rows[0].y0).toBeCloseTo(rows[1].y1, 6)
    expect(rows[1].y0).toBeCloseTo(rows[2].y1, 6)
  })

  it('leaves no seam between adjacent rows', () => {
    const rows = computeStrikeRows([24_000, 24_100, 24_200, 24_300], yFor)

    for (let i = 1; i < rows.length; i += 1) {
      expect(rows[i].y1).toBeCloseTo(rows[i - 1].y0, 6)
    }
  })

  it('gives a lone strike a thin band rather than an invented height', () => {
    const rows = computeStrikeRows([24_000], yFor)

    expect(rows[0].y1 - rows[0].y0).toBe(2)
  })

  it('handles an empty ladder', () => {
    expect(computeStrikeRows([], yFor)).toEqual([])
  })
})
