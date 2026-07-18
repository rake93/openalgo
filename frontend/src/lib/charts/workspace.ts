/**
 * Framework-agnostic controller for the /charts indicator workspace.
 *
 * Owns the openalgo-charts instance (price + volume), the OpenAlgo REST/WS
 * feeds with a CandleBuilder (one WS per workspace, doc §14), and delegates
 * all indicator lifecycle to the shared IndicatorHost (same engine worker and
 * renderer as the /trading panes).
 *
 * Pane model: 0 = price, 1 = volume, 2+ = one pane per sub-pane indicator.
 * openalgo-charts cannot remove panes, so removing a pane-owning indicator
 * triggers a chart rebuild from current state.
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
import type { IRProgram } from '@openalgo/indicator-engine'
import { IndicatorHost, type IndicatorInstance } from './indicator-host'

export type { IndicatorInstance } from './indicator-host'

export interface WorkspaceCallbacks {
  onStatus(text: string): void
  onWsState(state: string): void
  onIndicators(list: IndicatorInstance[]): void
  onSymbolLoaded(info: { symbol: string; exchange: string; interval: string; bars: number }): void
  onError(message: string): void
}

export interface WorkspaceOptions {
  apiKey: string
  wsUrl: string
  container: HTMLElement
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

export class ChartWorkspaceController {
  private readonly opts: WorkspaceOptions
  readonly indicators: IndicatorHost

  private chart: ReturnType<typeof createChart> | null = null
  private price: SeriesApi | null = null
  private volume: SeriesApi | null = null
  private rest: OpenAlgoDataFeed
  private ws: OpenAlgoWsFeed | null = null
  private builder: CandleBuilder | null = null
  private offLtp: (() => void) | null = null

  private bars: Bar[] = []
  private symbol = ''
  private exchange = ''
  private interval = '5m'
  private destroyed = false
  private previewId: string | null = null

  get manifest() {
    return this.indicators.manifest
  }

  get current() {
    return { symbol: this.symbol, exchange: this.exchange, interval: this.interval }
  }

  constructor(opts: WorkspaceOptions) {
    this.opts = opts
    this.rest = new OpenAlgoDataFeed({ baseUrl: '', apiKey: opts.apiKey })
    this.indicators = new IndicatorHost({
      onIndicators: (list) => opts.callbacks.onIndicators(list),
      onError: (message) => opts.callbacks.onError(message),
    })
    this.initChart()
    this.initWs()
  }

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
    this.indicators.attachChart({ chart: this.chart, anchorSeries: this.price, basePane: 2 })
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
    this.price?.setData(this.bars)
    this.setVolumeData()
    this.chart?.fitContent()

    const sec = intervalToSeconds(nextInterval)
    this.builder = sec ? new CandleBuilder({ intervalSec: sec, volumeMode: 'ltq-sum' }) : null
    const last = this.bars[this.bars.length - 1]
    if (this.builder && last) {
      this.builder.seed(last)
    }
    this.ws?.subscribe('LTP', symbol, exchange)

    await this.indicators.setDataset(this.bars, { symbol, exchange, interval: nextInterval })

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
    } else if (this.bars.length) {
      this.bars[this.bars.length - 1] = bar
    }
    this.price?.update(bar)
    this.volume?.update({ time: bar.time, open: 0, high: bar.volume || 0, low: 0, close: bar.volume || 0 })
    this.indicators.onBar(bar, isNew)
  }

  /* ── indicators (delegated to the shared host) ─────────────────────── */

  async addIndicator(definitionId: string, inputs?: Record<string, unknown>): Promise<void> {
    await this.indicators.add(definitionId, inputs)
  }

  async updateIndicatorInputs(instanceId: string, inputs: Record<string, unknown>): Promise<void> {
    await this.indicators.setInputs(instanceId, inputs)
  }

  async removeIndicator(instanceId: string): Promise<void> {
    const needsRebuild = await this.indicators.remove(instanceId)
    if (needsRebuild) {
      this.rebuildChart()
      await this.indicators.recreateSessions()
    }
  }

  /**
   * Live preview of the OpenScript editor: replace the single preview session
   * with the freshly-compiled IR. Only one preview indicator exists at a time.
   */
  async previewIr(ir: IRProgram): Promise<void> {
    await this.clearPreview()
    this.previewId = await this.indicators.addIr(ir)
  }

  async clearPreview(): Promise<void> {
    if (!this.previewId) return
    const id = this.previewId
    this.previewId = null
    const needsRebuild = await this.indicators.remove(id)
    if (needsRebuild) {
      this.rebuildChart()
      await this.indicators.recreateSessions()
    }
  }

  private rebuildChart(): void {
    this.chart?.destroy()
    this.initChart()
    this.price?.setData(this.bars)
    this.setVolumeData()
    this.chart?.fitContent()
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
    this.offLtp?.()
    this.indicators.dispose()
    this.ws?.close()
    this.chart?.destroy()
    this.chart = null
  }
}
