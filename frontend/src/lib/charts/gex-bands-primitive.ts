/**
 * Gamma Bands: the three GEX levels drawn through time.
 *
 * The live study (`gex-levels-primitive.ts`) draws Call Wall, Put Wall and
 * Zero-Gamma as horizontal lines at their CURRENT prices. This draws the same
 * three levels as they were at every recorded minute, so a reader can see when
 * a wall moved and whether price respected it before it did - the question a
 * single snapshot cannot answer, and the reason the recorder exists.
 *
 * A band and its live level are deliberately the same colour and width: they are
 * the same thing, now versus through time, and together they form ONE line per
 * level - solid where history recorded it, dashed beyond it at the current
 * value. `GexLevelsPrimitive` clips its dashed line away wherever a band covers
 * it, so the two are never drawn on top of each other.
 *
 * That clipping is not cosmetic. Measured on a live chart, a band sat within one
 * pixel of its own live level, dashed and opaque on top of solid and faded, and
 * the composite read as a single two-tone dashed line: a wall that had not moved
 * during the window appeared to have no band at all, while one that had moved
 * appeared to be the only band drawn. Same code, different data.
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
  // Fully opaque. The live level no longer overprints a band, so there is
  // nothing to sit behind, and a faded run of line meeting a solid one at the
  // edge of history would read as a seam in a level that did not change.
  opacity: 1,
  // OFF by default. The three levels are meant to read as three distinct lines;
  // a shaded region between two of them competes with exactly that. Still a
  // toggle in the Studies panel for anyone who wants the corridor.
  showCorridor: false,
  // Neutral rather than either wall's colour: the corridor belongs to both, and
  // tinting it green or red would imply a direction the region does not carry.
  corridorColor: '#7e8aa2',
  corridorOpacity: 0.1,
}

/** Radius in px (before dpr) of the dot marking a reading with no neighbours. */
const LONE_POINT_RADIUS_PX = 2

type BandKey = 'call_wall' | 'put_wall' | 'zero_gamma'

/**
 * How consecutive readings of one band are joined.
 *
 * `step` for a level that holds a value until it jumps to another (the walls,
 * which sit on strikes); `linear` for one that varies continuously between
 * readings (Zero-Gamma). See `bandSpecs`.
 */
type BandInterpolation = 'step' | 'linear'

/** One band's rendering, as `bandSpecs` describes it. */
interface BandSpec {
  key: BandKey
  shown: boolean
  colour: string
  interpolation: BandInterpolation
}

/** Everything `draw()` needs that depends on the data rather than the viewport. */
interface PreparedSegments {
  corridor: GexCorridorPoint[][]
  call_wall: GexBandPoint[][]
  put_wall: GexBandPoint[][]
  zero_gamma: GexBandPoint[][]
}

export class GexBandsPrimitive implements IPrimitive {
  private opts: GexBandsOptions
  private data: GexBandSeries | null = null
  private host: PrimitiveHost | null = null
  /**
   * Segments for the current data and options; null means "rebuild on next draw".
   *
   * `draw()` fires on every pan, zoom and tick, and re-splitting the whole
   * history each time cost 9.46 ms per frame at `MAX_HISTORY_POINTS` - 57% of a
   * 60fps budget, measured in the 2026-08-06 fd-audit. Splitting depends only on
   * the points and on `maxGapSeconds`; neither changes between frames, while the
   * viewport changes constantly. So the split is done once and the per-frame
   * work is reduced to walking the result.
   *
   * Exactly ONE prepared value per instance, and deliberately NOT a cache keyed
   * by data, window or viewport. A keyed cache here would be the unbounded
   * module-level registry the `fd-audit` skill exists to catch - a genuine leak
   * traded in to fix a cost that was never one.
   */
  private prepared: PreparedSegments | null = null

  constructor(opts: Partial<GexBandsOptions> = {}) {
    this.opts = { ...DEFAULT_GEX_BANDS_OPTIONS, ...opts }
  }

  attached(host: PrimitiveHost): void {
    this.host = host
  }

  detached(): void {
    this.host = null
    // Released with the host: the segments are derived data, and a detached
    // primitive that something still holds a reference to should not also be
    // holding a window's worth of them.
    this.prepared = null
  }

  zOrder(): ZOrder {
    return 'bottom'
  }

  // `autoscaleInfo` is DELIBERATELY not implemented - see the file header.

  setData(data: GexBandSeries | null): void {
    this.data = data
    this.prepared = null
    this.host?.requestUpdate()
  }

  setOptions(patch: Partial<GexBandsOptions>): void {
    this.opts = { ...this.opts, ...patch }
    // Invalidated on ANY option change, not only on `maxGapSeconds`. Options
    // change when a user clicks something, never per frame, so the recompute is
    // free - and gating it on the one option that currently feeds the split
    // would silently go stale the day another one does.
    this.prepared = null
    this.host?.requestUpdate()
  }

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const points = this.data?.points
    if (!points || points.length === 0) return

    const segments = this.segments(points)

    // The shaded region first, so both wall edges are drawn on top of it.
    // Gated on both walls being shown: a corridor is bounded BY them, and
    // shading up to a hidden edge would assert a boundary the reader cannot see.
    if (this.opts.showCorridor && this.opts.showCallWall && this.opts.showPutWall) {
      this.drawCorridor(ctx, rc, segments.corridor)
    }

    for (const band of this.bandSpecs()) {
      if (!band.shown) continue
      this.drawBand(ctx, rc, segments[band.key], band)
    }
  }

  /**
   * The split segments, built on first use after data or options changed.
   *
   * All four series are built together rather than per band: they share one
   * pass over the points, and a band that is switched off still costs nothing
   * to hold. See `prepared` for why this is memoised at all.
   */
  private segments(points: readonly GexHistoryPoint[]): PreparedSegments {
    if (this.prepared !== null) return this.prepared

    const gap = this.opts.maxGapSeconds
    const band = (key: BandKey) =>
      splitBandSegments(
        points.map((p) => ({ ts: p.ts, value: p[key] })),
        gap
      )

    this.prepared = {
      corridor: splitCorridorSegments(
        points.map((p) => ({ ts: p.ts, upper: p.call_wall, lower: p.put_wall })),
        gap
      ),
      call_wall: band('call_wall'),
      put_wall: band('put_wall'),
      zero_gamma: band('zero_gamma'),
    }
    return this.prepared
  }

  /**
   * The three bands, and the two things that differ between them.
   *
   * `interpolation` is the one that matters and is not cosmetic. A wall is
   * strike-quantised: it sits AT a strike until it moves to another strike, so
   * it is drawn as a step and a diagonal would imply it passed through prices
   * no strike ever occupied. Zero-Gamma is not a strike at all - it is a
   * crossing price interpolated between them, which moves by a few points every
   * minute and genuinely does pass through the values in between. Stepping it
   * asserts something false, and dashed at a minute's spacing the staircase
   * fragments into scattered marks rather than reading as a line.
   */
  private bandSpecs(): BandSpec[] {
    return [
      {
        key: 'call_wall',
        shown: this.opts.showCallWall,
        colour: this.opts.callColor,
        interpolation: 'step',
      },
      {
        key: 'put_wall',
        shown: this.opts.showPutWall,
        colour: this.opts.putColor,
        interpolation: 'step',
      },
      // Drawn last so it sits above the other two where they cross.
      {
        key: 'zero_gamma',
        shown: this.opts.showZeroGamma,
        colour: this.opts.zeroGammaColor,
        interpolation: 'linear',
      },
    ]
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
    segments: readonly GexCorridorPoint[][]
  ): void {
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
    segments: readonly GexBandPoint[][],
    band: BandSpec
  ): void {
    const { colour, interpolation } = band
    if (segments.length === 0) return

    const dpr = rc.dpr

    ctx.save()
    ctx.globalAlpha = this.opts.opacity
    ctx.strokeStyle = colour
    ctx.fillStyle = colour
    // Every band is a solid hairline, matching the live level's own width. The
    // live level is clipped away wherever a band covers it, so the two are
    // never collinear and the pair now reads as ONE line per level: solid
    // through recorded history, dashed at the current value beyond it. Zero
    // Gamma used to be dashed here to keep it distinct inside the shaded
    // corridor; with the corridor off by default that only fragmented it.
    ctx.lineWidth = Math.max(1, Math.round(dpr))
    ctx.setLineDash([])

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
        if (interpolation === 'step') {
          // Hold the old level across to the new x, then jump. See `bandSpecs`
          // for why only the walls are drawn this way.
          ctx.lineTo(x, previousY)
          if (y !== previousY) ctx.lineTo(x, y)
        } else {
          ctx.lineTo(x, y)
        }
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
