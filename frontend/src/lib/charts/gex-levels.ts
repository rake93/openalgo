/**
 * GEX Levels study for the /charts workspace.
 *
 * Draws dealer gamma-exposure levels (call wall, put wall, zero gamma, and the
 * per-strike bars) on the price axis. Unlike Volume Profile, Market Profile and
 * Order Flow (see profiles.ts), GEX is not derived from the chart's own bars at
 * all: it is a live option-chain snapshot for a *different* instrument than the
 * one charted (the underlying's option chain), fetched from the server on a
 * timer. That is also why it could not be an OpenScript indicator - the
 * engine's `request.security` is same-symbol only by design, and GEX needs the
 * whole option chain of a different instrument.
 *
 * Mirrors `ProfileManager`: a settings object, `snapshot()` / `restore()` so
 * the study persists with the saved layout, and lifecycle torn down through
 * `dispose()`. The chart primitive that paints these levels, and the workspace
 * wiring that supplies `instrument()` and calls `instrumentChanged()`, are a
 * later task - this file stops at the manager.
 */

import type { GEXLevelsResponse, GEXWeightBy } from '@/api/gex'

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
}

export class GexLevelsManager {
  private readonly cb: GexLevelsCallbacks
  private settings: GexLevelsConfig = structuredClone(DEFAULT_GEX_LEVELS_SETTINGS)

  /** The manager's single timer, live for as long as the study is enabled. */
  private timer: ReturnType<typeof setInterval> | null = null
  private controller: AbortController | null = null
  private disposed = false

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

  setConfig(patch: Partial<GexLevelsConfig>): void {
    const prev = this.settings
    this.settings = { ...prev, ...patch }
    this.cb.onChange()

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
      })
      .catch(() => {
        if (this.disposed || epoch !== this.epoch) return
        this.staleValue = true
      })
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
  }
}
