/**
 * Pure geometry and colour for the GEX Heatmap.
 *
 * Imports neither `openalgo-charts` nor `CanvasRenderingContext2D`, so the cell
 * layout and the colour scale are unit-testable with no chart underneath - the
 * same split `gex-bands-geometry.ts` and `gex-levels-geometry.ts` make, and for
 * the same reason: three defects reached the live chart last session because
 * jsdom calls handlers with no chart behind them.
 *
 * Two rules this file exists to enforce.
 *
 * **A gap stays blank.** A minute the recorder missed has no column, and a
 * strike a minute's chain did not carry is null. Neither is drawn. Stretching
 * the previous cell across the hole would paint gamma nobody measured, which is
 * the error `quality.py` and `direction.ts` already forbid for a missing input.
 *
 * **Colour encodes sign, not rank.** Net GEX is signed, so the scale is
 * diverging - one hue per sign with a neutral middle - never a rainbow and never
 * a hue at the midpoint. Measured on live recorded data the distribution is
 * genuinely two-sided (2,647 positive against 2,711 negative cells), so the
 * midpoint is a real value rather than an edge of the range.
 */

/** A cell's horizontal extent in device pixels. */
export interface GexHeatmapSpan {
  x0: number
  x1: number
}

/** A strike's vertical extent in device pixels. `y0` is the TOP (smaller y). */
export interface GexHeatmapRow {
  y0: number
  y1: number
}

export interface GexHeatmapColorOptions {
  /** Hue for positive net exposure. Shared with the Call Wall, so colour follows the entity. */
  positiveColor: string
  /** Hue for negative net exposure. Shared with the Put Wall. */
  negativeColor: string
  /** Alpha at `|value| == maxAbs`. Held well below 1 - this is a backdrop for candles. */
  maxAlpha: number
  /**
   * Alpha floor for a cell that WAS recorded but is near zero.
   *
   * Without it a zero-gamma strike fades to nothing and becomes
   * indistinguishable from a minute the recorder missed, which the reader is
   * being asked to interpret as two different things.
   */
  minAlpha: number
  /** Multiplier applied to a column recorded as degraded. */
  degradedAlphaScale: number
}

export const DEFAULT_HEATMAP_COLORS: GexHeatmapColorOptions = {
  // The study's existing wall colours rather than a new pair: the heatmap, the
  // bars and the walls are all the same signed quantity, and colour follows the
  // entity. Validated for colour-vision deficiency rather than assumed - the
  // pair separates at deutan dE 11.6 (target 8) and normal-vision dE 29.6.
  positiveColor: '#26a69a',
  negativeColor: '#ef5350',
  maxAlpha: 0.55,
  minAlpha: 0.06,
  degradedAlphaScale: 0.45,
}

/**
 * How hard a cell is painted, from its magnitude relative to the window.
 *
 * Compressed with a square root rather than drawn linearly. Net GEX is heavily
 * tailed: a handful of strikes near the money carry most of the exposure, and on
 * a linear ramp every other cell collapses to nearly invisible, leaving a
 * picture of two bright rows and a blank field. The square root keeps the tail
 * legible while preserving the ordering - it is monotonic, so a bigger magnitude
 * is always at least as strong.
 *
 * @param value The cell's signed exposure.
 * @param maxAbs The largest absolute value in the WINDOW, from the server.
 * @param options Alpha floor and ceiling.
 * @returns Alpha in 0..1. Zero exactly when the cell has no reading.
 */
export function heatmapAlpha(
  value: number | null,
  maxAbs: number,
  options: GexHeatmapColorOptions = DEFAULT_HEATMAP_COLORS
): number {
  // No reading is not a small reading. The caller must not draw this cell.
  if (value === null || !Number.isFinite(value)) return 0
  // A window whose every value is zero is a real state (a chain with no open
  // interest at all). Every recorded cell then sits on the floor, which still
  // distinguishes it from the minutes that were never recorded.
  if (!Number.isFinite(maxAbs) || maxAbs <= 0) return options.minAlpha

  const ratio = Math.min(1, Math.abs(value) / maxAbs)
  const scaled = Math.sqrt(ratio)
  return options.minAlpha + (options.maxAlpha - options.minAlpha) * scaled
}

/** Parse `#rrggbb` into its channels. Falls back to mid grey rather than throwing. */
function channels(hex: string): [number, number, number] {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match) return [128, 128, 128]
  const n = Number.parseInt(match[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/**
 * The fill for one cell, or null when there is nothing to draw.
 *
 * Null rather than a transparent colour so a caller cannot accidentally paint a
 * gap: `null` means "leave the background showing", and the renderer skips the
 * rect entirely.
 *
 * @param value The cell's signed exposure, or null for no reading.
 * @param maxAbs The largest absolute value in the window.
 * @param degraded Whether the column was recorded as degraded.
 * @param options Colours, alpha floor and ceiling.
 * @returns An `rgba(...)` string, or null for a cell that must stay blank.
 */
export function heatmapCellFill(
  value: number | null,
  maxAbs: number,
  degraded = false,
  options: GexHeatmapColorOptions = DEFAULT_HEATMAP_COLORS
): string | null {
  const alpha = heatmapAlpha(value, maxAbs, options)
  if (alpha <= 0) return null

  const [r, g, b] = channels((value ?? 0) >= 0 ? options.positiveColor : options.negativeColor)
  const finalAlpha = degraded ? alpha * options.degradedAlphaScale : alpha
  return `rgba(${r}, ${g}, ${b}, ${finalAlpha.toFixed(3)})`
}

/**
 * Horizontal extent of each column, with gaps left open.
 *
 * A column is drawn one CADENCE wide - from its own timestamp to one resolution
 * step later - rather than stretched to wherever the next column happens to be.
 * That is the whole gap rule: when the recorder misses ten minutes, the column
 * before the hole stays one minute wide and the hole stays empty. Stretching to
 * the next column would paint straight across the outage.
 *
 * Clamped so a cell never overruns its successor, which matters on a gapless
 * time axis where a session break collapses two distant timestamps next to each
 * other and one cadence of x can be wider than the gap to the next column.
 *
 * @param timestamps Column timestamps in epoch seconds, ascending.
 * @param resolutionSeconds Bucket width: 60, 300 or 900.
 * @param xFor Maps epoch seconds to a device-pixel x.
 * @returns One span per timestamp, in the same order.
 */
export function computeColumnSpans(
  timestamps: readonly number[],
  resolutionSeconds: number,
  xFor: (ts: number) => number
): GexHeatmapSpan[] {
  const width = Math.max(1, resolutionSeconds)
  return timestamps.map((ts, i) => {
    const x0 = xFor(ts)
    let x1 = xFor(ts + width)
    const next = timestamps[i + 1]
    if (next !== undefined) {
      const nextX = xFor(next)
      if (nextX < x1) x1 = nextX
    }
    // A degenerate axis (everything collapsed to one x) still gets a visible
    // sliver rather than a zero-width rect that paints nothing.
    return { x0, x1: Math.max(x1, x0 + 1) }
  })
}

/**
 * Vertical extent of each strike row, split at the midpoints between strikes.
 *
 * Midpoints rather than a fixed height: the strike ladder is not always evenly
 * spaced (a chain can carry 50-point strikes near the money and 100-point
 * strikes in the wings), and a fixed height would leave gaps between rows in the
 * wide stretches and overlap them in the tight ones. The outermost rows mirror
 * their single neighbour's half-spacing so the band has a finite edge.
 *
 * @param strikes Strike prices, ascending.
 * @param yFor Maps a price to a device-pixel y. Higher price yields smaller y.
 * @returns One row per strike, in the same order, with `y0` the top edge.
 */
export function computeStrikeRows(
  strikes: readonly number[],
  yFor: (price: number) => number
): GexHeatmapRow[] {
  if (strikes.length === 0) return []
  if (strikes.length === 1) {
    // Nothing to derive a spacing from. A thin band still says "this strike had
    // a reading" without claiming a height the data does not support.
    const y = yFor(strikes[0])
    return [{ y0: y - 1, y1: y + 1 }]
  }

  return strikes.map((strike, i) => {
    const previous = strikes[i - 1]
    const next = strikes[i + 1]
    const upper = next === undefined ? strike + (strike - previous) / 2 : (strike + next) / 2
    const lower = previous === undefined ? strike - (next - strike) / 2 : (strike + previous) / 2
    // yFor inverts price, so the HIGHER price is the smaller y.
    const yTop = yFor(upper)
    const yBottom = yFor(lower)
    return yTop <= yBottom ? { y0: yTop, y1: yBottom } : { y0: yBottom, y1: yTop }
  })
}
