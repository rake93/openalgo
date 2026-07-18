/**
 * IndicatorHost — chart-agnostic indicator instance manager.
 *
 * Owns the engine-worker sessions and one OpenAlgoChartsRenderer per active
 * indicator for a single chart surface. Both the /charts workspace and the
 * /trading terminal drive it: the host survives chart rebuilds (the chart
 * binding is re-attached after every rebuild, since openalgo-charts cannot
 * remove panes) and dataset switches (symbol/interval changes).
 */

import type { Chart, Bar, SeriesApi } from 'openalgo-charts'
import type {
  IndicatorManifestEntry,
  IndicatorOutput,
  OHLCVBar,
} from '@openalgo/indicator-engine'
import { datasetFromBars, toDatasetBuffers, datasetKey } from '@openalgo/indicator-engine'
import { registryManifest } from '@openalgo/indicator-engine/registry'
import { OpenAlgoChartsRenderer } from '@openalgo/indicator-engine/render/openalgo-charts'
import type { EngineWorkerClient } from '@openalgo/indicator-engine/worker-client'
import { getEngine } from './engine'

export interface IndicatorInstance {
  instanceId: string
  definitionId: string
  name: string
  overlay: boolean
  inputs: Record<string, unknown>
  pane?: number
  error?: string
}

export interface IndicatorHostCallbacks {
  onIndicators(list: IndicatorInstance[]): void
  onError(message: string): void
}

export interface ChartBinding {
  chart: Chart
  anchorSeries: SeriesApi
  /** First pane index available for own-pane indicators (2 on both routes). */
  basePane: number
}

let hostSeq = 0

export class IndicatorHost {
  private readonly cb: IndicatorHostCallbacks
  private readonly hostId: string
  private engine: EngineWorkerClient | null = null
  private binding: ChartBinding | null = null

  private readonly instances = new Map<string, IndicatorInstance>()
  private readonly renderers = new Map<string, OpenAlgoChartsRenderer>()
  private times: Float64Array = new Float64Array(0)
  private barCount = 0
  private currentKey = ''
  private meta = { symbol: '', exchange: '', timeframe: '' }
  private nextPane = 2
  private offOutputs: (() => void) | null = null
  private offErrors: (() => void) | null = null
  private disposed = false
  private seq = 0

  readonly manifest: readonly IndicatorManifestEntry[] = registryManifest

  constructor(cb: IndicatorHostCallbacks) {
    this.cb = cb
    hostSeq += 1
    this.hostId = `h${hostSeq}`
  }

  list(): IndicatorInstance[] {
    return [...this.instances.values()].map((i) => ({ ...i }))
  }

  /** Serializable state for persistence (localStorage / layouts API). */
  snapshot(): { definitionId: string; inputs: Record<string, unknown> }[] {
    return this.list().map((i) => ({ definitionId: i.definitionId, inputs: i.inputs }))
  }

  private async ensureEngine(): Promise<EngineWorkerClient> {
    if (!this.engine) {
      this.engine = await getEngine()
      this.offOutputs = this.engine.onOutputs((e) => this.applyOutputs(e.sessionId, e.outputs, e.scope))
      this.offErrors = this.engine.onError((e) => {
        if (e.sessionId) {
          const inst = this.instances.get(e.sessionId)
          if (inst) {
            inst.error = e.error.message
            this.emit()
          }
        }
        this.cb.onError(`${e.error.code}: ${e.error.message}`)
      })
    }
    return this.engine
  }

  /** (Re)bind to a chart after creation or rebuild; recreates all renderers. */
  attachChart(binding: ChartBinding): void {
    for (const renderer of this.renderers.values()) {
      renderer.dispose()
    }
    this.renderers.clear()
    this.binding = binding
    this.nextPane = binding.basePane
    for (const instance of this.instances.values()) {
      if (instance.pane !== undefined) {
        instance.pane = this.nextPane
        this.nextPane += 1
      }
      this.createRenderer(instance)
    }
  }

  /** Load/replace the dataset and recompute every active indicator. */
  async setDataset(bars: readonly Bar[], meta: { symbol: string; exchange: string; interval: string }): Promise<void> {
    if (this.disposed) return
    const engine = await this.ensureEngine()
    this.times = Float64Array.from(bars, (b) => b.time)
    this.barCount = bars.length
    this.meta = { symbol: meta.symbol, exchange: meta.exchange, timeframe: meta.interval }
    const oldKey = this.currentKey
    this.currentKey = `${this.hostId}:${datasetKey(meta.exchange, meta.symbol, meta.interval)}`
    const rows: OHLCVBar[] = bars.map((b) => ({
      time: b.time,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
      volume: b.volume ?? 0,
    }))
    await engine.setDataset(this.currentKey, toDatasetBuffers(datasetFromBars(rows)))
    if (oldKey && oldKey !== this.currentKey) {
      await engine.disposeDataset(oldKey).catch(() => undefined)
    }
    await this.recreateSessions()
  }

  /** Live bar event from the host's candle builder. */
  onBar(bar: Bar, isNew: boolean): void {
    if (this.disposed || !this.currentKey || !this.engine) return
    if (isNew) {
      const grown = new Float64Array(this.times.length + 1)
      grown.set(this.times)
      grown[this.times.length] = bar.time
      this.times = grown
      this.barCount += 1
    }
    void this.engine.updateBar(
      this.currentKey,
      { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume ?? 0 },
      isNew
    )
  }

  /** Add an indicator; returns whether a chart rebuild is required (never on add). */
  async add(definitionId: string, inputs?: Record<string, unknown>): Promise<string> {
    const entry = this.manifest.find((m) => m.id === definitionId)
    if (!entry) {
      throw new Error(`unknown indicator: ${definitionId}`)
    }
    this.seq += 1
    const instanceId = `${this.hostId}i${this.seq}`
    const instance: IndicatorInstance = {
      instanceId,
      definitionId,
      name: entry.shortName,
      overlay: entry.overlay,
      inputs: { ...Object.fromEntries(entry.inputs.map((i) => [i.id, i.defaultValue])), ...inputs },
    }
    if (!entry.overlay) {
      instance.pane = this.nextPane
      this.nextPane += 1
    }
    this.instances.set(instanceId, instance)
    this.createRenderer(instance)
    this.emit()
    if (this.currentKey) {
      await this.createSession(instance)
    }
    return instanceId
  }

  async setInputs(instanceId: string, inputs: Record<string, unknown>): Promise<void> {
    const instance = this.instances.get(instanceId)
    if (!instance || !this.engine) return
    instance.inputs = { ...instance.inputs, ...inputs }
    delete instance.error
    this.emit()
    const result = await this.engine.setInputs(instanceId, instance.inputs)
    this.applyOutputs(instanceId, result.outputs, 'full')
  }

  /**
   * Remove an indicator. Returns true when the caller must rebuild the chart
   * (own-pane instance — openalgo-charts panes are not removable) and then
   * call attachChart + recreateSessions (or use the host's rebuild helper).
   */
  async remove(instanceId: string): Promise<boolean> {
    const instance = this.instances.get(instanceId)
    if (!instance) return false
    this.instances.delete(instanceId)
    await this.engine?.disposeSession(instanceId).catch(() => undefined)
    const renderer = this.renderers.get(instanceId)
    this.renderers.delete(instanceId)
    if (instance.pane === undefined) {
      renderer?.dispose()
      this.emit()
      return false
    }
    this.emit()
    return true
  }

  async recreateSessions(): Promise<void> {
    if (!this.currentKey) return
    for (const instance of this.instances.values()) {
      await this.createSession(instance)
    }
  }

  dispose(): void {
    this.disposed = true
    this.offOutputs?.()
    this.offErrors?.()
    for (const instanceId of [...this.instances.keys()]) {
      void this.engine?.disposeSession(instanceId).catch(() => undefined)
    }
    if (this.currentKey) {
      void this.engine?.disposeDataset(this.currentKey).catch(() => undefined)
    }
    for (const renderer of this.renderers.values()) {
      renderer.dispose()
    }
    this.renderers.clear()
    this.instances.clear()
  }

  private createRenderer(instance: IndicatorInstance): void {
    const binding = this.binding
    if (!binding) return
    this.renderers.set(
      instance.instanceId,
      new OpenAlgoChartsRenderer(
        {
          chart: binding.chart,
          getTimes: () => this.times,
          resolvePane: (pane) => (pane === 'overlay' ? 0 : (instance.pane ?? 0)),
          anchorSeries: binding.anchorSeries,
        },
        instance.instanceId
      )
    )
  }

  private async createSession(instance: IndicatorInstance): Promise<void> {
    try {
      const engine = await this.ensureEngine()
      await engine.disposeSession(instance.instanceId).catch(() => undefined)
      const result = await engine.createSession({
        sessionId: instance.instanceId,
        datasetKey: this.currentKey,
        program: { kind: 'builtin', id: instance.definitionId },
        inputs: instance.inputs,
        mode: 'realtime',
        meta: this.meta,
      })
      this.applyOutputs(instance.instanceId, result.outputs, 'full')
    } catch (err) {
      instance.error = err instanceof Error ? err.message : String(err)
      this.emit()
    }
  }

  private applyOutputs(sessionId: string, outputs: IndicatorOutput[], scope: 'full' | 'update'): void {
    const renderer = this.renderers.get(sessionId)
    if (!renderer) return
    for (const output of outputs) {
      if (scope === 'full') {
        renderer.replace(output)
      } else {
        renderer.update(output, Math.max(0, this.barCount - 1))
      }
    }
  }

  private emit(): void {
    this.cb.onIndicators(this.list())
  }
}
