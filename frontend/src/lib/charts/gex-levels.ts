/**
 * GEX Levels study for the /charts workspace.
 *
 * Draws dealer gamma-exposure levels (call wall, put wall, zero gamma) and a
 * per-strike bar column - gamma or delta, per `GexLevelsConfig.metric` - on
 * the price axis. Unlike Volume Profile, Market Profile and Order Flow (see
 * profiles.ts), GEX is not derived from the chart's own bars at all: it is a
 * live option-chain snapshot for a *different* instrument than the one
 * charted (the underlying's option chain), fetched from the server on a
 * timer. That is also why it could not be an OpenScript indicator - the
 * engine's `request.security` is same-symbol only by design, and GEX needs the
 * whole option chain of a different instrument.
 *
 * Mirrors `ProfileManager`: a settings object, `snapshot()` / `restore()` so
 * the study persists with the saved layout, and lifecycle torn down through
 * `dispose()`. This file also owns the two primitives that paint the study -
 * `GexLevelsPrimitive` (levels and bars) and `GexOverlayPrimitive` (the bar
 * column's metric label and the per-strike hover readout, both painted at a
 * higher zOrder so price action can't cover them - see its doc comment in
 * `gex-levels-primitive.ts`). `attachChart` /
 * `syncPrimitive` mirror `ProfileManager.attachChart` / `rebuild()` exactly,
 * including the "drop, don't remove" handling of a chart rebuild, for both
 * primitives in lockstep. The workspace wiring that supplies `instrument()`
 * and calls `instrumentChanged()` is still a later task.
 */

import type { Chart } from 'openalgo-charts'
import type { GEXLevelsResponse, GEXWeightBy, GexMetric } from '@/api/gex'
import {
  GexLevelsPrimitive,
  type GexLevelsPrimitiveOptions,
  type GexOverlayOptions,
  GexOverlayPrimitive,
} from './gex-levels-primitive'

export interface GexLevelsConfig {
  enabled: boolean
  /**
   * 'oi' is the standing dealer book. NSE and BSE disseminate open interest
   * live in the tick feed, so the US rationale for volume-weighted GEX - that
   * official OI is a stale prior-night snapshot - does not apply here.
   * Weighting by volume instead gives a different read (today's flow only),
   * not a fix for staleness, so it is an opt-in rather than the default.
   */
  weightBy: GEXWeightBy
  /**
   * Which Greek the strike-bar profile is drawn from. Gamma says how hard
   * dealers must hedge; delta says which way the book already leans. Both
   * arrive in one payload, so switching costs no refetch.
   */
  metric: GexMetric
  /** Empty string means the nearest expiry, resolved server-side. */
  expiry: string
  showBars: boolean
  showCallWall: boolean
  showPutWall: boolean
  showZeroGamma: boolean
  showDashboard: boolean
  refreshSeconds: number
  side: 'left' | 'right'
  columnWidth: number
}

export const DEFAULT_GEX_LEVELS_SETTINGS: GexLevelsConfig = {
  enabled: false,
  weightBy: 'oi',
  metric: 'gamma',
  expiry: '',
  showBars: true,
  showCallWall: true,
  showPutWall: true,
  showZeroGamma: true,
  showDashboard: true,
  refreshSeconds: 60,
  side: 'right',
  columnWidth: 120,
}

/** The underlying instrument GEX is computed for - never the charted symbol itself. */
export interface GexInstrument {
  underlying: string
  exchange: string
}

export interface GexLevelsCallbacks {
  onChange(): void
  /** null when the charted instrument has no option chain at all. */
  instrument(): GexInstrument | null
  fetchLevels(
    params: { underlying: string; exchange: string; expiry_date: string; weight_by: GEXWeightBy },
    signal: AbortSignal
  ): Promise<GEXLevelsResponse>
  onSnapshot?(snapshot: GEXLevelsResponse | null): void
  /**
   * Width in px of a volume profile anchored on the same side, if any. The
   * manager does not know about Volume Profile itself - the workspace
   * controller supplies this so the GEX bar column can step inward and clear
   * it, mirroring `ProfileManager.volumeOptions()`'s handling of the Market
   * Profile collision.
   */
  volumeProfileWidthOnSide?(side: 'left' | 'right'): number
}

export class GexLevelsManager {
  private readonly cb: GexLevelsCallbacks
  private settings: GexLevelsConfig = structuredClone(DEFAULT_GEX_LEVELS_SETTINGS)

  /** The manager's single timer, live for as long as the study is enabled. */
  private timer: ReturnType<typeof setInterval> | null = null
  private controller: AbortController | null = null
  private disposed = false

  private chart: Chart | null = null
  private primitive: GexLevelsPrimitive | null = null
  private captionPrimitive: GexOverlayPrimitive | null = null

  /**
   * Bumped every time the charted instrument changes. Each outgoing request
   * captures the epoch it was issued under; when the response arrives, it is
   * published only if the epoch still matches. Without this, a slow response
   * for the previous underlying (say NIFTY) could land after the user has
   * already switched to BANKNIFTY and paint NIFTY's walls on BANKNIFTY's chart
   * - the abort signal alone is not enough, since the request may already be
   * past the point where aborting it stops the response from resolving.
   */
  private epoch = 0

  private snapshotValue: GEXLevelsResponse | null = null
  /**
   * True once a refresh has failed and the snapshot on screen is aged.
   *
   * A failed refresh keeps `snapshotValue` as it was rather than clearing it:
   * blanking levels a trader is actively watching is worse than showing them
   * a little stale, as long as the UI is honest about it via this flag.
   */
  private staleValue = false

  constructor(cb: GexLevelsCallbacks) {
    this.cb = cb
  }

  get config(): GexLevelsConfig {
    return structuredClone(this.settings)
  }

  /** Last successfully fetched snapshot, retained across a failed refresh. */
  get lastSnapshot(): GEXLevelsResponse | null {
    return this.snapshotValue
  }

  /** True when the snapshot being shown is older than the last failed refresh. */
  get stale(): boolean {
    return this.staleValue
  }

  /**
   * True when `snapshotValue` actually has bar data - a successful response
   * with at least one strike. Read by `captionOptions()`; see
   * `GexOverlayOptions.hasBars` for why the caption needs this and
   * `showBars` (a user setting, not a data fact) is not enough on its own.
   */
  private get hasBars(): boolean {
    const s = this.snapshotValue
    return s !== null && s.status === 'success' && (s.strikes?.length ?? 0) > 0
  }

  setConfig(patch: Partial<GexLevelsConfig>): void {
    const prev = this.settings
    this.settings = { ...prev, ...patch }
    this.cb.onChange()
    // Independent of the timer/enabled branching below: an options-only
    // change (column width, side, which lines are shown) must reach the
    // primitive immediately even while nothing about the refresh loop changes.
    this.syncPrimitive()

    const enabledChanged = patch.enabled !== undefined && patch.enabled !== prev.enabled
    const intervalChanged =
      patch.refreshSeconds !== undefined && patch.refreshSeconds !== prev.refreshSeconds
    // A change to what is being asked for should not wait out whatever
    // fraction of the interval happens to be left before it takes effect.
    const queryChanged =
      (patch.weightBy !== undefined && patch.weightBy !== prev.weightBy) ||
      (patch.expiry !== undefined && patch.expiry !== prev.expiry)

    if (!this.settings.enabled) {
      this.stopTimer()
      return
    }

    if (enabledChanged || queryChanged) {
      this.restartTimer()
      this.fetchNow()
    } else if (intervalChanged) {
      this.restartTimer()
    } else if (!this.timer) {
      // Enabled already, but no timer running yet - e.g. the instrument only
      // just resolved. Pick the loop back up.
      this.restartTimer()
    }
  }

  /**
   * Call when the charted instrument changes (a symbol switch). Bumps the
   * epoch so any in-flight request for the previous instrument is discarded
   * on arrival, aborts it outright, and clears the snapshot immediately -
   * levels belong to one underlying, and showing the previous one's while the
   * new one loads is worse than showing none.
   */
  instrumentChanged(): void {
    this.epoch += 1
    this.controller?.abort()
    this.controller = null
    this.snapshotValue = null
    this.staleValue = false
    this.cb.onSnapshot?.(null)
    this.primitive?.setData(null)
    // The snapshot just went to null - re-push captionOptions() so the
    // caption's hasBars gate reflects that immediately rather than waiting
    // for a fetch that may never come. An instrument with no option chain is
    // exactly that case: fetchNow() below returns before ever calling
    // fetchLevels(), so its .then() never runs, and without this call the
    // caption would stay pinned to whatever the previous instrument left it
    // showing - bars, walls and the readout card all gone, one caption left
    // floating on a chart with no GEX on it at all.
    this.syncPrimitive()
    if (this.settings.enabled) {
      this.restartTimer()
      this.fetchNow()
    } else {
      this.stopTimer()
    }
  }

  private stopTimer(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  }

  private restartTimer(): void {
    this.stopTimer()
    // An instrument with no option chain must never start a timer at all - a
    // timer that fires into a guard every tick is still a timer, running for
    // the life of the tab for no reason.
    if (!this.settings.enabled || !this.cb.instrument()) return
    const ms = Math.max(1, this.settings.refreshSeconds) * 1000
    this.timer = setInterval(() => this.fetchNow(), ms)
  }

  private fetchNow(): void {
    const instrument = this.cb.instrument()
    if (!this.settings.enabled || !instrument) return

    this.controller?.abort()
    const controller = new AbortController()
    this.controller = controller
    const epoch = this.epoch

    const params = {
      underlying: instrument.underlying,
      exchange: instrument.exchange,
      expiry_date: this.settings.expiry,
      weight_by: this.settings.weightBy,
    }

    this.cb
      .fetchLevels(params, controller.signal)
      .then((response) => {
        if (this.disposed || epoch !== this.epoch) return // Superseded by an instrument change.
        this.snapshotValue = response
        this.staleValue = false
        this.cb.onSnapshot?.(response)
        this.primitive?.setData(response)
        // hasBars is derived from snapshotValue, which just changed - re-push
        // captionOptions() so the caption's gate reflects the new response
        // (success with strikes, success with none, or an error body)
        // instead of whatever it showed as of the previous refresh.
        this.syncPrimitive()
      })
      .catch(() => {
        if (this.disposed || epoch !== this.epoch) return
        this.staleValue = true
      })
  }

  /* ── chart primitive ───────────────────────────────────────────────────── */

  /**
   * Bind to the current chart. Called on every chart rebuild.
   *
   * A rebuild has already DESTROYED the previous chart by the time this runs,
   * so the old primitive handle is dropped rather than removed from it -
   * calling `removePrimitive` on a chart that no longer exists is the same
   * hazard `ProfileManager.attachChart` and `IndicatorHost.attachChart` guard
   * against, and dropping the handle is the only side that can never throw.
   */
  attachChart(chart: Chart): void {
    this.chart = chart
    this.dropPrimitiveHandle()
    this.syncPrimitive()
    // A snapshot already held (the study stayed enabled across the rebuild)
    // must reach the freshly created primitive immediately - otherwise the
    // levels would blank out on every layout change and only reappear at the
    // next poll, up to `refreshSeconds` later.
    const primitive = this.primitive
    const snapshot = this.snapshotValue
    if (primitive && snapshot) primitive.setData(snapshot)
  }

  /**
   * Drop the primitive handle for a chart that is already gone, without
   * touching it.
   *
   * Split out of `attachChart` rather than inlined as `this.primitive = null`
   * there for a type-level reason, not a style one: TypeScript's control flow
   * analysis narrows `this.primitive` to the literal `null` type at an
   * unconditional assignment site and does not re-widen it across the
   * `syncPrimitive()` call that follows - even though that call reassigns the
   * field - so reading `this.primitive` afterwards in the same function body
   * type-checks as `never`. Moving the assignment behind its own function
   * boundary keeps that narrowing from leaking into `attachChart`. Drops the
   * caption primitive's handle the same way, for the same reason.
   */
  private dropPrimitiveHandle(): void {
    this.primitive = null
    this.captionPrimitive = null
  }

  /**
   * Create, remove, or simply re-configure the primitive to match current
   * settings. Called from `setConfig` (an options or enabled change) and from
   * `attachChart` (a chart rebuild). Mirrors `ProfileManager.rebuild()`.
   */
  private syncPrimitive(): void {
    const chart = this.chart
    if (!chart) return

    if (this.settings.enabled && !this.primitive) {
      this.primitive = new GexLevelsPrimitive(this.primitiveOptions())
      chart.addPrimitive(this.primitive, 0)
    } else if (!this.settings.enabled && this.primitive) {
      try {
        chart.removePrimitive(this.primitive)
      } catch {
        // The chart may already be gone (a rebuild that tore down this exact
        // chart instance without going through attachChart first); dropping
        // the handle below matters more than the removal succeeding.
      }
      this.primitive = null
    }

    // The metric caption is a second, independent primitive (see its doc
    // comment in gex-levels-primitive.ts for why it cannot just be one more
    // thing GexLevelsPrimitive draws) - created, removed and reconfigured in
    // lockstep with the main primitive, but through its own try/catch so a
    // chart that throws removing one still gets the other's handle dropped.
    if (this.settings.enabled && !this.captionPrimitive) {
      this.captionPrimitive = new GexOverlayPrimitive(this.captionOptions())
      chart.addPrimitive(this.captionPrimitive, 0)
    } else if (!this.settings.enabled && this.captionPrimitive) {
      try {
        chart.removePrimitive(this.captionPrimitive)
      } catch {
        // As above: the chart may already be gone.
      }
      this.captionPrimitive = null
    }

    // Re-pushed unconditionally, not just at construction: an options change
    // (column width, side, which lines are shown, or the volume-profile
    // inset) while the study stays enabled must still take effect.
    this.primitive?.setOptions(this.primitiveOptions())
    this.captionPrimitive?.setOptions(this.captionOptions())
  }

  private primitiveOptions(): Partial<GexLevelsPrimitiveOptions> {
    const c = this.settings
    return {
      showBars: c.showBars,
      showCallWall: c.showCallWall,
      showPutWall: c.showPutWall,
      showZeroGamma: c.showZeroGamma,
      side: c.side,
      columnWidth: c.columnWidth,
      metric: c.metric,
      // Volume Profile anchors to the same edge at 150px by default; the bar
      // column steps inward by that width so the two do not overlap. The
      // manager does not know about Volume Profile itself, so this is left to
      // an optional callback the workspace controller supplies - mirroring
      // `ProfileManager.volumeOptions()`'s handling of the Market Profile
      // collision.
      columnInset: this.cb.volumeProfileWidthOnSide?.(c.side) ?? 0,
    }
  }

  /**
   * Mostly mirrors `primitiveOptions()`, trimmed to the subset
   * `GexOverlayPrimitive` actually needs to place and word its label -
   * it has no walls, no `columnWidth`-scaled bars, nothing else to configure
   * - plus one field `primitiveOptions()` has no reason to carry: `hasBars`,
   * a fact about the snapshot rather than a setting, computed fresh on every
   * call so it is never one refresh behind.
   */
  private captionOptions(): Partial<GexOverlayOptions> {
    const c = this.settings
    const s = this.snapshotValue
    return {
      showBars: c.showBars,
      hasBars: this.hasBars,
      side: c.side,
      columnWidth: c.columnWidth,
      metric: c.metric,
      columnInset: this.cb.volumeProfileWidthOnSide?.(c.side) ?? 0,
      // The hover readout's raw material - see GexOverlayOptions.strikes.
      // Derived fresh from snapshotValue on every call, the same as hasBars,
      // so it is reachable from every mutation point (fetchNow's success
      // handler, instrumentChanged) without a new syncPrimitive() call site:
      // they already re-derive hasBars from this same field.
      strikes: s?.strikes ?? [],
      callWall: s?.call_wall ?? null,
      putWall: s?.put_wall ?? null,
    }
  }

  /* ── persistence ───────────────────────────────────────────────────────── */

  snapshot(): GexLevelsConfig {
    return structuredClone(this.settings)
  }

  restore(snap: Partial<GexLevelsConfig> | undefined): void {
    this.settings = { ...DEFAULT_GEX_LEVELS_SETTINGS, ...snap }
    // Mirrors ProfileManager.restore(): this only replaces the settings, it
    // does not itself start polling. The instrument is not necessarily known
    // yet at restore time (the chart may still be loading), so the caller
    // applies it once resolved and calls instrumentChanged() (or setConfig())
    // to kick off the first fetch, exactly as it would for a freshly toggled
    // study.
    this.stopTimer()
  }

  dispose(): void {
    this.disposed = true
    this.stopTimer()
    this.controller?.abort()
    this.controller = null
    // Mirrors ProfileManager.dispose(): just drop the handles, do not call
    // removePrimitive - dispose runs during workspace teardown, by which point
    // the chart itself is already on its way out.
    this.primitive = null
    this.captionPrimitive = null
    this.chart = null
  }
}
