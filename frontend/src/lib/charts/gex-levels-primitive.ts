/**
 * GEX Levels chart primitive.
 *
 * Draws three extended price levels (Call Wall, Put Wall, Zero-Gamma) and an
 * optional column of signed per-strike bars anchored in the plot margin. The
 * manager that fetches the option-chain snapshot and owns this primitive's
 * lifecycle lives in `gex-levels.ts`; this file is only the paint step.
 *
 * `IPrimitive` here is imported from the package root (`openalgo-charts`),
 * not a lazy tier, so this needs none of the `tier-compat.ts` casts that the
 * Volume/Market Profile and Footprint primitives require - those are built by
 * `openalgo-charts/profile`, whose generated types re-declare the shared
 * classes instead of importing them from the root.
 *
 * Contributes NOTHING to autoscale - see the comment on the class below for
 * why, and `profiles.ts`, which documents the same trap three times for the
 * other profile overlays.
 */

import type { IPrimitive, PrimitiveHost, PrimitiveRenderContext, ZOrder } from 'openalgo-charts'
import type { GEXLevelsResponse, GEXStrikeLevel, GexMetric } from '@/api/gex'

export interface GexLevelsPrimitiveOptions {
  showBars: boolean
  showCallWall: boolean
  showPutWall: boolean
  showZeroGamma: boolean
  /** Which plot edge the bar column is anchored near. */
  side: 'left' | 'right'
  /** Maximum single-direction bar length in px (before dpr scaling), like Volume Profile's `width`. */
  columnWidth: number
  /** Which Greek the bar column is drawn from. */
  metric: GexMetric
  /** Extra px inset so the column clears a same-side volume profile. */
  columnInset: number
  callColor: string
  putColor: string
  zeroGammaColor: string
}

export const DEFAULT_GEX_PRIMITIVE_OPTIONS: GexLevelsPrimitiveOptions = {
  showBars: true,
  showCallWall: true,
  showPutWall: true,
  showZeroGamma: true,
  side: 'right',
  columnWidth: 120,
  metric: 'gamma',
  columnInset: 0,
  callColor: '#26a69a',
  putColor: '#ef5350',
  zeroGammaColor: '#f5a623',
}

/** Label font size in px, scaled by dpr at draw time like every other primitive in this library. */
const LABEL_FONT_PX = 11
/**
 * How far inside the plot edge an off-screen level's stub sits. Pinning it
 * exactly to y=0 / y=plotHeight would draw it flush against the pane border
 * and time axis, so it steps in far enough to read as a distinct marker.
 */
const EDGE_MARKER_INSET_PX = 12
/** Horizontal reach of an off-screen stub - short on purpose, so it reads as "not really here" rather than a full-width line. */
const EDGE_MARKER_LENGTH_PX = 72
/** Bar-row thickness floor and ceiling in px (before dpr scaling); see `computeGexBarGeometry`. */
const MIN_BAR_ROW_HEIGHT_PX = 1
const DEFAULT_BAR_ROW_HEIGHT_PX = 6
const MAX_BAR_ROW_HEIGHT_PX = 14

/**
 * Format a price the way the rest of the GEX dashboard does: an index/stock
 * price that lands on a whole point (the common case for index strikes) shows
 * no decimals, anything with a fractional strike or level (routine on stock
 * options, e.g. `VEDL25APR24292.5CE`) keeps two.
 */
export function formatGexPrice(price: number): string {
  return Number.isInteger(price) ? price.toFixed(0) : price.toFixed(2)
}

export interface GexLevelPlacement {
  /** True when the level's y already lands inside the plot. */
  onScreen: boolean
  /** Where to actually draw the line/stub, in the same px units as the input y. */
  y: number
  /** Which way the level lies when off screen; null when on screen. */
  direction: 'above' | 'below' | null
}

/**
 * Decide where a level line actually gets drawn.
 *
 * The GEX strike window is 47 strikes wide, which is routinely much wider
 * than the chart's visible price range, so a wall sitting outside that range
 * is the common case, not an edge case. Off screen must not mean invisible:
 * a trader glancing at the chart needs to be able to tell "there is a call
 * wall above, just not on screen right now" rather than reading a clean chart
 * as "no call wall nearby" - so an out-of-range level collapses onto the
 * nearer plot edge as a short stub instead of being skipped.
 *
 * `y<0` is above the plot top and `y>plotHeight` is below the plot bottom,
 * which is what `priceToY` produces for the default (non-inverted) price
 * scale every GEX chart uses; this does not attempt to also read an inverted
 * scale correctly.
 */
export function computeGexLevelPlacement(
  y: number,
  plotHeight: number,
  edgeInset: number
): GexLevelPlacement {
  if (y >= 0 && y <= plotHeight) return { onScreen: true, y, direction: null }
  if (y < 0) return { onScreen: false, y: edgeInset, direction: 'above' }
  return { onScreen: false, y: Math.max(edgeInset, plotHeight - edgeInset), direction: 'below' }
}

export interface GexBarGeometry {
  strike: number
  /** y coordinate for this strike's row, in the same px units as the `priceToY` callback. */
  y: number
  /** Unsigned bar length, already scaled to `columnWidth`. 0 when there is no signal to scale against. */
  length: number
  /** `net_gex >= 0` - call-dominant / stabilising, drawn in the call colour. */
  positive: boolean
}

/**
 * Pure geometry for the per-strike bar column: which strikes are actually on
 * screen, how long each one's bar is, and how thick a row can be without
 * neighbouring bars overlapping.
 *
 * Takes `priceToY` as a plain callback rather than the library's `PriceScale`
 * so this can be unit-tested without a canvas or a chart - the real caller
 * passes `(p) => rc.priceScale.priceToY(p)`.
 */
export function computeGexBarGeometry(
  strikes: readonly GEXStrikeLevel[],
  priceToY: (price: number) => number,
  plotHeight: number,
  columnWidth: number,
  metric: GexMetric = 'gamma'
): { bars: GexBarGeometry[]; rowHeight: number } {
  // Clipping to the visible range is what replaces an autoscale contribution:
  // the study never asks the pane to widen to fit the strike window, it only
  // draws the part of that window which already fits.
  const visible = strikes.filter((s) => {
    const y = priceToY(s.strike)
    return y >= 0 && y <= plotHeight
  })
  if (visible.length === 0) return { bars: [], rowHeight: 0 }

  // One accessor for both the peak and the per-bar value, so the two can never
  // be scaled against different metrics. Gamma and delta exposure differ by
  // orders of magnitude, so a mismatch would render every bar as an invisible
  // sliver rather than as an obviously wrong chart.
  const exposureOf = (s: GEXStrikeLevel): number => (metric === 'delta' ? s.net_dex : s.net_gex)

  const peak = visible.reduce((max, s) => Math.max(max, Math.abs(exposureOf(s))), 0)
  // An all-zero snapshot has no signal to scale against; dividing by 0 would
  // turn every bar into NaN-width geometry instead of the "nothing to show"
  // it actually is, so a non-positive peak forces every length to 0.
  const bars: GexBarGeometry[] = visible.map((s) => ({
    strike: s.strike,
    y: priceToY(s.strike),
    length: peak > 0 ? (Math.abs(exposureOf(s)) / peak) * columnWidth : 0,
    positive: exposureOf(s) >= 0,
  }))

  return { bars, rowHeight: strikeRowHeightPx(strikes, priceToY) }
}

/**
 * Typical pixel gap between adjacent strikes, used to cap bar thickness so
 * rows never grow into an overlapping smear when the chart is zoomed out.
 * Computed from the full chain rather than just the visible slice, so the row
 * height stays stable as the viewport pans; the median (rather than the first
 * pair) guards against one odd gap - a strike missing at the edge of the
 * chain - skewing the whole column's thickness.
 */
function strikeRowHeightPx(
  strikes: readonly GEXStrikeLevel[],
  priceToY: (price: number) => number
): number {
  if (strikes.length < 2) return DEFAULT_BAR_ROW_HEIGHT_PX
  const gaps: number[] = []
  for (let i = 1; i < strikes.length; i++) {
    gaps.push(Math.abs(priceToY(strikes[i].strike) - priceToY(strikes[i - 1].strike)))
  }
  gaps.sort((a, b) => a - b)
  const median = gaps[Math.floor(gaps.length / 2)]
  return Math.max(MIN_BAR_ROW_HEIGHT_PX, Math.min(MAX_BAR_ROW_HEIGHT_PX, median - 1))
}

export class GexLevelsPrimitive implements IPrimitive {
  private opts: GexLevelsPrimitiveOptions
  private data: GEXLevelsResponse | null = null
  private host: PrimitiveHost | null = null

  constructor(opts: Partial<GexLevelsPrimitiveOptions> = {}) {
    this.opts = { ...DEFAULT_GEX_PRIMITIVE_OPTIONS, ...opts }
  }

  attached(host: PrimitiveHost): void {
    this.host = host
  }

  detached(): void {
    this.host = null
  }

  zOrder(): ZOrder {
    return 'bottom' // levels sit behind price action, like Volume/Market Profile
  }

  // `autoscaleInfo` is DELIBERATELY not implemented - do not add it back.
  // `profiles.ts` documents this trap three times for the other profile
  // overlays: a primitive whose `autoscaleInfo()` reports its own extent
  // drags the pane's price scale out to cover that extent, squashing the
  // candles into a sliver. The GEX strike window (47 strikes) spans far more
  // than the visible price range, so reporting it here would flatten the
  // chart exactly the way a full-history Volume Profile does. Bars clip to
  // the visible range instead (`computeGexBarGeometry`), and an off-screen
  // level becomes an edge marker (`computeGexLevelPlacement`) rather than
  // either being dropped or dragging the scale out to show it.

  setData(data: GEXLevelsResponse | null): void {
    this.data = data
    this.host?.requestUpdate()
  }

  setOptions(patch: Partial<GexLevelsPrimitiveOptions>): void {
    this.opts = { ...this.opts, ...patch }
    this.host?.requestUpdate()
  }

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const d = this.data
    // Covers both an explicit error response and the brief window before the
    // first fetch resolves - neither carries levels worth drawing over.
    if (!d || d.status !== 'success') return

    if (this.opts.showBars) this.drawBars(ctx, rc, d.strikes ?? [])

    if (this.opts.showCallWall && d.call_wall != null) {
      this.drawLevel(ctx, rc, d.call_wall, 'Call Wall', this.opts.callColor, true)
    }
    if (this.opts.showPutWall && d.put_wall != null) {
      this.drawLevel(ctx, rc, d.put_wall, 'Put Wall', this.opts.putColor, true)
    }
    // `zero_gamma: null` is an ordinary market state (the gamma profile does
    // not cross zero near the forward), not a missing value to fall back on -
    // the dashboard already shows "No local cross" for it, so this just skips
    // the line rather than drawing a misleading one at price 0.
    if (this.opts.showZeroGamma && d.zero_gamma != null) {
      this.drawLevel(ctx, rc, d.zero_gamma, 'Zero-Gamma', this.opts.zeroGammaColor, false)
    }
  }

  /**
   * One line across the plot with an inline label. See
   * `computeGexLevelPlacement` for what happens when the price falls outside
   * the visible range.
   */
  private drawLevel(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    price: number,
    label: string,
    color: string,
    dashed: boolean
  ): void {
    const dpr = rc.dpr
    const placement = computeGexLevelPlacement(
      rc.priceScale.priceToY(price),
      rc.plotHeight,
      EDGE_MARKER_INSET_PX
    )
    const y = Math.round(placement.y * dpr) + 0.5

    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = Math.max(1, Math.round(dpr))
    ctx.setLineDash(dashed ? [6 * dpr, 4 * dpr] : [])
    ctx.beginPath()
    ctx.moveTo(0, y)
    const xEnd = placement.onScreen
      ? rc.plotWidth * dpr
      : Math.min(EDGE_MARKER_LENGTH_PX * dpr, rc.plotWidth * dpr)
    ctx.lineTo(xEnd, y)
    ctx.stroke()
    ctx.setLineDash([])

    ctx.font = `${LABEL_FONT_PX * dpr}px system-ui, -apple-system, sans-serif`
    ctx.textBaseline = 'bottom'
    ctx.textAlign = 'left'
    ctx.fillStyle = color
    const text = placement.direction
      ? `${label} ${formatGexPrice(price)} (${placement.direction})`
      : `${label} ${formatGexPrice(price)}`
    ctx.fillText(text, 8 * dpr, y - 3 * dpr)
    ctx.restore()
  }

  /**
   * The signed per-strike bar column. Pixel-anchored rather than time-anchored
   * the way Volume Profile's session bars are (see `volume-profile-primitive.ts`)
   * because a strike has no x position of its own - `side` and `columnInset`
   * place the whole column in the plot margin instead of against a time range.
   */
  private drawBars(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    strikes: readonly GEXStrikeLevel[]
  ): void {
    if (strikes.length === 0) return
    const dpr = rc.dpr
    const priceToY = (price: number): number => rc.priceScale.priceToY(price)
    const { bars, rowHeight } = computeGexBarGeometry(
      strikes,
      priceToY,
      rc.plotHeight,
      this.opts.columnWidth,
      this.opts.metric
    )
    if (bars.length === 0) return

    // The axis line is the near edge of the "toward-the-plot-edge" half of the
    // column: a bar reaches from here up to `columnWidth` px. `columnInset`
    // steps the whole column further from the edge so it does not sit under a
    // same-side Volume Profile (150 px wide by default) - the caller supplies
    // the inset (typically the other study's width) rather than this
    // primitive guessing at what else is anchored to the same edge.
    const axisX =
      (this.opts.side === 'right'
        ? rc.plotWidth - this.opts.columnInset - this.opts.columnWidth
        : this.opts.columnInset + this.opts.columnWidth) * dpr

    const barThickness = Math.max(1, rowHeight * dpr - dpr)

    ctx.save()
    ctx.globalAlpha = 0.75
    for (const b of bars) {
      const y = b.y * dpr
      const len = b.length * dpr
      // Positive (call-dominant) bars always point right and negative
      // (put-dominant) always point left, in absolute screen space - `side`
      // only moves the axis line, so the diverging-bar read stays the same
      // regardless of which edge the column is pinned to.
      ctx.fillStyle = b.positive ? this.opts.callColor : this.opts.putColor
      const x = b.positive ? axisX : axisX - len
      ctx.fillRect(x, y - barThickness / 2, len, barThickness)
    }
    ctx.globalAlpha = 1

    // Zero-gamma-of-the-column reference line, purely a visual anchor for the
    // bars above - not to be confused with the `zero_gamma` price level, which
    // is drawn separately by `drawLevel`.
    ctx.strokeStyle = rc.theme.axisLine
    ctx.globalAlpha = 0.6
    ctx.lineWidth = Math.max(1, Math.round(dpr))
    ctx.setLineDash([2 * dpr, 3 * dpr])
    ctx.beginPath()
    ctx.moveTo(Math.round(axisX) + 0.5, 0)
    ctx.lineTo(Math.round(axisX) + 0.5, rc.plotHeight * dpr)
    ctx.stroke()
    ctx.restore()
  }
}
