/**
 * Version-history panel for the OpenScript editor — lists a script's immutable
 * versions (newest first), previews the source of the selected one, and can
 * restore it into the editor. Modeled on TradingView's Pine "Version history…"
 * dialog. Restoring loads the old source into the buffer as an unsaved change;
 * saving then appends a fresh version (source is never overwritten in place).
 */

import { useEffect, useState } from 'react'
import { getVersion, listVersions, type ScriptVersion } from '@/api/indicators'

interface VersionHistoryDialogProps {
  open: boolean
  scriptId: number | null
  /** Current version id, so the live version is marked in the list. */
  currentVersionId: number | null
  /** Load the chosen source into the editor buffer (as an unsaved change). */
  onRestore: (source: string, versionNumber: number) => void
  onClose: () => void
}

function whenLabel(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

export function VersionHistoryDialog({
  open,
  scriptId,
  currentVersionId,
  onRestore,
  onClose,
}: VersionHistoryDialogProps) {
  const [versions, setVersions] = useState<ScriptVersion[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Load the version list whenever the dialog opens.
  useEffect(() => {
    if (!open || !scriptId) return
    let alive = true
    setLoading(true)
    setError('')
    setSource('')
    listVersions(scriptId)
      .then((rows) => {
        if (!alive) return
        setVersions(rows)
        setSelectedId(rows[0]?.id ?? null)
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : 'Failed to load versions')
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [open, scriptId])

  // Fetch the selected version's source for preview.
  useEffect(() => {
    if (!open || !scriptId || selectedId == null) return
    let alive = true
    getVersion(scriptId, selectedId)
      .then((v) => {
        if (alive) setSource(v?.source_code ?? '')
      })
      .catch(() => {
        if (alive) setSource('')
      })
    return () => {
      alive = false
    }
  }, [open, scriptId, selectedId])

  if (!open) {
    return null
  }

  const selected = versions.find((v) => v.id === selectedId)

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[560px] max-h-full w-[760px] max-w-full flex-col rounded-lg border border-border bg-card text-foreground shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="text-base font-semibold">Version history</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-lg leading-none text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* Version list */}
          <div className="w-56 shrink-0 overflow-auto border-r border-border">
            {loading ? (
              <div className="px-3 py-3 text-sm text-muted-foreground">Loading…</div>
            ) : error ? (
              <div className="px-3 py-3 text-sm text-destructive">{error}</div>
            ) : versions.length === 0 ? (
              <div className="px-3 py-3 text-sm text-muted-foreground">No versions.</div>
            ) : (
              versions.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => setSelectedId(v.id)}
                  className={`flex w-full flex-col items-start gap-0.5 border-b border-border/50 px-3 py-2 text-left last:border-0 hover:bg-accent ${
                    v.id === selectedId ? 'bg-accent/60' : ''
                  }`}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    Version {v.version_number}
                    {v.id === currentVersionId && (
                      <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
                        current
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">{whenLabel(v.created_at)}</span>
                </button>
              ))
            )}
          </div>

          {/* Source preview */}
          <div className="min-h-0 flex-1 overflow-auto bg-background/40">
            {selected ? (
              <pre className="whitespace-pre px-4 py-3 font-mono text-xs leading-relaxed text-foreground">
                {source}
              </pre>
            ) : (
              <div className="px-4 py-3 text-sm text-muted-foreground">
                Select a version to preview its source.
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          <span className="text-xs text-muted-foreground">
            {selected
              ? `Version ${selected.version_number} · ${whenLabel(selected.created_at)}`
              : ''}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="h-9 rounded px-4 text-sm font-medium hover:bg-accent"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => selected && onRestore(source, selected.version_number)}
              disabled={!selected}
              title={
                selected?.id === currentVersionId
                  ? 'This is the current version — restoring reloads it into the editor'
                  : 'Load this version into the editor (save to keep it as a new version)'
              }
              className="h-9 rounded bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
            >
              Restore to editor
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
