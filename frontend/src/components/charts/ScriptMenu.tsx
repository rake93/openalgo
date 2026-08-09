/**
 * The "⋯" script actions menu for the OpenScript editor — mirrors TradingView's
 * Pine editor menu: Save · Make a copy · Rename · Version history · Delete ·
 * Create new · Recently used. Folds the editor's saved-script library into a
 * single dropdown.
 *
 * Presentational + controlled: the parent owns the open flag (so Ctrl+O can
 * toggle it) and every action callback. Manage actions (copy/rename/history)
 * need a saved script and are disabled until one exists.
 */

import { useEffect, useRef } from 'react'
import type { ScriptRecord } from '@/api/indicators'

interface ScriptMenuProps {
  open: boolean
  onToggle: () => void
  onClose: () => void
  scripts: ScriptRecord[]
  currentScriptId: number | null
  /** True once the script is saved — gates the manage actions. */
  canManage: boolean
  onMakeCopy: () => void
  onRename: () => void
  onVersionHistory: () => void
  /** Opens the confirm; the deletion itself is the parent's, never this menu's. */
  onDelete: () => void
  onCreateNew: () => void
  onOpen: (id: number) => void
}

const ITEM =
  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent'

export function ScriptMenu({
  open,
  onToggle,
  onClose,
  scripts,
  currentScriptId,
  canManage,
  onMakeCopy,
  onRename,
  onVersionHistory,
  onDelete,
  onCreateNew,
  onOpen,
}: ScriptMenuProps) {
  const rootRef = useRef<HTMLDivElement | null>(null)

  // Close on outside click / Escape while open.
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) onClose()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  const run = (fn: () => void) => () => {
    onClose()
    fn()
  }

  const recent = scripts.slice(0, 8)

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={onToggle}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Script menu"
        className="h-8 rounded bg-card px-3 text-sm font-medium leading-none hover:bg-accent"
      >
        •••
      </button>
      {open && (
        <div
          role="menu"
          className="absolute z-50 mt-1 w-72 overflow-hidden rounded border border-border bg-card py-1 shadow-lg"
        >
          <button type="button" onClick={run(onMakeCopy)} disabled={!canManage} className={ITEM}>
            Make a copy…
          </button>
          <button type="button" onClick={run(onRename)} disabled={!canManage} className={ITEM}>
            Rename…
          </button>
          <button
            type="button"
            onClick={run(onVersionHistory)}
            disabled={!canManage}
            className={ITEM}
          >
            Version history…
          </button>
          {/* Separated from the non-destructive actions above it so Delete is
           * never the neighbour of a mis-click on Rename. */}
          <div className="my-1 border-t border-border" />
          <button
            type="button"
            onClick={run(onDelete)}
            disabled={!canManage}
            className={`${ITEM} text-destructive hover:bg-destructive/10`}
          >
            Delete script…
          </button>

          <div className="my-1 border-t border-border" />

          <button type="button" onClick={run(onCreateNew)} className={ITEM}>
            + Create new
          </button>

          <div className="mt-1 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Recently used
          </div>
          {recent.length === 0 ? (
            <div className="px-3 py-1.5 text-sm text-muted-foreground">No saved scripts yet.</div>
          ) : (
            <div className="max-h-56 overflow-auto">
              {recent.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={run(() => onOpen(s.id))}
                  className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-sm hover:bg-accent ${
                    s.id === currentScriptId ? 'bg-accent/60' : ''
                  }`}
                >
                  <span className="truncate font-medium">{s.name}</span>
                  <span className="ml-2 shrink-0 text-xs text-muted-foreground">
                    {s.updated_at ? new Date(s.updated_at).toLocaleDateString() : ''}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
