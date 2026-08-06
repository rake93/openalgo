import { describe, expect, it } from 'vitest'
import {
  computeBandCoverage,
  DEFAULT_BAND_MAX_GAP_SECONDS,
  type GexBandPoint,
  splitBandSegments,
  splitCorridorSegments,
} from './gex-bands-geometry'

const M = 60

function run(...points: Array<[number, number | null]>): GexBandPoint[][] {
  return splitBandSegments(
    points.map(([ts, value]) => ({ ts, value })),
    DEFAULT_BAND_MAX_GAP_SECONDS
  )
}

describe('splitBandSegments', () => {
  it('keeps a continuous run as one segment', () => {
    const segments = run([0, 100], [M, 100], [2 * M, 110], [3 * M, 110])

    expect(segments).toHaveLength(1)
    expect(segments[0].map((p) => p.ts)).toEqual([0, M, 2 * M, 3 * M])
  })

  it('breaks the line where the recorder missed a tick', () => {
    // A failed tick has no row. Joining across it would draw a level that was
    // never read - the same error quality.py and direction.ts already forbid.
    const segments = run([0, 100], [M, 100], [10 * M, 200], [11 * M, 200])

    expect(segments).toHaveLength(2)
    expect(segments[0].map((p) => p.ts)).toEqual([0, M])
    expect(segments[1].map((p) => p.ts)).toEqual([10 * M, 11 * M])
  })

  it('tolerates a single missed minute without fragmenting the line', () => {
    // The threshold is 2.5 cadence intervals precisely so one dropped tick -
    // which happens on any rate limit - does not shatter an otherwise good
    // session into dozens of one-point segments.
    const segments = run([0, 100], [2 * M, 100], [3 * M, 100])

    expect(segments).toHaveLength(1)
  })

  it('breaks on a null and does not emit it as a value', () => {
    // zero_gamma: null is "no local cross" - a real reading, not a missing
    // number. Drawn as 0 it would put a band at the bottom of the chart.
    const segments = run([0, 100], [M, null], [2 * M, 120])

    expect(segments).toHaveLength(2)
    expect(segments[0].map((p) => p.value)).toEqual([100])
    expect(segments[1].map((p) => p.value)).toEqual([120])
    expect(segments.flat().some((p) => p.value === null)).toBe(false)
    expect(segments.flat().some((p) => p.value === 0)).toBe(false)
  })

  it('treats a run of nulls as one break, not one per null', () => {
    const segments = run([0, 100], [M, null], [2 * M, null], [3 * M, null], [4 * M, 120])

    expect(segments).toHaveLength(2)
  })

  it('keeps a lone reading as its own segment', () => {
    // A single point cannot be a line, but dropping it would hide a reading
    // that genuinely happened. The renderer draws it as a dot.
    const segments = run([0, 100], [30 * M, 200], [60 * M, 300])

    expect(segments).toHaveLength(3)
    expect(segments.every((s) => s.length === 1)).toBe(true)
  })

  it('returns nothing for an empty history', () => {
    expect(splitBandSegments([], DEFAULT_BAND_MAX_GAP_SECONDS)).toEqual([])
  })

  it('returns nothing when every value is null', () => {
    expect(run([0, null], [M, null])).toEqual([])
  })

  it('breaks across an overnight session gap for free', () => {
    // The chart's axis is gapless, so without this the band would run a
    // straight line from yesterday's close to today's open through a level
    // nobody observed. No separate session rule is needed - a break is simply
    // longer than the threshold.
    const segments = run([0, 100], [M, 100], [17 * 3600, 250], [17 * 3600 + M, 250])

    expect(segments).toHaveLength(2)
  })

  it('does not mutate or alias the caller-supplied points', () => {
    const points: GexBandPoint[] = [
      { ts: 0, value: 100 },
      { ts: M, value: null },
      { ts: 2 * M, value: 120 },
    ]
    const before = JSON.parse(JSON.stringify(points))

    splitBandSegments(points, DEFAULT_BAND_MAX_GAP_SECONDS)

    expect(points).toEqual(before)
  })

  it('is defensive about unsorted input rather than drawing a zigzag', () => {
    // The service returns ascending order, but a band drawn from a shuffled
    // list would fold back on itself and look like violent level churn.
    const segments = run([2 * M, 120], [0, 100], [M, 110])

    expect(segments).toHaveLength(1)
    expect(segments[0].map((p) => p.ts)).toEqual([0, M, 2 * M])
  })

  it('honours a caller-supplied gap threshold', () => {
    const points = [
      { ts: 0, value: 100 },
      { ts: 5 * M, value: 100 },
    ]

    expect(splitBandSegments(points, 600)).toHaveLength(1)
    expect(splitBandSegments(points, 120)).toHaveLength(2)
  })

  it('drops a non-finite value the way it drops a null', () => {
    // NaN reaches the chart as a coordinate and paints a line to nowhere;
    // JSON.parse can also yield one from a malformed payload.
    const segments = run([0, 100], [M, Number.NaN], [2 * M, 120])

    expect(segments).toHaveLength(2)
    expect(segments.flat().every((p) => Number.isFinite(p.value))).toBe(true)
  })
})

describe('DEFAULT_BAND_MAX_GAP_SECONDS', () => {
  it('is two and a half recorder cadence intervals', () => {
    // Tied to the recorder's 60s cadence: one missed tick (120s apart) must
    // not break the line, two (180s) must.
    expect(DEFAULT_BAND_MAX_GAP_SECONDS).toBe(150)
  })
})

function corridor(...rows: Array<[number, number | null, number | null]>) {
  return splitCorridorSegments(
    rows.map(([ts, upper, lower]) => ({ ts, upper, lower })),
    DEFAULT_BAND_MAX_GAP_SECONDS
  )
}

describe('splitCorridorSegments', () => {
  it('pairs the two walls into one filled run', () => {
    const segments = corridor([0, 24700, 24400], [M, 24700, 24400], [2 * M, 24750, 24400])

    expect(segments).toHaveLength(1)
    expect(segments[0]).toEqual([
      { ts: 0, upper: 24700, lower: 24400 },
      { ts: M, upper: 24700, lower: 24400 },
      { ts: 2 * M, upper: 24750, lower: 24400 },
    ])
  })

  it('breaks the corridor wherever the line would break', () => {
    const segments = corridor([0, 24700, 24400], [M, 24700, 24400], [10 * M, 24750, 24450])

    expect(segments).toHaveLength(2)
  })

  it('drops a minute where EITHER wall has no reading', () => {
    // A corridor needs both edges. Filling from a wall to nothing would invent
    // a boundary the data never had.
    const segments = corridor([0, 24700, 24400], [M, 24700, null], [2 * M, 24700, 24400])

    expect(segments).toHaveLength(2)
    expect(segments.flat()).toHaveLength(2)
  })

  it('keeps an inverted corridor rather than silently reordering it', () => {
    // If the put wall ever prints above the call wall that is a real reading
    // about a degenerate chain, and the fill should show it crossed rather
    // than hide it by sorting the pair.
    const segments = corridor([0, 24400, 24700])

    expect(segments[0][0]).toEqual({ ts: 0, upper: 24400, lower: 24700 })
  })

  it('returns nothing when a wall is absent throughout', () => {
    expect(corridor([0, 24700, null], [M, 24700, null])).toEqual([])
  })
})

describe('computeBandCoverage', () => {
  const reading = (ts: number, call: number | null, put: number | null, zg: number | null) => ({
    ts,
    call_wall: call,
    put_wall: put,
    zero_gamma: zg,
  })

  it('reports each level’s first and last reading', () => {
    const coverage = computeBandCoverage([
      reading(0, 24_800, 24_400, 24_600),
      reading(M, 24_800, 24_400, 24_610),
      reading(2 * M, 24_800, 24_400, 24_620),
    ])

    expect(coverage.call_wall).toEqual({ fromTs: 0, toTs: 2 * M })
    expect(coverage.zero_gamma).toEqual({ fromTs: 0, toTs: 2 * M })
  })

  it('returns null for a level that never had a reading', () => {
    // The case that matters: zero_gamma is null whenever the profile does not
    // cross zero near the forward, so its band draws nothing and the live line
    // must NOT be clipped against it.
    const coverage = computeBandCoverage([
      reading(0, 24_800, 24_400, null),
      reading(M, 24_800, 24_400, null),
    ])

    expect(coverage.call_wall).toEqual({ fromTs: 0, toTs: M })
    expect(coverage.zero_gamma).toBeNull()
  })

  it('spans only the minutes a level actually had readings', () => {
    // A level that appears late must not claim the whole window, or the live
    // line would be clipped over a span its band never drew.
    const coverage = computeBandCoverage([
      reading(0, 24_800, null, null),
      reading(M, 24_800, 24_400, null),
      reading(2 * M, 24_800, 24_400, null),
    ])

    expect(coverage.put_wall).toEqual({ fromTs: M, toTs: 2 * M })
  })

  it('ignores a non-finite reading the same way the splitter does', () => {
    const coverage = computeBandCoverage([
      reading(0, Number.NaN, 24_400, null),
      reading(M, 24_800, 24_400, null),
    ])

    expect(coverage.call_wall).toEqual({ fromTs: M, toTs: M })
  })

  it('returns null for every level when there are no points', () => {
    expect(computeBandCoverage([])).toEqual({
      call_wall: null,
      put_wall: null,
      zero_gamma: null,
    })
  })
})
