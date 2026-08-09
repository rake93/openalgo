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
import { formatSource } from '@openalgo/openscript/codemirror'
import { compile } from '@openalgo/openscript/compiler'
import { type MouseEvent as ReactMouseEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  createAlert,
  createScript,
  deleteScript,
  getScript,
  listAlerts,
  listScripts,
  listVersions,
  type ScriptRecord,
  updateScript,
} from '@/api/indicators'
import { type AlertCondition, CreateAlertDialog } from '@/components/charts/CreateAlertDialog'
import { DeleteScriptDialog } from '@/components/charts/DeleteScriptDialog'
import { IndicatorSettingsDialog } from '@/components/charts/IndicatorSettingsDialog'
import { InspectorPanel } from '@/components/charts/InspectorPanel'
import { OpenScriptEditor } from '@/components/charts/OpenScriptEditor'
import { ProfilePanel } from '@/components/charts/ProfilePanel'
import { ScriptMenu } from '@/components/charts/ScriptMenu'
import { VersionHistoryDialog } from '@/components/charts/VersionHistoryDialog'
import type { IndicatorInstance } from '@/lib/charts/indicator-host'
import { useInspectorPin } from '@/lib/charts/use-inspector-pin'
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
  const appMode = useThemeStore((s) => s.appMode)

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
  const [showDelete, setShowDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  /** Alerts that a delete would orphan. Null while unknown - see `askDelete`. */
  const [deleteAlerts, setDeleteAlerts] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [currentVersionId, setCurrentVersionId] = useState<number | null>(null)
  const [alertConditions, setAlertConditions] = useState<AlertCondition[]>([])
  const [showAlerts, setShowAlerts] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [crosshair, setCrosshair] = useState<CrosshairData | null>(null)
  /** Bar pinned for the series inspector (M8); see `useInspectorPin`. */
  const { pinned, setPinned } = useInspectorPin(crosshair)
  /** Source range the inspector asked the editor to reveal (M8). */
  const [revealSpan, setRevealSpan] = useState<{
    start: number
    end: number
    nonce: number
  } | null>(null)
  const splitRef = useRef<HTMLDivElement | null>(null)
  const [editorPct, setEditorPct] = useState(42)
  const onDividerDown = useCallback((e: ReactMouseEvent) => {
    e.preventDefault()
    const container = splitRef.current
    if (!container) return
    const onMove = (ev: MouseEvent) => {
      const rect = container.getBoundingClientRect()
      const pct = ((ev.clientX - rect.left) / rect.width) * 100
      setEditorPct(Math.max(20, Math.min(80, pct)))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
    }
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [])

  const setSource = useCallback((next: string) => {
    sourceRef.current = next
    setSourceState(next)
    setDirty(next !== savedSourceRef.current)
  }, [])

  // P4. The editor recompiles on every keystroke and `previewIr` tears the
  // session down, so an edited input has to live HERE rather than in the
  // preview instance — otherwise it reverts to its declared default as the
  // author types, which reads as the settings dialog being broken.
  //
  // Mirrored into a ref because `compileAndPreview` is a stable callback: it
  // must read the CURRENT values without being re-created on every edit,
  // which would restart the debounce.
  // A ref, not state: nothing RENDERS from these values. The dialog reads the
  // live instance's `inputs`, and the chart updates through the host, so
  // mirroring them into state would be a second source of truth that only
  // ever forced a redundant re-render.
  const previewInputsRef = useRef<Record<string, unknown>>({})
  const [settingsOpen, setSettingsOpen] = useState(false)

  // The editor previews exactly one indicator, so the preview IS the head of
  // the list — but it must come through `onIndicators`, not a read of the
  // controller during render. Reading at render (as ProfilePanel does) leaves
  // React with no idea the preview attached, so the Settings button stayed
  // DISABLED until some unrelated state change re-rendered the page. The host
  // already announces every list change; subscribing is the whole fix, and it
  // keeps the instance fresh after an input update too.
  const [previewInstance, setPreviewInstance] = useState<IndicatorInstance | null>(null)

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
      // Prune to the ids the CURRENT source declares. Editing a script renames
      // and removes inputs, and carrying a value for one that no longer exists
      // would quietly feed the engine a key it never declared.
      const declared = new Set(result.ir.inputs.map((d) => d.id))
      const kept: Record<string, unknown> = {}
      for (const [id, v] of Object.entries(previewInputsRef.current)) {
        if (declared.has(id)) kept[id] = v
      }
      if (Object.keys(kept).length !== Object.keys(previewInputsRef.current).length) {
        previewInputsRef.current = kept
      }
      const inputs = Object.keys(kept).length > 0 ? kept : undefined
      void controllerRef.current.previewIr(result.ir, inputs).catch(() => undefined)
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
          // The preview chart tracks the whole app theme (light, dark, and the
          // analyzer palette), not just light/dark.
          getTheme: () => {
            const s = useThemeStore.getState()
            return { mode: s.mode, appMode: s.appMode }
          },
          callbacks: {
            onStatus: setStatus,
            onSymbolLoaded: (info) => {
              setActive({ symbol: info.symbol, exchange: info.exchange })
              setIntervalValue(info.interval)
            },
            onError: (message) => setStatus(message),
            onIndicators: (list) => setPreviewInstance(list[0] ?? null),
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

  // The controller reads the theme from the store itself; naming both values
  // here is what makes the preview chart rebuild when either changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: mode and appMode are triggers, not reads
  useEffect(() => {
    controllerRef.current?.setTheme()
  }, [mode, appMode])

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

  // Open the delete confirm, and look up what else the delete takes with it.
  //
  // The alert count is BEST EFFORT and starts as null rather than 0: the dialog
  // words itself differently for "no alerts" and "could not find out", and a
  // failed lookup must not render the reassuring one.
  const askDelete = useCallback(async () => {
    setShowMenu(false)
    setDeleteAlerts(null)
    setShowDelete(true)
    if (!scriptId) return
    try {
      const [versions, alerts] = await Promise.all([listVersions(scriptId), listAlerts()])
      const versionIds = new Set(versions.map((v) => v.id))
      setDeleteAlerts(
        alerts.filter((a) => a.script_version_id !== null && versionIds.has(a.script_version_id))
          .length
      )
    } catch {
      // Leave it null - the dialog still warns, just without a number.
    }
  }, [scriptId])

  // Delete, then leave the editor on a blank buffer. Staying on the deleted
  // script's URL would show an editor whose Save silently creates a new script.
  const confirmDelete = useCallback(async () => {
    if (!scriptId) return
    setDeleting(true)
    try {
      await deleteScript(scriptId)
      setShowDelete(false)
      savedSourceRef.current = ''
      savedNameRef.current = ''
      setScriptName('')
      setCurrentVersionId(null)
      setDirty(false)
      setSource(SAMPLE)
      navigate('/charts/editor')
      compileAndPreview(SAMPLE)
    } catch (err) {
      // Deliberately leaves the dialog open: a failed delete that closed its own
      // confirm would read as a success.
      setStatus(err instanceof Error ? err.message : 'delete failed')
    } finally {
      setDeleting(false)
    }
  }, [scriptId, navigate, setSource, compileAndPreview])

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
          onDelete={() => void askDelete()}
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
        <button
          type="button"
          onClick={() => setProfileOpen((v) => !v)}
          title="Last-run telemetry: is this indicator recomputing incrementally?"
          className={`h-8 rounded px-3 text-sm font-medium hover:bg-accent ${
            profileOpen ? 'bg-accent text-primary' : 'bg-card'
          }`}
        >
          Profile
        </button>
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          disabled={!previewInstance}
          title="Edit this script's inputs and preview the result (P4)"
          className="h-8 rounded bg-card px-3 text-sm font-medium hover:bg-accent disabled:opacity-40"
        >
          Settings
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
      <div ref={splitRef} className="flex min-h-0 flex-1">
        <div
          className="flex min-w-[360px] min-h-0 flex-col border-r border-border"
          style={{ width: `${editorPct}%` }}
        >
          <div className="min-h-0 flex-1 overflow-hidden">
            <OpenScriptEditor value={source} onChange={onSourceChange} revealSpan={revealSpan} />
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

        <div
          onMouseDown={onDividerDown}
          className="w-1 shrink-0 cursor-col-resize bg-border transition-colors hover:bg-primary/60 active:bg-primary"
          title="Drag to resize"
        />

        <div className="relative min-h-0 flex-1">
          <div ref={containerRef} className="absolute inset-0" />
          {/* The crosshair OHLC readout is deliberately NOT rendered here. It is
              pinned top-left, which is exactly where the chart draws its indicator
              legend, so it covered the legend and swallowed clicks on it — the
              affordance you use to reach an indicator's settings. `crosshair` is
              still subscribed because `useInspectorPin` needs it; only the readout
              is gone. `/charts` keeps its own, toggleable from the status bar. */}
          {ready && profileOpen && (
            <ProfilePanel
              // The editor previews a single indicator and keeps no indicator
              // state of its own, so the list is read from the controller at
              // render. Crosshair movement already re-renders this component, so
              // the panel stays current without a new subscription.
              indicators={controllerRef.current?.indicators.list() ?? []}
              profileOf={(id) => controllerRef.current?.indicators.lastProfile(id)}
              onClose={() => setProfileOpen(false)}
            />
          )}
          {/* Settings is gated on `ready` ALONE. It sat inside the inspector's
              `pinned` branch at first, which tied opening an indicator's inputs
              to having a crosshair bar pinned — two unrelated things. */}
          {ready && (
            <IndicatorSettingsDialog
              instance={settingsOpen ? (previewInstance ?? null) : null}
              manifest={controllerRef.current?.manifest ?? []}
              onSave={(instanceId, inputs) => {
                // Two writes on purpose. `updateIndicatorInputs` applies the
                // change to the LIVE session immediately, with no recompile;
                // the ref/state is what carries it through the NEXT one.
                previewInputsRef.current = inputs
                void controllerRef.current?.updateIndicatorInputs(instanceId, inputs)
              }}
              onClose={() => setSettingsOpen(false)}
            />
          )}
          {ready && pinned && (
            <InspectorPanel
              bar={pinned}
              inspect={(instanceId, outputId, barIndex) =>
                controllerRef.current
                  ? controllerRef.current.indicators.inspect(instanceId, outputId, barIndex)
                  : Promise.resolve(null)
              }
              lastEpoch={(instanceId) => controllerRef.current?.indicators.lastEpoch(instanceId)}
              onClose={() => setPinned(null)}
              onPickSpan={(span) =>
                setRevealSpan((prev) => ({
                  start: span.start,
                  end: span.end,
                  nonce: (prev?.nonce ?? 0) + 1,
                }))
              }
            />
          )}
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

      <DeleteScriptDialog
        open={showDelete}
        onOpenChange={(next) => {
          // Never dismissable mid-request: closing under a delete in flight
          // leaves the user with no idea whether it landed.
          if (!deleting) setShowDelete(next)
        }}
        scriptName={scriptName}
        affectedAlerts={deleteAlerts}
        busy={deleting}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  )
}
