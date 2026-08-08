/**
 * The silent-fallback predicate (M8 §13.3).
 *
 * `recompute: 'full'` is USUALLY EXPECTED, which is what makes this dangerous. A
 * naive "warn when full" badge fires on every registry builtin, on every chart
 * load, and on every settings change — it would be trained away within a day and
 * would then hide the one case that matters.
 *
 * These tests exist to keep the predicate discriminating. Each benign case
 * asserts that the run really WAS full, so the test cannot pass merely because
 * nothing was full.
 *
 * See openalgo-openscript/docs/openscript-phase2-performance-profile-design.md.
 */

import { describe, expect, it } from 'vitest'
import type { PerfStats } from '@openalgo/openscript'
import { isSilentFallback, type IndicatorProfile } from './indicator-profile'

function profile(
  scope: 'full' | 'update',
  perf: Partial<PerfStats> & Pick<PerfStats, 'recompute'>
): IndicatorProfile {
  return { scope, perf: { computeMs: 0.1, bars: 100, ...perf } as PerfStats }
}

describe('isSilentFallback', () => {
  it('flags a live tick that fell back to full', () => {
    const p = profile('update', { recompute: 'full', fallbackReason: 'inputs-changed' })

    expect(p.perf.recompute).toBe('full') // non-vacuity: it really is full
    expect(isSilentFallback(p)).toBe(true)
  })

  it('does NOT flag a lookahead operator — structural, permanent, user-unfixable (M14a)', () => {
    // `ta.pivothigh` reads `right` bars FORWARD, so the engine deliberately
    // forces the whole indicator onto the full path on EVERY tick. The user
    // cannot act on that; only the engine can (register M14(b)). A permanent ⚠
    // is the same trained-away failure the predicate exists to prevent, one
    // level up. The row and its reason stay visible via fallbackLabel; only the
    // alarm is withheld.
    const p = profile('update', { recompute: 'full', fallbackReason: 'lookahead:ta.pivothigh' })

    expect(p.perf.recompute).toBe('full') // non-vacuity: it really is full
    expect(isSilentFallback(p)).toBe(false)
  })

  it('still flags a bare unsupported closure on a tick — unknown ops are not structural', () => {
    // Only `lookahead:*` is the expected-structural class. A reason that names
    // no operator class is exactly the "unnamed regression" the predicate must
    // keep loud.
    const p = profile('update', { recompute: 'full', fallbackReason: 'unsupported' })

    expect(isSilentFallback(p)).toBe(true)
  })

  it('does NOT flag a registry builtin — it has no IR by design', () => {
    const p = profile('update', { recompute: 'full', fallbackReason: 'builtin-no-ir' })

    // Non-vacuity: this run IS full, and must still not be flagged. A builtin is
    // full on every run forever; badging it would mark VWAP permanently.
    expect(p.perf.recompute).toBe('full')
    expect(isSilentFallback(p)).toBe(false)
  })

  it("does NOT flag a session's first run — a seed is never incremental", () => {
    const p = profile('full', { recompute: 'full', fallbackReason: 'incremental-pending' })

    expect(p.perf.recompute).toBe('full')
    expect(isSilentFallback(p)).toBe(false)
  })

  it('does NOT flag a settings change, which is full by construction', () => {
    const p = profile('full', { recompute: 'full' })

    expect(p.perf.recompute).toBe('full')
    expect(isSilentFallback(p)).toBe(false)
  })

  it('does NOT flag a healthy incremental tick', () => {
    const p = profile('update', { recompute: 'incremental' })

    expect(isSilentFallback(p)).toBe(false)
  })

  it('does NOT flag a tail recompute', () => {
    const p = profile('update', { recompute: 'tail' })

    expect(isSilentFallback(p)).toBe(false)
  })

  it('flags a full tick even with no reason given, rather than assuming it is fine', () => {
    // A fallback the engine did not name is still a fallback. Defaulting to
    // "benign" here would let an unnamed regression through silently.
    const p = profile('update', { recompute: 'full' })

    expect(isSilentFallback(p)).toBe(true)
  })
})
