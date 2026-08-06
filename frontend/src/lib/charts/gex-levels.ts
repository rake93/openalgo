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
 * higher zOrder so price action cannot cover them - see its doc comment in
 * `gex-levels-primitive.ts`). `attachChart` /
 * `syncPrimitive` mirror `ProfileManager.attachChart` / `rebuild()` exactly,
 * including the "drop, don't remove" handling of a chart rebuild, for both
 * primitives in lockstep. The workspace wiring that supplies `instrument()`
 * and calls `instrumentChanged()` is still a later task.
 */

import type { Chart } from 'openalgo-charts'
import type {
  GEXGridResponse,
  GEXHistoryResponse,
  GEXLevelsResponse,
  GEXWeightBy,
  GexMetric,
} from '@/api/gex'
import { computeBandCoverage, type GexBandCoverage } from './gex-bands-geometry'
import { type GexBandsOptions, GexBandsPrimitive } from './gex-bands-primitive'
import { GexHeatmapPrimitive } from './gex-heatmap-primitive'
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
  /**
   * Draw the three levels through time as well as at their current prices,
   * from the server's recorded history.
   *
   * Off by default, unlike the levels themselves. Bands draw NOTHING until the
   * instrument is on the recorder's watchlist, and a control that silently does
   * nothing when switched on is worse than one the user turns on deliberately -
   * the settings panel explains the state and offers to start recording.
   */
  showBands: boolean
  /**
   * How far back the bands reach, in hours. Bounded by the server's own
   * MAX_HISTORY_POINTS, which refuses an over-wide window rather than
   * truncating it.
   */
  bandsLookbackHours: number
  /**
   * Shade the region between the two wall bands.
   *
   * Off by default: the bands are meant to read as three distinct lines, and a
   * filled region between two of them competes with exactly that. Kept as a
   * control rather than deleted because the corridor is the range dealers are
   * hedging inside, and its width through the session is worth seeing on demand.
   */
  showBandsCorridor: boolean
  /**
   * Draw the recorded per-strike profile as a background field in the price pane.
   *
   * Mutually exclusive with the bar column, which this hides while it is on.
   * They are the same quantity - the profile is now, the heatmap is now and
   * every recorded minute before it - and drawing both encodes it twice while
   * the heatmap is the one that answers whether price respected a wall.
   *
   * Off by default for the same reason Bands is: it draws NOTHING until the
   * contract is on the recorder's watchlist.
   */
  showHeatmap: boolean
  showDashboard: boolean
  refreshSeconds: number
  side: 'left' | 'right'
  columnWidth: number
  /**
   * Pixel offset of the readout card from its default top-right anchor.
   * Persisted with the layout like every other study setting, so a card
   * moved out of the way stays moved.
   *
   * An offset of zero is indistinguishable from the card never having been
   * dragged, which is what makes this safe to add to an existing saved
   * layout: `restore` fills it from the defaults and nothing shifts.
   */
  cardOffset: { x: number; y: number }
}

/**
 * How many levels-poll intervals pass between history fetches.
 *
 * Recorded history is append-only and its newest point is at most one cadence
 * interval old, so a band lagging a few minutes is invisible on a chart whose
 * bands span hours. Re-fetching the whole window every 60 seconds would ship
 * thousands of points a minute to redraw a nearly identical picture.
 */
const HISTORY_REFRESH_MULTIPLE = 5

/**
 * Bucket width in seconds per resolution the grid endpoint can return.
 *
 * The Heatmap draws each column one bucket wide, so a thinned grid must widen
 * its cells to match or it would draw 1-minute slivers with four minutes of
 * background between them - which is the blank-cell rule's signal for "the
 * recorder missed this", said about minutes that were recorded.
 */
const RESOLUTION_SECONDS: Record<string, number> = { '1m': 60, '5m': 300, '15m': 900 }

export const DEFAULT_GEX_LEVELS_SETTINGS: GexLevelsConfig = {
  enabled: false,
  weightBy: 'oi',
  metric: 'gamma',
  expiry: '',
  showBars: true,
  showCallWall: true,
  showPutWall: true,
  showZeroGamma: true,
  showBands: false,
  bandsLookbackHours: 6,
  showBandsCorridor: false,
  showHeatmap: false,
  showDashboard: true,
  refreshSeconds: 60,
  side: 'right',
  columnWidth: 120,
  cardOffset: { x: 0, y: 0 },
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
  /**
   * Recorded history for Gamma Bands. Optional: a host that does not supply it
   * simply never draws bands, which is the same outcome as an instrument nobody
   * is recording, so nothing needs a second code path.
   */
  fetchHistory?(
    params: {
      underlying: string
      exchange: string
      expiry_date: string
      weight_by: GEXWeightBy
      from_ts: number
      to_ts: number
    },
    signal: AbortSignal
  ): Promise<GEXHistoryResponse>
  /** Recorded per-strike grid for the Heatmap. Optional, like `fetchHistory`. */
  fetchGrid?(
    params: {
      underlying: string
      exchange: string
      expiry_date: string
      weight_by: GEXWeightBy
      metric: GexMetric
      from_ts: number
      to_ts: number
    },
    signal: AbortSignal
  ): Promise<GEXGridResponse>
  onSnapshot?(snapshot: GEXLevelsResponse | null): void
  /** Fired when recorded history arrives, so the panel can say how much there is. */
  onHistory?(history: GEXHistoryResponse | null): void
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
  private bandsPrimitive: GexBandsPrimitive | null = null
  private heatmapPrimitive: GexHeatmapPrimitive | null = null
  private gridController: AbortController | null = null
  private gridValue: GEXGridResponse | null = null

  /**
   * The history timer is separate from, and far lazier than, the levels poll.
   *
   * Recorded history is append-only and the newest point is at most one cadence
   * interval old, so a band that is a minute behind is invisible - while
   * re-fetching a six-hour window every 60 seconds would ship thousands of
   * points a minute for a picture that barely changed. It runs at
   * `refreshSeconds * HISTORY_REFRESH_MULTIPLE`.
   */
  private historyTimer: ReturnType<typeof setInterval> | null = null
  private historyController: AbortController | null = null
  private historyValue: GEXHistoryResponse | null = null

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

  /** Last recorded history fetched, or null if none has arrived. */
  get lastHistory(): GEXHistoryResponse | null {
    return this.historyValue
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

    const bandsChanged = patch.showBands !== undefined && patch.showBands !== prev.showBands
    const lookbackChanged =
      patch.bandsLookbackHours !== undefined && patch.bandsLookbackHours !== prev.bandsLookbackHours

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

    // The recorded loop keys off a different set of changes than the levels
    // poll: the weighting and the lookback change what is asked for and the
    // refresh interval scales it, while the level toggles do not touch it.
    //
    // The METRIC is the one that splits the two overlays. The bands draw levels,
    // which stay computed from gamma whichever metric is selected, so gamma vs
    // delta means nothing to them - but it selects which recorded column the
    // grid reads, so the Heatmap has to re-ask for it.
    this.restartHistoryTimer()
    const heatmapChanged = patch.showHeatmap !== undefined && patch.showHeatmap !== prev.showHeatmap
    const metricChanged = patch.metric !== undefined && patch.metric !== prev.metric
    if (bandsChanged || queryChanged || lookbackChanged || enabledChanged) {
      this.fetchHistoryNow()
    } else if (heatmapChanged || metricChanged) {
      this.fetchGridNow()
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
    // History belongs to one contract just as firmly as the levels belong to
    // one underlying. Cleared outright rather than left to be overwritten: a
    // band of NIFTY walls hanging over a BANKNIFTY chart until the next fetch
    // lands is worse than a moment with no bands.
    this.historyController?.abort()
    this.historyController = null
    this.historyValue = null
    this.cb.onHistory?.(null)
    this.bandsPrimitive?.setData(null)
    this.gridController?.abort()
    this.gridController = null
    this.gridValue = null
    this.heatmapPrimitive?.setData(null)
    this.stopHistoryTimer()
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
        const hadExpiry = this.snapshotValue?.expiry_date
        this.snapshotValue = response
        this.staleValue = false
        this.cb.onSnapshot?.(response)
        this.primitive?.setData(response)
        // hasBars is derived from snapshotValue, which just changed - re-push
        // captionOptions() so the caption's gate reflects the new response
        // (success with strikes, success with none, or an error body)
        // instead of whatever it showed as of the previous refresh.
        this.syncPrimitive()

        // The history request needs a RESOLVED expiry, which only the live
        // response carries. Kick it as soon as one arrives, or when the
        // contract rolls underneath a "nearest" study - the previous
        // contract's bands would otherwise stay on screen against a chain
        // that has already moved on.
        if (response.expiry_date && response.expiry_date !== hadExpiry) {
          this.restartHistoryTimer()
          this.fetchHistoryNow()
        }
      })
      .catch(() => {
        if (this.disposed || epoch !== this.epoch) return
        this.staleValue = true
      })
  }

  /* ── recorded history (Gamma Bands) ────────────────────────────────────── */

  /**
   * Re-fetch recorded history now, out of band with the timer.
   *
   * For the moment after the watchlist changes: starting or stopping recording
   * changes what the next response will say, and waiting out a five-interval
   * poll to reflect a button the user just pressed reads as a dead control.
   */
  refreshHistory(): void {
    this.fetchHistoryNow()
  }

  private stopHistoryTimer(): void {
    if (this.historyTimer) clearInterval(this.historyTimer)
    this.historyTimer = null
  }

  private restartHistoryTimer(): void {
    this.stopHistoryTimer()
    // One timer for both recorded overlays: they read the same store at the
    // same cadence, and two timers would double the polling to show one
    // append-only series twice.
    if (!this.settings.enabled) return
    if (!this.settings.showBands && !this.settings.showHeatmap) return
    if (!this.cb.instrument()) return
    if (!this.cb.fetchHistory && !this.cb.fetchGrid) return
    const ms = Math.max(1, this.settings.refreshSeconds) * HISTORY_REFRESH_MULTIPLE * 1000
    this.historyTimer = setInterval(() => this.fetchHistoryNow(), ms)
  }

  /**
   * Fetch the recorded window, if there is anything to fetch it for.
   *
   * Depends on the LIVE snapshot having resolved first: the request must name a
   * resolved `expiry_date`, and a study configured for the nearest expiry does
   * not know which contract that is until the server tells it. So this is a
   * no-op until `snapshotValue` carries one, and the levels poll that fills it
   * calls back in here when it lands.
   */
  private fetchHistoryNow(): void {
    this.fetchGridNow()

    if (!this.settings.enabled || !this.settings.showBands) {
      this.bandsPrimitive?.setData(null)
      // The live levels were clipped around the bands; with no bands they must
      // go back to spanning the full width, or the level silently disappears
      // over the span history used to cover.
      this.primitive?.setOptions(this.primitiveOptions())
      return
    }

    const instrument = this.cb.instrument()
    const fetchHistory = this.cb.fetchHistory
    const expiry = this.snapshotValue?.expiry_date
    if (!instrument || !fetchHistory || !expiry) return

    this.historyController?.abort()
    const controller = new AbortController()
    this.historyController = controller
    const epoch = this.epoch

    const toTs = Math.floor(Date.now() / 1000)
    const fromTs = toTs - Math.max(1, this.settings.bandsLookbackHours) * 3600

    fetchHistory(
      {
        underlying: instrument.underlying,
        exchange: instrument.exchange,
        expiry_date: expiry,
        weight_by: this.settings.weightBy,
        from_ts: fromTs,
        to_ts: toTs,
      },
      controller.signal
    )
      .then((response) => {
        // Same guard as fetchNow: a slow history response for NIFTY must never
        // paint over BANKNIFTY after a symbol switch. The abort signal alone is
        // not enough once a request is past the point of no return.
        if (this.disposed || epoch !== this.epoch) return
        this.historyValue = response
        this.cb.onHistory?.(response)
        this.bandsPrimitive?.setData({ points: response.points ?? [] })
        // Coverage is derived from `historyValue`, so the live levels have to be
        // told it changed - otherwise the dash keeps overprinting the span the
        // bands just took over.
        this.primitive?.setOptions(this.primitiveOptions())
      })
      .catch(() => {
        // Deliberately silent, and deliberately NOT clearing what is drawn. A
        // failed history refresh leaves the existing bands up - they are a
        // record of the past, which does not become wrong because one request
        // failed. The levels' own `stale` flag already tells the reader the
        // study is having trouble.
        if (this.disposed || epoch !== this.epoch) return
      })
  }

  /**
   * Fetch the recorded grid for the Heatmap.
   *
   * Separate request from the bands', not a second shape off one response: the
   * grid carries every strike of every minute and the bands carry three levels,
   * so a reader with only Bands on must never pay for the grid. Same window and
   * the same resolved-expiry rule; the metric rides along because gamma and
   * delta are both recorded off one chain fetch.
   */
  private fetchGridNow(): void {
    if (!this.settings.enabled || !this.settings.showHeatmap) {
      this.heatmapPrimitive?.setData(null)
      return
    }

    const instrument = this.cb.instrument()
    const fetchGrid = this.cb.fetchGrid
    const expiry = this.snapshotValue?.expiry_date
    if (!instrument || !fetchGrid || !expiry) return

    this.gridController?.abort()
    const controller = new AbortController()
    this.gridController = controller
    const epoch = this.epoch

    const toTs = Math.floor(Date.now() / 1000)
    const fromTs = toTs - Math.max(1, this.settings.bandsLookbackHours) * 3600

    fetchGrid(
      {
        underlying: instrument.underlying,
        exchange: instrument.exchange,
        expiry_date: expiry,
        weight_by: this.settings.weightBy,
        metric: this.settings.metric,
        from_ts: fromTs,
        to_ts: toTs,
      },
      controller.signal
    )
      .then((response) => {
        // Same guard as the levels poll: a slow grid for NIFTY must never paint
        // over BANKNIFTY after a symbol switch.
        if (this.disposed || epoch !== this.epoch) return
        this.gridValue = response
        this.pushGrid()
      })
      .catch(() => {
        // Deliberately silent and deliberately NOT clearing what is drawn, for
        // the same reason the bands do not: a failed refresh does not make the
        // recorded past wrong.
        if (this.disposed || epoch !== this.epoch) return
      })
  }

  /** Hand the held grid to the primitive, in the shape it draws from. */
  private pushGrid(): void {
    const grid = this.gridValue
    if (!this.heatmapPrimitive) return
    if (!grid || grid.status !== 'success') {
      this.heatmapPrimitive.setData(null)
      return
    }
    this.heatmapPrimitive.setData({
      strikes: grid.strikes ?? [],
      columns: (grid.columns ?? []).map((c) => ({
        ts: c.ts,
        values: c.values,
        quality: c.quality,
      })),
      maxAbsValue: grid.max_abs_value ?? 0,
      resolutionSeconds: RESOLUTION_SECONDS[grid.resolution ?? '1m'] ?? 60,
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
    this.bandsPrimitive = null
    this.heatmapPrimitive = null
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

    // The bands are a third primitive, gated on `showBands` as well as
    // `enabled` - unlike the caption, they are an opt-in overlay and most
    // sessions will never turn them on. Same try/catch isolation so a chart
    // that throws removing one still gets the others' handles dropped.
    // The heatmap is a fourth primitive, and it is created BEFORE the bands on
    // purpose. Both sit at `zOrder: 'bottom'`, so within that layer the chart
    // paints them in the order they were added, and the levels through time
    // have to stay readable over the field they were computed from.
    const wantHeatmap = this.settings.enabled && this.settings.showHeatmap
    let heatmapJustCreated = false
    if (wantHeatmap && !this.heatmapPrimitive) {
      this.heatmapPrimitive = new GexHeatmapPrimitive()
      chart.addPrimitive(this.heatmapPrimitive, 0)
      heatmapJustCreated = true
      // Re-push whatever grid is already held, so toggling the Heatmap on does
      // not wait out a fetch interval to show what the manager already has.
      this.pushGrid()
    } else if (!wantHeatmap && this.heatmapPrimitive) {
      try {
        chart.removePrimitive(this.heatmapPrimitive)
      } catch {
        // As above: the chart may already be gone.
      }
      this.heatmapPrimitive = null
    }

    const wantBands = this.settings.enabled && this.settings.showBands
    // Bands already on when the heatmap appears would have been added FIRST and
    // would paint underneath it. Re-adding puts them back on top; it costs one
    // primitive swap on a toggle nobody flips per frame.
    if (heatmapJustCreated && this.bandsPrimitive) {
      try {
        chart.removePrimitive(this.bandsPrimitive)
      } catch {
        // As above.
      }
      this.bandsPrimitive = null
    }
    if (wantBands && !this.bandsPrimitive) {
      this.bandsPrimitive = new GexBandsPrimitive(this.bandsOptions())
      chart.addPrimitive(this.bandsPrimitive, 0)
      // Re-push whatever history is already held: toggling Bands on must not
      // wait out a fetch interval to show what the manager already has.
      if (this.historyValue) {
        this.bandsPrimitive.setData({ points: this.historyValue.points ?? [] })
      }
    } else if (!wantBands && this.bandsPrimitive) {
      try {
        chart.removePrimitive(this.bandsPrimitive)
      } catch {
        // As above: the chart may already be gone.
      }
      this.bandsPrimitive = null
    }

    // Re-pushed unconditionally, not just at construction: an options change
    // (column width, side, which lines are shown, or the volume-profile
    // inset) while the study stays enabled must still take effect.
    this.primitive?.setOptions(this.primitiveOptions())
    this.captionPrimitive?.setOptions(this.captionOptions())
    this.bandsPrimitive?.setOptions(this.bandsOptions())
  }

  /**
   * Which bands to draw follows the LEVEL toggles, not a separate set.
   *
   * A band is the same object as its level seen through time, so hiding the
   * Call Wall while leaving its history drawn would put an unlabelled line on
   * the chart with nothing to explain it.
   */
  private bandsOptions(): Partial<GexBandsOptions> {
    const c = this.settings
    return {
      showCallWall: c.showCallWall,
      showPutWall: c.showPutWall,
      showZeroGamma: c.showZeroGamma,
      showCorridor: c.showBandsCorridor,
    }
  }

  private primitiveOptions(): Partial<GexLevelsPrimitiveOptions> {
    const c = this.settings
    return {
      // The Heatmap replaces the bar column rather than joining it: the column
      // is this minute's profile and the field is every recorded minute of it,
      // so showing both encodes the same numbers twice in one pane.
      showBars: c.showBars && !c.showHeatmap,
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
      bandCoverage: this.bandCoverage(),
    }
  }

  /**
   * Where the bands already draw each level, for the live primitive to skip.
   *
   * Derived on every call rather than stored, so it can never be one refresh
   * behind the history the bands are actually drawing - the two are read from
   * the same `historyValue`. Null whenever Bands is off or empty, which restores
   * the full-width dashed lines.
   */
  private bandCoverage(): GexBandCoverage | null {
    if (!this.settings.enabled || !this.settings.showBands) return null
    const points = this.historyValue?.points
    if (!points || points.length === 0) return null
    return computeBandCoverage(points)
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
    this.stopHistoryTimer()
  }

  dispose(): void {
    this.disposed = true
    this.stopTimer()
    this.stopHistoryTimer()
    this.controller?.abort()
    this.controller = null
    this.historyController?.abort()
    this.historyController = null
    // Mirrors ProfileManager.dispose(): just drop the handles, do not call
    // removePrimitive - dispose runs during workspace teardown, by which point
    // the chart itself is already on its way out.
    this.primitive = null
    this.captionPrimitive = null
    this.bandsPrimitive = null
    this.chart = null
  }
}
