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

import { useCallback, useRef } from 'react'
import type { GEXLevelsResponse, GEXSentimentSignal, GexMetric } from '@/api/gex'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatGexMoney } from '@/lib/charts/gex-levels-geometry'
import { cn } from '@/lib/utils'

export interface GexCardOffset {
  x: number
  y: number
}

/** Card inset from the pane's top-right corner, matching `right-2 top-2`. */
const CARD_INSET_PX = 8

/**
 * Keep a dragged card inside its pane.
 *
 * The card is anchored top-right, so its natural left edge is
 * `container.width - inset - card.width` and its natural top is `inset`; the
 * offset translates from there. Clamping to "fully inside" rather than
 * "partly visible" is deliberate: a card that can be dragged half off the
 * edge looks broken rather than moved, and the header - the only drag
 * surface - would be the part that leaves first, stranding it.
 *
 * Pure and exported so the arithmetic is testable without a DOM.
 */
export function clampGexCardOffset(
  offset: GexCardOffset,
  card: { width: number; height: number },
  container: { width: number; height: number }
): GexCardOffset {
  const minX = -Math.max(0, container.width - CARD_INSET_PX - card.width)
  const maxX = CARD_INSET_PX
  const minY = -CARD_INSET_PX
  const maxY = Math.max(minY, container.height - CARD_INSET_PX - card.height)
  return {
    x: Math.min(maxX, Math.max(minX, offset.x)),
    y: Math.min(maxY, Math.max(minY, offset.y)),
  }
}

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
  /**
   * Where the card sits relative to its default top-right anchor. Zero means
   * the anchor itself, so an existing layout renders exactly as before.
   */
  offset?: GexCardOffset
  /**
   * Called as the header is dragged, and on a double-click of the header with
   * `{x: 0, y: 0}` to reset. Omit to make the card immovable.
   */
  onOffsetChange?(offset: GexCardOffset): void
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

export function GexDashboard({
  data,
  stale,
  metric,
  onHide,
  offset,
  onOffsetChange,
}: GexDashboardProps) {
  const cardRef = useRef<HTMLElement | null>(null)
  // Where the pointer grabbed, relative to the offset at that moment, so the
  // card tracks the cursor instead of jumping its corner to it.
  const grabRef = useRef<{ pointerX: number; pointerY: number; from: GexCardOffset } | null>(null)

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!onOffsetChange || e.button !== 0) return
      const grab = {
        pointerX: e.clientX,
        pointerY: e.clientY,
        from: offset ?? { x: 0, y: 0 },
      }
      grabRef.current = grab

      // Listen on window rather than relying on setPointerCapture. Capture is
      // the tidier API but it does not hold for every synthesised pointer, and
      // when it slips the moves are delivered to whatever is under the cursor -
      // the chart - so the card silently stops following. Window listeners
      // track the drag wherever the cursor goes, which is what a drag means.
      const move = (ev: PointerEvent) => {
        const card = cardRef.current
        const container = card?.offsetParent as HTMLElement | null
        const next = {
          x: grab.from.x + (ev.clientX - grab.pointerX),
          y: grab.from.y + (ev.clientY - grab.pointerY),
        }
        onOffsetChange(
          card && container
            ? clampGexCardOffset(
                next,
                { width: card.offsetWidth, height: card.offsetHeight },
                { width: container.clientWidth, height: container.clientHeight }
              )
            : next
        )
      }
      const up = () => {
        grabRef.current = null
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', up)
        window.removeEventListener('pointercancel', up)
      }
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', up)
      window.addEventListener('pointercancel', up)

      // stopPropagation, not just preventDefault: this card renders INSIDE the
      // chart's own container, so without it the press bubbles to the chart's
      // pointer handler and pans the chart out from under the card while the
      // card also moves. The close button never needed this because a click
      // does not pan - only a press-and-drag does.
      e.stopPropagation()
      e.preventDefault()
    },
    [offset, onOffsetChange]
  )

  // The only recovery if a layout change strands the card somewhere useless.
  const onDoubleClick = useCallback(() => onOffsetChange?.({ x: 0, y: 0 }), [onOffsetChange])

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
    <aside
      ref={cardRef}
      style={
        offset && (offset.x !== 0 || offset.y !== 0)
          ? { transform: `translate(${offset.x}px, ${offset.y}px)` }
          : undefined
      }
      className="pointer-events-none absolute right-2 top-2 z-20 w-[216px] rounded-md border border-border bg-popover/90 text-[11.5px] leading-snug shadow-lg backdrop-blur"
    >
      {/* The header is the drag handle, and the only part besides the close
          button that takes pointer events - the body carries a tooltip trigger
          and numbers worth selecting, and making all of it draggable would
          break both. */}
      <div
        onPointerDownCapture={onPointerDown}
        onDoubleClick={onDoubleClick}
        className={cn(
          'flex items-center gap-2 border-b border-border px-2.5 py-1.5',
          onOffsetChange && 'pointer-events-auto cursor-grab active:cursor-grabbing'
        )}
      >
        <span
          className="min-w-0 flex-1 truncate font-medium text-foreground"
          title={onOffsetChange ? 'Drag to move, double-click to reset' : undefined}
        >
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
