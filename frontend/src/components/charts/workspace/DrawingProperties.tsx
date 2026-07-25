/**
 * Floating properties bar for the selected drawing.
 *
 * Appears only while something is selected, docked just under the toolbar so it
 * never lands on the price action, and closes with the selection. Colour,
 * opacity, thickness and line style are the four things a trader restyles
 * constantly, so they are direct; text and the rest sit behind one more click.
 */

import { useState } from 'react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  DRAWING_SWATCHES,
  type Drawing,
  type DrawingStyle,
  type PositionStyle,
} from '@/lib/charts/drawing'
import { cn } from '@/lib/utils'
import { Icon } from './icons'
import { Eyebrow, IBtn, VDivider } from './primitives'

const WIDTHS = [1, 2, 3, 4]
const STYLES: { value: NonNullable<DrawingStyle['lineStyle']>; label: string; dash: string }[] = [
  { value: 'solid', label: 'Solid', dash: '' },
  { value: 'dashed', label: 'Dashed', dash: '6 4' },
  { value: 'dotted', label: 'Dotted', dash: '1.5 3' },
]

export interface DrawingPropertiesProps {
  drawing: Drawing
  /** Tools that render a `style.text` label (shapes) or are text themselves. */
  supportsText: boolean
  /** Long / short position tools, which size a trade from a risk budget. */
  isPosition: boolean
  /** The loaded instrument, so the sizing panel can explain what it is using. */
  instrument: { symbol: string; lotSize: number; freezeQty: number; lots: boolean } | null
  onPositionStyle(patch: PositionStyle): void
  onStyle(patch: DrawingStyle): void
  onToggleLock(): void
  onDuplicate(): void
  onDelete(): void
  onClose(): void
}

export function DrawingProperties(p: DrawingPropertiesProps) {
  const style = p.drawing.style
  const color = style.color ?? '#2962ff'
  const opacity = style.fillOpacity ?? 0.12

  return (
    <div
      className={cn(
        'pointer-events-auto absolute left-1/2 top-2 z-30 flex -translate-x-1/2 items-center gap-1',
        'rounded-lg border border-border bg-popover/95 px-1.5 py-1 shadow-lg backdrop-blur'
      )}
      role="toolbar"
      aria-label="Drawing properties"
    >
      {/* Colour + opacity */}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            title="Colour and opacity"
            className="grid h-7 w-7 place-items-center rounded-md hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span
              className="h-4 w-4 rounded-[4px] border border-black/25"
              style={{ backgroundColor: color }}
            />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[248px] space-y-3 p-3">
          <div className="space-y-1.5">
            <Eyebrow>Colour</Eyebrow>
            <div className="grid grid-cols-10 gap-1">
              {DRAWING_SWATCHES.map((sw) => (
                <button
                  key={sw}
                  type="button"
                  title={sw}
                  onClick={() => p.onStyle({ color: sw, fillColor: sw })}
                  style={{ backgroundColor: sw }}
                  className={cn(
                    'aspect-square w-full rounded-[3px] border border-black/25 transition-transform hover:scale-110',
                    color.toLowerCase() === sw &&
                      'outline outline-2 outline-primary outline-offset-1'
                  )}
                />
              ))}
            </div>
          </div>
          <label className="block space-y-1.5">
            <Eyebrow>Fill opacity</Eyebrow>
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0}
                max={1}
                step={0.02}
                value={opacity}
                onChange={(e) => p.onStyle({ fillOpacity: Number(e.target.value) })}
                className="h-1 flex-1 accent-primary"
              />
              <span className="w-9 text-right text-[11px] tabular-nums text-muted-foreground">
                {Math.round(opacity * 100)}%
              </span>
            </div>
          </label>
        </PopoverContent>
      </Popover>

      {/* Thickness */}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            title="Thickness"
            className="grid h-7 w-7 place-items-center rounded-md hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4" aria-hidden="true">
              <title>Thickness</title>
              <path d="M3 6h14" stroke="currentColor" strokeWidth={1} strokeLinecap="round" />
              <path d="M3 10h14" stroke="currentColor" strokeWidth={2} strokeLinecap="round" />
              <path d="M3 14.5h14" stroke="currentColor" strokeWidth={3.4} strokeLinecap="round" />
            </svg>
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-40 space-y-1 p-1.5">
          {WIDTHS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => p.onStyle({ lineWidth: w })}
              className={cn(
                'flex h-8 w-full items-center gap-3 rounded-md px-2 hover:bg-accent',
                (style.lineWidth ?? 2) === w && 'bg-primary/12'
              )}
            >
              <svg viewBox="0 0 60 12" className="h-3 flex-1" aria-hidden="true">
                <title>{`${w} pixel`}</title>
                <path d="M2 6h56" stroke="currentColor" strokeWidth={w} strokeLinecap="round" />
              </svg>
              <span className="w-6 text-right text-[11px] tabular-nums text-muted-foreground">
                {w}px
              </span>
            </button>
          ))}
        </PopoverContent>
      </Popover>

      {/* Line style */}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            title="Line style"
            className="grid h-7 w-7 place-items-center rounded-md hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4" aria-hidden="true">
              <title>Line style</title>
              <path
                d="M3 10h14"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeDasharray="5 3.5"
              />
            </svg>
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-40 space-y-1 p-1.5">
          {STYLES.map((s) => (
            <button
              key={s.value}
              type="button"
              onClick={() => p.onStyle({ lineStyle: s.value })}
              className={cn(
                'flex h-8 w-full items-center gap-3 rounded-md px-2 hover:bg-accent',
                (style.lineStyle ?? 'solid') === s.value && 'bg-primary/12'
              )}
            >
              <svg viewBox="0 0 60 12" className="h-3 flex-1" aria-hidden="true">
                <title>{s.label}</title>
                <path
                  d="M2 6h56"
                  stroke="currentColor"
                  strokeWidth={1.8}
                  strokeLinecap="round"
                  strokeDasharray={s.dash || undefined}
                />
              </svg>
              <span className="text-[11px] text-muted-foreground">{s.label}</span>
            </button>
          ))}
        </PopoverContent>
      </Popover>

      {p.supportsText && (
        <>
          <VDivider />
          <TextPopover style={style} onStyle={p.onStyle} />
        </>
      )}

      {p.isPosition && (
        <>
          <VDivider />
          <PositionPopover
            style={style}
            instrument={p.instrument}
            onStyle={(patch) => p.onPositionStyle(patch)}
          />
        </>
      )}

      <VDivider />

      <IBtn
        onClick={p.onToggleLock}
        active={p.drawing.locked}
        title={p.drawing.locked ? 'Unlock' : 'Lock'}
      >
        <Icon name={p.drawing.locked ? 'lock' : 'unlock'} className="h-4 w-4" />
      </IBtn>
      <IBtn onClick={p.onDuplicate} title="Duplicate">
        <Icon name="clone" className="h-4 w-4" />
      </IBtn>
      <IBtn
        onClick={p.onDelete}
        title="Delete (Del)"
        className="hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
      >
        <Icon name="trash" className="h-4 w-4" />
      </IBtn>

      <VDivider />

      <IBtn onClick={p.onClose} title="Deselect (Esc)">
        <Icon name="close" className="h-4 w-4" />
      </IBtn>
    </div>
  )
}

/**
 * Position-tool sizing.
 *
 * Two inputs are the trader's — the capital base and how much of it a single
 * trade may lose. Everything else comes from the instrument and is shown read
 * only, because a lot size is the exchange's decision, not a preference.
 */
function PositionPopover({
  style,
  instrument,
  onStyle,
}: {
  style: DrawingStyle
  instrument: { symbol: string; lotSize: number; freezeQty: number; lots: boolean } | null
  onStyle(patch: PositionStyle): void
}) {
  const account = style.accountSize ?? 100_000
  const risk = style.risk ?? 1
  const budget = (account * risk) / 100
  const lotSize = style.lotSize ?? 1

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          title="Position sizing"
          className="grid h-7 w-7 place-items-center rounded-md hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon name="position" className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[268px] space-y-3 p-3">
        <div className="space-y-1.5">
          <Eyebrow>Risk budget</Eyebrow>
          <label className="grid grid-cols-[1fr_auto] items-center gap-3">
            <span className="text-[12px]">Capital</span>
            <input
              type="number"
              min={0}
              step={10_000}
              value={account}
              onChange={(e) => onStyle({ accountSize: Number(e.target.value) })}
              className="h-7 w-[110px] rounded-md border border-border bg-background px-2 text-right text-[12px] tabular-nums outline-none focus:border-primary/60"
            />
          </label>
          <label className="grid grid-cols-[1fr_auto] items-center gap-3">
            <span className="text-[12px]">Risk per trade</span>
            <span className="flex items-center gap-1">
              <input
                type="number"
                min={0.1}
                max={100}
                step={0.1}
                value={risk}
                onChange={(e) => onStyle({ risk: Number(e.target.value) })}
                className="h-7 w-[74px] rounded-md border border-border bg-background px-2 text-right text-[12px] tabular-nums outline-none focus:border-primary/60"
              />
              <span className="text-[12px] text-muted-foreground">%</span>
            </span>
          </label>
          <p className="text-[11px] tabular-nums text-muted-foreground">
            {budget > 0
              ? `Stops out for ₹${Math.round(budget).toLocaleString('en-IN')}`
              : 'Set a capital base to size the trade'}
          </p>
        </div>

        <div className="space-y-1.5 border-t border-border pt-2.5">
          <Eyebrow>Instrument</Eyebrow>
          {instrument ? (
            <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11.5px] tabular-nums">
              <dt className="text-muted-foreground">Symbol</dt>
              <dd className="truncate text-right">{instrument.symbol}</dd>
              <dt className="text-muted-foreground">Lot size</dt>
              <dd className="text-right">{lotSize > 1 ? lotSize : '1 (cash)'}</dd>
              {instrument.freezeQty > 1 && (
                <>
                  <dt className="text-muted-foreground">Order cap</dt>
                  <dd className="text-right">{instrument.freezeQty}</dd>
                </>
              )}
            </dl>
          ) : (
            <p className="text-[11.5px] text-muted-foreground">No symbol loaded.</p>
          )}
          <p className="text-[10.5px] leading-snug text-muted-foreground">
            {lotSize > 1
              ? 'Derivatives trade in whole lots, so the size rounds down to a multiple of the lot and is capped by the exchange freeze limit.'
              : 'Cash trades in single units, so the size is a share count.'}
          </p>
        </div>

        <label className="flex items-center gap-2 border-t border-border pt-2.5 text-[12px]">
          <input
            type="checkbox"
            checked={style.showLabels !== false}
            onChange={(e) => onStyle({ showLabels: e.target.checked } as PositionStyle)}
            className="h-3.5 w-3.5 accent-primary"
          />
          Show the readout on the chart
        </label>
      </PopoverContent>
    </Popover>
  )
}

function TextPopover({
  style,
  onStyle,
}: {
  style: DrawingStyle
  onStyle(patch: DrawingStyle): void
}) {
  const [text, setText] = useState(style.text ?? '')
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          title="Label"
          className="grid h-7 w-7 place-items-center rounded-md hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon name="text" className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[260px] space-y-3 p-3">
        <label className="block space-y-1.5">
          <Eyebrow>Label</Eyebrow>
          <textarea
            value={text}
            rows={2}
            placeholder="Supply zone"
            onChange={(e) => setText(e.target.value)}
            onBlur={() => onStyle({ text })}
            className="w-full resize-y rounded-md border border-border bg-background p-2 text-[12px] outline-none focus:border-primary/60"
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1.5">
            <Eyebrow>Size</Eyebrow>
            <input
              type="number"
              min={8}
              max={48}
              value={style.fontSize ?? 13}
              onChange={(e) => onStyle({ fontSize: Number(e.target.value) })}
              className="h-7 w-full rounded-md border border-border bg-background px-2 text-[12px] tabular-nums outline-none focus:border-primary/60"
            />
          </label>
          <label className="space-y-1.5">
            <Eyebrow>Text colour</Eyebrow>
            <input
              type="color"
              value={style.fontColor ?? style.color ?? '#ffffff'}
              onChange={(e) => onStyle({ fontColor: e.target.value })}
              className="h-7 w-full cursor-pointer rounded-md border border-border bg-background p-0.5"
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1.5">
            <Eyebrow>Align</Eyebrow>
            <select
              value={style.textAlign ?? 'center'}
              onChange={(e) =>
                onStyle({ textAlign: e.target.value as NonNullable<DrawingStyle['textAlign']> })
              }
              className="h-7 w-full rounded-md border border-border bg-background px-1.5 text-[12px] outline-none focus:border-primary/60"
            >
              <option value="left">Left</option>
              <option value="center">Center</option>
              <option value="right">Right</option>
            </select>
          </label>
          <label className="space-y-1.5">
            <Eyebrow>Position</Eyebrow>
            <select
              value={style.textVAlign ?? 'top'}
              onChange={(e) =>
                onStyle({ textVAlign: e.target.value as NonNullable<DrawingStyle['textVAlign']> })
              }
              className="h-7 w-full rounded-md border border-border bg-background px-1.5 text-[12px] outline-none focus:border-primary/60"
            >
              <option value="top">Top</option>
              <option value="middle">Middle</option>
              <option value="bottom">Bottom</option>
            </select>
          </label>
        </div>
        <label className="flex items-center gap-2 text-[12px]">
          <input
            type="checkbox"
            checked={style.textPosition === 'outside'}
            onChange={(e) => onStyle({ textPosition: e.target.checked ? 'outside' : 'inside' })}
            className="h-3.5 w-3.5 accent-primary"
          />
          Place the label outside the shape
        </label>
      </PopoverContent>
    </Popover>
  )
}
