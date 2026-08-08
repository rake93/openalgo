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
  CalendarResolution,
  IndicatorManifestEntry,
  IndicatorOutput,
  InspectResult,
  PerfStats,
  IRProgram,
  OHLCVBar,
} from '@openalgo/openscript'
import {
  datasetFromBars,
  datasetKey,
  descriptorFromIR,
  outputIndexFromId,
  reconcileInputs,
  toDatasetBuffers,
} from '@openalgo/openscript'
import { registryManifest } from '@openalgo/openscript/registry'
import { OpenAlgoChartsRenderer } from '@openalgo/openscript/render/openalgo-charts'
import type { DrawingDiff, EngineWorkerClient } from '@openalgo/openscript/worker-client'
import type { Bar, Chart, SeriesApi } from 'openalgo-charts'
import { getEngine } from './engine'
import { isSilentFallback, type IndicatorProfile } from './indicator-profile'

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
  /**
   * `null` = the output is `na` at this bar.
   *
   * These rows used to be DROPPED. That was exactly backwards for diagnosis: an
   * indicator that plots nothing at the crosshair bar is the case the series
   * inspector (M8) exists to explain, and hiding the row left the user with
   * nothing to point at precisely when they needed it. The row is kept and
   * rendered as a dash.
   */
  value: number | null
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

/**
 * Identity of the saved script an indicator was built from.
 *
 * This — not `definitionId` — is what identifies a custom indicator. A layout
 * persists it so the instance can re-fetch its own authoritative IR from the
 * server on reopen; `definitionId: 'ir'` is a UI sentinel that says nothing
 * about WHICH script is running.
 *
 * Its PRESENCE is what makes an instance durable. An editor preview is built
 * from an unsaved buffer, has no identity to persist, and so has none.
 */
export interface ScriptIdentity {
  scriptId: number
  /** The immutable version. Pinning the version, not just the script, is what
   *  keeps a reopened chart showing the indicator that was saved rather than
   *  whatever the script has since become. */
  versionId: number
  /** sha-256 the server compiled this version from, for detecting a stored IR
   *  that no longer matches the source it claims. */
  sourceHash?: string
}

/**
 * One persisted indicator in a saved layout.
 *
 * `script` present  -> a durable OpenScript indicator, restored by re-fetching
 *                      that version's server-compiled IR.
 * `script` absent   -> a registry builtin, restored from the manifest by
 *                      `definitionId`.
 *
 * A legacy layout may contain `definitionId: 'ir'` with no `script`: an editor
 * preview written by a build that persisted them. Those cannot be restored —
 * there is no version to fetch — and consumers must say so rather than fail
 * quietly.
 */
export interface IndicatorSnapshotEntry {
  definitionId: string
  inputs: Record<string, unknown>
  script?: ScriptIdentity
  styleOverrides?: StyleOverrides
  visibility?: TimeframeVisibility
  hidden?: boolean
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
  /** Present only on a DURABLE custom indicator; an editor preview has none. */
  script?: ScriptIdentity
  /** Per-output color/width/opacity/visibility overrides (Style tab). */
  styleOverrides?: StyleOverrides
  /** Timeframe visibility (Visibility tab); undefined = always visible. */
  visibility?: TimeframeVisibility
  /** Legend eye toggle — every plot hidden without tearing the session down. */
  hidden?: boolean
}

/**
 * Resolve the settings metadata for one instance: its own IR when it owns one,
 * the registry manifest otherwise.
 *
 * Exported and used by BOTH `IndicatorSettingsDialog` and its test, so the test
 * exercises the production rule rather than a parallel copy that could stay
 * green while the dialog regressed.
 *
 * IR OWNERSHIP is the gate, never `definitionId === 'ir'` — that sentinel is a
 * platform-side UI convention, so binding behaviour to it is how a saved custom
 * indicator surfaced elsewhere would silently lose its settings form.
 */
export function resolveSettingsEntry(
  instance: Pick<IndicatorInstance, 'ir' | 'definitionId'>,
  manifest: readonly IndicatorManifestEntry[]
) {
  if (instance.ir) return descriptorFromIR(instance.ir)
  return manifest.find((m) => m.id === instance.definitionId)
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
  /** Latest session run counter per instance (M8). */
  private readonly epochs = new Map<string, number>()
  /**
   * Last run telemetry per instance (M8 §13.3).
   *
   * The engine has always sent this on every run and the host discarded it, so a
   * session that silently reverted from the incremental path to full recompute
   * was invisible. Kept, not aggregated: the last run answers "is incremental
   * working" completely.
   */
  private readonly profiles = new Map<string, IndicatorProfile>()
  /** Per-instance save queue. Await-before-commit is only sound if calls do not
   *  overlap: two in flight can commit out of order, letting an older rejection
   *  clobber a newer success. Both call sites are user-driven saves, so a rapid
   *  double-save reaches this. */
  private readonly saveQueue = new Map<string, Promise<unknown>>()
  private times: Float64Array = new Float64Array(0)
  private barCount = 0
  private currentKey = ''
  private meta = { symbol: '', exchange: '', timeframe: '' }
  /** The calendar the worker resolved for this session (G7 design 6.5). Recorded
   *  so a fallback is observable rather than silent; the UI may surface it later
   *  with no further protocol change. */
  private calendar: CalendarResolution | undefined
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

  /** The session's resolved calendar, or undefined before the first session. */
  calendarResolution(): CalendarResolution | undefined {
    return this.calendar
  }

  /**
   * Series inspector (M8): what is this output worth at `barIndex`, and if it is
   * `na`, where did the `na` come from.
   *
   * `outputId` is the id the chart and data window already carry; the engine's
   * own `outputIndexFromId` maps it back to the IR output index, so the
   * `out_${idx}` format stays in one repo.
   *
   * Returns `null` — never a fabricated answer — when the instance is unknown or
   * the id does not name an output. A refusal the ENGINE makes (a builtin, a bar
   * out of range) comes back inside `result` as a named reason instead, because
   * that is a fact about the session worth showing the user.
   */
  /**
   * Record one run's epoch and telemetry, however the event reached us.
   *
   * BOTH arrival paths must land here. The worker client routes a
   * `session-outputs` to `onOutputs` listeners only when `requestId === null`;
   * a seed, a settings change and a history reload arrive instead as the RESOLVED
   * VALUE of createSession/setInputs. Recording in the listener alone left a
   * freshly loaded chart with no epoch and no profile until its first tick —
   * exactly the moment someone opens a panel to look.
   */
  private noteRun(
    sessionId: string,
    event: { epoch: number; perf: PerfStats; drawings?: DrawingDiff[] | undefined },
    scope: 'full' | 'update'
  ): void {
    this.epochs.set(sessionId, event.epoch)
    const next: IndicatorProfile = { scope, perf: event.perf }
    // M2: fold the run's structural drawing churn into the retained profile.
    // Attached only when the run carried diffs — absence mirrors the wire's
    // "no structural change", so a quiet tick does not overwrite it with zeros
    // that would read as a measurement.
    if (event.drawings !== undefined) {
      let added = 0
      let updated = 0
      let removed = 0
      for (const dd of event.drawings) {
        for (const df of dd.diffs) {
          if (df.op === 'add') added++
          else if (df.op === 'update') updated++
          else removed++
        }
      }
      next.drawings = { added, updated, removed }
    }
    const before = this.profiles.get(sessionId)
    this.profiles.set(sessionId, next)

    // EDGE-TRIGGERED, on purpose. Consumers redraw the legend on `onIndicators`,
    // and a run happens on every tick — emitting each time would repaint the
    // whole legend stack continuously for a value that almost never changes.
    // Emitting only when the flagged state FLIPS keeps the badge live at no
    // per-tick cost.
    const was = before !== undefined && isSilentFallback(before)
    if (was !== isSilentFallback(next)) this.emit()
  }

  /** This instance's last run telemetry (M8 §13.3), or undefined before its
   *  first run. Pair with `isSilentFallback` to decide whether it is worth
   *  drawing attention to. */
  lastProfile(instanceId: string): IndicatorProfile | undefined {
    return this.profiles.get(instanceId)
  }

  /** The run counter of the values currently drawn for this instance (M8).
   *  Compare with an inspect answer's epoch to know whether it still holds. */
  lastEpoch(instanceId: string): number | undefined {
    return this.epochs.get(instanceId)
  }

  async inspect(
    instanceId: string,
    outputId: string,
    barIndex: number
  ): Promise<{ epoch: number; result: InspectResult } | null> {
    if (!this.instances.has(instanceId)) return null
    const outputIndex = outputIndexFromId(outputId)
    if (outputIndex === null) return null

    const engine = await this.ensureEngine()
    return engine.inspect({ sessionId: instanceId, outputIndex, barIndex })
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
        // A bar outside the series is genuinely nothing to show; `na` INSIDE it
        // is a fact worth showing, and the one the inspector explains.
        if (value === undefined) continue
        values.push({
          id: o.id,
          title: o.title,
          value: Number.isNaN(value) ? null : value,
          color,
        })
      }
      if (values.length > 0) rows.push({ instanceId: inst.instanceId, name: inst.name, values })
    }
    return rows
  }

  /**
   * Serializable state for persistence (localStorage / layouts API).
   *
   * Entry ORDER is load-bearing and must be preserved by any consumer:
   * `attachChart` hands panes out in instance order, so the stack order IS the
   * instance order. A pane index is therefore deliberately NOT persisted — it
   * would be a second, conflicting source of truth, and it would leave a hole
   * in the stack whenever one entry failed to restore.
   *
   * A custom OpenScript indicator is identified by `script`, never by
   * `definitionId` — that stays the `'ir'` sentinel, which names the KIND of
   * entry and says nothing about WHICH script is running. The IR itself is not
   * persisted: reopen re-fetches the authoritative IR for `script.versionId`
   * from the server, so a layout never carries a second copy of the program
   * that could drift from it.
   *
   * An editor PREVIEW (IR-owning, but with no saved script behind it) is
   * omitted entirely. It was compiled from an unsaved buffer, so there is no
   * version to re-fetch — persisting one would write an entry guaranteed to
   * fail on reopen.
   */
  snapshot(): IndicatorSnapshotEntry[] {
    const entries: IndicatorSnapshotEntry[] = []
    for (const i of this.list()) {
      if (i.ir && !i.script) continue
      const item: IndicatorSnapshotEntry = {
        definitionId: i.definitionId,
        inputs: i.inputs,
      }
      if (i.script) {
        item.script = i.script
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
      entries.push(item)
    }
    return entries
  }

  private async ensureEngine(): Promise<EngineWorkerClient> {
    if (!this.engine) {
      this.engine = await getEngine()
      this.offOutputs = this.engine.onOutputs((e) => {
        // M8: remember the run counter each session's values came from, so the
        // inspector can say when an answer no longer describes what is drawn.
        this.noteRun(e.sessionId, e, e.scope)
        this.applyOutputs(e.sessionId, e.outputs, e.scope)
      })
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
   *
   * Pass `script` to add a DURABLE indicator — one built from a saved script,
   * which a layout can persist and later restore by re-fetching that version's
   * IR. Omit it for an editor preview, which is built from an unsaved buffer
   * and has no identity to persist.
   *
   * The distinction is about persistence only. Both kinds own an IR and both
   * therefore run as `{kind:'ir'}` sessions on the incremental path: the engine
   * gate is IR ownership and must never become "is this durable".
   */
  async addIr(
    ir: IRProgram,
    options?: {
      inputs?: Record<string, unknown>
      styleOverrides?: StyleOverrides
      visibility?: TimeframeVisibility
      script?: ScriptIdentity
    }
  ): Promise<string> {
    this.seq += 1
    const instanceId = `${this.hostId}i${this.seq}`
    const instance: IndicatorInstance = {
      instanceId,
      definitionId: 'ir',
      name: ir.declaration.shortName ?? ir.declaration.name,
      overlay: ir.declaration.overlay,
      // Reconciled against the IR's own declarations rather than merged over
      // the defaults. Inputs arriving here come from a saved layout, so they
      // may name a setting the script no longer declares, hold the wrong type,
      // or sit outside the declared range — `reconcileInputs` drops, defaults
      // and clamps accordingly. It is also what `commitInputs` uses, so a value
      // cannot enter an instance one way on restore and another way on save.
      inputs: reconcileInputs(ir, options?.inputs ?? {}),
      ir,
      styleOverrides: options?.styleOverrides,
      visibility: options?.visibility,
      script: options?.script,
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

  /**
   * Save an input patch. Rejects when the worker refuses the patch — the callers
   * must surface that, since a settings save that silently failed would leave the
   * dialog showing values the engine never took.
   */
  async setInputs(instanceId: string, inputs: Record<string, unknown>): Promise<void> {
    const prior = this.saveQueue.get(instanceId) ?? Promise.resolve()
    const run = prior
      .catch(() => undefined) // a failed predecessor must not cancel this save
      .then(() => this.commitInputs(instanceId, inputs))
    this.saveQueue.set(instanceId, run)
    try {
      await run
    } finally {
      if (this.saveQueue.get(instanceId) === run) this.saveQueue.delete(instanceId)
    }
  }

  /**
   * Apply one input patch transactionally: the instance snapshot advances only
   * after the worker ACCEPTS it. Advancing first (as this did before) left the
   * instance holding values the worker never took, with the rejection unhandled.
   */
  private async commitInputs(instanceId: string, patch: Record<string, unknown>): Promise<void> {
    const instance = this.instances.get(instanceId)
    if (!instance || !this.engine) return
    const previous = instance.inputs
    const next = instance.ir
      ? reconcileInputs(instance.ir, { ...previous, ...patch })
      : { ...previous, ...patch }
    try {
      const result = await this.engine.setInputs(instanceId, next)
      instance.inputs = next
      delete instance.error
      this.noteRun(instanceId, result, 'full')
      this.applyOutputs(instanceId, result.outputs, 'full')
      this.emit()
    } catch (err) {
      // `previous` stands — both the committed inputs and the last outputs.
      instance.error = err instanceof Error ? err.message : String(err)
      this.emit()
      throw err
    }
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
    this.epochs.delete(instanceId)
    this.profiles.delete(instanceId)
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
    this.epochs.clear()
    this.profiles.clear()
    // Any save still in flight resolves to a no-op (its instance is gone), so
    // dropping the chain here just releases the retained promises.
    this.saveQueue.clear()
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
      if (result.calendar) {
        this.calendar = result.calendar
        if (result.calendar.provenance !== 'mapped') {
          console.warn(
            `[openscript] calendar ${result.calendar.warningCode} for exchange ` +
              `"${result.calendar.normalizedExchange}"; using ${result.calendar.semanticKey}`
          )
        }
      }
      this.noteRun(instance.instanceId, result, 'full')
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
    // Cache the full snapshot FIRST — before the renderer guard.
    //
    // This is data, not rendering: it backs the crosshair data window and the
    // series inspector, and a style change re-renders from it without a worker
    // recompute. Caching it below the guard tied that cache to a chart binding,
    // so a session whose renderer did not exist yet had no data-window values at
    // all. Lifecycle is unchanged — `remove` deletes the entry and `clear` drops
    // them all, both keyed by the same sessionId.
    if (scope === 'full') this.lastOutputs.set(sessionId, outputs)
    publishDebugOutputs(sessionId, outputs, scope)

    const renderer = this.renderers.get(sessionId)
    if (!renderer) return
    const instance = this.instances.get(sessionId)
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
/**
 * Publish the computed outputs of a session to `window.__openscript` so a chart
 * can be inspected for what it ACTUALLY produced, not what its pixels suggest.
 *
 * WHY THIS EXISTS. Three separate investigations stalled on the same blind spot:
 * a marker shipped with an unread `title` and nothing could show it, the M2
 * drawing-diff question could not be answered because `ObjectDiff` has no
 * observable consumer, and an HTF projected candle went missing while offline
 * replays of the same script produced it every time. Each was chased through
 * screenshots and canvas pixel sampling, and pixel inference produced a
 * FALSE POSITIVE at least once -- a colour target computed rather than measured
 * matched an unrelated zone and "confirmed" a candle that was not there.
 *
 * OFF unless asked for. Reading it costs a `localStorage` lookup per publish and
 * nothing else; enabling it holds one reference per session, which is the same
 * snapshot `lastOutputs` already retains.
 *
 *     localStorage.setItem('oa-openscript-debug', '1')   // then reload
 *     window.__openscript.outputs                        // sessionId -> outputs
 *     window.__openscript.drawings('EQH')                // titles + item geometry
 */
interface OpenScriptDebug {
  outputs: Record<string, IndicatorOutput[]>
  scopes: Record<string, string>
  drawings: (titleFilter?: string) => unknown[]
}

function publishDebugOutputs(
  sessionId: string,
  outputs: IndicatorOutput[],
  scope: 'full' | 'update'
): void {
  if (typeof window === 'undefined') return
  try {
    if (localStorage.getItem('oa-openscript-debug') !== '1') return
  } catch {
    return // storage unavailable (private mode / sandbox) — stay silent
  }
  const w = window as unknown as { __openscript?: OpenScriptDebug }
  if (!w.__openscript) {
    w.__openscript = {
      outputs: {},
      scopes: {},
      drawings(titleFilter?: string) {
        const rows: unknown[] = []
        for (const [sid, outs] of Object.entries(w.__openscript!.outputs)) {
          for (const o of outs) {
            if (o.kind !== 'zones' && o.kind !== 'levels' && o.kind !== 'marker') continue
            if (titleFilter && !o.title.includes(titleFilter)) continue
            const items = (o as unknown as { items?: unknown[]; markers?: unknown[] })
            rows.push({
              session: sid,
              kind: o.kind,
              title: o.title,
              // `ahead` is the forward projection distance; a drawing past the
              // last bar carries it instead of a resolvable time.
              items: (items.items ?? items.markers ?? []).map((it) => {
                const g = it as {
                  x1?: { bar: number; time: number | null; ahead?: number }
                  x2?: { bar: number; time: number | null; ahead?: number }
                  barIndex?: number
                  price?: number
                  top?: number
                  bottom?: number
                  text?: string
                }
                return {
                  x1: g.x1 ? { bar: g.x1.bar, ahead: g.x1.ahead ?? null } : undefined,
                  x2: g.x2 ? { bar: g.x2.bar, ahead: g.x2.ahead ?? null } : undefined,
                  barIndex: g.barIndex,
                  price: g.price,
                  top: g.top,
                  bottom: g.bottom,
                  text: g.text,
                }
              }),
            })
          }
        }
        return rows
      },
    }
  }
  w.__openscript.outputs[sessionId] = outputs
  w.__openscript.scopes[sessionId] = scope
}

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
