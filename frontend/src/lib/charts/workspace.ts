/**
 * Framework-agnostic controller for the /charts indicator workspace.
 *
 * Owns the openalgo-charts instance (price + volume), the OpenAlgo REST/WS
 * feeds with a CandleBuilder (one WS per workspace, doc §14), the shared
 * engine worker client, and one OpenAlgoChartsRenderer per active indicator
 * instance. React drives it through methods and receives updates through the
 * callback bag — same pattern as lib/trading/terminal.ts.
 *
 * Pane model: 0 = price, 1 = volume, 2+ = one pane per sub-pane indicator
 * instance. openalgo-charts cannot remove panes, so removing a pane-owning
 * indicator triggers a full chart rebuild from current state.
 */

import {
  CandleBuilder,
  createChart,
  darkTheme,
  lightTheme,
  intervalToSeconds,
  OpenAlgoDataFeed,
  OpenAlgoWsFeed,
  type Bar,
  type LtpEvent,
  type SeriesApi,
} from 'openalgo-charts'
import type { EngineWorkerClient } from '@openalgo/indicator-engine/worker-client'
import type { IndicatorManifestEntry, OHLCVBar } from '@openalgo/indicator-engine'
import { datasetFromBars, toDatasetBuffers, datasetKey } from '@openalgo/indicator-engine'
import { registryManifest } from '@openalgo/indicator-engine/registry'
import { OpenAlgoChartsRenderer } from '@openalgo/indicator-engine/render/openalgo-charts'

export interface IndicatorInstance {
  instanceId: string
  definitionId: string
  name: string
  overlay: boolean
  inputs: Record<string, unknown>
  /** Chart pane index for own-pane indicators (2+). */
  pane?: number
  error?: string
}

export interface WorkspaceCallbacks {
  onStatus(text: string): void
  onWsState(state: string): void
  onIndicators(list: IndicatorInstance[]): void
  onSymbolLoaded(info: { symbol: string; exchange: string; interval: string; bars: number }): void
  onError(message: string): void
  onPerf(info: { sessionId: string; computeMs: number }): void
}

export interface WorkspaceOptions {
  apiKey: string
  wsUrl: string
  container: HTMLElement
  engine: EngineWorkerClient
  isDark: () => boolean
  callbacks: WorkspaceCallbacks
}

const LOOKBACK_DAYS: Record<string, number> = {
  '1m': 5,
  '3m': 10,
  '5m': 15,
  '10m': 30,
  '15m': 45,
  '30m': 90,
  '1h': 180,
  D: 365 * 2,
  W: 365 * 5,
  M: 365 * 10,
}

function lookbackDays(interval: string): number {
  return LOOKBACK_DAYS[interval] ?? 30
}

let instanceSeq = 0

export class ChartWorkspaceController {
  readonly manifest: readonly IndicatorManifestEntry[] = registryManifest

  private chart: ReturnType<typeof createChart> | null = null
  private price: SeriesApi | null = null
  private volume: SeriesApi | null = null
  private rest: OpenAlgoDataFeed
  private ws: OpenAlgoWsFeed | null = null
  private builder: CandleBuilder | null = null
  private offLtp: (() => void) | null = null

  private bars: Bar[] = []
  private times: Float64Array = new Float64Array(0)
  private symbol = ''
  private exchange = ''
  private interval = '5m'
  private currentKey = ''

  private readonly instances = new Map<string, IndicatorInstance>()
  private readonly renderers = new Map<string, OpenAlgoChartsRenderer>()
  private nextPane = 2
  private offOutputs: (() => void) | null = null
  private offErrors: (() => void) | null = null
  private destroyed = false

  private readonly opts: WorkspaceOptions

  constructor(opts: WorkspaceOptions) {
    this.opts = opts
    this.rest = new OpenAlgoDataFeed({ baseUrl: '', apiKey: opts.apiKey })
    this.offOutputs = opts.engine.onOutputs((e) => this.applyOutputs(e.sessionId, e.outputs, e.scope, e.perf.computeMs))
    this.offErrors = opts.engine.onError((e) => {
      if (e.sessionId) {
        const inst = this.instances.get(e.sessionId)
        if (inst) {
          inst.error = e.error.message
          this.emitIndicators()
        }
      }
      this.opts.callbacks.onError(`${e.error.code}: ${e.error.message}`)
    })
    this.initChart()
    this.initWs()
  }

  /* ── chart + feeds ─────────────────────────────────────────────────── */

  private initChart(): void {
    this.chart = createChart(this.opts.container, {
      theme: this.opts.isDark() ? darkTheme : lightTheme,
    })
    this.price = this.chart.addSeries('candlestick', {})
    this.volume = this.chart.addSeries('histogram', {
      paneIndex: 1,
      priceFormat: { type: 'volume' },
      style: { priceLineVisible: false, lastValueVisible: false },
    })
  }

  setTheme(): void {
    this.chart?.setTheme(this.opts.isDark() ? darkTheme : lightTheme)
  }

  private initWs(): void {
    this.ws = new OpenAlgoWsFeed({ url: this.opts.wsUrl, apiKey: this.opts.apiKey })
    this.ws.onState((s: string) => this.opts.callbacks.onWsState(s))
    this.offLtp = this.ws.onLtp((ev: LtpEvent) => this.onTick(ev))
    this.ws.connect()
  }

  async load(symbol: string, exchange: string, interval?: string): Promise<void> {
    if (this.destroyed) return
    const nextInterval = interval ?? this.interval
    // Unsubscribe previous symbol.
    if (this.ws && this.symbol) {
      try {
        this.ws.unsubscribe('LTP', this.symbol, this.exchange)
      } catch {
        /* not subscribed */
      }
    }
    this.symbol = symbol
    this.exchange = exchange
    this.interval = nextInterval
    this.opts.callbacks.onStatus(`Loading ${symbol} ${nextInterval}…`)

    const to = Math.floor(Date.now() / 1000)
    const from = to - lookbackDays(nextInterval) * 86_400
    this.bars = await this.rest.getBars({ symbol, exchange, interval: nextInterval, from, to })
    this.times = Float64Array.from(this.bars, (b) => b.time)
    this.price?.setData(this.bars)
    this.setVolumeData()
    this.chart?.fitContent()

    // Candle builder seeded from the last historical bar.
    const sec = intervalToSeconds(nextInterval)
    this.builder = sec ? new CandleBuilder({ intervalSec: sec, volumeMode: 'ltq-sum' }) : null
    const last = this.bars[this.bars.length - 1]
    if (this.builder && last) {
      this.builder.seed(last)
    }
    this.ws?.subscribe('LTP', symbol, exchange)

    // Engine dataset + session rebuild on the new key.
    const oldKey = this.currentKey
    this.currentKey = datasetKey(exchange, symbol, nextInterval)
    const dataset = datasetFromBars(this.bars as OHLCVBar[])
    await this.opts.engine.setDataset(this.currentKey, toDatasetBuffers(dataset))
    if (oldKey && oldKey !== this.currentKey) {
      await this.opts.engine.disposeDataset(oldKey)
    }
    await this.recreateSessions()

    this.opts.callbacks.onSymbolLoaded({ symbol, exchange, interval: nextInterval, bars: this.bars.length })
    this.opts.callbacks.onStatus(`${symbol} ${nextInterval} — ${this.bars.length} bars`)
  }

  private setVolumeData(): void {
    this.volume?.setData(
      this.bars.map((b) => ({ time: b.time, open: 0, high: b.volume || 0, low: 0, close: b.volume || 0 }))
    )
  }

  private onTick(ev: LtpEvent): void {
    if (this.destroyed || !this.builder) return
    if (ev.symbol !== this.symbol || ev.exchange !== this.exchange) return
    const tick: { time: number; price: number; ltq?: number } = { time: ev.timeSec, price: ev.ltp }
    if (ev.ltq !== undefined) {
      tick.ltq = ev.ltq
    }
    const result = this.builder.onTick(tick)
    if (!result) return
    const { bar, isNew } = result
    if (isNew) {
      this.bars.push(bar)
      const grown = new Float64Array(this.times.length + 1)
      grown.set(this.times)
      grown[this.times.length] = bar.time
      this.times = grown
    } else if (this.bars.length) {
      this.bars[this.bars.length - 1] = bar
    }
    this.price?.update(bar)
    this.volume?.update({ time: bar.time, open: 0, high: bar.volume || 0, low: 0, close: bar.volume || 0 })
    void this.opts.engine.updateBar(
      this.currentKey,
      { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume ?? 0 },
      isNew
    )
  }

  /* ── indicators ────────────────────────────────────────────────────── */

  async addIndicator(definitionId: string, inputs?: Record<string, unknown>): Promise<string> {
    const entry = this.manifest.find((m) => m.id === definitionId)
    if (!entry) {
      throw new Error(`unknown indicator: ${definitionId}`)
    }
    instanceSeq += 1
    const instanceId = `ind${instanceSeq}`
    const merged = {
      ...Object.fromEntries(entry.inputs.map((i) => [i.id, i.defaultValue])),
      ...inputs,
    }
    const instance: IndicatorInstance = {
      instanceId,
      definitionId,
      name: entry.shortName,
      overlay: entry.overlay,
      inputs: merged,
    }
    if (!entry.overlay) {
      instance.pane = this.nextPane
      this.nextPane += 1
    }
    this.instances.set(instanceId, instance)
    this.createRenderer(instance)
    this.emitIndicators()
    await this.createSession(instance)
    return instanceId
  }

  async updateIndicatorInputs(instanceId: string, inputs: Record<string, unknown>): Promise<void> {
    const instance = this.instances.get(instanceId)
    if (!instance) return
    instance.inputs = { ...instance.inputs, ...inputs }
    delete instance.error
    this.emitIndicators()
    const result = await this.opts.engine.setInputs(instanceId, instance.inputs)
    this.applyOutputs(instanceId, result.outputs, 'full', result.perf.computeMs)
  }

  async removeIndicator(instanceId: string): Promise<void> {
    const instance = this.instances.get(instanceId)
    if (!instance) return
    this.instances.delete(instanceId)
    await this.opts.engine.disposeSession(instanceId).catch(() => undefined)
    const renderer = this.renderers.get(instanceId)
    this.renderers.delete(instanceId)
    if (instance.pane === undefined) {
      renderer?.dispose()
      this.emitIndicators()
      return
    }
    // Own-pane indicator: openalgo-charts cannot remove panes — rebuild.
    await this.rebuildChart()
    this.emitIndicators()
  }

  private async rebuildChart(): Promise<void> {
    for (const renderer of this.renderers.values()) {
      renderer.dispose()
    }
    this.renderers.clear()
    this.chart?.destroy()
    this.nextPane = 2
    this.initChart()
    this.price?.setData(this.bars)
    this.setVolumeData()
    this.chart?.fitContent()
    for (const instance of this.instances.values()) {
      if (instance.pane !== undefined) {
        instance.pane = this.nextPane
        this.nextPane += 1
      }
      this.createRenderer(instance)
    }
    await this.recreateSessions()
  }

  private createRenderer(instance: IndicatorInstance): void {
    if (!this.chart || !this.price) return
    const chart = this.chart
    const renderer = new OpenAlgoChartsRenderer(
      {
        chart,
        getTimes: () => this.times,
        resolvePane: (pane) => (pane === 'overlay' ? 0 : (instance.pane ?? 0)),
        anchorSeries: this.price,
      },
      instance.instanceId
    )
    this.renderers.set(instance.instanceId, renderer)
  }

  private async recreateSessions(): Promise<void> {
    if (!this.currentKey) return
    for (const instance of this.instances.values()) {
      await this.createSession(instance)
    }
  }

  private async createSession(instance: IndicatorInstance): Promise<void> {
    try {
      await this.opts.engine.disposeSession(instance.instanceId).catch(() => undefined)
      const result = await this.opts.engine.createSession({
        sessionId: instance.instanceId,
        datasetKey: this.currentKey,
        program: { kind: 'builtin', id: instance.definitionId },
        inputs: instance.inputs,
        mode: 'realtime',
        meta: { symbol: this.symbol, exchange: this.exchange, timeframe: this.interval },
      })
      this.applyOutputs(instance.instanceId, result.outputs, 'full', result.perf.computeMs)
    } catch (err) {
      instance.error = err instanceof Error ? err.message : String(err)
      this.emitIndicators()
    }
  }

  private applyOutputs(
    sessionId: string,
    outputs: import('@openalgo/indicator-engine').IndicatorOutput[],
    scope: 'full' | 'update',
    computeMs: number
  ): void {
    const renderer = this.renderers.get(sessionId)
    if (!renderer) return
    for (const output of outputs) {
      if (scope === 'full') {
        renderer.replace(output)
      } else {
        renderer.update(output, Math.max(0, this.bars.length - 1))
      }
    }
    this.opts.callbacks.onPerf({ sessionId, computeMs })
  }

  private emitIndicators(): void {
    this.opts.callbacks.onIndicators([...this.instances.values()].map((i) => ({ ...i })))
  }

  /* ── search + lifecycle ────────────────────────────────────────────── */

  async search(query: string): Promise<{ symbol: string; exchange: string; name?: string }[]> {
    try {
      const res = await fetch('/api/v1/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apikey: this.opts.apiKey, query }),
      })
      const j = (await res.json()) as { data?: { symbol: string; exchange: string; name?: string }[] }
      return (j.data || []).slice(0, 30)
    } catch {
      return []
    }
  }

  destroy(): void {
    this.destroyed = true
    this.offOutputs?.()
    this.offErrors?.()
    this.offLtp?.()
    for (const instanceId of [...this.instances.keys()]) {
      void this.opts.engine.disposeSession(instanceId).catch(() => undefined)
    }
    if (this.currentKey) {
      void this.opts.engine.disposeDataset(this.currentKey).catch(() => undefined)
    }
    for (const renderer of this.renderers.values()) {
      renderer.dispose()
    }
    this.renderers.clear()
    this.ws?.close()
    this.chart?.destroy()
    this.chart = null
  }
}
