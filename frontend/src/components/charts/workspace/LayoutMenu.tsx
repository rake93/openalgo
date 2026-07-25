/**
 * Saved-layout menu.
 *
 * A layout is the whole workspace — symbol, timeframe, chart type, both
 * indicator runtimes, drawings, studies, trading preferences, and the viewport.
 * The active one auto-saves as you work; the menu is for keeping more than one
 * (an intraday setup and a swing setup, say) and moving between them.
 */

import { useState } from 'react'
import type { ChartLayoutRecord } from '@/api/indicators'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { Icon } from './icons'
import { IBtn } from './primitives'

export interface LayoutMenuProps {
  layouts: ChartLayoutRecord[]
  activeId: number | null
  saving: boolean
  onSwitch(id: number): void
  onSaveNow(): void
  onSaveAs(name: string): void
  onRename(id: number, name: string): void
  onDelete(id: number): void
}

export function LayoutMenu(p: LayoutMenuProps) {
  const [prompt, setPrompt] = useState<{ kind: 'saveAs' | 'rename'; name: string } | null>(null)
  const active = p.layouts.find((l) => l.id === p.activeId)

  const submit = () => {
    if (!prompt) return
    const name = prompt.name.trim()
    if (!name) return
    if (prompt.kind === 'saveAs') p.onSaveAs(name)
    else if (p.activeId != null) p.onRename(p.activeId, name)
    setPrompt(null)
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IBtn title={active ? `Layout: ${active.name}` : 'Layouts'} aria-label="Layouts">
            <Icon name="layout" className="h-4 w-4" />
          </IBtn>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel className="text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
            Layouts
          </DropdownMenuLabel>
          {p.layouts.length === 0 && (
            <p className="px-2 py-1.5 text-[12px] text-muted-foreground">
              None saved yet. This one saves itself as you work.
            </p>
          )}
          {p.layouts.map((l) => (
            <DropdownMenuItem
              key={l.id}
              onSelect={() => l.id !== p.activeId && p.onSwitch(l.id)}
              className="gap-2 text-[13px]"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate">{l.name}</span>
                {l.symbol && (
                  <span className="block truncate text-[10.5px] text-muted-foreground">
                    {l.symbol} · {l.timeframe}
                  </span>
                )}
              </span>
              {l.id === p.activeId && <span className="text-primary">✓</span>}
            </DropdownMenuItem>
          ))}

          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={p.onSaveNow} className="text-[13px]">
            Save now
            <span className="ml-auto text-[10.5px] text-muted-foreground">Ctrl+S</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => setPrompt({ kind: 'saveAs', name: '' })}
            className="text-[13px]"
          >
            Save as new layout…
          </DropdownMenuItem>
          {active && (
            <>
              <DropdownMenuItem
                onSelect={() => setPrompt({ kind: 'rename', name: active.name })}
                className="text-[13px]"
              >
                Rename “{active.name}”…
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => p.onDelete(active.id)}
                className="text-[13px] text-destructive focus:text-destructive"
              >
                Delete “{active.name}”
              </DropdownMenuItem>
            </>
          )}
          <p
            className={cn(
              'px-2 pb-1 pt-1.5 text-[10.5px] text-muted-foreground',
              p.saving && 'text-primary'
            )}
          >
            {p.saving ? 'Saving…' : 'Changes save automatically'}
          </p>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={prompt !== null} onOpenChange={(open) => !open && setPrompt(null)}>
        <DialogContent className="w-[360px] max-w-[92vw]">
          <DialogHeader>
            <DialogTitle className="text-[15px]">
              {prompt?.kind === 'saveAs' ? 'Save as a new layout' : 'Rename this layout'}
            </DialogTitle>
          </DialogHeader>
          <input
            value={prompt?.name ?? ''}
            placeholder="Intraday order flow"
            onChange={(e) => setPrompt((s) => (s ? { ...s, name: e.target.value } : s))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            className="h-9 w-full rounded-md border border-border bg-background px-2.5 text-[13px] outline-none focus:border-primary/60"
          />
          <DialogFooter className="gap-2">
            <Button variant="ghost" size="sm" onClick={() => setPrompt(null)}>
              Cancel
            </Button>
            <Button size="sm" disabled={!prompt?.name.trim()} onClick={submit}>
              {prompt?.kind === 'saveAs' ? 'Save' : 'Rename'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
