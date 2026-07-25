/**
 * Indicator picker.
 *
 * The workspace runs two indicator runtimes and this is the one place a trader
 * meets them: the OpenScript engine (which also runs scripts you write) and the
 * chart library's built-ins. Each row says which engine computes it, because
 * that determines what you can do next — engine indicators get the full
 * Inputs / Style / Visibility dialog and can raise alerts; library indicators
 * get settings and pane chrome.
 *
 * Search matches name, short name and category, so "vol" finds Volume, VWAP and
 * the volatility group.
 */

import type { IndicatorManifestEntry } from '@openalgo/openscript'
import type { IndicatorDescriptor } from 'openalgo-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { Icon } from './icons'

export type IndicatorSource = 'engine' | 'library'

export interface PickerRow {
  id: string
  name: string
  detail: string
  category: string
  overlay: boolean
  source: IndicatorSource
}

export interface IndicatorPickerProps {
  open: boolean
  onOpenChange(open: boolean): void
  engine: readonly IndicatorManifestEntry[]
  library: readonly IndicatorDescriptor[]
  onAdd(id: string, source: IndicatorSource): void
}

export function IndicatorPicker({
  open,
  onOpenChange,
  engine,
  library,
  onAdd,
}: IndicatorPickerProps) {
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState<'all' | IndicatorSource>('all')
  const inputRef = useRef<HTMLInputElement | null>(null)

  // A picker's first job is to take the query, so focus follows the open state
  // rather than an autofocus attribute (which fires before the dialog mounts).
  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.focus())
    else setQuery('')
  }, [open])

  const rows = useMemo<PickerRow[]>(() => {
    const fromEngine: PickerRow[] = engine.map((m) => ({
      id: m.id,
      name: m.name,
      detail: m.shortName,
      category: m.category,
      overlay: m.overlay,
      source: 'engine',
    }))
    const fromLibrary: PickerRow[] = library.map((d) => ({
      id: d.id,
      name: d.name,
      detail: d.id,
      category: d.category ?? 'Other',
      overlay: d.placement === 'onchart',
      source: 'library',
    }))
    return [...fromEngine, ...fromLibrary].sort((a, b) => a.name.localeCompare(b.name))
  }, [engine, library])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return rows.filter((r) => {
      if (group !== 'all' && r.source !== group) return false
      if (!q) return true
      return (
        r.name.toLowerCase().includes(q) ||
        r.detail.toLowerCase().includes(q) ||
        r.category.toLowerCase().includes(q)
      )
    })
  }, [rows, query, group])

  const byCategory = useMemo(() => {
    const map = new Map<string, PickerRow[]>()
    for (const r of filtered) {
      const list = map.get(r.category)
      if (list) list.push(r)
      else map.set(r.category, [r])
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [filtered])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[76vh] w-[540px] max-w-[92vw] flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border px-4 pb-3 pt-4">
          <DialogTitle className="text-[15px]">Indicators</DialogTitle>
        </DialogHeader>

        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <div className="relative flex-1">
            <Icon
              name="search"
              className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search indicators"
              className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-2 text-[13px] outline-none focus:border-primary/60"
            />
          </div>
          <div className="inline-flex items-center gap-0.5 rounded-lg border border-border/70 bg-muted/40 p-0.5">
            {(
              [
                ['all', 'All'],
                ['engine', 'Engine'],
                ['library', 'Library'],
              ] as const
            ).map(([g, label]) => (
              <button
                key={g}
                type="button"
                onClick={() => setGroup(g)}
                className={cn(
                  'h-6 rounded-[5px] px-2 text-[11px] font-semibold text-muted-foreground transition-colors hover:text-foreground',
                  group === g && 'bg-background text-primary shadow-sm'
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">
          {byCategory.length === 0 && (
            <p className="px-3 py-8 text-center text-[13px] text-muted-foreground">
              Nothing matches “{query}”. Try a shorter word.
            </p>
          )}
          {byCategory.map(([category, items]) => (
            <section key={category} className="mb-1.5">
              <h3 className="px-2.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.09em] text-muted-foreground">
                {category}
              </h3>
              {items.map((r) => (
                <button
                  key={`${r.source}:${r.id}`}
                  type="button"
                  onClick={() => {
                    onAdd(r.id, r.source)
                    onOpenChange(false)
                  }}
                  className="flex w-full items-center gap-3 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-accent"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium">{r.name}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {r.overlay ? 'On the price pane' : 'Own pane'} · {r.detail}
                    </span>
                  </span>
                  <span
                    className={cn(
                      'shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em]',
                      r.source === 'engine'
                        ? 'bg-primary/12 text-primary'
                        : 'bg-muted text-muted-foreground'
                    )}
                  >
                    {r.source}
                  </span>
                </button>
              ))}
            </section>
          ))}
        </div>

        <footer className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
          Engine indicators run in the OpenScript worker and support alerts and custom scripts.
          Library indicators are computed by the chart itself.
        </footer>
      </DialogContent>
    </Dialog>
  )
}
