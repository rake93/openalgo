/**
 * Host for the `openalgo-charts/indicators` tier.
 *
 * The workspace runs two indicator runtimes side by side: the OpenScript WASM
 * engine (`IndicatorHost`, which also compiles user scripts) and the chart
 * library's own 18 built-ins. They render into the same pane stack, so pane
 * indices must come from one allocator — the controller claims panes for the
 * engine first, then hands this host the next free index.
 *
 * The chart owns each live `IndicatorApi`; this class owns the *model* (id +
 * settings + pane), so a chart rebuild re-adds every instance from scratch in
 * the same order and the layout round-trips through JSON.
 */

import 'openalgo-charts/indicators'
import {
  type Chart,
  getIndicator,
  hasIndicator,
  type IndicatorApi,
  type IndicatorDescriptor,
  type IndicatorInput,
  type IndicatorSettings,
  indicatorDefaults,
  indicatorStyleInputs,
  registeredIndicators,
} from 'openalgo-charts'

/** A live library-indicator instance as the UI sees it. */
export interface LibraryIndicatorInstance {
  instanceId: string
  indicatorId: string
  name: string
  category: string
  /** True when it overlays the price pane rather than taking its own. */
  overlay: boolean
  settings: IndicatorSettings
  paneIndex: number
  hidden: boolean
}

/** Serialised form stored in the layout. */
export interface LibraryIndicatorSnapshot {
  indicatorId: string
  settings: IndicatorSettings
  hidden?: boolean
}

export interface LibraryIndicatorsCallbacks {
  onChange(list: LibraryIndicatorInstance[]): void
  onError(message: string): void
  /** The gear on a pane legend was pressed — the host opens its own dialog. */
  onSettingsRequest?(instanceId: string): void
  /** A pane-owning instance went away, so the pane stack must be rebuilt. */
  onNeedsRebuild?(): void
}

interface Entry {
  instanceId: string
  indicatorId: string
  settings: IndicatorSettings
  hidden: boolean
  /** Live handle on the current chart; null between rebuilds. */
  api: IndicatorApi | null
  paneIndex: number
}

let seq = 0

export class LibraryIndicators {
  private readonly cb: LibraryIndicatorsCallbacks
  private readonly entries: Entry[] = []
  private chart: Chart | null = null
  private basePane = 1

  constructor(cb: LibraryIndicatorsCallbacks) {
    this.cb = cb
  }

  /** Every registered descriptor, for the picker. */
  get catalogue(): IndicatorDescriptor[] {
    return registeredIndicators()
  }

  descriptor(indicatorId: string): IndicatorDescriptor | null {
    return hasIndicator(indicatorId) ? getIndicator(indicatorId) : null
  }

  /** Input descriptors for a settings form: the declared ones plus generated style. */
  formInputs(indicatorId: string): { inputs: IndicatorInput[]; style: IndicatorInput[] } {
    const d = this.descriptor(indicatorId)
    if (!d) return { inputs: [], style: [] }
    return { inputs: [...d.inputs], style: indicatorStyleInputs(d) }
  }

  list(): LibraryIndicatorInstance[] {
    return this.entries.map((e) => {
      const d = this.descriptor(e.indicatorId)
      return {
        instanceId: e.instanceId,
        indicatorId: e.indicatorId,
        name: d?.name ?? e.indicatorId,
        category: d?.category ?? 'Other',
        overlay: (d?.placement ?? 'onchart') === 'onchart',
        settings: { ...e.settings },
        paneIndex: e.paneIndex,
        hidden: e.hidden,
      }
    })
  }

  /**
   * (Re)bind to a chart. `basePane` is the first pane index free for sub-pane
   * indicators after the engine-backed ones have claimed theirs.
   */
  attachChart(chart: Chart, basePane: number): void {
    this.chart = chart
    this.basePane = basePane
    let next = basePane
    for (const e of this.entries) {
      const d = this.descriptor(e.indicatorId)
      if (!d) {
        e.api = null
        continue
      }
      const pane = d.placement === 'pane' ? next++ : 0
      e.paneIndex = pane
      try {
        e.api = chart.addIndicator(e.indicatorId, e.settings, { paneIndex: pane })
        if (e.hidden) e.api.setVisible(false)
      } catch (err) {
        e.api = null
        this.cb.onError(`${e.indicatorId}: ${(err as Error).message}`)
      }
    }
  }

  add(indicatorId: string, settings?: IndicatorSettings): string | null {
    const d = this.descriptor(indicatorId)
    if (!d) {
      this.cb.onError(`unknown indicator: ${indicatorId}`)
      return null
    }
    seq += 1
    const instanceId = `lib${seq}`
    const merged = { ...indicatorDefaults(d), ...settings }
    const pane = d.placement === 'pane' ? this.nextFreePane() : 0
    const entry: Entry = {
      instanceId,
      indicatorId,
      settings: merged,
      hidden: false,
      api: null,
      paneIndex: pane,
    }
    if (this.chart) {
      try {
        entry.api = this.chart.addIndicator(indicatorId, merged, { paneIndex: pane })
      } catch (err) {
        this.cb.onError(`${indicatorId}: ${(err as Error).message}`)
        return null
      }
    }
    this.entries.push(entry)
    this.emit()
    return instanceId
  }

  /** One past the highest pane any instance currently owns. */
  private nextFreePane(): number {
    let max = this.basePane - 1
    for (const e of this.entries) {
      const d = this.descriptor(e.indicatorId)
      if (d?.placement === 'pane') max = Math.max(max, e.paneIndex)
    }
    return max + 1
  }

  setSettings(instanceId: string, patch: IndicatorSettings): void {
    const e = this.entries.find((x) => x.instanceId === instanceId)
    if (!e) return
    e.settings = { ...e.settings, ...patch }
    e.api?.setSettings(patch)
    this.emit()
  }

  setHidden(instanceId: string, hidden: boolean): void {
    const e = this.entries.find((x) => x.instanceId === instanceId)
    if (!e) return
    e.hidden = hidden
    e.api?.setVisible(!hidden)
    this.emit()
  }

  remove(instanceId: string): void {
    const i = this.entries.findIndex((x) => x.instanceId === instanceId)
    if (i < 0) return
    const [e] = this.entries.splice(i, 1)
    const ownedPane = this.descriptor(e.indicatorId)?.placement === 'pane'
    try {
      e.api?.remove()
    } catch {
      /* already gone with the chart */
    }
    this.emit()
    // Removing a pane renumbers everything above it, so the stack is rebuilt
    // rather than patched — the same rule the engine-backed host follows.
    if (ownedPane) this.cb.onNeedsRebuild?.()
  }

  /** The chart's own legend removed an instance — mirror it into the model. */
  onRemovedByLegend(payload: unknown): void {
    const id = (payload as { instanceId?: string; indicatorId?: string } | null)?.instanceId
    if (!id) return
    const i = this.entries.findIndex((x) => x.api?.id === id)
    if (i < 0) return
    this.entries.splice(i, 1)
    this.emit()
  }

  /** The chart's legend gear was pressed — surface it to the host's dialog. */
  requestSettings(payload: unknown): void {
    const id = (payload as { instanceId?: string } | null)?.instanceId
    if (!id) return
    const e = this.entries.find((x) => x.api?.id === id)
    if (e) this.cb.onSettingsRequest?.(e.instanceId)
  }

  /**
   * The chart recomputes library indicators itself whenever series data
   * changes, so a live bar needs no explicit push — this is the hook kept for
   * Tier-2 instances whose data is external.
   */
  onData(): void {
    /* the runtime recomputes from the series; nothing to push */
  }

  /** Point every legend row at the hovered bar (or the latest when null). */
  onCrosshair(index: number | null): void {
    for (const e of this.entries) {
      if (!e.api) continue
      if (index == null) e.api.updateLegendValues()
      else e.api.updateLegendValues(index)
    }
  }

  snapshot(): LibraryIndicatorSnapshot[] {
    return this.entries.map((e) => ({
      indicatorId: e.indicatorId,
      settings: { ...e.settings },
      ...(e.hidden ? { hidden: true } : {}),
    }))
  }

  restore(items: readonly LibraryIndicatorSnapshot[]): void {
    for (const item of items) {
      const id = this.add(item.indicatorId, item.settings)
      if (id && item.hidden) this.setHidden(id, true)
    }
  }

  dispose(): void {
    this.entries.length = 0
    this.chart = null
  }

  private emit(): void {
    this.cb.onChange(this.list())
  }
}
