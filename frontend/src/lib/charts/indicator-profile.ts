/**
 * Per-indicator run telemetry (M8, roadmap Phase 2 §13.3).
 *
 * The engine emits `PerfStats` on every single run and the platform used to drop
 * it entirely. This is the shape the host retains and the rule that decides when
 * a run is worth complaining about.
 *
 * See openalgo-openscript/docs/openscript-phase2-performance-profile-design.md.
 */

import type { PerfStats } from '@openalgo/openscript'

/** One indicator's last run. */
export interface IndicatorProfile {
  /** 'full' = a (re)creation, settings change or history reload; 'update' = a bar
   *  event. The distinction is what makes `isSilentFallback` possible. */
  scope: 'full' | 'update'
  perf: PerfStats
}

/**
 * Did this run lose the incremental path when it should have had it?
 *
 * `recompute: 'full'` is USUALLY CORRECT, and that is the whole difficulty:
 *
 * - a **registry builtin** has no IR by design (the eligibility rule in
 *   `worker-core`), so it is full on every run, forever;
 * - a session's **first run** is a seed, and `reset` is never incremental;
 * - **set-inputs / set-dataset** run full by construction.
 *
 * A badge that fired on all of those would mark every built-in permanently, fire
 * on every chart load, and fire on every settings change — it would be trained
 * away, and would then hide the real case. So the rule isolates exactly one
 * thing: a LIVE TICK that should have taken the dirty-range path and did not.
 *
 * `builtin-no-ir` is excluded BY NAME rather than by asking "is this a builtin?"
 * — the engine's reason is the authoritative statement of why the run went full,
 * and re-deriving it from the instance would be a second, weaker copy of the
 * eligibility rule, free to disagree with the engine.
 *
 * `lookahead:*` is excluded for the same shape of reason (register M14(a)): a
 * pivot reads `right` bars FORWARD, so the engine deliberately and PERMANENTLY
 * forces such an indicator onto the full path. The user cannot act on that —
 * only the engine can (M14(b)) — and a structural, permanent, user-unfixable ⚠
 * is the trained-away failure this predicate exists to prevent, one level up.
 * The row and its reason stay visible via `fallbackLabel`; only the alarm is
 * withheld. Note the class is `lookahead:` WITH an operator name — a bare
 * `unsupported` names no structural cause and stays flagged.
 *
 * An unnamed fallback IS flagged. A reason the engine did not give is still a
 * fallback, and defaulting to benign there would let an unnamed regression
 * through in silence — which is the exact failure this whole deliverable exists
 * to end.
 */
export function isSilentFallback(profile: IndicatorProfile): boolean {
  if (profile.scope !== 'update') return false
  if (profile.perf.recompute !== 'full') return false
  const reason = profile.perf.fallbackReason
  if (reason === 'builtin-no-ir') return false
  if (reason?.startsWith('lookahead:')) return false
  return true
}

/** Human-readable cause, for the panel and the badge tooltip. */
export function fallbackLabel(profile: IndicatorProfile): string {
  const reason = profile.perf.fallbackReason
  if (reason === undefined) return 'recomputed in full; the engine gave no reason'
  // `inputs-changed:<key>` names the offending input after the colon.
  const [head, key] = reason.split(':')
  if (head === 'inputs-changed') {
    return key
      ? `recomputed in full — an input changed alongside the tick (${key})`
      : 'recomputed in full — an input changed alongside the tick'
  }
  if (head === 'lookahead') return `recomputed in full — ${key ?? 'a lookahead operator'}`
  if (reason === 'unsupported') return 'recomputed in full — this script is not on the incremental path'
  if (reason === 'incremental-pending') return 'recomputed in full — this update shape is not served incrementally'
  if (reason === 'htf-not-incremental') return 'recomputed in full — the higher-timeframe resample was rebuilt'
  if (reason === 'analysis-error') return 'recomputed in full — the incremental analysis failed'
  return `recomputed in full — ${reason}`
}
