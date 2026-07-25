/**
 * Profiles and order flow for the /charts workspace.
 *
 * Wraps `openalgo-charts/profile`: Volume Profile (session or composite),
 * Market Profile / TPO (letters, POC, value area, initial balance, single
 * prints, naked levels), Footprint, and cumulative delta.
 *
 * Data honesty
 *   Volume Profile and Market Profile are derivable from OHLCV history, so they
 *   cover everything the chart has loaded.
 *   Footprint needs trade-by-trade data classified as hitting the bid or the
 *   ask. OpenAlgo streams depth and LTP but does not store historical
 *   classified trades, so the footprint here is **live-session only**: each tick
 *   is classified against the best bid/ask from the depth stream (the standard
 *   quote rule) and accumulates from the moment the chart connected. It is an
 *   approximation of a real tape, and the UI says so.
 */

import type { Bar, Chart, CrosshairMoveEvent, MarketDepth } from 'openalgo-charts'
import {
  computeMarketProfile,
  computeVolumeProfileSessions,
  Footprint,
  FootprintAggregator,
  type FootprintBar,
  type FootprintOptions,
  MarketProfile,
  type MarketProfileOptions,
  type MarketProfilePrimitiveOptions,
  rowTicksFor,
  TRADING_HOURS,
  VolumeProfile,
  type VolumeProfilePrimitiveOptions,
  type VolumeProfileSession,
} from 'openalgo-charts/profile'
import { asPrimitive } from './tier-compat'

export type ProfileOverlay = 'volume' | 'market' | 'footprint'

/** Timeframe the live footprint aggregates into. */
export type FootprintTimeframe =
  | { mode: 'interval'; seconds: number }
  | { mode: 'ticks'; count: number }
  | { mode: 'volume'; perBar: number }

/**
 * `visible` is the volume profile every trader means by default: one
 * distribution over the bars currently on screen, recomputed as the viewport
 * moves. The library builds it by passing the visible slice with
 * `session: 'composite'`; the other values group by calendar instead.
 *
 * It also keeps the pane honest. `VolumeProfile.autoscaleInfo()` reports the
 * profile's own extent, so a profile over *all* loaded history drags the price
 * scale out to that history's range and squashes the bars you are looking at.
 * A visible-range profile spans exactly what is already on screen.
 */
export type VolumeSessionMode = VolumeProfileSession | 'visible'

export interface VolumeProfileConfig {
  enabled: boolean
  session: VolumeSessionMode
  valueAreaPercent: number
  /** Row height in price units; converted to ticks against the instrument tick. */
  rowSize: number
  displayMode: VolumeProfilePrimitiveOptions['displayMode']
  side: VolumeProfilePrimitiveOptions['side']
  width: number
  showPoc: boolean
  showValueArea: boolean
  highlightValueArea: boolean
}

export interface MarketProfileConfig {
  enabled: boolean
  session: MarketProfileOptions['session']
  blockMinutes: number
  valueAreaPercent: number
  initialBalancePeriods: number
  rowSize: number
  /** Named trading window from TRADING_HOURS, or 'all-hours'. */
  window: string
  /**
   * Build only the sessions on screen. `MarketProfile.autoscaleInfo()` reports
   * the extent of every session it holds, so profiling all loaded history drags
   * the price scale across weeks of range and squashes today's bars into a
   * sliver. Sessions off screen cannot be read anyway. Turn it off when you
   * want naked levels carried forward from sessions further back.
   */
  visibleOnly: boolean
  compositeSessions: number
  blockDisplay: MarketProfilePrimitiveOptions['blockDisplay']
  colorMode: MarketProfilePrimitiveOptions['colorMode']
  split: boolean
  showSinglePrints: boolean
  showInitialBalance: boolean
  showNakedLevels: boolean
  showDevelopingPoc: boolean
  showVolumeProfile: boolean
  showDayType: boolean
}

export interface FootprintConfig {
  enabled: boolean
  timeframe: FootprintTimeframe
  rowSize: number
  displayMode: FootprintOptions['displayMode']
  imbalanceRatio: number
  stackedImbalances: number
  statsRows: FootprintOptions['statsRows']
  showCandle: boolean
  showPoc: boolean
}

export interface ProfileSettings {
  volume: VolumeProfileConfig
  market: MarketProfileConfig
  footprint: FootprintConfig
}

export const DEFAULT_PROFILE_SETTINGS: ProfileSettings = {
  volume: {
    enabled: false,
    session: 'visible',
    valueAreaPercent: 0.7,
    rowSize: 0,
    displayMode: 'buySell',
    side: 'right',
    width: 150,
    showPoc: true,
    showValueArea: true,
    highlightValueArea: true,
  },
  market: {
    enabled: false,
    session: 'day',
    blockMinutes: 30,
    valueAreaPercent: 0.7,
    initialBalancePeriods: 2,
    rowSize: 0,
    window: 'india',
    visibleOnly: true,
    compositeSessions: 1,
    blockDisplay: 'auto',
    colorMode: 'period',
    split: false,
    showSinglePrints: true,
    showInitialBalance: true,
    showNakedLevels: false,
    showDevelopingPoc: false,
    showVolumeProfile: false,
    showDayType: false,
  },
  footprint: {
    enabled: false,
    timeframe: { mode: 'interval', seconds: 300 },
    rowSize: 0,
    displayMode: 'bidask',
    imbalanceRatio: 3,
    stackedImbalances: 3,
    statsRows: ['volume', 'delta', 'deltaPct', 'cvd'],
    showCandle: true,
    showPoc: true,
  },
}

/** What the pointer is over, for the host-drawn inspector. */
export interface ProfileHover {
  kind: ProfileOverlay
  lines: { label: string; value: string }[]
}

export interface ProfileManagerCallbacks {
  onChange(): void
  onHover?(hover: ProfileHover | null): void
  refPrice(): number
  tickSize(): number
  /** Logical bar range currently on screen, for the visible-range profile. */
  visibleRange(): { from: number; to: number } | null
}

/**
 * Default row height for a profile or footprint, derived from the instrument.
 *
 * Row size is the one setting that has to change per instrument: the same
 * number that reads well on NIFTY is meaningless on a ₹400 stock. Scaling to
 * price (then snapping to the tick) reproduces what desks actually use on the
 * NSE index futures, and degrades sensibly everywhere else:
 *
 * | Instrument      | tick | price   | profile row | footprint row |
 * |-----------------|------|---------|-------------|---------------|
 * | NIFTY fut       | 0.10 | ~23,800 | 5           | 2             |
 * | BANKNIFTY fut   | 0.20 | ~52,000 | 10          | 4             |
 * | RELIANCE        | 0.10 | ~1,500  | 0.3         | 0.1           |
 * | BHEL            | 0.05 | ~415    | 0.10        | 0.05          |
 *
 * The 2-point NIFTY footprint brick is the figure openalgo-charts' own
 * `rowTicks` documentation uses, and a 5-point TPO row over a 150–300 point
 * NIFTY session gives roughly 30–60 rows — the density a market profile is
 * meant to be read at. Never finer than one tick.
 */
const opposite = (side: VolumeProfilePrimitiveOptions['side']) =>
  side === 'right' ? 'left' : 'right'

export function autoRowSize(kind: 'profile' | 'footprint', refPrice: number, tick: number): number {
  const t = tick > 0 ? tick : 0.05
  const price = Math.abs(refPrice)
  if (!(price > 0)) return t
  const fraction = kind === 'profile' ? 0.0002 : 0.00008
  return Math.max(t, price * fraction)
}

export class ProfileManager {
  private readonly cb: ProfileManagerCallbacks
  private settings: ProfileSettings = structuredClone(DEFAULT_PROFILE_SETTINGS)

  private chart: Chart | null = null
  private rawBars: readonly Bar[] = []
  /**
   * Profiles anchor to bar *times*. A movement-driven chart type (Renko, P&F,
   * Kagi, ...) replaces the plotted elements with ones that do not correspond
   * one-to-one with the raw bars, so a profile computed from raw OHLCV would
   * land at the wrong x. Overlays stay configured but are not drawn there.
   */
  private timeIndexed = true

  private volumePrim: VolumeProfile | null = null
  private marketPrim: MarketProfile | null = null
  private footprintPrim: Footprint | null = null

  /** Live footprint state — only ever built from ticks seen this session. */
  private agg: FootprintAggregator | null = null
  private footprintBars: FootprintBar[] = []
  private bestBid = 0
  private bestAsk = 0
  private lastTradePrice = 0

  constructor(cb: ProfileManagerCallbacks) {
    this.cb = cb
  }

  get config(): ProfileSettings {
    return structuredClone(this.settings)
  }

  isEnabled(overlay: ProfileOverlay): boolean {
    return this.settings[overlay].enabled
  }

  /** Live footprint bars accumulated so far (empty until ticks arrive). */
  get footprintBarCount(): number {
    return this.footprintBars.length
  }

  /* ── lifecycle ─────────────────────────────────────────────────────────── */

  attachChart(chart: Chart, rawBars: readonly Bar[], timeIndexed: boolean): void {
    this.chart = chart
    this.rawBars = rawBars
    this.timeIndexed = timeIndexed
    // Primitives belong to the destroyed chart; drop the handles and rebuild
    // from the settings, which are the source of truth.
    this.volumePrim = null
    this.marketPrim = null
    this.footprintPrim = null
    this.rebuild()
  }

  setBars(rawBars: readonly Bar[]): void {
    this.rawBars = rawBars
    this.refreshData()
  }

  /** True when profile overlays can be drawn on the current chart type. */
  get available(): boolean {
    return this.timeIndexed
  }

  /**
   * Row height in ticks. `rowSize: 0` means auto — see {@link autoRowSize}.
   *
   * One tick per row is almost never what a trader wants: NIFTY futures tick in
   * 0.10, so a one-tick profile over a 300-point session is 3,000 rows of noise.
   */
  private rowTicks(rowSize: number, kind: 'profile' | 'footprint'): number {
    const tick = this.cb.tickSize()
    const size = rowSize > 0 ? rowSize : autoRowSize(kind, this.cb.refPrice(), tick)
    return rowTicksFor(size, tick)
  }

  private rebuild(): void {
    const chart = this.chart
    if (!chart) return
    const on = (overlay: ProfileOverlay) => this.timeIndexed && this.settings[overlay].enabled

    if (on('volume') && !this.volumePrim) {
      this.volumePrim = new VolumeProfile(null, this.volumeOptions())
      chart.addPrimitive(asPrimitive(this.volumePrim), 0)
    } else if (!on('volume') && this.volumePrim) {
      chart.removePrimitive(asPrimitive(this.volumePrim))
      this.volumePrim = null
    }

    if (on('market') && !this.marketPrim) {
      this.marketPrim = new MarketProfile(this.computeMarket(), this.marketOptions())
      chart.addPrimitive(asPrimitive(this.marketPrim), 0)
    } else if (!on('market') && this.marketPrim) {
      chart.removePrimitive(asPrimitive(this.marketPrim))
      this.marketPrim = null
    }

    if (on('footprint') && !this.footprintPrim) {
      this.footprintPrim = new Footprint(this.footprintOptions())
      chart.addPrimitive(asPrimitive(this.footprintPrim), 0)
      this.ensureAggregator()
    } else if (!on('footprint') && this.footprintPrim) {
      chart.removePrimitive(asPrimitive(this.footprintPrim))
      this.footprintPrim = null
    }

    // Options are re-pushed on every rebuild, not just at construction: the
    // volume profile moves its labels when the market profile is switched on,
    // so one study's options depend on another's state.
    this.volumePrim?.setOptions(this.volumeOptions())
    this.marketPrim?.setOptions(this.marketOptions())
    this.footprintPrim?.setOptions(this.footprintOptions())

    this.syncRangeWatcher()
    this.refreshData()
  }

  private refreshData(): void {
    if (this.volumePrim) this.volumePrim.setData(this.computeVolume())
    if (this.marketPrim) this.marketPrim.setData(this.computeMarket())
    if (this.footprintPrim) this.footprintPrim.setBars([...this.footprintBars])
  }

  /* ── computation ───────────────────────────────────────────────────────── */

  private computeVolume() {
    const c = this.settings.volume
    // A visible-range profile is one composite distribution over the on-screen
    // slice; every other mode groups the whole loaded range by calendar.
    const bars = c.session === 'visible' ? this.visibleBars() : this.rawBars
    const session: VolumeProfileSession = c.session === 'visible' ? 'composite' : c.session
    return computeVolumeProfileSessions(bars, {
      tickSize: this.cb.tickSize() * this.rowTicks(c.rowSize, 'profile'),
      session,
      valueAreaPercent: c.valueAreaPercent,
      deltaFromBarDirection: c.displayMode !== 'total',
    })
  }

  /** The slice of bars currently on screen, clamped to the loaded data. */
  private visibleBars(): readonly Bar[] {
    const r = this.cb.visibleRange()
    if (!r) return this.rawBars
    const from = Math.max(0, Math.floor(r.from))
    const to = Math.min(this.rawBars.length, Math.ceil(r.to) + 1)
    return to > from ? this.rawBars.slice(from, to) : this.rawBars
  }

  /**
   * A visible-range profile has to follow the viewport, and the chart emits no
   * scroll or zoom event — so the range is sampled and the profile recomputed
   * only when it actually moved. The watcher runs solely while such a profile
   * is on, and is torn down with the manager.
   */
  private rangeTimer: ReturnType<typeof setInterval> | null = null
  private lastRange = ''

  private syncRangeWatcher(): void {
    const volumeFollows = this.settings.volume.enabled && this.settings.volume.session === 'visible'
    const marketFollows = this.settings.market.enabled && this.settings.market.visibleOnly
    const want = volumeFollows || marketFollows
    if (want && !this.rangeTimer) {
      this.rangeTimer = setInterval(() => {
        const r = this.cb.visibleRange()
        const key = r ? `${Math.round(r.from)}:${Math.round(r.to)}` : ''
        if (key === this.lastRange) return
        this.lastRange = key
        if (volumeFollows) this.volumePrim?.setData(this.computeVolume())
        if (marketFollows) this.marketPrim?.setData(this.computeMarket())
      }, 250)
    } else if (!want && this.rangeTimer) {
      clearInterval(this.rangeTimer)
      this.rangeTimer = null
      this.lastRange = ''
    }
  }

  private computeMarket() {
    const c = this.settings.market
    const window = TRADING_HOURS[c.window]
    return computeMarketProfile(c.visibleOnly ? this.visibleBars() : this.rawBars, {
      tickSize: this.cb.tickSize(),
      rowTicks: this.rowTicks(c.rowSize, 'profile'),
      session: c.session,
      blockMinutes: c.blockMinutes,
      valueAreaPercent: c.valueAreaPercent,
      initialBalancePeriods: c.initialBalancePeriods,
      compositeSessions: Math.max(1, c.compositeSessions),
      // A single print run of 2+ promotes a buying / selling tail.
      tailEdges: 2,
      ...(window ? { window } : {}),
    })
  }

  private volumeOptions(): Partial<VolumeProfilePrimitiveOptions> {
    const c = this.settings.volume
    return {
      displayMode: c.displayMode,
      side: c.side,
      width: c.width,
      showPoc: c.showPoc,
      showValueArea: c.showValueArea,
      highlightValueArea: c.highlightValueArea,
      // Both studies label their POC and value area at the session edge. With
      // the market profile also on they stack on top of each other against the
      // price axis, so the volume profile's move to the far edge of its own
      // bars instead.
      labelSide: this.settings.market.enabled ? opposite(c.side) : c.side,
      zOrder: 'bottom',
    }
  }

  private marketOptions(): Partial<MarketProfilePrimitiveOptions> {
    const c = this.settings.market
    return {
      blockDisplay: c.blockDisplay,
      colorMode: c.colorMode,
      split: c.split,
      showSinglePrints: c.showSinglePrints,
      showInitialBalance: c.showInitialBalance,
      showNakedLevels: c.showNakedLevels,
      showDevelopingPoc: c.showDevelopingPoc,
      showDevelopingVa: c.showDevelopingPoc,
      showVolumeProfile: c.showVolumeProfile,
      showDayType: c.showDayType,
      showOpenType: c.showDayType,
      zOrder: 'bottom',
    }
  }

  private footprintOptions(): Partial<FootprintOptions> {
    const c = this.settings.footprint
    return {
      displayMode: c.displayMode,
      imbalanceRatio: c.imbalanceRatio,
      stackedImbalances: c.stackedImbalances,
      statsRows: c.statsRows,
      showCandle: c.showCandle,
      showPoc: c.showPoc,
      tickSize: this.cb.tickSize() * this.rowTicks(c.rowSize, 'footprint'),
    }
  }

  /* ── settings ──────────────────────────────────────────────────────────── */

  setVolumeConfig(patch: Partial<VolumeProfileConfig>): void {
    this.settings.volume = { ...this.settings.volume, ...patch }
    this.volumePrim?.setOptions(this.volumeOptions())
    this.rebuild()
    this.cb.onChange()
  }

  setMarketConfig(patch: Partial<MarketProfileConfig>): void {
    this.settings.market = { ...this.settings.market, ...patch }
    this.marketPrim?.setOptions(this.marketOptions())
    this.rebuild()
    this.cb.onChange()
  }

  setFootprintConfig(patch: Partial<FootprintConfig>): void {
    const tfChanged =
      patch.timeframe !== undefined &&
      JSON.stringify(patch.timeframe) !== JSON.stringify(this.settings.footprint.timeframe)
    const rowChanged =
      patch.rowSize !== undefined && patch.rowSize !== this.settings.footprint.rowSize
    this.settings.footprint = { ...this.settings.footprint, ...patch }
    if (tfChanged || rowChanged) {
      // The aggregator bakes in the timeframe and the row grid, so changing
      // either restarts the tape rather than mixing two bucketings.
      this.agg = null
      this.footprintBars = []
      this.ensureAggregator()
    }
    this.footprintPrim?.setOptions(this.footprintOptions())
    this.rebuild()
    this.cb.onChange()
  }

  toggle(overlay: ProfileOverlay, on?: boolean): void {
    const next = on ?? !this.settings[overlay].enabled
    if (overlay === 'volume') this.setVolumeConfig({ enabled: next })
    else if (overlay === 'market') this.setMarketConfig({ enabled: next })
    else this.setFootprintConfig({ enabled: next })
  }

  /* ── live order flow ───────────────────────────────────────────────────── */

  private ensureAggregator(): void {
    if (this.agg || !this.settings.footprint.enabled) return
    const c = this.settings.footprint
    this.agg = new FootprintAggregator(
      c.timeframe,
      this.cb.tickSize(),
      this.rowTicks(c.rowSize, 'footprint')
    )
  }

  /** Best bid/ask, used to classify each print as hitting the bid or the ask. */
  onDepth(depth: MarketDepth): void {
    const bid = depth.bids?.[0]?.price
    const ask = depth.asks?.[0]?.price
    if (typeof bid === 'number' && bid > 0) this.bestBid = bid
    if (typeof ask === 'number' && ask > 0) this.bestAsk = ask
  }

  /**
   * Feed one print into the live footprint. Classification is the quote rule:
   * at or above the ask is buy-initiated, at or below the bid is sell-initiated,
   * and an in-between print falls back to the tick rule (compare with the
   * previous print). Without a real classified tape this is the honest best
   * available, and it is what the panel's caveat refers to.
   */
  onTrade(tick: { time: number; price: number; qty: number }): void {
    if (!this.settings.footprint.enabled || !tick.qty) return
    this.ensureAggregator()
    if (!this.agg) return
    let side: 'bid' | 'ask'
    if (this.bestAsk > 0 && tick.price >= this.bestAsk) side = 'ask'
    else if (this.bestBid > 0 && tick.price <= this.bestBid) side = 'bid'
    else side = tick.price >= this.lastTradePrice ? 'ask' : 'bid'
    this.lastTradePrice = tick.price

    const u = this.agg.onTick({ time: tick.time, price: tick.price, qty: tick.qty, side })
    if (u.isNew) this.footprintBars.push(u.bar)
    else if (this.footprintBars.length) this.footprintBars[this.footprintBars.length - 1] = u.bar
    else this.footprintBars.push(u.bar)
    // Bound the live tape so a long session cannot grow without limit.
    if (this.footprintBars.length > 400)
      this.footprintBars.splice(0, this.footprintBars.length - 400)
    this.footprintPrim?.setBars([...this.footprintBars])
  }

  /* ── hover inspector ───────────────────────────────────────────────────── */

  onCrosshair(e: CrosshairMoveEvent): void {
    const onHover = this.cb.onHover
    if (!onHover) return
    const p = (e as { point?: { x: number; y: number } | null }).point
    if (!p) {
      onHover(null)
      return
    }
    if (this.footprintPrim) {
      const hit = this.footprintPrim.hoverAt(p.x, p.y)
      if (hit) {
        onHover({
          kind: 'footprint',
          lines: [
            ...(hit.cell
              ? [
                  { label: 'Price', value: String(hit.cell.price) },
                  { label: 'Bid', value: String(hit.cell.bidVol) },
                  { label: 'Ask', value: String(hit.cell.askVol) },
                ]
              : []),
            { label: 'Volume', value: String(Math.round(hit.stats.volume)) },
            { label: 'Delta', value: String(Math.round(hit.stats.delta)) },
            { label: 'Delta %', value: `${hit.stats.deltaPct.toFixed(1)}%` },
            { label: 'CVD', value: String(Math.round(hit.stats.cvd)) },
            { label: 'Trades', value: String(hit.stats.trades) },
          ],
        })
        return
      }
    }
    if (this.marketPrim) {
      const hit = this.marketPrim.hoverAt(p.x, p.y)
      if (hit) {
        onHover({
          kind: 'market',
          lines: [
            { label: 'Price', value: String(hit.price) },
            { label: 'TPO', value: `${hit.level.letters} (${hit.level.count})` },
            { label: 'POC', value: String(hit.session.poc) },
            { label: 'Value area', value: `${hit.session.val} – ${hit.session.vah}` },
            ...(hit.isSinglePrint ? [{ label: 'Note', value: 'single print' }] : []),
          ],
        })
        return
      }
    }
    onHover(null)
  }

  /* ── persistence ───────────────────────────────────────────────────────── */

  snapshot(): ProfileSettings {
    return structuredClone(this.settings)
  }

  restore(snap: Partial<ProfileSettings> | undefined): void {
    if (!snap) return
    this.settings = {
      volume: { ...DEFAULT_PROFILE_SETTINGS.volume, ...snap.volume },
      market: { ...DEFAULT_PROFILE_SETTINGS.market, ...snap.market },
      footprint: { ...DEFAULT_PROFILE_SETTINGS.footprint, ...snap.footprint },
    }
  }

  dispose(): void {
    if (this.rangeTimer) clearInterval(this.rangeTimer)
    this.rangeTimer = null
    this.chart = null
    this.volumePrim = null
    this.marketPrim = null
    this.footprintPrim = null
    this.agg = null
    this.footprintBars = []
  }
}
