/**
 * GEX Levels chart primitives.
 *
 * `GexLevelsPrimitive` draws three extended price levels (Call Wall, Put
 * Wall, Zero-Gamma) and an optional column of signed per-strike bars -
 * gamma or delta, per `GexLevelsConfig.metric` - anchored in the plot
 * margin, at `zOrder: 'bottom'`. `GexOverlayPrimitive` is everything that
 * has to paint at `zOrder: 'top'` instead, so price action can never cover
 * it: the label naming which of the two the bar column currently reads, and
 * a hover readout for whichever strike's row the pointer is over (both
 * metrics for that strike, plus its wall status) - see its own doc comment.
 * The manager that fetches the option-chain snapshot and owns both
 * primitives' lifecycle lives in `gex-levels.ts`; this file is only the
 * paint step.
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

import type {
  IPrimitive,
  PrimitiveHit,
  PrimitiveHost,
  PrimitiveRenderContext,
  ZOrder,
} from 'openalgo-charts'
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
/**
 * Bar-row thickness floor in px (before dpr scaling): prevents neighbouring
 * bars from smearing into an overlapping mass when the chart is zoomed out
 * and the pixel gap between strikes shrinks toward 0. See `strikeRowHeightPx`.
 */
const MIN_BAR_ROW_HEIGHT_PX = 1
const DEFAULT_BAR_ROW_HEIGHT_PX = 6
/**
 * Bar-row thickness ceiling, as a fraction of the pane height rather than a
 * fixed px value.
 *
 * This used to be a flat 14px, which has no relationship to the actual
 * pixel gap between strikes and so caps bar thickness far below it at almost
 * any zoom level except fully zoomed out - a 50-point NIFTY strike spacing
 * is routinely 150-200px on screen once zoomed in even moderately, so a
 * 14px bar sits as an isolated sliver in the middle of that gap instead of
 * tiling into a continuous profile the way the bars either side of it do.
 * The ceiling's actual job is the opposite failure mode: at extreme zoom,
 * when only two or three strikes are visible at all, an unbounded row height
 * would let a single strike's bar swallow most of the pane. Scaling with
 * `plotHeight` keeps that guard proportional at every pane size instead of
 * being calibrated for one specific one.
 */
const MAX_BAR_ROW_HEIGHT_FRACTION = 0.25
/**
 * Gap in px (before dpr scaling) between the plot bottom and the metric
 * caption. Bottom, not top: the top band is where the pane legend's row 0
 * (symbol + OHLCV) lives, and where an off-screen level's edge-marker stub
 * (`EDGE_MARKER_INSET_PX`) is most often drawn - the bottom is the one edge
 * neither of those two routinely claims.
 */
const BAR_CAPTION_BOTTOM_PX = 12

/**
 * Format a price the way the rest of the GEX dashboard does: an index/stock
 * price that lands on a whole point (the common case for index strikes) shows
 * no decimals, anything with a fractional strike or level (routine on stock
 * options, e.g. `VEDL25APR24292.5CE`) keeps two.
 */
export function formatGexPrice(price: number): string {
  return Number.isInteger(price) ? price.toFixed(0) : price.toFixed(2)
}

/**
 * Format a GEX/DEX-scale money figure the way the rest of the study does:
 * quoted in crore - >= 1 crore prints "X.XX Cr", >= 1 lakh prints "X.XX L",
 * anything smaller a plain grouped integer. Sign is kept on negatives; a
 * missing or non-finite value is an em dash, never a bare "0" or "NaN" that
 * could be mistaken for a real reading of zero.
 *
 * The single formatter for every crore-scale figure the GEX study shows -
 * `GexDashboard`'s numeric card imports this rather than keeping its own
 * copy (it used to define an identical `formatMoney` locally; that copy is
 * gone), and the canvas hover readout below (`gexReadoutLines`, via
 * `formatGexSignedMoney`) reuses it too. One implementation, so a fix to the
 * Cr/L thresholds or the rounding is never made twice.
 */
export function formatGexMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const sign = v < 0 ? '-' : ''
  const abs = Math.abs(v)
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)} Cr`
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)} L`
  return `${sign}${Math.round(abs).toLocaleString('en-IN')}`
}

/**
 * Same formatting as {@link formatGexMoney}, but always signed - a leading
 * "+" on a non-negative value too, not just the "-" `formatGexMoney` already
 * keeps on negatives. Only the hover readout uses this: it draws GEX and DEX
 * for the same strike side by side specifically so their signs can be
 * compared at a glance (see the module doc comment - strike 24000 being
 * -1318 Cr gamma but +679 Cr delta is the entire point of the feature), and
 * an unsigned positive number there would read as ambiguous rather than as
 * "the opposite of the row above it", which is usually the case.
 */
function formatGexSignedMoney(v: number | null | undefined): string {
  const formatted = formatGexMoney(v)
  if (v === null || v === undefined || !Number.isFinite(v) || v < 0) return formatted
  return `+${formatted}`
}

/**
 * Text for the on-canvas label naming which metric the bar column currently
 * reads, drawn by `GexOverlayPrimitive`.
 *
 * A `Record`, not a two-way ternary: `GexMetric` gaining a third member (say
 * `'vanna'`) makes this a compile error instead of a silent "Gamma" label
 * over a vanna column - the one thing this function exists to prevent.
 *
 * Wording avoids "book" standing alone: this workspace already has a real
 * order book (a depth feed, `pollBook()`, the trade panel), so an unqualified
 * "(book)" risks being read as that rather than the option chain's
 * open-interest book. "OI-book" disambiguates in the same few characters.
 *
 * Both metrics get a label - not just delta - so that whenever the bar
 * column is actually drawn, which Greek it reads is never left to be
 * inferred from bar shape or colour alone: every other line in the study -
 * Call Wall, Put Wall, Zero-Gamma - is computed server-side from gamma
 * regardless of this setting, only the bar column's source flips, and delta
 * additionally inverts the frame of reference. DEX is the open-interest
 * book's delta (see `services/gex_levels/delta_exposure.py`), so a positive
 * (call-coloured) bar means the book is long, where under gamma the same
 * colour means dealers are long.
 *
 * `GexOverlayPrimitive` draws no caption when the bar column is
 * switched off (`showBars`) or when the manager's most recent snapshot has
 * nothing to show (`hasBars` - no data yet, an error response, or an
 * instrument with no option chain: see `GexLevelsManager.captionOptions()`),
 * and that absence is fine - there is then nothing on screen for it to
 * disambiguate. It does NOT cover a viewport panned or zoomed away from
 * every strike in an otherwise-good snapshot: telling visible strikes from
 * off-screen ones needs the price scale, which is genuine draw-time
 * knowledge the options-level `hasBars` gate does not have - and that case
 * is the benign one anyway, since the walls stay on screen and the study is
 * plainly still live.
 */
const GEX_METRIC_CAPTIONS: Record<GexMetric, string> = {
  gamma: 'Gamma · dealer sign',
  delta: 'Delta · OI-book sign',
}

export function gexMetricCaption(metric: GexMetric): string {
  return GEX_METRIC_CAPTIONS[metric]
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
  /**
   * Sign of the selected metric's exposure at this strike, drawn in the call
   * colour when true. The two metrics read opposite parties: under gamma,
   * positive means dealers are long (stabilising); under delta, positive
   * means the open-interest book is long, and dealers hold the negation -
   * see `services/gex_levels/delta_exposure.py`'s module docstring.
   */
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
  metric: GexMetric
): { bars: GexBarGeometry[]; rowHeight: number } {
  // Clipping to the visible range is what replaces an autoscale contribution:
  // the study never asks the pane to widen to fit the strike window, it only
  // draws the part of that window which already fits.
  const visible = strikes.filter((s) => {
    const y = priceToY(s.strike)
    return y >= 0 && y <= plotHeight
  })
  if (visible.length === 0) return { bars: [], rowHeight: 0 }

  // Gamma exposure carries an F^2 * 0.01 factor and delta exposure only F, so
  // the two are off by a large factor - a mismatch here would render every
  // bar as an invisible sliver rather than as an obviously wrong chart.
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

  return { bars, rowHeight: strikeRowHeightPx(strikes, priceToY, plotHeight) }
}

/**
 * Typical pixel gap between adjacent strikes - bars are supposed to tile
 * into a continuous profile, so a row's thickness tracks this gap directly,
 * clamped only at the two ends documented on `MIN_BAR_ROW_HEIGHT_PX` /
 * `MAX_BAR_ROW_HEIGHT_FRACTION`. Computed from the full chain rather than
 * just the visible slice, so the row height stays stable as the viewport
 * pans; the median (rather than the first pair) guards against one odd gap
 * - a strike missing at the edge of the chain - skewing the whole column's
 * thickness.
 */
function strikeRowHeightPx(
  strikes: readonly GEXStrikeLevel[],
  priceToY: (price: number) => number,
  plotHeight: number
): number {
  if (strikes.length < 2) return DEFAULT_BAR_ROW_HEIGHT_PX
  const gaps: number[] = []
  for (let i = 1; i < strikes.length; i++) {
    gaps.push(Math.abs(priceToY(strikes[i].strike) - priceToY(strikes[i - 1].strike)))
  }
  gaps.sort((a, b) => a - b)
  const median = gaps[Math.floor(gaps.length / 2)]
  const maxRowHeight = plotHeight * MAX_BAR_ROW_HEIGHT_FRACTION
  return Math.max(MIN_BAR_ROW_HEIGHT_PX, Math.min(maxRowHeight, median - 1))
}

/**
 * Device-px x of the bar column's zero-reference axis - the line a positive
 * bar extends right from and a negative bar extends left from.
 *
 * Shared by `GexLevelsPrimitive.drawBars` (which draws the bars against it)
 * and `GexOverlayPrimitive` (which centres the caption on it) so the
 * two primitives can never drift apart into disagreeing about where the
 * column actually is - they are two different `IPrimitive`s at two different
 * zOrders, not two branches of one function, precisely because a caption
 * drawn from inside `drawBars` would inherit the bars' `zOrder: 'bottom'`
 * and be paintable-over by the candles.
 */
export function gexColumnAxisX(
  plotWidth: number,
  side: 'left' | 'right',
  columnInset: number,
  columnWidth: number,
  dpr: number
): number {
  return (
    (side === 'right' ? plotWidth - columnInset - columnWidth : columnInset + columnWidth) * dpr
  )
}

/**
 * Which strike row (if any) sits under a pointer position - the pure
 * geometry a later task uses to show a hover readout for the bar column.
 *
 * Works entirely in **CSS-pixel space**, dpr-independent: `x`/`y` are meant
 * to be the same coordinates a pointer/mouse event already carries relative
 * to the plot, and they line up directly with `GexBarGeometry.y`/`.length`
 * as `computeGexBarGeometry` produces them - both are pre-dpr-scaling
 * values, the same as `rowHeight`. `drawBars` only multiplies by `dpr` at
 * the point it actually issues canvas calls, so nothing here needs a dpr
 * argument; internally it calls `gexColumnAxisX(..., 1)` to get that same
 * axis position back in CSS pixels rather than the device-pixel value
 * `drawBars` uses.
 *
 * Delegates the column's horizontal extent to `gexColumnAxisX` instead of
 * re-deriving `side`/`columnInset`/`columnWidth` into an axis position here
 * - the same reason `GexOverlayPrimitive` shares it with `drawBars`
 * rather than recomputing its own: two independent formulas for "where is
 * the axis" are two things that can silently drift apart, one of which
 * would leave the hover region misaligned with the bars actually painted.
 *
 * A bar's row band is `rowHeight` tall, centred on its `y` (matching how
 * `drawBars` centres `barThickness` on `b.y * dpr`). When a point falls in
 * more than one band - adjacent bands can touch or, at extreme zoom,
 * overlap - the band whose centre is nearest the point wins, rather than
 * whichever bar happens to come first in the array; an exact tie (the point
 * equidistant from two centres) resolves to whichever of those two is
 * earlier in `bars`, via a strict `<` when updating the best match.
 *
 * Takes a single options object, not positional arguments, deliberately:
 * the realistic call site sits right next to `drawBars`' own
 * `gexColumnAxisX(rc.plotWidth, this.opts.side, this.opts.columnInset,
 * this.opts.columnWidth, dpr)` call, and `columnWidth`/`columnInset` are
 * both bare `number`s that `side` also separates positionally in that call -
 * a maintainer copying that argument order in would silently transpose them
 * here, which typechecks and produces a hit region quietly offset from the
 * bars it is meant to track. Field names mirror `gexColumnAxisX`'s own
 * parameter names so the mapping at the call site stays visible.
 */
export function gexHitTestStrike(opts: {
  bars: readonly GexBarGeometry[]
  rowHeight: number
  plotWidth: number
  columnWidth: number
  side: 'left' | 'right'
  columnInset: number
  x: number
  y: number
}): GexBarGeometry | null {
  const { bars, rowHeight, plotWidth, columnWidth, side, columnInset, x, y } = opts
  if (bars.length === 0) return null

  const axisX = gexColumnAxisX(plotWidth, side, columnInset, columnWidth, 1)
  if (x < axisX - columnWidth || x > axisX + columnWidth) return null

  const halfRow = rowHeight / 2
  let best: GexBarGeometry | null = null
  let bestDistance = Infinity
  for (const bar of bars) {
    if (y < bar.y - halfRow || y > bar.y + halfRow) continue
    const distance = Math.abs(y - bar.y)
    if (distance < bestDistance) {
      bestDistance = distance
      best = bar
    }
  }
  return best
}

/**
 * externalId this primitive reports for a given strike's row via `hitTest`.
 * `drawHoverReadout` resolves `rc.hoverId` back to a strike by recomputing
 * this same id for each of the frame's bars and matching by equality, rather
 * than parsing the id apart - both directions go through this one function,
 * so they can never drift into disagreeing about what a given id means. (A
 * plain template literal would in fact round-trip safely even through
 * `Number(...)` - `String(n)` and `Number(s)` are exact inverses for every
 * finite JS number, fractional strikes like VEDL's 292.5 included - but
 * matching by recomputed id avoids leaning on that.)
 */
export function gexStrikeExternalId(strike: number): string {
  return `gex-strike-${strike}`
}

export interface GexReadoutLine {
  text: string
  /**
   * True for the strike header and whichever metric row is the study's
   * active one; false (dimmed) for the other metric row and the wall line -
   * so the reader's eye lands on the number the bar column is currently
   * drawn from, while the rest still reads as available context rather than
   * being hidden outright.
   */
  emphasis: boolean
  /** Canvas fillStyle for this line - sign-coded for the metric rows (call/put colour), fixed for the header and wall lines. */
  color: string
}

/**
 * The hover readout's text content for one strike - always both metrics, per
 * the module doc comment: the readout exists specifically so a trader can
 * see a strike like 24000 is put-dominant under gamma but call-dominant
 * under delta without switching `metric` back and forth. A wall line is
 * appended only when the strike actually is the Call Wall or Put Wall -
 * checked independently rather than as an if/else, so a data anomaly (both
 * true at once) does not silently drop one instead of just looking odd.
 *
 * Pure and canvas-free so the exact lines can be pinned without a fake ctx;
 * `drawHoverReadout` positions and paints exactly what this returns.
 */
export function gexReadoutLines(opts: {
  strike: number
  netGex: number
  netDex: number
  metric: GexMetric
  isCallWall: boolean
  isPutWall: boolean
  headerColor: string
  callColor: string
  putColor: string
}): GexReadoutLine[] {
  const {
    strike,
    netGex,
    netDex,
    metric,
    isCallWall,
    isPutWall,
    headerColor,
    callColor,
    putColor,
  } = opts
  const lines: GexReadoutLine[] = [
    { text: formatGexPrice(strike), emphasis: true, color: headerColor },
    {
      text: `GEX  ${formatGexSignedMoney(netGex)}`,
      emphasis: metric === 'gamma',
      color: netGex >= 0 ? callColor : putColor,
    },
    {
      text: `DEX  ${formatGexSignedMoney(netDex)}`,
      emphasis: metric === 'delta',
      color: netDex >= 0 ? callColor : putColor,
    },
  ]
  if (isCallWall) lines.push({ text: 'Call wall', emphasis: false, color: callColor })
  if (isPutWall) lines.push({ text: 'Put wall', emphasis: false, color: putColor })
  return lines
}

export interface GexReadoutBoxGeometry {
  x: number
  y: number
  width: number
  height: number
}

/**
 * Where the hover readout box sits, in the same pre-dpr CSS-pixel space as
 * `gexHitTestStrike` and `GexBarGeometry.y` - `drawHoverReadout` only
 * multiplies by `dpr` at the point it issues canvas calls, matching every
 * other geometry helper in this file.
 *
 * Horizontally: inset `gap` px from whichever side of the bar column has more
 * room. The column's own footprint is `[axisX - columnWidth, axisX +
 * columnWidth]` around its axis (bars extend both ways from it - the same
 * span `gexHitTestStrike` treats as the column), so "more room" compares the
 * space outside that footprint, not just an arbitrary left/right split of the
 * plot.
 *
 * Vertically: centred on the hovered row's `rowY`, then clamped so the box
 * never runs off the plot. The risk is highest at the topmost or bottommost
 * visible strike, where a naive centred placement would put half the box
 * outside the pane.
 *
 * Both axes clamp into `[0, plotDimension - boxDimension]`, with
 * `Math.max(0, ...)` on the upper bound so a box larger than the plot still
 * lands at 0 rather than at a negative coordinate.
 */
export function computeGexReadoutBoxGeometry(opts: {
  rowY: number
  boxWidth: number
  boxHeight: number
  plotWidth: number
  plotHeight: number
  side: 'left' | 'right'
  columnInset: number
  columnWidth: number
  gap: number
}): GexReadoutBoxGeometry {
  const { rowY, boxWidth, boxHeight, plotWidth, plotHeight, side, columnInset, columnWidth, gap } =
    opts
  const axisX = gexColumnAxisX(plotWidth, side, columnInset, columnWidth, 1)
  const roomLeft = axisX - columnWidth
  const roomRight = plotWidth - (axisX + columnWidth)
  const placeLeft = roomLeft > roomRight

  const clamp = (v: number, max: number): number => Math.min(Math.max(v, 0), Math.max(0, max))
  const xUnclamped = placeLeft ? axisX - columnWidth - gap - boxWidth : axisX + columnWidth + gap
  const x = clamp(xUnclamped, plotWidth - boxWidth)
  const y = clamp(rowY - boxHeight / 2, plotHeight - boxHeight)

  return { x, y, width: boxWidth, height: boxHeight }
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
    const axisX = gexColumnAxisX(
      rc.plotWidth,
      this.opts.side,
      this.opts.columnInset,
      this.opts.columnWidth,
      dpr
    )

    const barThickness = Math.max(1, rowHeight * dpr - dpr)

    // The metric caption is NOT drawn here. It used to be, but this primitive
    // is zOrder 'bottom' (painted before the candles), so a caption drawn
    // from inside this method would be paintable-over by price action - see
    // `GexOverlayPrimitive` below, a separate zOrder: 'top' primitive
    // that the manager creates and syncs alongside this one.
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

export interface GexOverlayOptions {
  /** Mirrors `GexLevelsPrimitiveOptions.showBars` - no bar column, nothing to caption. */
  showBars: boolean
  /**
   * True when the manager's most recent snapshot actually has bar data - see
   * `GexLevelsManager.captionOptions()`. Independent of `showBars`: `showBars`
   * is a user setting (draw the column at all), `hasBars` is a data fact (is
   * there anything in it right now). Both must be true for the caption to
   * draw - without this, switching to an instrument with no option chain
   * leaves the caption pinned to an empty chart with no bars, no walls and no
   * readout card under it, and no way to dismiss it short of switching
   * instrument again (`gexAvailable === false` disables the Studies-panel
   * toggle too).
   */
  hasBars: boolean
  side: 'left' | 'right'
  columnWidth: number
  metric: GexMetric
  columnInset: number
  /**
   * The same strikes `GexLevelsPrimitive.drawBars` draws from - needed so
   * `hitTest`/`drawHoverReadout` can recompute identical bar geometry
   * (`computeGexBarGeometry`) and find the strike under the pointer. Not a
   * second copy of the snapshot: the manager derives this fresh from
   * `snapshotValue` on every `captionOptions()` call, exactly like `hasBars`
   * already did before this primitive needed anything more than a boolean.
   */
  strikes: readonly GEXStrikeLevel[]
  /** Wall prices, so the readout can append its "Call wall" / "Put wall" line. */
  callWall: number | null
  putWall: number | null
}

export const DEFAULT_GEX_OVERLAY_OPTIONS: GexOverlayOptions = {
  showBars: true,
  // false, not true: the constructor always runs one setOptions() behind a
  // real GexLevelsManager.captionOptions() call in the same synchronous
  // syncPrimitive() pass (see gex-levels.ts), so this default never actually
  // reaches a draw() call in practice - but "nothing to show" is the correct
  // default to fail toward if that ever stops being true.
  hasBars: false,
  side: 'right',
  columnWidth: 120,
  metric: 'gamma',
  columnInset: 0,
  strikes: [],
  callWall: null,
  putWall: null,
}

/** Hover-readout box: a fixed width and per-row height rather than text-measured, so the box size is deterministic and testable without a canvas - like the DOM sidebar card's own fixed width. */
const READOUT_BOX_WIDTH_PX = 150
const READOUT_LINE_HEIGHT_PX = 16
const READOUT_PADDING_PX = 8
/** Gap in px between the bar column's footprint and the readout box. */
const READOUT_GAP_PX = 10

/**
 * Draws the "which metric" label for the bar column, and - the reason this
 * primitive now also implements `hitTest` - the per-strike hover readout, at
 * `zOrder: 'top'`. Deliberately a second, separate `IPrimitive` rather than
 * either of those being one more thing `GexLevelsPrimitive.drawBars` paints.
 *
 * A primitive's `zOrder()` is fixed for its entire `draw()` call - there is
 * no way for one primitive to paint part of itself behind the candles and
 * part of it in front. `GexLevelsPrimitive` is `zOrder: 'bottom'` so the
 * bars and walls sit behind price action like Volume/Market Profile; the
 * caption and the hover readout are exactly the opposite requirement - a
 * warning label and a readout that price action can paint over both defeat
 * their own purpose. The only way to get both is two primitives. The manager
 * (`gex-levels.ts`) creates, removes and reconfigures this one in lockstep
 * with the main primitive - see `syncPrimitive()`.
 *
 * Still deliberately has no `setData`: unlike `GexLevelsPrimitive`, this
 * primitive never holds the snapshot itself, even now that it needs the
 * per-strike data (`strikes`) and the walls to hit-test and draw the
 * readout. The manager derives all of it fresh from `snapshotValue` on every
 * `captionOptions()` call and passes it as plain options, the same as
 * `metric` or `side` always were - `hasBars` set this pattern before this
 * primitive needed anything richer than a boolean. So there is still nothing
 * to blank between refreshes and no second copy of the snapshot for the
 * manager to keep in sync - only more fields on an options object it was
 * already re-pushing on every change.
 */
export class GexOverlayPrimitive implements IPrimitive {
  private opts: GexOverlayOptions
  private host: PrimitiveHost | null = null

  constructor(opts: Partial<GexOverlayOptions> = {}) {
    this.opts = { ...DEFAULT_GEX_OVERLAY_OPTIONS, ...opts }
  }

  attached(host: PrimitiveHost): void {
    this.host = host
  }

  detached(): void {
    this.host = null
  }

  zOrder(): ZOrder {
    return 'top'
  }

  setOptions(patch: Partial<GexOverlayOptions>): void {
    this.opts = { ...this.opts, ...patch }
    this.host?.requestUpdate()
  }

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    if (!this.opts.showBars || !this.opts.hasBars) return
    const dpr = rc.dpr
    const axisX = gexColumnAxisX(
      rc.plotWidth,
      this.opts.side,
      this.opts.columnInset,
      this.opts.columnWidth,
      dpr
    )
    const y = rc.plotHeight * dpr - BAR_CAPTION_BOTTOM_PX * dpr

    ctx.save()
    ctx.font = `${LABEL_FONT_PX * dpr}px system-ui, -apple-system, sans-serif`
    ctx.textBaseline = 'bottom'
    ctx.textAlign = 'center'
    // `ChartTheme` (openalgo-charts) has no generic foreground-text field -
    // only `axisText`, which is `--muted-foreground` (see chartTheme.ts) and
    // reads as dim axis-tick chrome, not as a label worth noticing. This app
    // bridges the gap by adding `text` (`--foreground`) onto the theme object
    // it builds (see `AppChartTheme` in `@/lib/trading/chartTheme`), which
    // `ChartTheme` itself does not declare - hence the structural read here
    // rather than a typed property access - falling back to `axisText` for
    // any caller (tests, a future non-app embedding) that only supplies the
    // bare `ChartTheme` shape.
    ctx.fillStyle = (rc.theme as { text?: string }).text ?? rc.theme.axisText
    ctx.fillText(gexMetricCaption(this.opts.metric), axisX, y)
    ctx.restore()

    this.drawHoverReadout(ctx, rc)
  }

  /**
   * Bar geometry for the current frame, shared by `hitTest` and
   * `drawHoverReadout` - the same `computeGexBarGeometry` call
   * `GexLevelsPrimitive.drawBars` makes (same strikes, `priceToY`,
   * `plotHeight`, `columnWidth` and `metric`), so the hover hit region and
   * the readout it feeds can never drift from what `drawBars` actually
   * painted, or from each other.
   */
  private computeBars(rc: PrimitiveRenderContext): { bars: GexBarGeometry[]; rowHeight: number } {
    const priceToY = (price: number): number => rc.priceScale.priceToY(price)
    return computeGexBarGeometry(
      this.opts.strikes,
      priceToY,
      rc.plotHeight,
      this.opts.columnWidth,
      this.opts.metric
    )
  }

  /**
   * Topmost hit under the pointer, in media px (pre-dpr) - see
   * `gexHitTestStrike`'s doc comment for the coordinate space. Delegates the
   * actual point-in-band test to it, over bar geometry recomputed by
   * `computeBars` so the hit region can never drift from the bars
   * `GexLevelsPrimitive.drawBars` actually painted.
   *
   * `distance` is the pixel gap from the pointer to the row's own y,
   * matching how `PriceLinePrimitive.hitTest` computes it - `bestHit` picks
   * the nearest hit across every primitive on the pane before it looks at
   * z-order, so an actually-closer primitive still wins on a tie rather than
   * this one winning on z-order alone.
   *
   * No `cursor` and no `draggable: true` here, deliberately: either arms a
   * drag in `chart.ts` (`hit.draggable === true`, or `cursor === 'ns-resize'`
   * with a drag callback registered) and would turn a press-and-pan over the
   * bar column into a drag attempt instead. A plain hit only sets hover
   * state - see `_onPointerDown` in `openalgo-charts`' `chart.ts`.
   *
   * One inherent side effect of opting into hit-testing at all: drawing
   * tools are themselves primitives with their own `hitTest`
   * (`openalgo-charts`' `draw/layer.ts`), and a click that lands on nothing
   * is how a click deselects the active drawing. A click over the bar column
   * now always resolves to a non-null hit here, so it no longer reads as
   * "clicked on nothing" - an active drawing selected elsewhere on the pane
   * stays selected. Not a bug to fix, just worth not rediscovering.
   */
  hitTest(x: number, y: number, rc: PrimitiveRenderContext): PrimitiveHit | null {
    if (!this.opts.showBars || !this.opts.hasBars) return null
    const { bars, rowHeight } = this.computeBars(rc)
    const hit = gexHitTestStrike({
      bars,
      rowHeight,
      plotWidth: rc.plotWidth,
      columnWidth: this.opts.columnWidth,
      side: this.opts.side,
      columnInset: this.opts.columnInset,
      x,
      y,
    })
    if (!hit) return null
    return {
      externalId: gexStrikeExternalId(hit.strike),
      zOrder: 'top',
      distance: Math.abs(y - hit.y),
    }
  }

  /**
   * The per-strike readout box - drawn only when `rc.hoverId` names one of
   * this frame's bars (see `hitTest`). Resolves the id back to a strike by
   * recomputing bars (`computeBars`) and matching `gexStrikeExternalId` by
   * equality rather than parsing the id apart, so the two directions can
   * never disagree about what a given id means (see `gexStrikeExternalId`'s
   * doc comment).
   *
   * Draws nothing when `showBars`/`hasBars` already gated the whole `draw()`
   * call, when nothing is hovered, or when the hovered id does not resolve
   * to one of this frame's bars (a stale id from the previous frame's data,
   * momentarily, while a fresh snapshot is still in flight).
   */
  private drawHoverReadout(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const hoverId = rc.hoverId
    if (!hoverId) return

    const { bars } = this.computeBars(rc)
    const bar = bars.find((b) => gexStrikeExternalId(b.strike) === hoverId)
    if (!bar) return
    // computeGexBarGeometry already reduced each strike to sign/length; the
    // readout prints the actual GEX/DEX figures, so the source record is
    // looked up again by strike rather than trying to recover them from bar.
    const strikeData = this.opts.strikes.find((s) => s.strike === bar.strike)
    if (!strikeData) return

    const dpr = rc.dpr
    const headerColor = (rc.theme as { text?: string }).text ?? rc.theme.axisText
    // Exact float equality, not a fuzzy comparison - correct today because
    // `gex_levels_service.py` serialises both `strike` (per level) and
    // `call_wall`/`put_wall` from the same `exposures` list, all unrounded
    // (`find_walls` selects `call.strike` straight from those objects). It
    // stops being correct the moment either side of this comparison starts
    // being rounded or independently recomputed - `zero_gamma` right next to
    // it in that same service IS rounded, which is exactly the kind of edit
    // that would make a wall stop matching its strike here with no error,
    // just a "Call wall"/"Put wall" line that quietly stops appearing.
    const lines = gexReadoutLines({
      strike: bar.strike,
      netGex: strikeData.net_gex,
      netDex: strikeData.net_dex,
      metric: this.opts.metric,
      isCallWall: this.opts.callWall != null && bar.strike === this.opts.callWall,
      isPutWall: this.opts.putWall != null && bar.strike === this.opts.putWall,
      headerColor,
      // Reaches across to GexLevelsPrimitive's own option defaults rather
      // than carrying a second callColor/putColor on this primitive's own
      // options: neither GexLevelsConfig nor primitiveOptions()/
      // captionOptions() (gex-levels.ts) ever actually override these two -
      // they are fixed constants dressed up as options, not real per-instance
      // config. Duplicating them here would be a second field to keep in
      // sync with a value that never changes, for a sync path that does not
      // exist. If callColor/putColor ever become genuinely configurable,
      // this reach-across is what breaks (readout colours drift from the
      // bars'), and that is the signal to add them to GexOverlayOptions too.
      callColor: DEFAULT_GEX_PRIMITIVE_OPTIONS.callColor,
      putColor: DEFAULT_GEX_PRIMITIVE_OPTIONS.putColor,
    })

    const boxHeight = READOUT_PADDING_PX * 2 + lines.length * READOUT_LINE_HEIGHT_PX
    // Clamped so a pane narrower than the box's usual width (a small chart,
    // a stacked multi-pane layout) still fits the box on screen instead of
    // computeGexReadoutBoxGeometry being handed a boxWidth it can only clamp
    // the position of, never shrink.
    const boxWidth = Math.min(READOUT_BOX_WIDTH_PX, rc.plotWidth)
    const box = computeGexReadoutBoxGeometry({
      rowY: bar.y,
      boxWidth,
      boxHeight,
      plotWidth: rc.plotWidth,
      plotHeight: rc.plotHeight,
      side: this.opts.side,
      columnInset: this.opts.columnInset,
      columnWidth: this.opts.columnWidth,
      gap: READOUT_GAP_PX,
    })

    const bx = box.x * dpr
    const by = box.y * dpr
    const bw = box.width * dpr
    const bh = box.height * dpr

    ctx.save()
    ctx.globalAlpha = 0.92
    ctx.fillStyle = rc.theme.background
    ctx.fillRect(bx, by, bw, bh)
    ctx.globalAlpha = 1

    ctx.strokeStyle = rc.theme.axisLine
    ctx.lineWidth = Math.max(1, Math.round(dpr))
    // Crisp 1px border, matching drawLevel/drawBars: snap the origin to a
    // half-pixel boundary so the stroke lands on a single device pixel
    // instead of straddling two and blurring, and reset any inherited dash
    // state before stroking - the same defensive setLineDash([]) drawLevel
    // does, even though this primitive never dashes anything itself; nothing
    // here guarantees the canvas arrived in this draw() call un-dashed by
    // whatever a previous primitive last set.
    ctx.setLineDash([])
    ctx.strokeRect(
      Math.round(bx) + 0.5,
      Math.round(by) + 0.5,
      Math.max(1, Math.round(bw)),
      Math.max(1, Math.round(bh))
    )

    // Clip to the box before the text loop, after the border (clipping first
    // would shave the outer half of the border's own stroke) - an overlong
    // line then truncates at the box edge instead of overflowing bare onto
    // the chart.
    ctx.beginPath()
    ctx.rect(bx, by, bw, bh)
    ctx.clip()

    ctx.textAlign = 'left'
    ctx.textBaseline = 'top'
    const padX = READOUT_PADDING_PX * dpr
    let ty = by + READOUT_PADDING_PX * dpr
    for (const line of lines) {
      ctx.globalAlpha = line.emphasis ? 1 : 0.6
      ctx.font = line.emphasis
        ? `600 ${LABEL_FONT_PX * dpr}px system-ui, -apple-system, sans-serif`
        : `${LABEL_FONT_PX * dpr}px system-ui, -apple-system, sans-serif`
      ctx.fillStyle = line.color
      ctx.fillText(line.text, bx + padX, ty)
      ty += READOUT_LINE_HEIGHT_PX * dpr
    }
    ctx.globalAlpha = 1
    ctx.restore()
  }
}
