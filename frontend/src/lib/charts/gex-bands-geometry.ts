/**
 * Pure geometry for Gamma Bands: turning recorded level history into drawable
 * segments.
 *
 * Imports neither `openalgo-charts` nor `CanvasRenderingContext2D`, so it is
 * unit-testable without a canvas or a chart - the same split
 * `gex-levels-geometry.ts` makes for the live study's bars and readout, and for
 * the same reason: last session three defects reached the live chart because
 * jsdom calls handlers with no chart underneath, and the more of the decision
 * making that lives out here, the less of it depends on a browser to check.
 *
 * The one rule this file exists to enforce: **a gap must look like a gap.** The
 * recorder writes nothing for a minute it could not complete, and the study's
 * own `quality.py` and `direction.ts` already forbid rendering a missing input
 * as a value. A band that ran a straight line across a ten-minute outage would
 * be drawing a level nobody observed, with no way for the reader to tell.
 */

/** One recorded reading of one level. `value` is null where the level had no reading. */
export interface GexBandPoint {
  /** Epoch seconds, floored to the recorder's cadence. */
  ts: number
  /**
   * The level's price, or null.
   *
   * Null is a real reading, not missing data: `zero_gamma` is null whenever the
   * gamma profile does not cross zero near the forward, which is an ordinary
   * market state the study already labels "No local cross". Substituting 0
   * would draw a band along the bottom of the chart.
   */
  value: number | null
}

/**
 * How far apart two readings may be and still be joined, in seconds.
 *
 * Two and a half of the recorder's 60-second cadence intervals. One missed tick
 * leaves neighbours 120s apart and must NOT break the line - a single rate-limit
 * hit would otherwise shatter a good session into dozens of one-point segments.
 * Two consecutive misses (180s) is a real outage and does break it.
 *
 * An overnight session break exceeds this by hours, so it breaks for free and
 * needs no separate rule. That matters more than it looks: the chart's time axis
 * is gapless, so without the break a band would run a straight line from
 * yesterday's close to today's open, across a level that was never read.
 */
export const DEFAULT_BAND_MAX_GAP_SECONDS = 150

function isDrawable(point: GexBandPoint): boolean {
  // Non-finite as well as null: NaN reaches the chart as a coordinate and paints
  // a line to nowhere, and a malformed payload can produce one through JSON.
  return point.value !== null && Number.isFinite(point.value)
}

/**
 * Split a level's history into runs that may be drawn as continuous lines.
 *
 * Breaks on two things, and both are readings rather than rendering choices:
 * a time gap wider than `maxGapSeconds` (the recorder missed those minutes), and
 * a null or non-finite value (the level itself had no reading).
 *
 * A single surviving point yields a one-point segment rather than being dropped.
 * It cannot be a line, but it did happen, and the renderer shows it as a dot -
 * silently discarding it would hide a real reading.
 *
 * @param points Readings for one level. Sorted ascending here rather than
 *   assumed: the service returns them in order, but a band drawn from a shuffled
 *   list folds back on itself and reads as violent level churn.
 * @param maxGapSeconds Largest joinable gap. See `DEFAULT_BAND_MAX_GAP_SECONDS`.
 * @returns Segments in time order. Never mutates or aliases `points`.
 */
export function splitBandSegments(
  points: readonly GexBandPoint[],
  maxGapSeconds: number = DEFAULT_BAND_MAX_GAP_SECONDS
): GexBandPoint[][] {
  if (points.length === 0) return []

  const ordered = [...points].sort((a, b) => a.ts - b.ts)

  const segments: GexBandPoint[][] = []
  let current: GexBandPoint[] = []

  for (const point of ordered) {
    if (!isDrawable(point)) {
      // The level had no reading here. End the run; a following reading starts
      // a new one. A run of nulls therefore costs one break, not one each.
      if (current.length > 0) segments.push(current)
      current = []
      continue
    }

    const previous = current[current.length - 1]
    if (previous !== undefined && point.ts - previous.ts > maxGapSeconds) {
      segments.push(current)
      current = []
    }

    current.push({ ts: point.ts, value: point.value })
  }

  if (current.length > 0) segments.push(current)
  return segments
}

/** One minute of every level, as the history endpoint returns it. */
export interface GexLevelReadings {
  ts: number
  call_wall: number | null
  put_wall: number | null
  zero_gamma: number | null
}

/** The time span one level's band draws over, or null if it draws nothing. */
export type GexBandSpan = { fromTs: number; toTs: number } | null

/** Per level, the span its band covers. */
export interface GexBandCoverage {
  call_wall: GexBandSpan
  put_wall: GexBandSpan
  zero_gamma: GexBandSpan
}

/**
 * The span each level's band actually draws over.
 *
 * `GexLevelsPrimitive` uses this to clip its dashed live line away wherever the
 * band already draws that level. The two are the same quantity at the same
 * price, so drawing both put a dash directly on top of a solid line and neither
 * read as its own object - a wall that had not moved during the window appeared
 * to have no band at all.
 *
 * Computed PER LEVEL, and from readings rather than from the request window,
 * because the three do not cover the same span. `zero_gamma` is null whenever
 * the profile does not cross zero near the forward, so a session where it never
 * crossed draws no Zero-Gamma band at all - and clipping the live line over a
 * band that is not there would erase the level rather than defer to it.
 *
 * @param points Readings in any order; only the extremes are used.
 * @returns A span per level, null for any level with no finite reading.
 */
export function computeBandCoverage(points: readonly GexLevelReadings[]): GexBandCoverage {
  const spanFor = (key: 'call_wall' | 'put_wall' | 'zero_gamma'): GexBandSpan => {
    let from = Number.POSITIVE_INFINITY
    let to = Number.NEGATIVE_INFINITY
    for (const point of points) {
      const value = point[key]
      if (value === null || !Number.isFinite(value)) continue
      if (point.ts < from) from = point.ts
      if (point.ts > to) to = point.ts
    }
    return from <= to ? { fromTs: from, toTs: to } : null
  }

  return {
    call_wall: spanFor('call_wall'),
    put_wall: spanFor('put_wall'),
    zero_gamma: spanFor('zero_gamma'),
  }
}

/** One minute of the corridor: both walls, paired. */
export interface GexCorridorPoint {
  ts: number
  upper: number
  lower: number
}

/** A minute's two wall readings before pairing. Either may be absent. */
export interface GexCorridorReading {
  ts: number
  upper: number | null
  lower: number | null
}

/**
 * Split the two wall series into runs that may be FILLED as a corridor.
 *
 * The corridor between the Call Wall and the Put Wall is the region dealers
 * are hedging inside, and drawing it as a shaded band rather than two thin
 * lines is what makes it legible on a chart that already carries a VWAP, three
 * dashed live levels and the candles themselves.
 *
 * A minute is dropped unless BOTH walls have a reading. Filling from a wall to
 * nothing would invent a boundary the data never had - the same rule
 * `splitBandSegments` applies to a single level, applied to a pair.
 *
 * An inverted pair (put wall above call wall) is kept as-is rather than
 * reordered. It is a real reading about a degenerate chain, and silently
 * sorting it would hide the crossing that makes it interesting.
 *
 * @param readings Per-minute wall pairs, any order.
 * @param maxGapSeconds Largest joinable gap. See `DEFAULT_BAND_MAX_GAP_SECONDS`.
 * @returns Corridor runs in time order. Never mutates or aliases `readings`.
 */
export function splitCorridorSegments(
  readings: readonly GexCorridorReading[],
  maxGapSeconds: number = DEFAULT_BAND_MAX_GAP_SECONDS
): GexCorridorPoint[][] {
  if (readings.length === 0) return []

  const ordered = [...readings].sort((a, b) => a.ts - b.ts)

  const segments: GexCorridorPoint[][] = []
  let current: GexCorridorPoint[] = []

  for (const reading of ordered) {
    const { upper, lower } = reading
    if (upper === null || lower === null || !Number.isFinite(upper) || !Number.isFinite(lower)) {
      if (current.length > 0) segments.push(current)
      current = []
      continue
    }

    const previous = current[current.length - 1]
    if (previous !== undefined && reading.ts - previous.ts > maxGapSeconds) {
      segments.push(current)
      current = []
    }

    current.push({ ts: reading.ts, upper, lower })
  }

  if (current.length > 0) segments.push(current)
  return segments
}
