/**
 * /charts/editor — OpenScript indicator editor with live chart preview.
 *
 * A CodeMirror pane (engine-linted OpenScript) on the left; the shared
 * chart workspace on the right. Every edit is debounced, compiled in-browser
 * to IR, and previewed as a single `{kind:'ir'}` session on the chart — so
 * the values you see equal what the server (and Python strategies) compute.
 *
 * Scripts persist as immutable versions via /indicators/api/scripts; the route
 * `/charts/editor/:scriptId` loads a saved script.
 */

import type { Diagnostic } from '@openalgo/openscript'
import { compile } from '@openalgo/openscript/compiler'
import { formatSource } from '@openalgo/openscript/codemirror'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  createAlert,
  createScript,
  getScript,
  listScripts,
  type ScriptRecord,
  updateScript,
} from '@/api/indicators'
import { type AlertCondition, CreateAlertDialog } from '@/components/charts/CreateAlertDialog'
import { DataWindow } from '@/components/charts/DataWindow'
import { OpenScriptEditor } from '@/components/charts/OpenScriptEditor'
import { ScriptMenu } from '@/components/charts/ScriptMenu'
import { VersionHistoryDialog } from '@/components/charts/VersionHistoryDialog'
import { ChartWorkspaceController, type CrosshairData } from '@/lib/charts/workspace'
import { useThemeStore } from '@/stores/themeStore'

const DEFAULT_SYMBOL = { symbol: 'NIFTY', exchange: 'NSE_INDEX' }
const INTERVALS = ['1m', '5m', '15m', '1h', 'D'] as const

const SAMPLE = `//@version=1
indicator("EMA Crossover", overlay=true)

fast = ta.ema(close, 9)
slow = ta.ema(close, 21)

bullish = ta.crossover(fast, slow)
bearish = ta.crossunder(fast, slow)

plot(fast, "Fast EMA", color=color.green, linewidth=2)
plot(slow, "Slow EMA", color=color.red, linewidth=2)

plotshape(bullish, title="Buy", location=location.belowbar, shape=shape.arrowup, color=color.green)
plotshape(bearish, title="Sell", location=location.abovebar, shape=shape.arrowdown, color=color.red)

alertcondition(bullish, "EMA Buy", "Fast EMA crossed above Slow EMA")
`

interface SearchRow {
  symbol: string
  exchange: string
  name?: string
}

export default function ChartEditor() {
  const params = useParams()
  const navigate = useNavigate()
  const scriptId = params.scriptId ? Number(params.scriptId) : null

  const containerRef = useRef<HTMLDivElement | null>(null)
  const controllerRef = useRef<ChartWorkspaceController | null>(null)
  const compileTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const sourceRef = useRef(SAMPLE)
  const savedSourceRef = useRef('')
  const savedNameRef = useRef('')
  const nameInputRef = useRef<HTMLInputElement | null>(null)
  const mode = useThemeStore((s) => s.mode)

  const [ready, setReady] = useState(false)
  const [source, setSourceState] = useState(SAMPLE)
  const [scriptName, setScriptName] = useState('')
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([])
  const [status, setStatus] = useState('Connecting…')
  const [noApiKey, setNoApiKey] = useState(false)
  const [interval, setIntervalValue] = useState('5m')
  const [active, setActive] = useState(DEFAULT_SYMBOL)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchRow[]>([])
  const [scripts, setScripts] = useState<ScriptRecord[]>([])
  const [showMenu, setShowMenu] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [currentVersionId, setCurrentVersionId] = useState<number | null>(null)
  const [alertConditions, setAlertConditions] = useState<AlertCondition[]>([])
  const [showAlerts, setShowAlerts] = useState(false)
  const [crosshair, setCrosshair] = useState<CrosshairData | null>(null)

  const setSource = useCallback((next: string) => {
    sourceRef.current = next
    setSourceState(next)
    setDirty(next !== savedSourceRef.current)
  }, [])

  const compileAndPreview = useCallback((src: string) => {
    const result = compile(src)
    setDiagnostics(result.diagnostics)
    const alerts: AlertCondition[] = []
    for (const o of result.ir?.outputs ?? []) {
      if (o.kind === 'alertcondition') {
        alerts.push({ conditionId: o.conditionId, title: o.title, message: o.message })
      }
    }
    setAlertConditions(alerts)
    if (result.ir && controllerRef.current) {
      void controllerRef.current.previewIr(result.ir).catch(() => undefined)
    }
  }, [])

  // Bootstrap: chart controller + default symbol.
  useEffect(() => {
    let alive = true
    let controller: ChartWorkspaceController | null = null
    ;(async () => {
      try {
        const [keyRes, cfgRes] = await Promise.all([
          fetch('/api/websocket/apikey').then((r) => r.json()),
          fetch('/api/websocket/config').then((r) => r.json()),
        ])
        if (!alive) return
        if (keyRes.status !== 'success') {
          setNoApiKey(true)
          return
        }
        if (!containerRef.current) return
        controller = new ChartWorkspaceController({
          apiKey: keyRes.api_key,
          wsUrl: cfgRes.websocket_url || 'ws://127.0.0.1:8765',
          container: containerRef.current,
          isDark: () => useThemeStore.getState().mode === 'dark',
          callbacks: {
            onStatus: setStatus,
            onWsState: () => undefined,
            onIndicators: () => undefined,
            onSymbolLoaded: (info) => {
              setActive({ symbol: info.symbol, exchange: info.exchange })
              setIntervalValue(info.interval)
            },
            onError: (message) => setStatus(message),
          },
        })
        controllerRef.current = controller
        controller.subscribeCrosshair(setCrosshair)
        await controller.load(DEFAULT_SYMBOL.symbol, DEFAULT_SYMBOL.exchange, '5m')
        if (alive) setReady(true)
      } catch (err) {
        if (alive) setStatus(err instanceof Error ? err.message : 'failed to start editor')
      }
    })()
    return () => {
      alive = false
      if (compileTimer.current) clearTimeout(compileTimer.current)
      controller?.destroy()
      controllerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Load the saved script (or compile the current buffer) once ready / on nav.
  useEffect(() => {
    if (!ready) return
    let alive = true
    ;(async () => {
      if (scriptId) {
        const s = await getScript(scriptId).catch(() => undefined)
        if (!alive) return
        if (s && s.source !== undefined) {
          savedSourceRef.current = s.source
          savedNameRef.current = s.name
          sourceRef.current = s.source
          setSourceState(s.source)
          setScriptName(s.name)
          setCurrentVersionId(s.current_version_id)
          setDirty(false)
          compileAndPreview(s.source)
          return
        }
        setStatus('Script not found')
      }
      compileAndPreview(sourceRef.current)
    })()
    return () => {
      alive = false
    }
  }, [ready, scriptId, compileAndPreview])

  useEffect(() => {
    controllerRef.current?.setTheme()
  }, [mode])

  const onSourceChange = useCallback(
    (next: string) => {
      setSource(next)
      if (compileTimer.current) clearTimeout(compileTimer.current)
      compileTimer.current = setTimeout(() => compileAndPreview(next), 400)
    },
    [setSource, compileAndPreview]
  )

  const formatDoc = useCallback(() => {
    onSourceChange(formatSource(sourceRef.current))
  }, [onSourceChange])

  const doSearch = useCallback(async (q: string) => {
    setQuery(q)
    if (q.length < 2) {
      setResults([])
      return
    }
    const rows = await controllerRef.current?.search(q)
    setResults(rows ?? [])
  }, [])

  const pick = useCallback(async (row: SearchRow) => {
    setResults([])
    setQuery('')
    await controllerRef.current?.load(row.symbol, row.exchange)
  }, [])

  const changeInterval = useCallback(
    async (iv: string) => {
      setIntervalValue(iv)
      await controllerRef.current?.load(active.symbol, active.exchange, iv)
    },
    [active]
  )

  const save = useCallback(async () => {
    const src = sourceRef.current
    const name = scriptName.trim() || 'Untitled indicator'
    setSaving(true)
    try {
      if (scriptId) {
        const updated = await updateScript(scriptId, { name, source: src })
        savedSourceRef.current = src
        savedNameRef.current = name
        setDirty(false)
        if (updated) setCurrentVersionId(updated.current_version_id)
      } else {
        const created = await createScript({ name, source: src })
        if (created) {
          savedSourceRef.current = src
          savedNameRef.current = created.name
          setScriptName(created.name)
          setCurrentVersionId(created.current_version_id)
          setDirty(false)
          navigate(`/charts/editor/${created.id}`)
        }
      }
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'save failed')
    } finally {
      setSaving(false)
    }
  }, [scriptId, scriptName, navigate])

  const toggleMenu = useCallback(async () => {
    const next = !showMenu
    setShowMenu(next)
    if (next) {
      setScripts(await listScripts().catch(() => []))
    }
  }, [showMenu])

  const openScript = useCallback(
    (id: number) => {
      setShowMenu(false)
      navigate(`/charts/editor/${id}`)
    },
    [navigate]
  )

  const newScript = useCallback(() => {
    setShowMenu(false)
    savedSourceRef.current = ''
    savedNameRef.current = ''
    setScriptName('')
    setCurrentVersionId(null)
    setSource(SAMPLE)
    navigate('/charts/editor')
    compileAndPreview(SAMPLE)
  }, [setSource, navigate, compileAndPreview])

  // Fork the current buffer into a brand-new script ("… copy") and open it.
  const makeCopy = useCallback(async () => {
    const src = sourceRef.current
    const base = scriptName.trim() || 'Untitled indicator'
    setSaving(true)
    try {
      const created = await createScript({ name: `${base} copy`, source: src })
      if (created) {
        savedSourceRef.current = src
        savedNameRef.current = created.name
        setScriptName(created.name)
        setCurrentVersionId(created.current_version_id)
        setDirty(false)
        navigate(`/charts/editor/${created.id}`)
      }
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'copy failed')
    } finally {
      setSaving(false)
    }
  }, [scriptName, navigate])

  // Rename = focus the name field; the field commits a name-only update on blur
  // (name-only PUT does not append a version — the source is untouched).
  const renameFocus = useCallback(() => {
    nameInputRef.current?.focus()
    nameInputRef.current?.select()
  }, [])

  const commitRename = useCallback(async () => {
    const name = scriptName.trim()
    if (!scriptId || !name || name === savedNameRef.current) {
      return
    }
    try {
      const updated = await updateScript(scriptId, { name })
      if (updated) {
        setScriptName(updated.name)
        savedNameRef.current = updated.name
      }
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'rename failed')
    }
  }, [scriptId, scriptName])

  // Load a historical version's source into the buffer as an unsaved change;
  // saving then appends it as a new version (immutable history is never edited).
  const restoreVersion = useCallback(
    (src: string, versionNumber: number) => {
      setShowVersions(false)
      setSource(src)
      compileAndPreview(src)
      setStatus(`Loaded version ${versionNumber} — save to keep it as a new version`)
    },
    [setSource, compileAndPreview]
  )

  const handleCreateAlert = useCallback(
    async ({ conditionId, triggerMode }: { conditionId: string; triggerMode: string }) => {
      if (!currentVersionId) {
        throw new Error('Save the script first.')
      }
      await createAlert({
        script_version_id: currentVersionId,
        symbol: active.symbol,
        exchange: active.exchange,
        timeframe: interval,
        condition_id: conditionId,
        trigger_mode: triggerMode,
      })
    },
    [currentVersionId, active, interval]
  )

  // Pine-style shortcuts: Ctrl/Cmd+S saves, Ctrl/Cmd+O opens the library.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) {
        return
      }
      const key = e.key.toLowerCase()
      if (key === 's') {
        e.preventDefault()
        void save()
      } else if (key === 'o') {
        e.preventDefault()
        void toggleMenu()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [save, toggleMenu])

  const errorCount = diagnostics.filter((d) => d.severity === 'error').length

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background text-foreground">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <Link
          to="/charts"
          className="flex h-8 items-center rounded px-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          ‹ Charts
        </Link>
        <span className="text-sm font-semibold">ƒx</span>
        <input
          ref={nameInputRef}
          value={scriptName}
          onChange={(e) => setScriptName(e.target.value)}
          onBlur={() => void commitRename()}
          onKeyDown={(e) => {
            if (e.key === 'Enter') nameInputRef.current?.blur()
          }}
          placeholder="Untitled indicator"
          className="h-8 w-48 rounded border border-border bg-card px-2 text-sm outline-none focus:border-primary"
        />
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="h-8 rounded bg-primary px-3 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {saving ? 'Saving…' : dirty ? 'Save •' : 'Save'}
        </button>
        <ScriptMenu
          open={showMenu}
          onToggle={() => void toggleMenu()}
          onClose={() => setShowMenu(false)}
          scripts={scripts}
          currentScriptId={scriptId}
          canManage={scriptId !== null}
          onMakeCopy={() => void makeCopy()}
          onRename={renameFocus}
          onVersionHistory={() => setShowVersions(true)}
          onCreateNew={newScript}
          onOpen={openScript}
        />
        <button
          type="button"
          onClick={formatDoc}
          title="Format (Shift-Alt-F)"
          className="h-8 rounded bg-card px-3 text-sm font-medium hover:bg-accent"
        >
          Format
        </button>
        <button
          type="button"
          onClick={() => setShowAlerts(true)}
          disabled={alertConditions.length === 0}
          title={
            alertConditions.length === 0
              ? 'Add an alertcondition() to enable alerts'
              : 'Create alerts'
          }
          className="h-8 rounded bg-card px-3 text-sm font-medium hover:bg-accent disabled:opacity-40"
        >
          🔔 Alerts{alertConditions.length ? ` (${alertConditions.length})` : ''}
        </button>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="relative">
            <input
              value={query}
              onChange={(e) => void doSearch(e.target.value)}
              placeholder={`${active.symbol} (${active.exchange})`}
              className="h-8 w-44 rounded border border-border bg-card px-2 text-sm outline-none focus:border-primary"
            />
            {results.length > 0 && (
              <div className="absolute right-0 z-50 mt-1 max-h-72 w-72 overflow-auto rounded border border-border bg-card shadow-lg">
                {results.map((r) => (
                  <button
                    key={`${r.exchange}:${r.symbol}`}
                    type="button"
                    onClick={() => void pick(r)}
                    className="flex w-full items-center justify-between px-2 py-1.5 text-left text-sm hover:bg-accent"
                  >
                    <span className="font-medium">{r.symbol}</span>
                    <span className="text-xs text-muted-foreground">{r.exchange}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1">
            {INTERVALS.map((iv) => (
              <button
                key={iv}
                type="button"
                onClick={() => void changeInterval(iv)}
                className={`h-8 rounded px-2 text-xs font-medium ${
                  interval === iv ? 'bg-primary text-primary-foreground' : 'bg-card hover:bg-accent'
                }`}
              >
                {iv}
              </button>
            ))}
          </div>
          <span className="text-xs">
            {errorCount > 0 ? (
              <span className="rounded bg-destructive/20 px-2 py-1 font-medium text-destructive">
                {errorCount} error{errorCount === 1 ? '' : 's'}
              </span>
            ) : (
              <span className="rounded bg-green-500/15 px-2 py-1 font-medium text-green-500">
                Compiled ✓
              </span>
            )}
          </span>
        </div>
      </div>

      {/* Split: editor | preview */}
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[42%] min-w-[360px] min-h-0 flex-col border-r border-border">
          <div className="min-h-0 flex-1 overflow-hidden">
            <OpenScriptEditor value={source} onChange={onSourceChange} />
          </div>
          <div className="max-h-40 shrink-0 overflow-auto border-t border-border bg-card/40 text-xs">
            {diagnostics.length === 0 ? (
              <div className="px-3 py-2 text-muted-foreground">No problems.</div>
            ) : (
              <ul>
                {diagnostics.map((d, i) => (
                  <li
                    key={`${d.code}-${d.span.start}-${i}`}
                    className="flex gap-2 border-b border-border/50 px-3 py-1.5 last:border-0"
                  >
                    <span
                      className={d.severity === 'error' ? 'text-destructive' : 'text-yellow-500'}
                    >
                      {d.severity === 'error' ? '✕' : '⚠'}
                    </span>
                    <span className="font-mono text-muted-foreground">
                      Ln {d.span.line}:{d.span.column}
                    </span>
                    <span className="font-mono text-muted-foreground">{d.code}</span>
                    <span className="flex-1">{d.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="relative min-h-0 flex-1">
          <div ref={containerRef} className="absolute inset-0" />
          {ready && <DataWindow data={crosshair} />}
          {noApiKey && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
              Generate an API key at /apikey to use the editor preview.
            </div>
          )}
          {!ready && !noApiKey && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
              {status}
            </div>
          )}
        </div>
      </div>

      <CreateAlertDialog
        open={showAlerts}
        symbol={active.symbol}
        exchange={active.exchange}
        timeframe={interval}
        conditions={alertConditions}
        canCreate={!!currentVersionId && !dirty}
        onCreate={handleCreateAlert}
        onClose={() => setShowAlerts(false)}
      />

      <VersionHistoryDialog
        open={showVersions}
        scriptId={scriptId}
        currentVersionId={currentVersionId}
        onRestore={restoreVersion}
        onClose={() => setShowVersions(false)}
      />
    </div>
  )
}
