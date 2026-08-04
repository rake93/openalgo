/**
 * GEX Levels numeric dashboard (chart-workspace GEX Levels study, Task 13).
 *
 * The chart draws Call Wall, Put Wall and Zero-Gamma as lines on the price
 * axis plus a per-strike gamma column; this panel is the numbers behind those
 * lines. Nothing here is computed — every value is read straight from the
 * `/gex/api/gex-levels` response, matching {@link InspectorPanel}'s role of
 * reading rather than recalculating.
 */

import type { GEXLevelsResponse } from '@/api/gex'
import { cn } from '@/lib/utils'

export interface GexDashboardProps {
  data: GEXLevelsResponse | null
  /** The newest refresh failed; what is shown is the previous snapshot. */
  stale: boolean
}

const GREEN = 'text-emerald-600 dark:text-emerald-400'
const RED = 'text-red-600 dark:text-red-400'
const AMBER = 'text-amber-600 dark:text-amber-400'

/**
 * GEX is quoted in crore. Indian short form: >= 1 crore as "X.XX Cr", >= 1
 * lakh as "X.XX L", otherwise a plain rounded number. Sign is kept on
 * negatives; a missing or non-finite value is an em dash, never a bare "0" or
 * "NaN" that could be mistaken for a real reading of zero.
 */
function formatMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const sign = v < 0 ? '-' : ''
  const abs = Math.abs(v)
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)} Cr`
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)} L`
  return `${sign}${Math.round(abs).toLocaleString('en-IN')}`
}

/** Strike-price levels (Call Wall, Put Wall, Zero-Gamma) — not crore-scale money. */
function formatPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return v.toLocaleString('en-IN', { maximumFractionDigits: 2 })
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

export function GexDashboard({ data, stale }: GexDashboardProps) {
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
      <div className="border-b border-border px-2.5 py-1.5">
        <span className="font-medium text-foreground">
          GEX Levels{data.underlying ? ` · ${data.underlying}` : ''}
        </span>
      </div>

      {stale && (
        <p className={cn('border-b border-border px-2.5 py-1.5 leading-snug', AMBER)}>
          Last refresh failed — showing the previous snapshot.
        </p>
      )}

      <dl className="grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5 px-2.5 py-2 tabular-nums">
        <Row label="Call GEX" value={formatMoney(data.total_call_gex)} tone={GREEN} />
        <Row label="Put GEX" value={formatMoney(data.total_put_gex)} tone={RED} />
        <Row label="Net GEX" value={formatMoney(data.net_gex)} tone={regimeTone} emphasis />
        <Row label="Regime" value={regimeLabel} tone={regimeTone} emphasis />
        {sentiment && (
          <Row
            label="Sentiment"
            value={`${sentimentLabel} ${sentiment.agreeing}/${sentiment.participating}`}
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
