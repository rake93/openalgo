import type { Bar } from 'openalgo-charts'
import { describe, expect, it } from 'vitest'
import {
  bucketStart,
  marketOpenSeconds,
  mergedIntervalGroups,
  resampleBars,
  resamplePlan,
  sessionAnchor,
} from './resample'

/** 2026-07-24 09:15 IST as UTC seconds (IST is UTC+5:30, no DST). */
const NSE_OPEN = Date.UTC(2026, 6, 24, 3, 45, 0) / 1000

const bar = (time: number, o: number, h: number, l: number, c: number, v = 1): Bar => ({
  time,
  open: o,
  high: h,
  low: l,
  close: c,
  volume: v,
})

describe('market open alignment', () => {
  it('uses 09:15 for equity and F&O segments', () => {
    expect(marketOpenSeconds('NSE')).toBe(33_300)
    expect(marketOpenSeconds('NFO')).toBe(33_300)
    expect(marketOpenSeconds('nse_index')).toBe(33_300)
  })

  it('uses 09:00 for commodity and currency segments', () => {
    expect(marketOpenSeconds('MCX')).toBe(32_400)
    expect(marketOpenSeconds('CDS')).toBe(32_400)
  })

  it('falls back to 09:15 for anything unknown', () => {
    expect(marketOpenSeconds('WEIRD')).toBe(33_300)
  })

  it('anchors to the session open of the IST day, not to UTC midnight', () => {
    // Mid-session timestamp resolves back to that day's 09:15.
    expect(sessionAnchor(NSE_OPEN + 4 * 3600, 'NSE')).toBe(NSE_OPEN)
    // MCX opens 15 minutes earlier on the same day.
    expect(sessionAnchor(NSE_OPEN + 4 * 3600, 'MCX')).toBe(NSE_OPEN - 900)
  })
})

describe('bucketStart', () => {
  it('starts the first bucket exactly at the open', () => {
    expect(bucketStart(NSE_OPEN, 180, 'NSE')).toBe(NSE_OPEN)
  })

  it('groups by the target size measured from the open', () => {
    // 09:15, 09:18, 09:21 for 3-minute buckets.
    expect(bucketStart(NSE_OPEN + 60, 180, 'NSE')).toBe(NSE_OPEN)
    expect(bucketStart(NSE_OPEN + 179, 180, 'NSE')).toBe(NSE_OPEN)
    expect(bucketStart(NSE_OPEN + 180, 180, 'NSE')).toBe(NSE_OPEN + 180)
    expect(bucketStart(NSE_OPEN + 500, 180, 'NSE')).toBe(NSE_OPEN + 360)
  })

  it('handles 4-minute buckets, which do not divide the epoch day evenly', () => {
    expect(bucketStart(NSE_OPEN + 239, 240, 'NSE')).toBe(NSE_OPEN)
    expect(bucketStart(NSE_OPEN + 241, 240, 'NSE')).toBe(NSE_OPEN + 240)
  })

  it('puts a pre-open print in the bucket below the open, never on it', () => {
    expect(bucketStart(NSE_OPEN - 1, 180, 'NSE')).toBe(NSE_OPEN - 180)
  })
})

describe('resamplePlan', () => {
  const broker = ['1m', '5m', '15m', '25m', '1h', 'D']

  it('returns null when the broker already serves the interval', () => {
    expect(resamplePlan('5m', broker)).toBeNull()
    expect(resamplePlan('D', broker)).toBeNull()
  })

  it('picks the coarsest divisor so the request stays small', () => {
    // 2h divides by 1h, not by 1m.
    expect(resamplePlan('2h', broker)).toEqual({ source: '1h', targetSec: 7200 })
    // 45m divides by 15m.
    expect(resamplePlan('45m', broker)).toEqual({ source: '15m', targetSec: 2700 })
    // 10m divides by 5m.
    expect(resamplePlan('10m', broker)).toEqual({ source: '5m', targetSec: 600 })
  })

  it('falls back to the finest interval when nothing coarser divides', () => {
    expect(resamplePlan('3m', broker)).toEqual({ source: '1m', targetSec: 180 })
    expect(resamplePlan('4m', broker)).toEqual({ source: '1m', targetSec: 240 })
  })

  it('refuses calendar intervals, which are not arithmetic', () => {
    expect(resamplePlan('W', broker)).toBeNull()
    expect(resamplePlan('M', broker)).toBeNull()
  })

  it('returns null when no native interval divides the target', () => {
    expect(resamplePlan('7m', ['5m', '15m'])).toBeNull()
  })
})

describe('resampleBars', () => {
  it('folds OHLCV correctly: first open, extremes, last close, summed volume', () => {
    const src = [
      bar(NSE_OPEN, 100, 105, 99, 104, 10),
      bar(NSE_OPEN + 60, 104, 110, 103, 107, 20),
      bar(NSE_OPEN + 120, 107, 108, 95, 96, 30),
      // second bucket
      bar(NSE_OPEN + 180, 96, 97, 90, 92, 40),
    ]
    const out = resampleBars(src, 180, 'NSE')
    expect(out).toHaveLength(2)
    expect(out[0]).toEqual({
      time: NSE_OPEN,
      open: 100,
      high: 110,
      low: 95,
      close: 96,
      volume: 60,
    })
    expect(out[1]).toEqual({
      time: NSE_OPEN + 180,
      open: 96,
      high: 97,
      low: 90,
      close: 92,
      volume: 40,
    })
  })

  it('does not merge across a day boundary', () => {
    const nextDayOpen = NSE_OPEN + 86_400
    const out = resampleBars([bar(NSE_OPEN, 1, 1, 1, 1), bar(nextDayOpen, 2, 2, 2, 2)], 180, 'NSE')
    expect(out.map((b) => b.time)).toEqual([NSE_OPEN, nextDayOpen])
  })

  it('leaves the input alone for a degenerate target', () => {
    const src = [bar(NSE_OPEN, 1, 1, 1, 1)]
    expect(resampleBars(src, 0, 'NSE')).toEqual(src)
    expect(resampleBars([], 180, 'NSE')).toEqual([])
  })
})

describe('mergedIntervalGroups', () => {
  it('keeps a native interval the derived ladder omits', () => {
    const merged = mergedIntervalGroups([
      { label: 'minutes', items: ['1m', '5m', '15m', '25m'] },
      { label: 'hours', items: ['1h'] },
      { label: 'days', items: ['D'] },
    ])
    const minutes = merged.find((g) => g.label === 'minutes')
    // 25m is Dhan-only and must survive; 3m/4m come from the ladder.
    expect(minutes?.items).toContain('25m')
    expect(minutes?.items).toContain('3m')
    expect(minutes?.items).toContain('4m')
  })

  it('sorts each group by duration', () => {
    const merged = mergedIntervalGroups([{ label: 'minutes', items: ['25m'] }])
    const minutes = merged.find((g) => g.label === 'minutes')?.items ?? []
    const idx = (t: string) => minutes.indexOf(t)
    expect(idx('1m')).toBeLessThan(idx('3m'))
    expect(idx('15m')).toBeLessThan(idx('25m'))
    expect(idx('25m')).toBeLessThan(idx('30m'))
  })

  it('passes broker-only groups such as seconds straight through', () => {
    const merged = mergedIntervalGroups([
      { label: 'seconds', items: ['5s', '30s'] },
      { label: 'minutes', items: ['1m'] },
    ])
    expect(merged.find((g) => g.label === 'seconds')?.items).toEqual(['5s', '30s'])
  })
})
