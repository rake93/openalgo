/**
 * /charts — production chart workspace with engine-backed indicators.
 * Price + volume, live candles, worker/WASM indicators, generated settings
 * forms, and server-side layout persistence (auto-saved "default" layout).
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useThemeStore } from '@/stores/themeStore'
import {
  createLayout,
  listLayouts,
  updateLayout,
  type ChartLayoutState,
} from '@/api/indicators'
import { IndicatorSettingsDialog } from '@/components/charts/IndicatorSettingsDialog'
import {
  ChartWorkspaceController,
  type IndicatorInstance,
} from '@/lib/charts/workspace'

const INTERVALS = ['1m', '3m', '5m', '15m', '30m', '1h', 'D', 'W'] as const
const DEFAULT_SYMBOL = { symbol: 'NIFTY', exchange: 'NSE_INDEX' }
const LAYOUT_NAME = 'default'

interface SearchRow {
  symbol: string
  exchange: string
  name?: string
}

export default function ChartWorkspace() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const controllerRef = useRef<ChartWorkspaceController | null>(null)
  const layoutIdRef = useRef<number | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mode = useThemeStore((s) => s.mode)

  const [ready, setReady] = useState(false)
  const [noApiKey, setNoApiKey] = useState(false)
  const [status, setStatus] = useState('Connecting…')
  const [wsState, setWsState] = useState('idle')
  const [interval, setIntervalValue] = useState('5m')
  const [active, setActive] = useState(DEFAULT_SYMBOL)
  const [indicators, setIndicators] = useState<IndicatorInstance[]>([])
  const [settingsFor, setSettingsFor] = useState<IndicatorInstance | null>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchRow[]>([])
  const [showLibrary, setShowLibrary] = useState(false)

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
            onWsState: setWsState,
            onIndicators: setIndicators,
            onSymbolLoaded: (info) => {
              setActive({ symbol: info.symbol, exchange: info.exchange })
              setIntervalValue(info.interval)
            },
            onError: (message) => setStatus(message),
          },
        })
        controllerRef.current = controller
        setReady(true)

        // Restore the auto-saved default layout (symbol/interval/indicators).
        let restored = false
        try {
          const layouts = await listLayouts()
          const saved = layouts.find((l) => l.name === LAYOUT_NAME)
          if (saved) {
            layoutIdRef.current = saved.id
            if (saved.symbol && saved.exchange) {
              await controller.load(saved.symbol, saved.exchange, saved.timeframe || '5m')
              restored = true
            }
            for (const item of saved.layout?.indicators ?? []) {
              await controller.addIndicator(item.definitionId, item.inputs).catch(() => undefined)
            }
          }
        } catch {
          /* layouts API unavailable — fall through to default */
        }
        if (!restored) {
          await controller.load(DEFAULT_SYMBOL.symbol, DEFAULT_SYMBOL.exchange, '5m')
        }
      } catch (err) {
        if (alive) setStatus(err instanceof Error ? err.message : 'failed to start workspace')
      }
    })()
    return () => {
      alive = false
      if (saveTimer.current) clearTimeout(saveTimer.current)
      controller?.destroy()
      controllerRef.current = null
    }
  }, [])

  useEffect(() => {
    controllerRef.current?.setTheme()
  }, [mode])

  // Debounced auto-save of the default layout.
  useEffect(() => {
    if (!ready) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      const controller = controllerRef.current
      if (!controller) return
      const state: ChartLayoutState = { indicators: controller.indicators.snapshot() }
      const meta = controller.current
      void (async () => {
        try {
          if (layoutIdRef.current) {
            await updateLayout(layoutIdRef.current, {
              symbol: meta.symbol,
              exchange: meta.exchange,
              timeframe: meta.interval,
              layout: state,
            })
          } else {
            const created = await createLayout({
              name: LAYOUT_NAME,
              symbol: meta.symbol,
              exchange: meta.exchange,
              timeframe: meta.interval,
              layout: state,
            })
            if (created) layoutIdRef.current = created.id
          }
        } catch {
          /* persistence is best-effort */
        }
      })()
    }, 1200)
  }, [ready, indicators, active, interval])

  const doSearch = useCallback(async (q: string) => {
    setQuery(q)
    if (q.length < 2) {
      setResults([])
      return
    }
    const rows = await controllerRef.current?.search(q)
    setResults(rows ?? [])
  }, [])

  const pick = useCallback(
    async (row: SearchRow) => {
      setResults([])
      setQuery('')
      await controllerRef.current?.load(row.symbol, row.exchange, interval)
    },
    [interval]
  )

  const changeInterval = useCallback(
    async (iv: string) => {
      setIntervalValue(iv)
      await controllerRef.current?.load(active.symbol, active.exchange, iv)
    },
    [active]
  )

  const addIndicator = useCallback(async (definitionId: string) => {
    setShowLibrary(false)
    await controllerRef.current?.addIndicator(definitionId)
  }, [])

  const manifest = controllerRef.current?.manifest ?? []

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col bg-background text-foreground">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="relative">
          <input
            value={query}
            onChange={(e) => void doSearch(e.target.value)}
            placeholder={`${active.symbol} (${active.exchange})`}
            className="h-8 w-56 rounded border border-border bg-card px-2 text-sm outline-none focus:border-primary"
          />
          {results.length > 0 && (
            <div className="absolute z-50 mt-1 max-h-72 w-72 overflow-auto rounded border border-border bg-card shadow-lg">
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
        <div className="relative">
          <button
            type="button"
            onClick={() => setShowLibrary((v) => !v)}
            className="h-8 rounded bg-card px-3 text-sm font-medium hover:bg-accent"
          >
            + Indicators
          </button>
          {showLibrary && (
            <div className="absolute z-50 mt-1 max-h-80 w-72 overflow-auto rounded border border-border bg-card shadow-lg">
              {manifest.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => void addIndicator(m.id)}
                  className="flex w-full items-center justify-between px-2 py-1.5 text-left text-sm hover:bg-accent"
                >
                  <span>{m.name}</span>
                  <span className="text-xs uppercase text-muted-foreground">{m.category}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <Link
          to="/charts/editor"
          className="h-8 rounded bg-card px-3 text-sm font-medium leading-8 hover:bg-accent"
          title="Write a custom OpenScript indicator"
        >
          ƒx Editor
        </Link>
        <div className="flex flex-wrap items-center gap-1">
          {indicators.map((ind) => (
            <span
              key={ind.instanceId}
              title={ind.error}
              className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${
                ind.error ? 'bg-destructive/20 text-destructive' : 'bg-accent'
              }`}
            >
              <button type="button" onClick={() => setSettingsFor(ind)} className="hover:underline">
                {ind.name}
              </button>
              <button
                type="button"
                onClick={() => void controllerRef.current?.removeIndicator(ind.instanceId)}
                className="ml-1 font-bold opacity-60 hover:opacity-100"
                aria-label={`Remove ${ind.name}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} className="absolute inset-0" />
        {noApiKey && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
            Generate an API key at /apikey to use the chart workspace.
          </div>
        )}
        {!ready && !noApiKey && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
            {status}
          </div>
        )}
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between border-t border-border px-3 py-1 text-xs text-muted-foreground">
        <span>{status}</span>
        <span className={wsState === 'open' ? 'text-green-500' : ''}>ws: {wsState}</span>
      </div>

      <IndicatorSettingsDialog
        instance={settingsFor}
        manifest={manifest}
        onSave={(instanceId, inputs) =>
          controllerRef.current?.updateIndicatorInputs(instanceId, inputs)
        }
        onClose={() => setSettingsFor(null)}
      />
    </div>
  )
}
