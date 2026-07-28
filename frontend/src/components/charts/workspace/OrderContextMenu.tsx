/**
 * Right-click menu, anchored at the price under the cursor.
 *
 * Each order row states the whole order in one line — side, size, type and the
 * price it would use — because a right-click is one click away from a live order
 * and the label is the last thing the trader reads. Rows the exchange would
 * reject (a buy limit above the market, a buy stop below it) are shown disabled
 * with the reason rather than hidden, so the menu stays in one place.
 *
 * Below the orders sit the view actions, matching `/trading`: they are occasional
 * enough not to deserve permanent toolbar space, and the right-click is where a
 * trader already is when they want them.
 */

import { useEffect, useRef, useState } from 'react'
import type { CtxItem, OrderSide, OrderType } from '@/lib/charts/trading-layer'
import { cn } from '@/lib/utils'
import { Icon } from './icons'

const ROW =
  'flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-foreground/85 transition-colors hover:bg-accent hover:text-foreground'

export interface OrderContextMenuProps {
  x: number
  y: number
  price: string
  items: CtxItem[]
  hasOrders: boolean
  /** Drawing rail visibility, so the row can name the action it will perform. */
  railVisible: boolean
  grid: { vertLines: boolean; horzLines: boolean }
  onPick(side: OrderSide, type: OrderType): void
  onCancelAll(): void
  onResetScale(): void
  onToggleRail(): void
  onGrid(patch: { vertLines?: boolean; horzLines?: boolean }): void
  onClose(): void
}

export function OrderContextMenu(p: OrderContextMenuProps) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [gridSub, setGridSub] = useState(false)

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) p.onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') p.onClose()
    }
    window.addEventListener('pointerdown', onDown, true)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [p.onClose])

  // Keep the menu on screen when the click lands near an edge.
  const left = Math.min(p.x, window.innerWidth - 250)
  const top = Math.min(p.y, window.innerHeight - 300)

  return (
    <div
      ref={ref}
      role="menu"
      style={{ left, top }}
      className="fixed z-50 w-[236px] rounded-lg border border-border bg-popover/97 p-1 shadow-xl backdrop-blur"
    >
      <div className="px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
        At {p.price}
      </div>
      {p.items.map((item) => (
        <button
          key={`${item.side}-${item.type}`}
          type="button"
          role="menuitem"
          disabled={!item.enabled}
          title={
            item.enabled
              ? undefined
              : `A ${item.side.toLowerCase()} ${item.type.toLowerCase()} is not valid on this side of the last price`
          }
          onClick={() => {
            p.onPick(item.side, item.type)
            p.onClose()
          }}
          className={cn(
            'flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[12.5px] transition-colors',
            'hover:bg-accent disabled:pointer-events-none disabled:opacity-35',
            item.side === 'BUY' ? 'hover:text-emerald-500' : 'hover:text-rose-500'
          )}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 shrink-0 rounded-[2px]',
              item.side === 'BUY' ? 'bg-emerald-500' : 'bg-rose-500'
            )}
          />
          <span className="truncate">{item.label}</span>
        </button>
      ))}
      {p.hasOrders && (
        <>
          <div className="my-1 h-px bg-border" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              p.onCancelAll()
              p.onClose()
            }}
            className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[12.5px] text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
          >
            <span className="h-1.5 w-1.5 shrink-0 rounded-[2px] bg-muted-foreground" />
            Cancel every working order
          </button>
        </>
      )}

      <div className="my-1 h-px bg-border" />

      <button
        type="button"
        role="menuitem"
        className={ROW}
        onClick={() => {
          p.onResetScale()
          p.onClose()
        }}
      >
        <Icon name="reset" className="h-3.5 w-3.5 opacity-70" />
        Reset chart view
      </button>

      <button
        type="button"
        role="menuitem"
        className={ROW}
        onClick={() => {
          p.onToggleRail()
          p.onClose()
        }}
      >
        <Icon name="trend" className="h-3.5 w-3.5 opacity-70" />
        {p.railVisible ? 'Hide drawing tools' : 'Show drawing tools'}
      </button>

      <div className="relative" onMouseLeave={() => setGridSub(false)}>
        <button
          type="button"
          role="menuitem"
          aria-expanded={gridSub}
          className={ROW}
          // Opens on hover the way a nested menu is expected to, and a click
          // opens it too — for touch and keyboard, which never hover.
          //
          // The click *opens* rather than toggles: a pointer click is always
          // preceded by the hover that already opened the submenu, so a toggle
          // closed it again the instant you clicked the row you were aiming at.
          //
          // `stopPropagation` because this menu closes itself on any outside
          // pointerdown, and that listener would tear down the menu the submenu
          // belongs to.
          onMouseEnter={() => setGridSub(true)}
          onClick={(e) => {
            e.stopPropagation()
            setGridSub(true)
          }}
        >
          <Icon name="grid" className="h-3.5 w-3.5 opacity-70" />
          Grid
          <Icon name="chevron" className="ml-auto h-3.5 w-3.5 -rotate-90 opacity-60" />
        </button>
        {gridSub && (
          <div className="absolute left-full top-0 ml-1 w-40 rounded-lg border border-border bg-popover/97 p-1 shadow-xl">
            {(
              [
                ['vertLines', 'Vertical lines'],
                ['horzLines', 'Horizontal lines'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="menuitemcheckbox"
                aria-checked={p.grid[key]}
                className={ROW}
                onClick={(e) => {
                  e.stopPropagation()
                  p.onGrid({ [key]: !p.grid[key] })
                }}
              >
                <span className="w-3 shrink-0 text-primary">{p.grid[key] ? '✓' : ''}</span>
                {label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
