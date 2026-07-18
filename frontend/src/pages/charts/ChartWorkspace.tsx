/**
 * /charts — production chart workspace with engine-backed indicators.
 * Phase 0/1: price + volume, live candles, add/edit/remove built-in
 * indicators computed in the worker on the Rust/WASM core.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useThemeStore } from '@/stores/themeStore'
import { getEngine } from '@/lib/charts/engine'
import {
  ChartWorkspaceController,
  type IndicatorInstance,
} from '@/lib/charts/workspace'

const INTERVALS = ['1m', '3m', '5m', '15m', '30m', '1h', 'D', 'W'] as const
const DEFAULT_SYMBOL = { symbol: 'NIFTY', exchange: 'NSE_INDEX' }

interface SearchRow {
  symbol: string
  exchange: string
  name?: string
}

export default function ChartWorkspace() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const controllerRef = useRef<ChartWorkspaceController | null>(null)
  const mode = useThemeStore((s) => s.mode)

  const [ready, setReady] = useState(false)
  const [noApiKey, setNoApiKey] = useState(false)
  const [status, setStatus] = useState('Connecting…')
  const [wsState, setWsState] = useState('idle')
  const [interval, setIntervalValue] = useState('5m')
  const [active, setActive] = useState(DEFAULT_SYMBOL)
  const [indicators, setIndicators] = useState<IndicatorInstance[]>([])
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchRow[]>([])
  const [perfMs, setPerfMs] = useState<number | null>(null)
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
        const engine = await getEngine()
        if (!alive || !containerRef.current) return
        controller = new ChartWorkspaceController({
          apiKey: keyRes.api_key,
          wsUrl: cfgRes.websocket_url || 'ws://127.0.0.1:8765',
          container: containerRef.current,
          engine,
          isDark: () => useThemeStore.getState().mode === 'dark',
          callbacks: {
            onStatus: setStatus,
            onWsState: setWsState,
            onIndicators: setIndicators,
            onSymbolLoaded: (info) =>
              setActive({ symbol: info.symbol, exchange: info.exchange }),
            onError: (message) => setStatus(message),
            onPerf: (p) => setPerfMs(p.computeMs),
          },
        })
        controllerRef.current = controller
        setReady(true)
        await controller.load(DEFAULT_SYMBOL.symbol, DEFAULT_SYMBOL.exchange, '5m')
      } catch (err) {
        if (alive) setStatus(err instanceof Error ? err.message : 'failed to start workspace')
      }
    })()
    return () => {
      alive = false
      controller?.destroy()
      controllerRef.current = null
    }
  }, [])

  useEffect(() => {
    controllerRef.current?.setTheme()
  }, [mode])

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
        {/* Active indicator chips */}
        <div className="flex flex-wrap items-center gap-1">
          {indicators.map((ind) => (
            <span
              key={ind.instanceId}
              title={ind.error}
              className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${
                ind.error ? 'bg-destructive/20 text-destructive' : 'bg-accent'
              }`}
            >
              {ind.name}
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
        <span className="flex items-center gap-3">
          {perfMs !== null && <span>calc {perfMs.toFixed(1)} ms</span>}
          <span className={wsState === 'open' ? 'text-green-500' : ''}>ws: {wsState}</span>
        </span>
      </div>
    </div>
  )
}
