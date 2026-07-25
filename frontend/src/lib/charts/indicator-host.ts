/**
 * IndicatorHost — chart-agnostic indicator instance manager.
 *
 * Owns the engine-worker sessions and one OpenAlgoChartsRenderer per active
 * indicator for a single chart surface. Both the /charts workspace and the
 * /trading terminal drive it: the host survives chart rebuilds (the chart
 * binding is re-attached after every rebuild, since openalgo-charts cannot
 * remove panes) and dataset switches (symbol/interval changes).
 */

import type {
  IndicatorManifestEntry,
  IndicatorOutput,
  IRProgram,
  OHLCVBar,
} from '@openalgo/openscript'
import { datasetFromBars, datasetKey, toDatasetBuffers } from '@openalgo/openscript'
import { registryManifest } from '@openalgo/openscript/registry'
import { OpenAlgoChartsRenderer } from '@openalgo/openscript/render/openalgo-charts'
import type { EngineWorkerClient } from '@openalgo/openscript/worker-client'
import type { Bar, Chart, SeriesApi } from 'openalgo-charts'
import { getEngine } from './engine'

/** Per-output style override, applied on the main thread before rendering. */
export interface OutputStyleOverride {
  color?: string
  lineWidth?: number
  lineStyle?: 'solid' | 'dashed' | 'dotted'
  /** 0..1; folded into the color's alpha channel. */
  opacity?: number
  /** Default true; false removes the output's series (Visibility tab). */
  visible?: boolean
}

/** Style overrides keyed by IndicatorOutput.id. */
export type StyleOverrides = Record<string, OutputStyleOverride>

/** One indicator output's value at a bar index (crosshair data window). */
export interface DataWindowValue {
  id: string
  title: string
  value: number
  color: string
}

/** An active indicator's values at a bar index (crosshair data window). */
export interface DataWindowRow {
  instanceId: string
  name: string
  values: DataWindowValue[]
}

/** One ranged timeframe category (e.g. minutes 1–59) in the Visibility tab. */
export interface RangeVisibility {
  on: boolean
  min: number
  max: number
}

/**
 * Per-indicator timeframe visibility (Visibility tab) — the indicator renders
 * only when the current chart interval's category is enabled and its value is
 * within [min,max]. Undefined = always visible. Ticks/Ranges have no OpenAlgo
 * interval so they never gate the standard resolutions.
 */
export interface TimeframeVisibility {
  ticks: boolean
  seconds: RangeVisibility
  minutes: RangeVisibility
  hours: RangeVisibility
  days: RangeVisibility
  weeks: RangeVisibility
  months: RangeVisibility
  ranges: boolean
}

export const DEFAULT_TF_VISIBILITY: TimeframeVisibility = {
  ticks: true,
  seconds: { on: true, min: 1, max: 59 },
  minutes: { on: true, min: 1, max: 59 },
  hours: { on: true, min: 1, max: 24 },
  days: { on: true, min: 1, max: 366 },
  weeks: { on: true, min: 1, max: 52 },
  months: { on: true, min: 1, max: 12 },
  ranges: true,
}

type RangeCategory = 'seconds' | 'minutes' | 'hours' | 'days' | 'weeks' | 'months'

/** Parse an OpenAlgo interval string (e.g. '5m', '1h', 'D', 'W', 'M') to a
 *  ranged category + numeric value, or null when it maps to no category. */
function parseInterval(tf: string): { cat: RangeCategory; value: number } | null {
  const t = tf.trim()
  const sub = /^(\d+)(s|m|h)$/.exec(t)
  if (sub) {
    const n = Number(sub[1])
    if (sub[2] === 's') return { cat: 'seconds', value: n }
    if (sub[2] === 'm') return { cat: 'minutes', value: n }
    return { cat: 'hours', value: n }
  }
  const period = /^(\d+)?([DWM])$/.exec(t)
  if (period) {
    const n = period[1] ? Number(period[1]) : 1
    if (period[2] === 'D') return { cat: 'days', value: n }
    if (period[2] === 'W') return { cat: 'weeks', value: n }
    return { cat: 'months', value: n }
  }
  return null
}

export interface IndicatorInstance {
  instanceId: string
  definitionId: string
  name: string
  overlay: boolean
  inputs: Record<string, unknown>
  pane?: number
  error?: string
  /** Present for custom OpenScript indicators — runs as an IR session. */
  ir?: IRProgram
  /** Per-output color/width/opacity/visibility overrides (Style tab). */
  styleOverrides?: StyleOverrides
  /** Timeframe visibility (Visibility tab); undefined = always visible. */
  visibility?: TimeframeVisibility
  /** Legend eye toggle — every plot hidden without tearing the session down. */
  hidden?: boolean
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
  /** Last full outputs per session — lets a style change re-render with no worker recompute. */
  private readonly lastOutputs = new Map<string, IndicatorOutput[]>()
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

  /** Per-indicator output values at a bar index — feeds the crosshair data window. */
  valuesAtIndex(index: number): DataWindowRow[] {
    const rows: DataWindowRow[] = []
    for (const inst of this.instances.values()) {
      const outputs = this.lastOutputs.get(inst.instanceId)
      if (!outputs) continue
      const values: DataWindowValue[] = []
      for (const o of outputs) {
        const ov = inst.styleOverrides?.[o.id]
        if (ov?.visible === false) continue
        let value: number | undefined
        let color = ''
        if (o.kind === 'line' || o.kind === 'histogram') {
          if (index >= 0 && index < o.values.length) value = o.values[index]
          color = ov?.color ?? o.style.color
        } else if (o.kind === 'candle') {
          if (index >= 0 && index < o.close.length) value = o.close[index]
          color = ov?.color ?? o.style.upColor
        } else {
          continue
        }
        if (value === undefined || Number.isNaN(value)) continue
        values.push({ id: o.id, title: o.title, value, color })
      }
      if (values.length > 0) rows.push({ instanceId: inst.instanceId, name: inst.name, values })
    }
    return rows
  }

  /** Serializable state for persistence (localStorage / layouts API). */
  snapshot(): {
    definitionId: string
    inputs: Record<string, unknown>
    styleOverrides?: StyleOverrides
    visibility?: TimeframeVisibility
    hidden?: boolean
  }[] {
    return this.list().map((i) => {
      const item: {
        definitionId: string
        inputs: Record<string, unknown>
        styleOverrides?: StyleOverrides
        visibility?: TimeframeVisibility
        hidden?: boolean
      } = {
        definitionId: i.definitionId,
        inputs: i.inputs,
      }
      if (i.styleOverrides && Object.keys(i.styleOverrides).length > 0) {
        item.styleOverrides = i.styleOverrides
      }
      if (i.visibility) {
        item.visibility = i.visibility
      }
      if (i.hidden) {
        item.hidden = true
      }
      return item
    })
  }

  private async ensureEngine(): Promise<EngineWorkerClient> {
    if (!this.engine) {
      this.engine = await getEngine()
      this.offOutputs = this.engine.onOutputs((e) =>
        this.applyOutputs(e.sessionId, e.outputs, e.scope)
      )
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
      // These renderers hold series on the *previous* chart, which the caller
      // has usually destroyed already — taking every series with it. Disposal
      // is then a no-op that throws reaching into the dead chart, and letting
      // it escape would abandon the rest of the rebind, silently dropping the
      // remaining indicators.
      try {
        renderer.dispose()
      } catch {
        /* previous chart already destroyed */
      }
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
  async setDataset(
    bars: readonly Bar[],
    meta: { symbol: string; exchange: string; interval: string }
  ): Promise<void> {
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
      {
        time: bar.time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume ?? 0,
      },
      isNew
    )
  }

  /** Add an indicator; returns whether a chart rebuild is required (never on add). */
  async add(
    definitionId: string,
    inputs?: Record<string, unknown>,
    styleOverrides?: StyleOverrides,
    visibility?: TimeframeVisibility
  ): Promise<string> {
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
      styleOverrides,
      visibility,
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

  /**
   * Add a custom OpenScript indicator from a compiled IRProgram. Mirrors `add()`
   * but takes declaration/inputs from the IR instead of the builtin manifest.
   */
  async addIr(
    ir: IRProgram,
    inputs?: Record<string, unknown>,
    styleOverrides?: StyleOverrides
  ): Promise<string> {
    this.seq += 1
    const instanceId = `${this.hostId}i${this.seq}`
    const instance: IndicatorInstance = {
      instanceId,
      definitionId: 'ir',
      name: ir.declaration.shortName ?? ir.declaration.name,
      overlay: ir.declaration.overlay,
      inputs: { ...Object.fromEntries(ir.inputs.map((i) => [i.id, i.defaultValue])), ...inputs },
      ir,
      styleOverrides,
    }
    if (!ir.declaration.overlay) {
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
   * Apply per-output style overrides (color/width/line-style/opacity/visibility)
   * without a worker recompute — re-renders from the last emitted outputs.
   */
  setStyleOverrides(instanceId: string, overrides: StyleOverrides): void {
    const instance = this.instances.get(instanceId)
    if (!instance) return
    instance.styleOverrides = Object.keys(overrides).length > 0 ? overrides : undefined
    this.emit()
    this.rerenderInstance(instanceId)
  }

  /**
   * Hide or show every plot of one instance without disposing its session —
   * the pane legend's eye toggle. Mirrors the timeframe-visibility path: the
   * series are dropped while hidden and re-created from the cached outputs when
   * shown again, so no worker recompute is involved either way.
   */
  setHidden(instanceId: string, hidden: boolean): void {
    const instance = this.instances.get(instanceId)
    if (!instance || instance.hidden === hidden) return
    instance.hidden = hidden
    this.emit()
    this.rerenderInstance(instanceId)
  }

  /**
   * Move a pane-owning instance one slot up or down the pane stack.
   *
   * Panes are handed out in instance order by `attachChart`, so the stack order
   * *is* the instance order — reordering the map is what moves the pane, and
   * asking the chart to move it would be undone by the next rebuild. Returns
   * false at the ends of the stack, or for an overlay (which owns no pane).
   * The caller rebuilds the chart to apply the new order.
   */
  movePane(instanceId: string, direction: -1 | 1): boolean {
    const ordered = [...this.instances.values()]
    const owners = ordered.filter((i) => i.pane !== undefined)
    const at = owners.findIndex((i) => i.instanceId === instanceId)
    const to = at + direction
    if (at < 0 || to < 0 || to >= owners.length) return false
    const ia = ordered.indexOf(owners[at])
    const ib = ordered.indexOf(owners[to])
    ordered[ia] = owners[to]
    ordered[ib] = owners[at]
    this.instances.clear()
    for (const instance of ordered) this.instances.set(instance.instanceId, instance)
    this.emit()
    return true
  }

  /** Set timeframe visibility (Visibility tab); hides/shows at the current interval. */
  setVisibility(instanceId: string, visibility: TimeframeVisibility | undefined): void {
    const instance = this.instances.get(instanceId)
    if (!instance) return
    instance.visibility = visibility
    this.emit()
    this.rerenderInstance(instanceId)
  }

  /**
   * Rebuild an instance's renderer from its last outputs — style is baked at
   * series-create time, so this reverts cleared overrides, re-applies timeframe
   * visibility, and rebuilds the marker pane anchor cleanly (no worker recompute).
   */
  private rerenderInstance(instanceId: string): void {
    const instance = this.instances.get(instanceId)
    const outputs = this.lastOutputs.get(instanceId)
    if (!instance || !outputs) return
    this.renderers.get(instanceId)?.dispose()
    this.renderers.delete(instanceId)
    this.createRenderer(instance)
    this.applyOutputs(instanceId, outputs, 'full')
  }

  /** Whether an instance is visible on the current chart interval (Visibility tab). */
  private instanceVisibleAtInterval(instance: IndicatorInstance): boolean {
    const v = instance.visibility
    if (!v) return true
    const parsed = parseInterval(this.meta.timeframe)
    if (!parsed) return true
    const r = v[parsed.cat]
    return r.on && parsed.value >= r.min && parsed.value <= r.max
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
    this.lastOutputs.delete(instanceId)
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
    this.lastOutputs.clear()
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
        program: instance.ir
          ? { kind: 'ir', ir: instance.ir }
          : { kind: 'builtin', id: instance.definitionId },
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

  private applyOutputs(
    sessionId: string,
    outputs: IndicatorOutput[],
    scope: 'full' | 'update'
  ): void {
    const renderer = this.renderers.get(sessionId)
    if (!renderer) return
    const instance = this.instances.get(sessionId)
    // Cache the full snapshot so a later style change can re-render without a worker recompute.
    if (scope === 'full') this.lastOutputs.set(sessionId, outputs)
    // Hidden by the legend's eye, or off-timeframe (Visibility tab): drop every
    // series. The cached outputs stay, so unhiding needs no recompute.
    if (instance && (instance.hidden || !this.instanceVisibleAtInterval(instance))) {
      for (const output of outputs) renderer.remove(output.id)
      return
    }
    const overrides = instance?.styleOverrides
    for (const output of outputs) {
      const override = overrides?.[output.id]
      if (override?.visible === false) {
        // Hidden: drop the series and skip re-adding it (idempotent on ticks too).
        renderer.remove(output.id)
        continue
      }
      // Style is applied at series-create time (add / setStyleOverrides /
      // attachChart); replace/update only carry data, so a live style change
      // goes through setStyleOverrides' renderer rebuild rather than here.
      const styled = override ? styleOutput(output, override) : output
      if (scope === 'full') {
        renderer.replace(styled)
      } else {
        renderer.update(styled, Math.max(0, this.barCount - 1))
      }
    }
  }

  private emit(): void {
    this.cb.onIndicators(this.list())
  }
}

/** Fold an alpha (0..1) into a hex or rgb(a) color; returns the input unchanged
 *  when opacity is full or the color can't be parsed (e.g. named colors). */
function withAlpha(color: string, alpha: number): string {
  if (!(alpha < 1)) return color
  const a = Math.max(0, Math.min(1, alpha))
  if (color.startsWith('#')) {
    let hex = color.slice(1)
    if (hex.length === 3) hex = hex.replace(/./g, (c) => c + c)
    if (hex.length >= 6) {
      const r = Number.parseInt(hex.slice(0, 2), 16)
      const g = Number.parseInt(hex.slice(2, 4), 16)
      const b = Number.parseInt(hex.slice(4, 6), 16)
      return `rgba(${r}, ${g}, ${b}, ${a})`
    }
  }
  const m = color.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i)
  if (m) return `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${a})`
  return color
}

/** Return a shallow copy of an output with the override merged into its style.
 *  Only stylable kinds (line/hline/histogram/fill) are touched; values arrays
 *  are shared by reference (never mutated). */
function styleOutput(output: IndicatorOutput, o: OutputStyleOverride): IndicatorOutput {
  switch (output.kind) {
    case 'line':
    case 'hline': {
      const style = { ...output.style }
      if (o.color) style.color = o.color
      if (o.opacity != null) style.color = withAlpha(style.color, o.opacity)
      if (o.lineWidth != null) style.lineWidth = o.lineWidth
      if (o.lineStyle) style.lineStyle = o.lineStyle
      return { ...output, style }
    }
    case 'histogram': {
      const style = { ...output.style }
      if (o.color) style.color = o.color
      if (o.opacity != null) style.color = withAlpha(style.color, o.opacity)
      return { ...output, style }
    }
    case 'fill': {
      const style = { ...output.style }
      if (o.color) style.color = o.color
      if (o.opacity != null) style.opacity = o.opacity
      return { ...output, style }
    }
    default:
      return output
  }
}
