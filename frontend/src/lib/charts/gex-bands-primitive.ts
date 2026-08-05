/**
 * Gamma Bands: the three GEX levels drawn through time.
 *
 * The live study (`gex-levels-primitive.ts`) draws Call Wall, Put Wall and
 * Zero-Gamma as horizontal lines at their CURRENT prices. This draws the same
 * three levels as they were at every recorded minute, so a reader can see when
 * a wall moved and whether price respected it before it did - the question a
 * single snapshot cannot answer, and the reason the recorder exists.
 *
 * A band and its live level are deliberately the same colour: they are the same
 * thing, now versus through time. The band is thinner and slightly transparent
 * so the live level still reads as "where it is" against "where it was".
 *
 * Segment logic lives in `gex-bands-geometry.ts` and is tested without a canvas.
 * This file adds only the `IPrimitive` and the canvas calls.
 *
 * Contributes NOTHING to autoscale, and must not start: `profiles.ts` documents
 * that trap three times and `gex-levels-primitive.ts` documents it again - a
 * primitive that reports its own extent drags the pane's price scale out to
 * cover it and squashes the candles into a sliver. Walls routinely sit hundreds
 * of points from spot, so this one would be among the worst offenders.
 */

import type { IPrimitive, PrimitiveHost, PrimitiveRenderContext, ZOrder } from 'openalgo-charts'
import {
  DEFAULT_BAND_MAX_GAP_SECONDS,
  type GexBandPoint,
  type GexCorridorPoint,
  splitBandSegments,
  splitCorridorSegments,
} from './gex-bands-geometry'

/** One recorded minute: every level at that moment. Mirrors the server's `points[]`. */
export interface GexHistoryPoint {
  ts: number
  call_wall: number | null
  put_wall: number | null
  zero_gamma: number | null
}

export interface GexBandSeries {
  points: readonly GexHistoryPoint[]
}

export interface GexBandsOptions {
  showCallWall: boolean
  showPutWall: boolean
  showZeroGamma: boolean
  /** Largest joinable gap in seconds. See `DEFAULT_BAND_MAX_GAP_SECONDS`. */
  maxGapSeconds: number
  callColor: string
  putColor: string
  zeroGammaColor: string
  /** Bands sit behind the live levels; this is what keeps them from competing. */
  opacity: number
  /**
   * Shade the region between the two walls.
   *
   * This is what makes the feature read as *bands* rather than as three more
   * lines on a chart that already carries a VWAP, three dashed live levels and
   * the candles. The corridor is the range dealers are hedging inside, and its
   * width through the session is the thing worth seeing - two thin lines make
   * the reader measure it by eye.
   */
  showCorridor: boolean
  corridorColor: string
  /** Deliberately very low: the corridor is a backdrop, never a foreground object. */
  corridorOpacity: number
}

export const DEFAULT_GEX_BANDS_OPTIONS: GexBandsOptions = {
  showCallWall: true,
  showPutWall: true,
  showZeroGamma: true,
  maxGapSeconds: DEFAULT_BAND_MAX_GAP_SECONDS,
  // The same three colours the live levels use - a band is the same object as
  // its level, seen through time.
  callColor: '#26a69a',
  putColor: '#ef5350',
  zeroGammaColor: '#f5a623',
  opacity: 0.8,
  showCorridor: true,
  // Neutral rather than either wall's colour: the corridor belongs to both, and
  // tinting it green or red would imply a direction the region does not carry.
  corridorColor: '#7e8aa2',
  corridorOpacity: 0.1,
}

/** Radius in px (before dpr) of the dot marking a reading with no neighbours. */
const LONE_POINT_RADIUS_PX = 2

type BandKey = 'call_wall' | 'put_wall' | 'zero_gamma'

export class GexBandsPrimitive implements IPrimitive {
  private opts: GexBandsOptions
  private data: GexBandSeries | null = null
  private host: PrimitiveHost | null = null

  constructor(opts: Partial<GexBandsOptions> = {}) {
    this.opts = { ...DEFAULT_GEX_BANDS_OPTIONS, ...opts }
  }

  attached(host: PrimitiveHost): void {
    this.host = host
  }

  detached(): void {
    this.host = null
  }

  zOrder(): ZOrder {
    return 'bottom'
  }

  // `autoscaleInfo` is DELIBERATELY not implemented - see the file header.

  setData(data: GexBandSeries | null): void {
    this.data = data
    this.host?.requestUpdate()
  }

  setOptions(patch: Partial<GexBandsOptions>): void {
    this.opts = { ...this.opts, ...patch }
    this.host?.requestUpdate()
  }

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const points = this.data?.points
    if (!points || points.length === 0) return

    // The shaded region first, so both wall edges are drawn on top of it.
    // Gated on both walls being shown: a corridor is bounded BY them, and
    // shading up to a hidden edge would assert a boundary the reader cannot see.
    if (this.opts.showCorridor && this.opts.showCallWall && this.opts.showPutWall) {
      this.drawCorridor(ctx, rc, points)
    }

    const bands: Array<[BandKey, boolean, string, boolean]> = [
      ['call_wall', this.opts.showCallWall, this.opts.callColor, false],
      ['put_wall', this.opts.showPutWall, this.opts.putColor, false],
      // Dotted, and drawn last so it reads as a marker INSIDE the corridor
      // rather than as a third edge of it.
      ['zero_gamma', this.opts.showZeroGamma, this.opts.zeroGammaColor, true],
    ]

    for (const [key, shown, colour, dotted] of bands) {
      if (!shown) continue
      this.drawBand(ctx, rc, points, key, colour, dotted)
    }
  }

  /**
   * Shade between the two walls, one filled polygon per unbroken run.
   *
   * Built by walking the upper edge left to right and the lower edge back
   * right to left, both as step lines so the fill's boundary matches the
   * strokes exactly - a fill that sloped where its edge stepped would leave
   * visible slivers at every wall move.
   */
  private drawCorridor(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    points: readonly GexHistoryPoint[]
  ): void {
    const segments = splitCorridorSegments(
      points.map((p) => ({ ts: p.ts, upper: p.call_wall, lower: p.put_wall })),
      this.opts.maxGapSeconds
    )
    if (segments.length === 0) return

    ctx.save()
    ctx.globalAlpha = this.opts.corridorOpacity
    ctx.fillStyle = this.opts.corridorColor

    for (const segment of segments) {
      // A single minute has no width to fill.
      if (segment.length < 2) continue

      ctx.beginPath()
      this.traceStepEdge(ctx, rc, segment, 'upper', false)
      this.traceStepEdge(ctx, rc, segment, 'lower', true)
      ctx.closePath()
      ctx.fill()
    }

    ctx.restore()
  }

  /**
   * Append one edge of a corridor segment to the current path as a step line.
   *
   * @param reverse Walk right to left, for the return leg that closes the
   *   polygon. The step order inverts with it so the returned edge traces the
   *   same outline the forward one would.
   */
  private traceStepEdge(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    segment: readonly GexCorridorPoint[],
    edge: 'upper' | 'lower',
    reverse: boolean
  ): void {
    const dpr = rc.dpr
    const ordered = reverse ? [...segment].reverse() : segment

    let previousY = rc.priceScale.priceToY(ordered[0][edge]) * dpr
    const firstX = this.xFor(rc, ordered[0].ts)
    if (reverse) ctx.lineTo(firstX, previousY)
    else ctx.moveTo(firstX, previousY)

    for (let i = 1; i < ordered.length; i += 1) {
      const point = ordered[i]
      const x = this.xFor(rc, point.ts)
      const y = rc.priceScale.priceToY(point[edge]) * dpr
      if (reverse) {
        // Walking backwards, the vertical comes before the horizontal so the
        // outline retraces the forward step rather than mirroring it.
        if (y !== previousY) ctx.lineTo(this.xFor(rc, ordered[i - 1].ts), y)
        ctx.lineTo(x, y)
      } else {
        ctx.lineTo(x, previousY)
        if (y !== previousY) ctx.lineTo(x, y)
      }
      previousY = y
    }
  }

  private drawBand(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    points: readonly GexHistoryPoint[],
    key: BandKey,
    colour: string,
    dotted = false
  ): void {
    const readings: GexBandPoint[] = points.map((p) => ({ ts: p.ts, value: p[key] }))
    const segments = splitBandSegments(readings, this.opts.maxGapSeconds)
    if (segments.length === 0) return

    const dpr = rc.dpr

    ctx.save()
    ctx.globalAlpha = this.opts.opacity
    ctx.strokeStyle = colour
    ctx.fillStyle = colour
    // The wall edges carry the corridor's shape, so they are a touch heavier
    // than a hairline - at 1px against candles and a VWAP they read as noise.
    ctx.lineWidth = Math.max(1, Math.round(dpr * (dotted ? 1 : 1.5)))
    ctx.setLineDash(dotted ? [2 * dpr, 3 * dpr] : [])

    for (const segment of segments) {
      // One path PER SEGMENT. A single path across all of them would join the
      // last reading before an outage to the first one after it, drawing a
      // level nobody observed.
      ctx.beginPath()

      if (segment.length === 1) {
        // A lone reading cannot be a line, but it did happen. A dot says so
        // without implying it held for any length of time.
        const only = segment[0]
        const x = this.xFor(rc, only.ts)
        const y = rc.priceScale.priceToY(only.value as number) * dpr
        ctx.arc(x, y, LONE_POINT_RADIUS_PX * dpr, 0, Math.PI * 2)
        ctx.fill()
        continue
      }

      let previousY = rc.priceScale.priceToY(segment[0].value as number) * dpr
      ctx.moveTo(this.xFor(rc, segment[0].ts), previousY)

      for (let i = 1; i < segment.length; i += 1) {
        const point = segment[i]
        const x = this.xFor(rc, point.ts)
        const y = rc.priceScale.priceToY(point.value as number) * dpr
        // Step, not slope. A wall sits AT a strike until it moves to another
        // strike; a diagonal would imply the level passed through prices
        // between them that no strike ever occupied.
        ctx.lineTo(x, previousY)
        if (y !== previousY) ctx.lineTo(x, y)
        previousY = y
      }

      ctx.stroke()
    }

    ctx.restore()
  }

  /**
   * Epoch seconds to an x coordinate.
   *
   * `timeToIndexFloat`, not `timeToIndex`: a snapshot is floored to the
   * recorder's minute cadence while the chart may be on any timeframe, so an
   * exact-match lookup would silently drop four of every five points on a
   * 5-minute chart and all but one on an hourly. The float version also handles
   * positions BETWEEN bars, which the gapless axis makes common - everything a
   * session break collapsed away lands there.
   */
  private xFor(rc: PrimitiveRenderContext, ts: number): number {
    return rc.timeScale.indexToX(rc.dataLayer.timeToIndexFloat(ts)) * rc.dpr
  }
}
