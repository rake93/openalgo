/**
 * GEX Levels numeric dashboard (chart-workspace GEX Levels study, Task 13).
 *
 * The chart draws Call Wall, Put Wall and Zero-Gamma as lines on the price
 * axis, plus a per-strike bar column that reads either gamma or delta; this
 * panel is the numbers behind those lines. Every one of those numbers -
 * including Regime - is computed from gamma regardless of which metric the
 * bars currently show, which is why this card's Bars row and delta caveat
 * exist: they are the on-screen reminder of that split. Nothing here is
 * computed - every value is read straight from the `/gex/api/gex-levels`
 * response, matching {@link InspectorPanel}'s role of reading rather than
 * recalculating.
 */

import type { GEXLevelsResponse, GEXSentimentSignal, GexMetric } from '@/api/gex'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatGexMoney } from '@/lib/charts/gex-levels-primitive'
import { cn } from '@/lib/utils'

export interface GexDashboardProps {
  data: GEXLevelsResponse | null
  /** The newest refresh failed; what is shown is the previous snapshot. */
  stale: boolean
  /**
   * Which metric the chart's bar column currently reads. Every other number
   * in this card - Call/Put/Net GEX, Regime, the walls - is gamma regardless
   * of this setting, so it is shown and, under delta, called out: the Bars
   * row alone would let a reader assume the rest of the card follows the bar
   * column, which it never does.
   */
  metric: GexMetric
  /**
   * Dismiss the card. The study keeps running and its levels stay on the
   * chart - this hides the readout only, and the Studies panel switches it
   * back on. Omit to render no close control.
   */
  onHide?(): void
}

const GREEN = 'text-emerald-600 dark:text-emerald-400'
const RED = 'text-red-600 dark:text-red-400'
const AMBER = 'text-amber-600 dark:text-amber-400'

/** Strike-price levels (Call Wall, Put Wall, Zero-Gamma) — not crore-scale money. */
function formatPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return v.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

/** Same colour convention as the main rows: bullish green, bearish red,
 * neutral/unavailable muted - never a fourth colour for the reader to learn. */
function signalTone(bias: GEXSentimentSignal['bias']): string {
  if (bias === 'bullish') return GREEN
  if (bias === 'bearish') return RED
  return 'text-muted-foreground'
}

function Row({
  label,
  value,
  tone,
  emphasis,
}: {
  label: string
  value: React.ReactNode
  tone?: string
  emphasis?: boolean
}) {
  return (
    <div className="contents">
      <dt className="truncate text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          'truncate text-right tabular-nums',
          emphasis && 'text-[12.5px] font-semibold',
          tone
        )}
      >
        {value}
      </dd>
    </div>
  )
}

export function GexDashboard({ data, stale, metric, onHide }: GexDashboardProps) {
  if (!data || data.status !== 'success') return null

  const regime = data.regime
  /**
   * Suppressive / Amplifying — deliberately never Bullish / Bearish.
   *
   * Positive net gamma is not bullish, it is *stabilising*: dealers hedging
   * their long gamma sell into rallies and buy dips, so price pins. Negative
   * gamma amplifies moves in BOTH directions — calling it "bearish" would
   * read as a short signal at the exact moment a gamma-driven squeeze is
   * running upward. This is the single most likely thing a future
   * contributor "fixes" back to a directional word; don't.
   */
  const regimeLabel =
    regime === 'suppressive' ? 'Suppressive' : regime === 'amplifying' ? 'Amplifying' : '—'
  const regimeTone =
    regime === 'suppressive' ? GREEN : regime === 'amplifying' ? RED : 'text-muted-foreground'

  const sentiment = data.sentiment
  /**
   * Bullish / Bearish / Neutral — a genuinely directional read, separate from
   * Regime above. It is NOT derived from net GEX's sign (that would print
   * bearish during a gamma-driven squeeze upward); see
   * services/gex_levels/sentiment.py for the three signals it rests on.
   *
   * The count alongside the label — e.g. "Bullish 2/3" — is `agreeing` of
   * `participating`, so a one-signal verdict is visibly a one-signal verdict
   * and never mistaken for a unanimous one.
   */
  const sentimentLabel =
    sentiment?.bias === 'bullish'
      ? 'Bullish'
      : sentiment?.bias === 'bearish'
        ? 'Bearish'
        : sentiment
          ? 'Neutral'
          : undefined
  const sentimentTone =
    sentiment?.bias === 'bullish'
      ? GREEN
      : sentiment?.bias === 'bearish'
        ? RED
        : 'text-muted-foreground'

  const quality = data.quality
  const qualityTone = quality ? (quality.verdict === 'good' ? GREEN : AMBER) : undefined

  return (
    <aside className="pointer-events-none absolute right-2 top-2 z-20 w-[216px] rounded-md border border-border bg-popover/90 text-[11.5px] leading-snug shadow-lg backdrop-blur">
      <div className="flex items-center gap-2 border-b border-border px-2.5 py-1.5">
        <span className="min-w-0 flex-1 truncate font-medium text-foreground">
          GEX Levels{data.underlying ? ` · ${data.underlying}` : ''}
        </span>
        {onHide && (
          // The card is pointer-events-none so it never swallows a click meant
          // for the chart underneath. The close control is the one part that
          // has to take clicks, so it opts back in.
          <button
            type="button"
            onClick={onHide}
            aria-label="Hide the GEX Levels card"
            title="Hide - the levels stay on the chart"
            className="pointer-events-auto -mr-1 grid h-4 w-4 shrink-0 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <svg viewBox="0 0 16 16" className="h-3 w-3" aria-hidden="true">
              <path
                d="M4 4l8 8M12 4l-8 8"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                fill="none"
              />
            </svg>
          </button>
        )}
      </div>

      {stale && (
        <p className={cn('border-b border-border px-2.5 py-1.5 leading-snug', AMBER)}>
          Last refresh failed — showing the previous snapshot.
        </p>
      )}

      {metric === 'delta' && (
        <p className={cn('border-b border-border px-2.5 py-1.5 leading-snug', AMBER)}>
          Bars show DEX, the open-interest book. Walls, Zero-Gamma and Regime below stay gamma;
          dealer delta is the opposite sign.
        </p>
      )}

      <dl className="grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5 px-2.5 py-2 tabular-nums">
        <Row label="Bars" value={metric === 'delta' ? 'Delta (DEX)' : 'Gamma (GEX)'} />
        <Row label="Call GEX" value={formatGexMoney(data.total_call_gex)} tone={GREEN} />
        <Row label="Put GEX" value={formatGexMoney(data.total_put_gex)} tone={RED} />
        <Row label="Net GEX" value={formatGexMoney(data.net_gex)} tone={regimeTone} emphasis />
        <Row label="Regime" value={regimeLabel} tone={regimeTone} emphasis />
        {sentiment && (
          <Row
            label="Sentiment"
            value={
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* The card is pointer-events-none (see the close button above) so
                   * this trigger opts back in, the same way, or hover never fires.
                   * The tone class is repeated here (Row's dd carries it too) because
                   * this span, not the dd, is now the text-bearing leaf node. */}
                  <span className={cn('pointer-events-auto cursor-help', sentimentTone)}>
                    {sentimentLabel} {sentiment.agreeing}/{sentiment.participating}
                  </span>
                </TooltipTrigger>
                <TooltipContent
                  side="left"
                  className="max-w-[280px] whitespace-normal px-3 py-2 text-left text-[11.5px] leading-snug"
                >
                  <p className="font-medium">Sentiment · {sentimentLabel}</p>
                  <p className="text-background/70">
                    score {sentiment.score >= 0 ? '+' : ''}
                    {sentiment.score.toFixed(2)} · {sentiment.agreeing} of {sentiment.participating}{' '}
                    agree
                  </p>
                  {sentiment.signals.map((s) => (
                    <div key={s.key} className="mt-2">
                      <p>
                        <span className="font-medium">{s.label}</span>
                        {' · '}
                        <span className={signalTone(s.bias)}>{s.bias}</span>
                        {` · weight ${s.weight}`}
                      </p>
                      <p>{s.detail}</p>
                      <p className="text-background/70">{s.why}</p>
                    </div>
                  ))}
                  <p className="mt-2 text-background/70">
                    The count is how many signals agree with the verdict. A Neutral with a low count
                    means signals cancelled rather than all reading flat.
                  </p>
                </TooltipContent>
              </Tooltip>
            }
            tone={sentimentTone}
            emphasis
          />
        )}
        <Row label="Call Wall" value={formatPrice(data.call_wall)} tone={GREEN} />
        <Row label="Put Wall" value={formatPrice(data.put_wall)} tone={RED} />
        <Row
          label="Zero-Gamma"
          value={data.zero_gamma == null ? 'No local cross' : formatPrice(data.zero_gamma)}
          tone={AMBER}
        />
        <Row label="Expiry" value={data.expiry_date ?? '—'} />
        <Row
          label="Data status"
          value={quality ? `${quality.strikes_priced} of ${quality.strikes_used} strikes` : '—'}
          tone={qualityTone}
        />
      </dl>

      {quality && quality.notes.length > 0 && (
        <div className="border-t border-border px-2.5 py-1.5 text-muted-foreground">
          {quality.notes.map((note) => (
            <p key={note} className="leading-snug">
              {note}
            </p>
          ))}
        </div>
      )}
    </aside>
  )
}
