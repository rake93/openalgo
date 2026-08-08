/**
 * Performance profile panel (M8, roadmap Phase 2 §13.3).
 *
 * Answers one question: **is the incremental runtime actually working for this
 * indicator, or has it silently reverted to full recompute?**
 *
 * Every number here is telemetry the engine already emitted and the platform
 * used to discard. Nothing is measured, derived or aggregated on this side — the
 * last run answers the question completely, so there is no rolling window.
 *
 * See openalgo-openscript/docs/openscript-phase2-performance-profile-design.md.
 */

import { BUILD_STAMP, formatBuildStamp } from '@openalgo/openscript'
import type { IndicatorInstance } from '@/lib/charts/indicator-host'
import { fallbackLabel, isSilentFallback, type IndicatorProfile } from '@/lib/charts/indicator-profile'

const ms = (n: number | undefined): string => (n === undefined ? '—' : `${n.toFixed(2)} ms`)

const bytes = (n: number | undefined): string => {
  // Absent means NOT MEASURED (an unbudgeted run), which is not the same as zero.
  if (n === undefined) return 'not measured'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function Row({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 tabular-nums">
      <span className="text-muted-foreground">{label}</span>
      <span className={dim ? 'text-muted-foreground' : ''}>{value}</span>
    </div>
  )
}

function Entry({ name, profile }: { name: string; profile: IndicatorProfile | undefined }) {
  if (!profile) {
    return (
      <div className="border-t border-border/60 py-2 first:border-t-0">
        <div className="font-medium">{name}</div>
        <div className="text-muted-foreground">has not run yet</div>
      </div>
    )
  }

  const { perf } = profile
  const flagged = isSilentFallback(profile)
  const dirty =
    perf.dirtyFrom === undefined || perf.dirtyTo === undefined
      ? '—'
      : perf.dirtyFrom === perf.dirtyTo
        ? `bar ${perf.dirtyFrom}`
        : `bars ${perf.dirtyFrom}–${perf.dirtyTo}`

  return (
    <div className="border-t border-border/60 py-2 first:border-t-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate font-medium">{name}</span>
        <span className={flagged ? 'shrink-0 text-destructive' : 'shrink-0 text-muted-foreground'}>
          {perf.recompute}
          {flagged ? ' ⚠' : ''}
        </span>
      </div>

      {flagged && <div className="mt-0.5 text-[11px] text-destructive">{fallbackLabel(profile)}</div>}

      {/* A full run that is NOT flagged is expected — a seed, a settings change,
          a builtin, or a structural lookahead operator — and saying WHICH stops
          it reading as a problem. The lookahead case keeps its engine-given
          reason (M14a's deal: the ⚠ is withheld, the fact is not) — the generic
          seed/settings text would be false for a live tick. */}
      {!flagged && perf.recompute === 'full' && (
        <div className="mt-0.5 text-[11px] text-muted-foreground">
          {perf.fallbackReason === 'builtin-no-ir'
            ? 'built-in indicator — no script graph to run incrementally'
            : perf.fallbackReason?.startsWith('lookahead:')
              ? fallbackLabel(profile)
              : 'expected: a seed, settings change or history reload'}
        </div>
      )}

      <div className="mt-1 space-y-0.5">
        {perf.recompute !== 'full' && (
          <>
            <Row label="dirty" value={dirty} />
            <Row
              label="nodes"
              value={perf.dirtyNodeCount === undefined ? '—' : String(perf.dirtyNodeCount)}
            />
          </>
        )}
        <Row label="compute" value={ms(perf.computeMs)} />
        <Row label="emit" value={ms(perf.emitMs)} />
        <Row label="peak bytes" value={bytes(perf.peakBytes)} dim={perf.peakBytes === undefined} />
        <Row label="bars" value={String(perf.bars)} />
        {/* M2: structural drawing churn, present only on a run that changed an
            object list — absence means "no change", so no dimmed placeholder. */}
        {profile.drawings && (
          <Row
            label="drawings"
            value={`+${profile.drawings.added} ~${profile.drawings.updated} −${profile.drawings.removed}`}
          />
        )}
      </div>
    </div>
  )
}

export function ProfilePanel({
  indicators,
  profileOf,
  onClose,
}: {
  indicators: IndicatorInstance[]
  profileOf: (instanceId: string) => IndicatorProfile | undefined
  onClose: () => void
}) {
  return (
    <div className="absolute bottom-2 right-2 z-30 flex max-h-[calc(100%-1rem)] w-72 flex-col rounded-md border border-border bg-card/95 text-xs shadow-lg backdrop-blur">
      <div className="flex items-center justify-between gap-2 border-b border-border px-2.5 py-1.5">
        <span className="font-medium">Profile · last run</span>
        <button
          type="button"
          onClick={onClose}
          className="rounded px-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Close profile"
        >
          ✕
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-2.5 py-1">
        {indicators.length === 0 ? (
          <div className="py-2 text-muted-foreground">No indicators on this chart.</div>
        ) : (
          indicators.map((inst) => (
            <Entry key={inst.instanceId} name={inst.name} profile={profileOf(inst.instanceId)} />
          ))
        )}
      </div>

      <BuildStampRow />
    </div>
  )
}

/**
 * Which engine build this page is running (M6 piece B, trap T1).
 *
 * A PANEL-LEVEL fact, not a per-indicator one: there is one bundled engine per
 * page, so it sits in the footer rather than inside each `Entry` — which also
 * means it still shows when no indicator has been added yet.
 *
 * It does NOT detect staleness and must not imply that it does. The frontend
 * bundles the engine at build time, so a bundle cannot know what the repo looks
 * like now; only the server can do the live recompute that makes the freshness
 * endpoint exact. What this answers is "which engine produced what I am looking
 * at", which is the question the 2026-07-30 session could not answer for ~14
 * hours while the editor and the chart quietly disagreed.
 *
 * `isDevBuild` is rendered distinctly on purpose. A dev bundle showing something
 * that reads like a real build identity would be believed, and a stamp that can
 * lie about which code you are running is worse than no stamp at all.
 */
function BuildStampRow() {
  const title = BUILD_STAMP.isDevBuild
    ? 'Running the engine from source — no build identity was injected.'
    : `engine source ${BUILD_STAMP.fingerprint} · built ${BUILD_STAMP.builtAt}\n` +
      'Identifies this bundle. It cannot detect staleness — rebuild frontend/dist after an engine change.'

  return (
    <div
      className="flex items-baseline justify-between gap-3 border-t border-border px-2.5 py-1 tabular-nums"
      title={title}
    >
      <span className="text-muted-foreground">engine build</span>
      <span className={BUILD_STAMP.isDevBuild ? 'text-amber-500' : 'text-muted-foreground'}>
        {formatBuildStamp()}
      </span>
    </div>
  )
}
