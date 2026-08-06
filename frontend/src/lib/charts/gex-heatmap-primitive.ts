/**
 * GEX Heatmap: the recorded per-strike profile as a background layer.
 *
 * Time across, the strike ladder down, signed exposure as colour. It shares the
 * PRICE pane deliberately: its y axis *is* the strike ladder, and the whole
 * point is that a band of colour lines up with the candles that did or did not
 * break it. In a separate pane the reader would eyeball two y axes against each
 * other - doing by hand the comparison the picture exists to make.
 *
 * Contributes NOTHING to autoscale, and must not start. `profiles.ts` documents
 * that trap three times and both GEX primitives document it again; this one
 * would be the worst offender of the three, because it spans the entire strike
 * window - 48 strikes over 2,350 points on a live NIFTY chain - and reporting
 * that extent would flatten the candles into a hairline.
 *
 * Draws BEHIND the bands. Both sit at `zOrder: 'bottom'`, so the manager adds
 * this primitive first and paint order does the rest: the levels through time
 * must stay readable over the field they came from.
 *
 * The cell layout and the colour scale are pure and live in
 * `gex-heatmap-geometry.ts`. This file adds the `IPrimitive` and the canvas
 * calls, and caches the one part of the work that does not depend on the
 * viewport.
 */

import type { IPrimitive, PrimitiveHost, PrimitiveRenderContext, ZOrder } from 'openalgo-charts'
import {
  computeColumnSpans,
  computeStrikeRows,
  DEFAULT_HEATMAP_COLORS,
  type GexHeatmapColorOptions,
  heatmapCellFill,
} from './gex-heatmap-geometry'

/** One recorded minute, aligned to `GexHeatmapData.strikes`. */
export interface GexHeatmapColumn {
  ts: number
  values: (number | null)[]
  quality: string | null
}

export interface GexHeatmapData {
  /** The shared y axis, ascending. */
  strikes: number[]
  columns: GexHeatmapColumn[]
  /** Largest absolute value in the window, for normalising the scale. */
  maxAbsValue: number
  /** Bucket width of the returned grid: 60, 300 or 900. */
  resolutionSeconds: number
}

export interface GexHeatmapOptions extends GexHeatmapColorOptions {
  /** Whole-layer opacity, on top of each cell's own alpha. */
  opacity: number
}

export const DEFAULT_GEX_HEATMAP_OPTIONS: GexHeatmapOptions = {
  ...DEFAULT_HEATMAP_COLORS,
  opacity: 1,
}

export class GexHeatmapPrimitive implements IPrimitive {
  private opts: GexHeatmapOptions
  private data: GexHeatmapData | null = null
  private host: PrimitiveHost | null = null
  /**
   * Per-cell fill strings, indexed `[column][strike]`, or null to leave blank.
   *
   * Cached for the same reason `GexBandsPrimitive` caches its segments, and with
   * more at stake: a live window is 114 columns x 48 strikes = 5,472 cells, and
   * building that many `rgba(...)` strings on every pan, zoom and tick is work
   * that cannot change between frames. Colour depends only on the value, the
   * window maximum and the options - never on the viewport.
   *
   * Exactly ONE cached value per instance, rebuilt on `setData`/`setOptions` and
   * released on detach. Never a cache keyed by anything: that would be the
   * unbounded registry the `fd-audit` skill exists to catch.
   */
  private fills: (string | null)[][] | null = null

  constructor(opts: Partial<GexHeatmapOptions> = {}) {
    this.opts = { ...DEFAULT_GEX_HEATMAP_OPTIONS, ...opts }
  }

  attached(host: PrimitiveHost): void {
    this.host = host
  }

  detached(): void {
    this.host = null
    this.fills = null
  }

  zOrder(): ZOrder {
    return 'bottom'
  }

  // `autoscaleInfo` is DELIBERATELY not implemented - see the file header.

  setData(data: GexHeatmapData | null): void {
    this.data = data
    this.fills = null
    this.host?.requestUpdate()
  }

  setOptions(patch: Partial<GexHeatmapOptions>): void {
    this.opts = { ...this.opts, ...patch }
    this.fills = null
    this.host?.requestUpdate()
  }

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const data = this.data
    if (!data || data.columns.length === 0 || data.strikes.length === 0) return

    const dpr = rc.dpr
    const plotWidth = rc.plotWidth * dpr
    const plotHeight = rc.plotHeight * dpr

    const spans = computeColumnSpans(
      data.columns.map((c) => c.ts),
      data.resolutionSeconds,
      (ts) => rc.timeScale.indexToX(rc.dataLayer.timeToIndexFloat(ts)) * dpr
    )
    const rows = computeStrikeRows(data.strikes, (price) => rc.priceScale.priceToY(price) * dpr)
    const fills = this.cellFills(data)

    ctx.save()
    ctx.globalAlpha = this.opts.opacity

    for (let c = 0; c < spans.length; c += 1) {
      const span = spans[c]
      // Whole columns off-screen are the common case when zoomed in, and
      // skipping them here is what keeps a 20,000-cell window cheap to pan.
      if (span.x1 < 0 || span.x0 > plotWidth) continue

      const columnFills = fills[c]
      for (let s = 0; s < rows.length; s += 1) {
        const fill = columnFills[s]
        // Null is a cell with no reading. Leaving it unpainted IS the gap rule -
        // there is no "faint" version of a minute the recorder missed.
        if (fill === null) continue

        const row = rows[s]
        if (row.y1 < 0 || row.y0 > plotHeight) continue

        ctx.fillStyle = fill
        // Rounded to whole device pixels: at 48 rows a fractional edge leaves a
        // visible seam of background between neighbours, which reads as a grid
        // of missing readings rather than a continuous field.
        const y0 = Math.round(row.y0)
        const y1 = Math.round(row.y1)
        const x0 = Math.round(span.x0)
        const x1 = Math.round(span.x1)
        ctx.fillRect(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0))
      }
    }

    ctx.restore()
  }

  /** Build the fill cache if the data or options changed since the last draw. */
  private cellFills(data: GexHeatmapData): (string | null)[][] {
    if (this.fills !== null) return this.fills

    this.fills = data.columns.map((column) => {
      const degraded = column.quality === 'degraded'
      return data.strikes.map((_strike, s) =>
        heatmapCellFill(column.values[s] ?? null, data.maxAbsValue, degraded, this.opts)
      )
    })
    return this.fills
  }
}
