/**
 * /charts/editor — OpenScript indicator editor with live chart preview.
 *
 * A CodeMirror pane (engine-linted OpenScript) on the left; the shared
 * chart workspace on the right. Every edit is debounced, compiled in-browser
 * to IR, and previewed as a single `{kind:'ir'}` session on the chart — so
 * the values you see equal what the server (and Python strategies) compute.
 */

import { compile } from '@openalgo/indicator-engine/compiler'
import type { Diagnostic } from '@openalgo/indicator-engine'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { OpenScriptEditor } from '@/components/charts/OpenScriptEditor'
import { ChartWorkspaceController } from '@/lib/charts/workspace'
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
  const containerRef = useRef<HTMLDivElement | null>(null)
  const controllerRef = useRef<ChartWorkspaceController | null>(null)
  const compileTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const readyRef = useRef(false)
  const mode = useThemeStore((s) => s.mode)

  const [source, setSource] = useState(SAMPLE)
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([])
  const [status, setStatus] = useState('Connecting…')
  const [noApiKey, setNoApiKey] = useState(false)
  const [interval, setIntervalValue] = useState('5m')
  const [active, setActive] = useState(DEFAULT_SYMBOL)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchRow[]>([])

  const compileAndPreview = useCallback((src: string) => {
    const result = compile(src)
    setDiagnostics(result.diagnostics)
    const controller = controllerRef.current
    if (result.ir && controller && readyRef.current) {
      void controller.previewIr(result.ir).catch(() => undefined)
    }
  }, [])

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
        await controller.load(DEFAULT_SYMBOL.symbol, DEFAULT_SYMBOL.exchange, '5m')
        readyRef.current = true
        compileAndPreview(source)
      } catch (err) {
        if (alive) setStatus(err instanceof Error ? err.message : 'failed to start editor')
      }
    })()
    return () => {
      alive = false
      if (compileTimer.current) clearTimeout(compileTimer.current)
      controller?.destroy()
      controllerRef.current = null
      readyRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    controllerRef.current?.setTheme()
  }, [mode])

  const onSourceChange = useCallback(
    (next: string) => {
      setSource(next)
      if (compileTimer.current) clearTimeout(compileTimer.current)
      compileTimer.current = setTimeout(() => compileAndPreview(next), 400)
    },
    [compileAndPreview]
  )

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

  const errorCount = diagnostics.filter((d) => d.severity === 'error').length

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col bg-background text-foreground">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <Link
          to="/charts"
          className="flex h-8 items-center rounded px-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          ‹ Charts
        </Link>
        <span className="text-sm font-semibold">ƒx OpenScript Editor</span>
        <div className="relative">
          <input
            value={query}
            onChange={(e) => void doSearch(e.target.value)}
            placeholder={`${active.symbol} (${active.exchange})`}
            className="h-8 w-52 rounded border border-border bg-card px-2 text-sm outline-none focus:border-primary"
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
        <div className="ml-auto text-xs">
          {errorCount > 0 ? (
            <span className="rounded bg-destructive/20 px-2 py-1 font-medium text-destructive">
              {errorCount} error{errorCount === 1 ? '' : 's'}
            </span>
          ) : (
            <span className="rounded bg-green-500/15 px-2 py-1 font-medium text-green-500">Compiled ✓</span>
          )}
        </div>
      </div>

      {/* Split: editor | preview */}
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[42%] min-w-[360px] flex-col border-r border-border">
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
                    <span className={d.severity === 'error' ? 'text-destructive' : 'text-yellow-500'}>
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
          {noApiKey && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
              Generate an API key at /apikey to use the editor preview.
            </div>
          )}
          {!readyRef.current && !noApiKey && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
              {status}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
