/**
 * Market direction readout.
 *
 * Presentation only: it renders a {@link DirectionVerdict} and computes nothing.
 * Exact signals are grouped above the reconstructed ones under their caveat, so a
 * reader can see at a glance which half of the verdict is measured and which half
 * is inferred from a snapshot feed.
 *
 * Bias is carried by the wording and an arrow as well as by colour, never colour
 * alone — the same reason the rest of the workspace labels its up/down states.
 */

import type { Bias, DirectionSignal, DirectionVerdict } from '@/lib/charts/direction'
import { cn } from '@/lib/utils'
import { Eyebrow } from './primitives'

export interface DirectionPanelProps {
  verdict: DirectionVerdict
  symbol: string
  interval: string
  /** A charted put rises when its underlying falls; the panel says so. */
  isPut?: boolean
  /** False when the OI baseline had to fall back to the first live observation. */
  oiFromSession?: boolean
}

const TONE: Record<Bias, string> = {
  bullish: 'text-emerald-600 dark:text-emerald-400',
  bearish: 'text-red-600 dark:text-red-400',
  neutral: 'text-muted-foreground',
  unavailable: 'text-muted-foreground/50',
}

const ARROW: Record<Bias, string> = {
  bullish: '↑',
  bearish: '↓',
  neutral: '→',
  unavailable: '',
}

const WORD: Record<DirectionVerdict['composite'], string> = {
  bullish: 'BULLISH',
  bearish: 'BEARISH',
  neutral: 'NEUTRAL',
}

function Row({ s }: { s: DirectionSignal }) {
  return (
    <div className="flex items-baseline justify-between gap-2 py-[3px]">
      <dt className="shrink-0 text-[11.5px] text-muted-foreground">{s.label}</dt>
      <dd className={cn('truncate text-right text-[11.5px] tabular-nums', TONE[s.bias])}>
        {s.detail}
        {s.bias !== 'unavailable' && <span className="ml-1">{ARROW[s.bias]}</span>}
      </dd>
    </div>
  )
}

export function DirectionPanel(p: DirectionPanelProps) {
  const { verdict: v } = p
  const exact = v.signals.filter((s) => s.exact)
  const inferred = v.signals.filter((s) => !s.exact)
  // Score is normalised to the weight that participated, so it is a share.
  const strength = Math.round(Math.abs(v.score) * 100)

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <div className="border-b border-border px-3 py-2">
        <Eyebrow className="mb-1.5">
          {p.symbol} {p.interval}
        </Eyebrow>
        <div className="flex items-baseline justify-between gap-2">
          <span className={cn('text-base font-semibold tracking-tight', TONE[v.composite])}>
            {WORD[v.composite]}
          </span>
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {v.participating === 0
              ? 'no data'
              : `${v.agreeing} of ${v.participating} · ${strength}%`}
          </span>
        </div>
        {v.participating === 0 && (
          <p className="mt-1.5 text-[11px] leading-snug text-muted-foreground">
            Nothing to read yet. The exact signals arrive on the live depth feed, and delta needs
            the Order flow study switched on.
          </p>
        )}
      </div>

      <div className="px-3 py-2">
        <Eyebrow className="mb-1">Exact</Eyebrow>
        <dl>
          {exact.map((s) => (
            <Row key={s.key} s={s} />
          ))}
        </dl>
      </div>

      <div className="border-t border-border px-3 py-2">
        <Eyebrow className="mb-1">Inferred</Eyebrow>
        <p className="mb-1.5 text-[11px] leading-snug text-muted-foreground">
          Reconstructed with the quote rule at depth-packet granularity — OpenAlgo's feed carries no
          trade-by-trade tape. Counts half as much as an exact signal.
        </p>
        <dl>
          {inferred.map((s) => (
            <Row key={s.key} s={s} />
          ))}
        </dl>
      </div>

      {p.isPut && (
        <p className="mx-3 mb-2 rounded-md border border-border bg-muted/40 px-2.5 py-2 text-[11px] leading-snug text-muted-foreground">
          This is a put, so the bias describes the option's own premium. A bullish reading here
          means the underlying is falling.
        </p>
      )}

      <p className="mt-auto shrink-0 border-t border-border px-3 py-2 text-[11px] leading-snug text-muted-foreground">
        {p.oiFromSession === false
          ? 'This feed reports no historical open interest, so OI buildup is measured from the first tick seen after connecting rather than the previous close.'
          : "OI buildup is measured against the previous session's close, as the exchange's own change-in-OI figures are."}
      </p>
    </div>
  )
}
