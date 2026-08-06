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
 * The pure geometry and formatting behind both primitives - bar layout,
 * hit-testing, price/money formatting, the readout's box placement and text
 * content - lives in `gex-levels-geometry.ts`, which imports neither
 * `openalgo-charts` nor `CanvasRenderingContext2D` and can be unit-tested
 * without a canvas or a chart. This file imports from it and adds only the
 * two `IPrimitive` implementations that actually issue canvas calls.
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
import type { GexBandCoverage, GexBandSpan } from './gex-bands-geometry'
import {
  computeGexBarGeometry,
  computeGexLevelPlacement,
  computeGexReadoutBoxGeometry,
  formatGexPrice,
  type GexBarGeometry,
  gexColumnAxisX,
  gexHitTestStrike,
  gexMetricCaption,
  gexReadoutLines,
  gexStrikeExternalId,
} from './gex-levels-geometry'

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
  /**
   * Where Gamma Bands already draw each level, so this primitive can stand back.
   *
   * A band and its live level are the same quantity at the same price. Drawn
   * together they landed within one pixel of each other - dashed and opaque over
   * solid - and the composite read as a single two-tone dashed line rather than
   * as two objects. Clipping the dash away over the recorded span makes the pair
   * read as ONE line per level: solid where it was recorded, dashed at the
   * current value beyond it.
   *
   * Null when Bands is off or has no history, which restores the full-width
   * dashed line exactly as before.
   */
  bandCoverage: GexBandCoverage | null
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
  bandCoverage: null,
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
 * Gap in px (before dpr scaling) between the plot bottom and the metric
 * caption. Bottom, not top: the top band is where the pane legend's row 0
 * (symbol + OHLCV) lives, and where an off-screen level's edge-marker stub
 * (`EDGE_MARKER_INSET_PX`) is most often drawn - the bottom is the one edge
 * neither of those two routinely claims.
 */
const BAR_CAPTION_BOTTOM_PX = 12

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

    const covered = this.opts.bandCoverage
    if (this.opts.showCallWall && d.call_wall != null) {
      this.drawLevel(
        ctx,
        rc,
        d.call_wall,
        'Call Wall',
        this.opts.callColor,
        true,
        covered?.call_wall
      )
    }
    if (this.opts.showPutWall && d.put_wall != null) {
      this.drawLevel(ctx, rc, d.put_wall, 'Put Wall', this.opts.putColor, true, covered?.put_wall)
    }
    // `zero_gamma: null` is an ordinary market state (the gamma profile does
    // not cross zero near the forward), not a missing value to fall back on -
    // the dashboard already shows "No local cross" for it, so this just skips
    // the line rather than drawing a misleading one at price 0.
    if (this.opts.showZeroGamma && d.zero_gamma != null) {
      this.drawLevel(
        ctx,
        rc,
        d.zero_gamma,
        'Zero-Gamma',
        this.opts.zeroGammaColor,
        false,
        covered?.zero_gamma
      )
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
    dashed: boolean,
    covered: GexBandSpan = null
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
    const xEnd = placement.onScreen
      ? rc.plotWidth * dpr
      : Math.min(EDGE_MARKER_LENGTH_PX * dpr, rc.plotWidth * dpr)

    // Skip the span a band already draws. Up to two runs, because recorded
    // history can sit in the middle of the visible range with older bars to its
    // left; both sides still need the level.
    const skip = this.coveredSpanX(rc, covered)
    if (skip === null) {
      ctx.moveTo(0, y)
      ctx.lineTo(xEnd, y)
    } else {
      const [skipFrom, skipTo] = skip
      if (skipFrom > 0) {
        ctx.moveTo(0, y)
        ctx.lineTo(Math.min(skipFrom, xEnd), y)
      }
      if (skipTo < xEnd) {
        ctx.moveTo(Math.max(skipTo, 0), y)
        ctx.lineTo(xEnd, y)
      }
    }
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
   * A band's covered time span as an x range in device pixels, or null.
   *
   * `timeToIndexFloat` for the same reason `GexBandsPrimitive` uses it: a
   * snapshot is floored to the recorder's minute cadence while the chart may be
   * on any timeframe, so an exact-match lookup would land nowhere on a 5-minute
   * chart and the clip would silently never apply.
   *
   * The label is deliberately NOT clipped. It is the only thing that states the
   * level's current price in words, and a reader whose whole visible range is
   * covered by history would otherwise lose it entirely.
   */
  private coveredSpanX(rc: PrimitiveRenderContext, covered: GexBandSpan): [number, number] | null {
    if (!covered) return null
    const xFor = (ts: number) => rc.timeScale.indexToX(rc.dataLayer.timeToIndexFloat(ts)) * rc.dpr
    const from = xFor(covered.fromTs)
    const to = xFor(covered.toTs)
    if (!Number.isFinite(from) || !Number.isFinite(to)) return null
    return from <= to ? [from, to] : [to, from]
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
