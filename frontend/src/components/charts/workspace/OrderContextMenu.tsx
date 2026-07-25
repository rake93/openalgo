/**
 * Right-click order menu, anchored at the price under the cursor.
 *
 * Each row states the whole order in one line — side, size, type and the price
 * it would use — because a right-click is one click away from a live order and
 * the label is the last thing the trader reads. Rows that would be rejected by
 * the exchange (a buy limit above the market, a buy stop below it) are shown
 * disabled with the reason rather than hidden, so the menu stays in one place.
 */

import { useEffect, useRef } from 'react'
import type { CtxItem, OrderSide, OrderType } from '@/lib/charts/trading-layer'
import { cn } from '@/lib/utils'

export interface OrderContextMenuProps {
  x: number
  y: number
  price: string
  items: CtxItem[]
  hasOrders: boolean
  onPick(side: OrderSide, type: OrderType): void
  onCancelAll(): void
  onClose(): void
}

export function OrderContextMenu(p: OrderContextMenuProps) {
  const ref = useRef<HTMLDivElement | null>(null)

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
    </div>
  )
}
