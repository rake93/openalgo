/**
 * Framework-agnostic controller for the /charts workspace.
 *
 * Owns the openalgo-charts instance and every feature tier the engine ships:
 * 18 chart types (11 time-indexed + 6 movement-driven transforms), broker
 * timeframes, engine-backed and library indicators, drawing tools, profiles /
 * order flow, and the on-chart trading layer. React drives it through plain
 * methods and receives updates through the callback bag, so the canvas, the
 * tick path, and the WebSocket lifecycle stay off React's render path and
 * unmount is a single `destroy()`.
 *
 * Pane model
 *   0        price series, volume overlay (hidden scale), overlay indicators
 *   1..n     one pane per sub-pane indicator, allocated in creation order
 * Volume can also take a pane of its own, which shifts the indicator base by 1.
 *
 * A rebuild (chart type change, theme change, pane removal) destroys and
 * recreates the chart; every subsystem re-attaches from its own model, so the
 * controller's state — not the canvas — is the source of truth.
 */

import type { IRProgram } from '@openalgo/openscript'
import {
  type Bar,
  CandleBuilder,
  createChart,
  LogoWatermark,
  type LtpEvent,
  type MarketDepth,
  OpenAlgoDataFeed,
  OpenAlgoTradeFeed,
  OpenAlgoWsFeed,
  PaneLegend,
  type PriceLine,
  type SeriesApi,
  type SeriesStyle,
  type SeriesType,
  TickBarAggregator,
} from 'openalgo-charts'
import { runTransform } from 'openalgo-charts/transform'
import { type GEXHistoryResponse, type GEXLevelsResponse, gexApi } from '@/api/gex'
import { getScript, getVersion } from '@/api/indicators'
import { buildChartTheme, isLightTheme, volumeColor } from '@/lib/trading/chartTheme'
import { displayDp, fmtPrice, money, snapTick, tickSize } from '@/lib/trading/format'
import {
  type IntervalData,
  type IntervalGroup,
  intervalGroups,
  intervalSeconds,
  lookbackDays,
  pickInterval,
} from '@/lib/trading/intervals'
import type { AppMode, ThemeMode } from '@/stores/themeStore'
import {
  chartTypeDef,
  DEFAULT_TRANSFORM_SETTINGS,
  effectiveBoxSize,
  makeTransform,
  type TransformSettings,
} from './chart-types'
import { type DirectionVerdict, readDirection } from './direction'
import { DrawingManager, type DrawingSnapshot } from './drawing'
import { EventMarkerLayer, expiryEvent, type TradeRow, tradeMarkers } from './event-markers'
import { type GexInstrument, type GexLevelsConfig, GexLevelsManager } from './gex-levels'
import {
  type DataWindowRow,
  IndicatorHost,
  type IndicatorInstance,
  type IndicatorSnapshotEntry,
  type ScriptIdentity,
  type StyleOverrides,
  type TimeframeVisibility,
} from './indicator-host'
import { isSilentFallback } from './indicator-profile'
import {
  type LibraryIndicatorInstance,
  type LibraryIndicatorSnapshot,
  LibraryIndicators,
} from './library-indicators'
import { liveBarTimeframe, parseLiveBar } from './live-bars'
import { type ProfileHover, ProfileManager, type ProfileSettings } from './profiles'
import { mergedIntervalGroups, resampleBars, resamplePlan, sessionAnchor } from './resample'
import { TradingLayer, type TradingSnapshot, type TradingViewState } from './trading-layer'

export type { IndicatorInstance } from './indicator-host'
export type { LibraryIndicatorInstance } from './library-indicators'

type ChartInstance = ReturnType<typeof createChart>

const UP = '#26a69a'
const DOWN = '#ef5350'
const DERIVATIVE_EXCHANGES = new Set(['NFO', 'BFO', 'CDS', 'BCD', 'MCX', 'NCO', 'NCDEX'])
const QUOTE_ONLY = new Set(['NSE_INDEX', 'BSE_INDEX', 'MCX_INDEX', 'GLOBAL_INDEX'])
const VISIBLE_BARS = 140
/** Headroom over the volume overlay so its tallest bar fills a fifth of the pane. */
const VOLUME_MARGIN_TOP = 4

const nowSec = () => Math.floor(Date.now() / 1000)

/** The `definitionId` sentinel a custom OpenScript entry carries. It marks the
 *  KIND of entry; `script` is what identifies WHICH script. */
const IR_DEFINITION_ID = 'ir'

/** Outcome of resolving one saved entry: an IR to add, a reason it cannot be
 *  restored, or neither (a registry builtin, added by `definitionId`). */
interface RestoreResolution {
  ir?: IRProgram
  error?: string
}

/** Name a saved entry in a message the user can act on. */
function describeRestoreEntry(item: IndicatorSnapshotEntry): string {
  if (item.script) {
    return `script ${item.script.scriptId} (version ${item.script.versionId})`
  }
  return item.definitionId
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** Crosshair snapshot: the hovered bar plus each indicator's reading there. */
export interface CrosshairData {
  time: number | null
  /** Dataset bar index under the crosshair — what the series inspector (M8)
   *  needs to ask the engine about this bar. */
  index: number
  bar: Bar
  rows: DataWindowRow[]
}

/** Everything the toolbar needs about the loaded instrument. */
export interface SymbolView {
  symbol: string
  exchange: string
  name: string
  /** FnO lot-based entry: the qty input means lots, multiplied by lotsize. */
  lots: boolean
  lotsize: number
  tick: number
  freezeQty: number
  /** Indices and other quote-only segments cannot be traded. */
  quoteOnly: boolean
  productOptions: string[]
}

export interface SearchRow {
  symbol: string
  exchange: string
  name?: string
  [k: string]: unknown
}

/** Where the volume histogram is drawn. */
export type VolumeMode = 'overlay' | 'pane' | 'off'

export interface GridOptions {
  vertLines: boolean
  horzLines: boolean
}

/** The whole workspace, serialised into the layout's free-form JSON. */
export interface WorkspaceSnapshot {
  chartType: string
  transform: TransformSettings
  volumeMode: VolumeMode
  grid: GridOptions
  indicators: ReturnType<IndicatorHost['snapshot']>
  libraryIndicators: LibraryIndicatorSnapshot[]
  drawings: DrawingSnapshot
  profiles: ProfileSettings
  gexLevels: GexLevelsConfig
  trading: TradingSnapshot
  /** Fill and expiry markers on the chart. */
  markers: boolean
  /** Visible logical bar range, re-applied once data has loaded. */
  viewport?: { from: number; to: number }
}

/** What `onSymbolLoaded` reports: the instrument plus how it was loaded. */
export interface SymbolLoadedInfo extends SymbolView {
  interval: string
  bars: number
}

/**
 * Every callback is optional. The OpenScript editor at /charts/editor drives
 * this same controller with a much smaller bag (status, symbol, error), so
 * requiring the full set would break it.
 */
export interface WorkspaceCallbacks {
  onStatus?(text: string): void
  onToast?(message: string, kind: 'ok' | 'err' | ''): void
  /** Legacy alias used by the editor; receives the text of any error toast. */
  onError?(message: string): void
  onWsState?(state: string): void
  onIndicators?(list: IndicatorInstance[]): void
  onLibraryIndicators?(list: LibraryIndicatorInstance[]): void
  onSymbolLoaded?(info: SymbolLoadedInfo): void
  onLtp?(ltp: number, changePct: number | null): void
  onIntervals?(groups: IntervalGroup[], interval: string): void
  onDrawingChange?(state: {
    tool: string | null
    selected: string | null
    canUndo: boolean
    canRedo: boolean
  }): void
  onTrading?(view: TradingViewState): void
  onProfileHover?(hover: ProfileHover | null): void
  /** A new GEX snapshot arrived, or null when the instrument has none. */
  onGexSnapshot?(snapshot: GEXLevelsResponse | null): void
  /**
   * Recorded GEX history arrived for Gamma Bands, or null when the instrument
   * changed. The host uses it to tell the reader whether the current contract
   * is being recorded at all - an empty `points` list is the normal answer for
   * anything not on the recorder's watchlist.
   */
  onGexHistory?(history: GEXHistoryResponse | null): void
  /** A pane legend's gear was pressed — the host opens its settings dialog. */
  onIndicatorSettings?(instanceId: string, source: 'engine' | 'library'): void
  /** Confirm an order when the trading panel is not armed. */
  confirmOrder?(summary: string): Promise<boolean>
  onDirty?(): void
}

export interface WorkspaceOptions {
  apiKey: string
  wsUrl: string
  container: HTMLElement
  /** Full app theme. Preferred over `isDark`. */
  getTheme?: () => { mode: ThemeMode; appMode: AppMode }
  /** Legacy light/dark switch used by the editor. */
  isDark?: () => boolean
  callbacks: WorkspaceCallbacks
}

/** Every callback present, so the controller body never has to null-check. */
type ResolvedCallbacks = { [K in keyof WorkspaceCallbacks]-?: NonNullable<WorkspaceCallbacks[K]> }

/**
 * Fill in the optional callbacks. `onToast` forwards error text to the legacy
 * `onError` hook so a host that only supplies the old bag still sees failures,
 * and `confirmOrder` defaults to *declining* — an unwired host must never end
 * up placing an unconfirmed order.
 */
function resolveCallbacks(cb: WorkspaceCallbacks): ResolvedCallbacks {
  const noop = () => undefined
  return {
    onStatus: cb.onStatus ?? noop,
    onToast:
      cb.onToast ??
      ((message, kind) => {
        if (kind === 'err') cb.onError?.(message)
        else cb.onStatus?.(message)
      }),
    onError: cb.onError ?? noop,
    onWsState: cb.onWsState ?? noop,
    onIndicators: cb.onIndicators ?? noop,
    onLibraryIndicators: cb.onLibraryIndicators ?? noop,
    onSymbolLoaded: cb.onSymbolLoaded ?? noop,
    onLtp: cb.onLtp ?? noop,
    onIntervals: cb.onIntervals ?? noop,
    onDrawingChange: cb.onDrawingChange ?? noop,
    onTrading: cb.onTrading ?? noop,
    onProfileHover: cb.onProfileHover ?? noop,
    onGexSnapshot: cb.onGexSnapshot ?? noop,
    onGexHistory: cb.onGexHistory ?? noop,
    onIndicatorSettings: cb.onIndicatorSettings ?? noop,
    confirmOrder: cb.confirmOrder ?? (() => Promise.resolve(false)),
    onDirty: cb.onDirty ?? noop,
  }
}

export class ChartWorkspaceController {
  private readonly opts: WorkspaceOptions
  private readonly cb: ResolvedCallbacks

  readonly indicators: IndicatorHost
  readonly library: LibraryIndicators
  readonly drawing: DrawingManager
  readonly profiles: ProfileManager
  readonly gexLevels: GexLevelsManager
  readonly trading: TradingLayer
  readonly markers = new EventMarkerLayer()

  private chart: ChartInstance | null = null
  private price: SeriesApi | null = null
  private volume: SeriesApi | null = null
  private ltpLine: PriceLine | null = null
  private symbolLegend: PaneLegend | null = null
  private volumeLegend: PaneLegend | null = null

  private rest: OpenAlgoDataFeed
  private tradeFeed: OpenAlgoTradeFeed
  private ws: OpenAlgoWsFeed | null = null
  private builder: CandleBuilder | null = null
  /** Set instead of `builder` when the timeframe is a tick or volume bar. */
  private ticker: TickBarAggregator | null = null
  private offLtp: (() => void) | null = null
  private offDepth: (() => void) | null = null
  /** Last cumulative day volume seen, for {@link tradedSince}. -1 = no baseline. */
  private cumVolume = -1
  /**
   * Exchange-stated fields off the depth payload, for the direction readout. Left
   * undefined until seen — a broker that omits one must read as "unknown", not 0.
   */
  private book: { oi?: number; tbq?: number; tsq?: number; vwap?: number } = {}
  /** Fallback baseline: first OI and price seen live, when history carries none. */
  private baseOi: number | undefined
  private basePrice: number | undefined

  /** Raw broker bars — the single source of truth every view derives from. */
  private rawBars: Bar[] = []
  /** What is actually plotted (raw, or transform output for movement types). */
  private shownBars: Bar[] = []
  private sym: SymbolView | null = null
  private interval = '5m'
  private intervalList: IntervalGroup[] = []
  /** Resolutions the broker serves natively; everything else is resampled. */
  private brokerIntervals: string[] = []
  private chartType = 'candlestick'
  private transform: TransformSettings = { ...DEFAULT_TRANSFORM_SETTINGS }
  private volumeMode: VolumeMode = 'overlay'
  private grid: GridOptions = { vertLines: true, horzLines: true }

  private lastLtp: number | null = null
  private prevClose: number | null = null
  private liveBucket: number | null = null
  /**
   * The GEX underlying the manager was last told about, as `underlying:exchange`
   * (empty string for an instrument with no chain). Null until the first
   * `connectLive`, which is what makes a restored layout's study start polling.
   */
  private lastGexKey: string | null = null
  private depthActive = false
  private ltpPollTimer: ReturnType<typeof setInterval> | null = null
  private reconcileTimer: ReturnType<typeof setTimeout> | null = null
  private destroyed = false
  private previewId: string | null = null
  private crosshairCb: ((data: CrosshairData | null) => void) | null = null

  get manifest() {
    return this.indicators.manifest
  }

  get current() {
    return {
      symbol: this.sym?.symbol ?? '',
      exchange: this.sym?.exchange ?? '',
      interval: this.interval,
      chartType: this.chartType,
    }
  }

  get symbolView(): SymbolView | null {
    return this.sym
  }

  get transformSettings(): TransformSettings {
    return { ...this.transform }
  }

  get volumePlacement(): VolumeMode {
    return this.volumeMode
  }

  get gridOptions(): GridOptions {
    return { ...this.grid }
  }

  get bars(): readonly Bar[] {
    return this.rawBars
  }

  constructor(opts: WorkspaceOptions) {
    this.opts = opts
    this.cb = resolveCallbacks(opts.callbacks)
    this.rest = new OpenAlgoDataFeed({ baseUrl: '', apiKey: opts.apiKey })
    this.tradeFeed = new OpenAlgoTradeFeed({
      baseUrl: '',
      apiKey: opts.apiKey,
      strategy: 'chart-workspace',
    })
    this.indicators = new IndicatorHost({
      onIndicators: (list) => {
        this.cb.onIndicators(list)
        this.syncIndicatorLegends()
        this.cb.onDirty()
      },
      onError: (message) => this.cb.onToast(message, 'err'),
    })
    this.library = new LibraryIndicators({
      onChange: (list) => {
        this.cb.onLibraryIndicators(list)
        this.cb.onDirty()
      },
      onError: (message) => this.cb.onToast(message, 'err'),
      onSettingsRequest: (instanceId) => this.cb.onIndicatorSettings(instanceId, 'library'),
      onNeedsRebuild: () => {
        if (this.rawBars.length) this.buildChart()
      },
    })
    this.drawing = new DrawingManager({
      onChange: (state) => {
        this.cb.onDrawingChange(state)
        this.cb.onDirty()
      },
    })
    this.profiles = new ProfileManager({
      onChange: () => this.cb.onDirty(),
      onHover: (hover) => this.cb.onProfileHover(hover),
      refPrice: () => this.refPrice(),
      tickSize: () => this.tick(),
      visibleRange: () => this.chart?.getVisibleLogicalRange() ?? null,
    })
    this.gexLevels = new GexLevelsManager({
      onChange: () => this.cb.onDirty(),
      instrument: () => this.gexInstrument(),
      fetchLevels: (params, signal) => gexApi.getGEXLevels(params, signal),
      fetchHistory: (params, signal) => gexApi.getGEXHistory(params, signal),
      onSnapshot: (snap) => this.cb.onGexSnapshot(snap),
      onHistory: (history) => this.cb.onGexHistory(history),
      volumeProfileWidthOnSide: (side) => {
        const v = this.profiles.config.volume
        return v.enabled && v.side === side ? v.width : 0
      },
    })
    this.trading = new TradingLayer({
      feed: this.tradeFeed,
      api: (path, body) => this.api(path, body),
      symbol: () => this.sym,
      mode: () => (this.theme().appMode === 'live' ? 'live' : 'analyze'),
      marketPrice: () => this.marketPrice(),
      snap: (n) => this.snap(n),
      fmt: (n) => this.fmt(n),
      money,
      onToast: (m, k) => this.cb.onToast(m, k),
      onView: (v) => this.cb.onTrading(v),
      onDirty: () => this.cb.onDirty(),
      gate: (summary) => this.cb.confirmOrder(summary),
      topInset: () => this.priceLegendInset(),
    })
  }

  /* ── instrument-bound formatting ───────────────────────────────────────── */

  private refPrice(): number {
    return this.lastLtp || (this.rawBars.length ? this.rawBars[this.rawBars.length - 1].close : 0)
  }
  private tick(): number {
    return tickSize(this.sym?.tick, this.refPrice())
  }
  /** Display precision — capped by price magnitude; see `displayDp`. */
  private dp(): number {
    return displayDp(this.sym?.tick, this.refPrice())
  }
  private fmt(n: number): string {
    return fmtPrice(n, this.sym?.tick, this.refPrice(), this.dp())
  }
  private snap(n: number): number {
    return snapTick(n, this.sym?.tick, this.refPrice())
  }
  private marketPrice(): number | null {
    return (
      this.lastLtp ?? (this.rawBars.length ? this.rawBars[this.rawBars.length - 1].close : null)
    )
  }

  /**
   * The underlying whose option chain backs the GEX levels, or null when there
   * is none to fetch.
   *
   * An option's own chart is excluded deliberately: its price axis is premium,
   * not underlying price, so an underlying-price level cannot be drawn on it.
   * A future maps to its own root. Everything else passes through and the
   * server decides whether a chain exists — an exchange allowlist here would
   * duplicate knowledge that already lives in option_chain_service.
   */
  private gexInstrument(): GexInstrument | null {
    const sym = this.sym
    if (!sym) return null
    if (/\d+(CE|PE)$/.test(sym.symbol)) return null
    const root = sym.symbol.replace(/\d{2}[A-Z]{3}\d{2}FUT$/, '')
    return { underlying: root || sym.symbol, exchange: sym.exchange }
  }

  /**
   * Whether the GEX Levels study can run for the charted instrument at all —
   * the Studies panel disables the toggle when this is false. Kept as a thin
   * public wrapper over the private {@link gexInstrument} so the symbol-parsing
   * rule (no option's own chart, a future maps to its root) lives in one place.
   */
  get gexAvailable(): boolean {
    return this.gexInstrument() !== null
  }

  /**
   * The underlying GEX is computed for, or null if this chart has none.
   *
   * Public so the host can match the charted instrument against the snapshot
   * recorder's watchlist. Goes through the same private resolver as
   * {@link gexAvailable}, so the panel can never offer to record a symbol the
   * study itself would refuse to run on.
   */
  gexUnderlying(): GexInstrument | null {
    return this.gexInstrument()
  }

  /**
   * The app theme the canvas chrome tracks. `getTheme` carries the analyzer
   * palette as well as light/dark; `isDark` is the editor's simpler switch.
   */
  private theme(): { mode: ThemeMode; appMode: AppMode } {
    if (this.opts.getTheme) return this.opts.getTheme()
    return { mode: this.opts.isDark?.() ? 'dark' : 'light', appMode: 'live' }
  }

  /* ── OpenAlgo REST gateway ─────────────────────────────────────────────── */

  async api<T = { status?: string; message?: string; data?: unknown }>(
    path: string,
    body: Record<string, unknown> = {}
  ): Promise<T> {
    const res = await fetch(`/api/v1/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apikey: this.opts.apiKey, ...body }),
    })
    const j = (await res.json().catch(() => ({}))) as T & { status?: string; message?: string }
    if (!res.ok || j.status === 'error') {
      throw new Error(j.message || `${path} failed (${res.status})`)
    }
    return j
  }

  async search(query: string, exchange?: string, limit = 30): Promise<SearchRow[]> {
    try {
      const j = await this.api<{ data?: SearchRow[] }>('search', {
        query,
        ...(exchange ? { exchange } : {}),
      })
      return (j.data || []).slice(0, limit)
    } catch {
      return []
    }
  }

  private cleanError(e: unknown): string {
    console.error('[charts]', e)
    const raw = String((e as Error)?.message || e || 'request failed')
    return (
      raw
        .replace(/^openalgo-charts:\s*/i, '')
        .replace(/^\/api\/v1\/[\w/]+\s+failed\s+\(\d+\)(:\s*)?/i, '')
        .trim() || 'request failed'
    )
  }

  /* ── bootstrap ─────────────────────────────────────────────────────────── */

  async init(): Promise<void> {
    let groups: IntervalGroup[]
    try {
      const j = await this.api<{ data?: IntervalData }>('intervals')
      groups = intervalGroups(j.data || {})
    } catch {
      groups = intervalGroups({
        minutes: ['1m', '3m', '5m', '15m', '30m'],
        hours: ['1h'],
        days: ['D'],
        weeks: ['W'],
      })
    }
    // What the broker serves natively, kept for `resamplePlan`.
    this.brokerIntervals = groups.flatMap((g) => g.items)
    // What the menu offers: the native set plus everything derivable from it.
    this.intervalList = mergedIntervalGroups(groups)
    this.interval = pickInterval(this.intervalList, this.interval)
    this.cb.onIntervals(this.intervalList, this.interval)

    this.ws = new OpenAlgoWsFeed({ url: this.opts.wsUrl, apiKey: this.opts.apiKey })
    this.ws.onState((s: string) => {
      this.cb.onWsState(s)
      if (s === 'closed' || s === 'error' || s === 'reconnecting') this.startLtpFallback()
    })
    this.ws.onControl((m) => {
      if (m.type === 'auth' && m.status !== 'success') this.cb.onWsState('auth failed')
    })
    this.ws.onOrderUpdate((e) => {
      this.trading.onOrderUpdate(e)
      // A fill is a new marker on the chart.
      if (e.status === 'complete') void this.refreshTradeMarkers()
    })
    this.ws.connect()
    this.ws.subscribeOrders()
    this.trading.start()
  }

  get intervalGroups(): IntervalGroup[] {
    return this.intervalList
  }

  /* ── chart build ───────────────────────────────────────────────────────── */

  /** First pane index available to sub-pane indicators. */
  private indicatorBasePane(): number {
    return this.volumeMode === 'pane' ? 2 : 1
  }

  private buildChart(): void {
    if (this.chart) this.chart.destroy()
    this.opts.container.innerHTML = ''
    const { mode, appMode } = this.theme()
    const light = isLightTheme(mode, appMode)
    const dp = this.dp()

    const chart = createChart(this.opts.container, {
      priceAxisWidth: 78,
      theme: buildChartTheme(mode, appMode),
      grid: { vertLines: this.grid.vertLines, horzLines: this.grid.horzLines },
    })
    this.chart = chart

    // Row 0 of the price pane: symbol + timeframe + the O/H/L/C/V readout. Added
    // before any indicator so their legend rows stack underneath it.
    this.symbolLegend = new PaneLegend({ id: 'symbol', title: '', params: '', row: 0, actions: [] })
    chart.addPrimitive(this.symbolLegend, 0)

    // Every indicator legend belonged to the chart just destroyed. Dropping the
    // handles is what makes `syncIndicatorLegends` build fresh ones — otherwise
    // it finds a stale entry, takes the "already exists" path, and the rows are
    // silently missing after a symbol or timeframe change.
    this.indicatorLegends.clear()

    const def = chartTypeDef(this.chartType)
    const style: SeriesStyle = def.baseline
      ? { baseValue: this.rawBars.reduce((s, b) => s + b.close, 0) / (this.rawBars.length || 1) }
      : {}
    this.price = chart.addSeries(def.series as SeriesType, {
      style,
      priceFormat: { type: 'custom', formatter: (p: number) => p.toFixed(dp) },
    })

    this.volume = null
    this.volumeLegend = null
    if (this.volumeMode !== 'off') {
      if (this.volumeMode === 'overlay') {
        // An overlay price scale (priceScaleId '') autoscales on its own but
        // draws no axis, so the right-hand column stays a clean price ladder.
        this.volume = chart.addSeries('histogram', {
          paneIndex: 0,
          priceScaleId: '',
          style: { color: volumeColor(mode, appMode), base: 0 },
          priceFormat: { type: 'volume' },
        })
        // `marginTop` is headroom added to the data span, not a reserved
        // fraction: autoscale gives `max = high * (1 + marginTop)` for a series
        // based at zero, so the tallest bar ends up at `1 / (1 + marginTop)` of
        // the pane. 4 puts it at a fifth, which is the height a volume study
        // wants under price.
        this.volume.priceScale().setOptions({ marginTop: VOLUME_MARGIN_TOP, marginBottom: 0 })
        this.volumeLegend = new PaneLegend({ id: 'volume', title: 'Vol', params: '', actions: [] })
        chart.addPrimitive(this.volumeLegend, 0)
      } else {
        this.volume = chart.addSeries('histogram', {
          paneIndex: 1,
          style: { color: volumeColor(mode, appMode) },
          priceFormat: { type: 'volume' },
        })
        this.volumeLegend = new PaneLegend({
          id: 'volume',
          title: 'Volume',
          params: '',
          actions: [],
        })
        chart.addPrimitive(this.volumeLegend, 1)
      }
    }

    this.applyData()

    // Rebind every subsystem to the fresh chart. Order matters: the engine-backed
    // indicators claim their panes first, then the library tier continues the
    // allocation from where they stopped.
    const base = this.indicatorBasePane()
    this.indicators.attachChart({ chart, anchorSeries: this.price, basePane: base })
    const claimed = this.indicators.list().filter((i) => i.pane !== undefined).length
    this.library.attachChart(chart, base + claimed)
    this.drawing.attachChart(chart)
    this.profiles.attachChart(chart, this.rawBars, !def.movement)
    this.gexLevels.attachChart(chart)
    this.trading.attachChart(chart)

    // Default zoom: a fixed number of recent bars, so the visible price range is
    // the same on every screen width.
    if (this.shownBars.length > VISIBLE_BARS) {
      const to = this.shownBars.length - 1 + 4
      chart.timeScale.setVisibleLogicalRange({ from: to - VISIBLE_BARS, to })
    } else if (chart.timeScale.barSpacing > 14) {
      chart.timeScale.setBarSpacing(14)
    }

    const lp = this.marketPrice()
    this.ltpLine =
      lp != null
        ? chart.addPriceLine(
            { price: lp, color: '#e0b020', lineWidth: 1, dashed: true, id: 'ltp' },
            0
          )
        : null

    chart.addPrimitive(
      new LogoWatermark({
        src: '/images/openalgo-mark.svg',
        position: 'bottom-left',
        height: 30,
        margin: 10,
        opacity: 0.75,
        tint: light ? undefined : '#e4e8f4',
      }),
      chart.panes().length - 1
    )

    // Fill arrows hang off the price series; the expiry flag rides the time axis.
    this.markers.attach(this.price, chart.addEventMarkers(0))

    this.syncIndicatorLegends()
    this.attachChartEvents()
    this.setLegend(this.shownBars[this.shownBars.length - 1] ?? null)
  }

  private attachChartEvents(): void {
    const chart = this.chart
    if (!chart) return

    chart.subscribeCrosshairMove((e) => {
      this.setLegend(e.bar ?? this.shownBars[this.shownBars.length - 1] ?? null)
      this.profiles.onCrosshair(e)
      this.library.onCrosshair(e.index)
      this.updateIndicatorLegendValues(e.index)
      const cb = this.crosshairCb
      if (!cb) return
      if (e.index == null || !e.bar) {
        cb(null)
        return
      }
      cb({ time: e.time, index: e.index, bar: e.bar, rows: this.indicators.valuesAtIndex(e.index) })
    })

    // One click channel: the trading layer claims its own ids, the pane legends
    // of engine-backed indicators claim `os:<instanceId>::<action>`.
    chart.subscribeClick((id) => {
      if (id.startsWith('os:')) {
        this.handleIndicatorLegendAction(id)
        return
      }
      this.trading.onClick(id)
    })
    chart.subscribeDrag(
      (id, price, time) => this.trading.onDrag(id, price, time),
      (id, price, time) => this.trading.onDragEnd(id, price, time)
    )

    // The library tier's legends emit rather than opening a dialog (the engine
    // ships no DOM), so the React layer renders the generated form.
    chart.on('indicatorSettings', (p) => this.library.requestSettings(p))
    chart.on('indicatorRemoved', (p) => this.library.onRemovedByLegend(p))
    for (const ev of ['paneRemoved', 'paneMoved']) {
      chart.on(ev, () => this.cb.onDirty())
    }
  }

  /* ── data plumbing ─────────────────────────────────────────────────────── */

  /** Push raw bars through the active chart type and refresh every series. */
  private applyData(): void {
    if (!this.price) return
    const def = chartTypeDef(this.chartType)
    const t = makeTransform(this.chartType, this.transform, this.refPrice(), this.tick())
    if (t) {
      this.shownBars = runTransform(t, this.rawBars)
    } else {
      this.shownBars = this.rawBars
    }
    this.price.setData(this.shownBars)
    if (this.volume) {
      // A transform emits fewer elements than the raw bars, so companion series
      // must be re-bucketed onto the transformed times — feeding them the raw
      // bars would scatter every element back onto the raw timestamps.
      this.volume.setData(def.movement ? this.bucketVolume(this.shownBars) : this.volumeBars())
    }
    this.profiles.setBars(this.rawBars)
  }

  private volumeBars(): Bar[] {
    return this.rawBars.map((b) => ({
      time: b.time,
      open: 0,
      high: b.volume || 0,
      low: 0,
      close: b.volume || 0,
    }))
  }

  /** Sum the raw volume of the source bars behind each transformed element. */
  private bucketVolume(elements: readonly Bar[]): Bar[] {
    const out: Bar[] = []
    let ri = 0
    for (const el of elements) {
      let v = 0
      while (ri < this.rawBars.length && this.rawBars[ri].time <= el.time) {
        v += this.rawBars[ri].volume || 0
        ri++
      }
      out.push({ time: el.time, open: 0, high: v, low: 0, close: v })
    }
    let rest = 0
    while (ri < this.rawBars.length) {
      rest += this.rawBars[ri].volume || 0
      ri++
    }
    const last = out[out.length - 1]
    if (last && rest) {
      last.high += rest
      last.close += rest
    }
    return out
  }

  /* ── legends ───────────────────────────────────────────────────────────── */

  private setLegend(bar: Bar | null): void {
    if (!this.symbolLegend || !this.sym) return
    const lot = this.sym.lots ? ` · lot ${this.sym.lotsize}` : ''
    this.symbolLegend.setOptions({
      title: this.sym.symbol,
      params: `${this.interval} · ${this.sym.exchange}${lot}`,
    })
    if (!bar) {
      this.symbolLegend.setValues([])
      this.volumeLegend?.setValues([])
      return
    }
    const col = bar.close >= bar.open ? UP : DOWN
    const ref = this.prevClose
    const mark = this.lastLtp ?? bar.close
    const chg = ref ? mark - ref : null
    const pct = ref ? ((mark - ref) / ref) * 100 : null
    this.symbolLegend.setValues([
      { label: 'O', text: this.fmt(bar.open), color: col },
      { label: 'H', text: this.fmt(bar.high), color: col },
      { label: 'L', text: this.fmt(bar.low), color: col },
      { label: 'C', text: this.fmt(bar.close), color: col },
      ...(chg != null && pct != null
        ? [
            {
              text: `${chg >= 0 ? '+' : ''}${this.fmt(chg)} (${chg >= 0 ? '+' : ''}${pct.toFixed(2)}%)`,
              color: chg >= 0 ? UP : DOWN,
            },
          ]
        : []),
    ])
    // A forming bar has no volume until the feed reports a traded quantity, and
    // printing `0` reads as "nothing traded" rather than "not known yet".
    this.volumeLegend?.setValues([{ text: bar.volume ? compact(bar.volume) : '—', color: col }])
  }

  /**
   * One pane-legend row per engine-backed indicator, mirroring what the library
   * tier draws for its own — the OpenScript renderer only creates series, so the
   * chrome (name, params, eye, gear, close) belongs to the host.
   */
  private readonly indicatorLegends = new Map<string, PaneLegend>()

  private syncIndicatorLegends(): void {
    const chart = this.chart
    if (!chart) return
    const live = new Set<string>()
    for (const inst of this.indicators.list()) {
      live.add(inst.instanceId)
      const pane = inst.pane ?? 0
      let legend = this.indicatorLegends.get(inst.instanceId)
      // §13.3: mark an indicator whose last LIVE TICK lost the incremental path.
      // `PaneLegend` (openalgo-charts) has no badge or tooltip API, so the marker
      // rides the params line and CARRIES ITS OWN SHORT REASON rather than
      // relying on a hover the legend cannot show. The profile panel has detail.
      const profile = this.indicators.lastProfile(inst.instanceId)
      const flagged = profile !== undefined && isSilentFallback(profile)
      const params = flagged
        ? `${summariseInputs(inst.inputs)}  ⚠ full recompute`
        : summariseInputs(inst.inputs)
      if (!legend) {
        legend = new PaneLegend({
          id: `os:${inst.instanceId}`,
          title: inst.name,
          params,
          actions:
            inst.pane === undefined
              ? ['hide', 'settings', 'close']
              : ['up', 'down', 'hide', 'settings', 'maximize', 'close'],
        })
        this.indicatorLegends.set(inst.instanceId, legend)
        chart.addPrimitive(legend, pane)
      } else {
        legend.setOptions({ title: inst.name, params })
      }
    }
    for (const [id, legend] of this.indicatorLegends) {
      if (live.has(id)) continue
      chart.removePrimitive(legend)
      this.indicatorLegends.delete(id)
    }
    // The Buy/Sell panel no longer tracks the stack's height — it floats over the
    // indicator rows at a fixed dock — so a legend change does not move it.
  }

  /** Route `os:<instanceId>::<action>` from an engine-backed indicator legend. */
  private handleIndicatorLegendAction(externalId: string): void {
    const [head, action] = externalId.split('::')
    const instanceId = head.slice(3)
    const inst = this.indicators.list().find((i) => i.instanceId === instanceId)
    if (!inst) return
    switch (action) {
      case 'close':
        void this.removeIndicator(instanceId)
        break
      case 'settings':
        this.cb.onIndicatorSettings(instanceId, 'engine')
        break
      case 'hide': {
        const next = !inst.hidden
        this.indicatorLegends.get(instanceId)?.setOptions({ hidden: next })
        this.indicators.setHidden(instanceId, next)
        break
      }
      case 'up':
      case 'down':
        // Reorder the instances, then rebuild — panes are allocated in instance
        // order, so moving the chart's pane alone would be undone next rebuild.
        if (this.indicators.movePane(instanceId, action === 'up' ? -1 : 1)) {
          this.buildChart()
          void this.indicators.recreateSessions()
          this.cb.onDirty()
        }
        break
      case 'maximize':
        if (inst.pane !== undefined) this.chart?.maximizePane(inst.pane)
        break
      default:
        break
    }
  }

  /**
   * Where the inline Buy/Sell panel docks: clear of the symbol row, and sitting
   * *over* the indicator legends rather than below them.
   *
   * It used to clear the whole legend stack, which meant every indicator added or
   * removed shifted the panel down or up — and once dragged, it had to be put back
   * again. The panel is a floating control on the top layer, so overlaying the
   * indicator rows is both stable and what a trader expects; its hits declare a
   * priority so a legend underneath cannot swallow them.
   */
  private priceLegendInset(): number {
    // 6px top inset plus ~18px for the symbol row, matching PaneLegend's metrics.
    return 12 + 18
  }

  /** Push the hovered bar's readings into each engine-backed legend row. */
  private updateIndicatorLegendValues(index: number | null): void {
    if (!this.indicatorLegends.size) return
    const rows = index == null ? [] : this.indicators.valuesAtIndex(index)
    const byInstance = new Map(rows.map((r) => [r.instanceId, r]))
    for (const [instanceId, legend] of this.indicatorLegends) {
      const row = byInstance.get(instanceId)
      legend.setValues(
        row
          ? row.values.map((v) => ({
              // `null` = the output is `na` at this bar. The data window keeps
              // these rows now (they are what the series inspector explains), so
              // the legend has to render them rather than format `null` as a price.
              text: v.value === null ? '—' : this.fmt(v.value),
              color: v.color,
            }))
          : []
      )
    }
  }

  /* ── symbol + history ──────────────────────────────────────────────────── */

  /**
   * Load an instrument. Both call shapes are supported because the OpenScript
   * editor drives this controller with plain strings while the workspace passes
   * a whole search row (which already carries lotsize and friends).
   */
  async load(symbol: string, exchange: string, interval?: string): Promise<boolean>
  async load(pick: SearchRow, interval?: string, opts?: { silent?: boolean }): Promise<boolean>
  async load(
    a: string | SearchRow,
    b?: string,
    c?: string | { silent?: boolean }
  ): Promise<boolean> {
    const pick: SearchRow = typeof a === 'string' ? { symbol: a, exchange: b as string } : a
    const interval = typeof a === 'string' ? (c as string | undefined) : b
    const opts = (typeof a === 'string' ? undefined : (c as { silent?: boolean })) ?? {}
    return this.loadInstrument(pick, interval, opts)
  }

  private async loadInstrument(
    pick: SearchRow,
    interval?: string,
    opts: { silent?: boolean } = {}
  ): Promise<boolean> {
    if (this.destroyed) return false
    const nextInterval = interval ?? this.interval
    if (this.ws && this.sym) {
      try {
        this.ws.unsubscribe(
          this.sym.quoteOnly ? 'LTP' : 'Depth',
          this.sym.symbol,
          this.sym.exchange
        )
      } catch {
        /* not subscribed */
      }
    }

    let info: Record<string, unknown> = { ...pick }
    try {
      const j = await this.api<{ data?: Record<string, unknown> }>('symbol', {
        symbol: pick.symbol,
        exchange: pick.exchange,
      })
      info = { ...pick, ...(j.data || {}) }
    } catch {
      /* the search row already carries the essentials */
    }
    const exchange = String(info.exchange)
    const lotsize = Number(info.lotsize) || 1
    const lots = DERIVATIVE_EXCHANGES.has(exchange) && lotsize > 1
    this.sym = {
      symbol: String(info.symbol),
      exchange,
      name: String(info.name || ''),
      lotsize,
      lots,
      tick: Number(info.tick_size) || 0.05,
      freezeQty: Number(info.freeze_qty) || 1,
      quoteOnly: QUOTE_ONLY.has(exchange),
      productOptions: lots ? ['MIS', 'NRML'] : ['MIS', 'CNC'],
    }
    this.interval = nextInterval
    this.lastLtp = null
    this.prevClose = null
    this.liveBucket = null
    this.cb.onStatus(`Loading ${this.sym.symbol} ${nextInterval}…`)

    // Tick and volume bars cannot be derived from OHLCV, so they skip history
    // entirely and build from the live stream instead of failing on an empty
    // response. See `live-bars.ts`.
    const live = parseLiveBar(nextInterval)
    if (live) {
      this.rawBars = []
    } else {
      const to = nowSec()
      try {
        this.rawBars = await this.fetchBars(
          nextInterval,
          to - lookbackDays(nextInterval) * 86_400,
          to
        )
      } catch (e) {
        this.rawBars = []
        if (!opts.silent) this.cb.onToast(`history error: ${this.cleanError(e)}`, 'err')
        return false
      }
      if (!this.rawBars.length) {
        if (!opts.silent) {
          this.cb.onToast(
            `no history for ${this.sym.symbol} ${this.sym.exchange} ${nextInterval}`,
            'err'
          )
        }
        return false
      }
    }
    const last = this.rawBars[this.rawBars.length - 1]
    this.prevClose = last ? (this.rawBars[this.rawBars.length - 2]?.close ?? last.open) : null
    this.lastLtp = last ? last.close : null

    this.buildChart()
    await this.syncIndicatorDataset()
    this.cb.onSymbolLoaded({
      ...this.sym,
      interval: nextInterval,
      bars: this.rawBars.length,
    })
    if (this.lastLtp != null) this.cb.onLtp(this.lastLtp, this.changePct())
    this.cb.onStatus(
      live
        ? `${this.sym.symbol} ${nextInterval} — building from live ticks`
        : `${this.sym.symbol} ${nextInterval} — ${this.rawBars.length} bars`
    )
    this.connectLive()
    // A live-bar chart has no history to reconcile against.
    if (!live) this.scheduleReconcile()
    this.markers.setExpiries(expiryEvent(info.expiry))
    void this.refreshTradeMarkers()
    // Position-tool sizing follows the instrument: shares for cash, whole lots
    // for derivatives, capped by the exchange freeze quantity.
    this.drawing.bindInstrument({
      lotSize: this.sym.lots ? this.sym.lotsize : 1,
      freezeQty: this.sym.freezeQty,
    })
    return true
  }

  /**
   * Draw the account's own fills for this symbol. Best-effort: a broker that
   * cannot serve a tradebook, or that stamps trades in a format we cannot read,
   * simply leaves the chart unmarked.
   */
  async refreshTradeMarkers(): Promise<void> {
    if (!this.sym) return
    try {
      const j = await this.api<{ data?: TradeRow[] }>('tradebook')
      if (!this.sym) return
      this.markers.setFills(
        tradeMarkers(j.data ?? [], this.sym.symbol, this.sym.exchange, (n) => this.fmt(n))
      )
    } catch {
      /* no tradebook available for this broker or session */
    }
  }

  /** Toggle the fill / expiry markers. */
  setMarkersVisible(on: boolean): void {
    this.markers.setEnabled(on)
    this.cb.onDirty()
  }

  /**
   * History for one interval, resampling when the broker does not serve it
   * natively. The source request is the coarsest native interval that divides
   * the target, so a 2h chart costs a 1h request rather than a 1m one.
   */
  private async fetchBars(interval: string, from: number, to: number): Promise<Bar[]> {
    if (!this.sym) return []
    const plan = resamplePlan(interval, this.brokerIntervals)
    const bars = await this.rest.getBars({
      symbol: this.sym.symbol,
      exchange: this.sym.exchange,
      interval: plan ? plan.source : interval,
      from,
      to,
    })
    return plan ? resampleBars(bars, plan.targetSec, this.sym.exchange) : bars
  }

  private changePct(): number | null {
    if (this.lastLtp == null || !this.prevClose) return null
    return ((this.lastLtp - this.prevClose) / this.prevClose) * 100
  }

  private async syncIndicatorDataset(): Promise<void> {
    if (!this.sym) return
    try {
      await this.indicators.setDataset(this.rawBars, {
        symbol: this.sym.symbol,
        exchange: this.sym.exchange,
        interval: this.interval,
      })
    } catch (e) {
      // Indicators are additive: the chart and trading keep working without them.
      console.error('[charts] indicator engine unavailable', e)
    }
  }

  /* ── live data ─────────────────────────────────────────────────────────── */

  private connectLive(): void {
    if (!this.ws || !this.sym) return
    const live = parseLiveBar(this.interval)
    this.ticker = live ? new TickBarAggregator(liveBarTimeframe(live)) : null
    const sec = live ? null : intervalSeconds(this.interval)
    this.builder = sec
      ? new CandleBuilder({
          intervalSec: sec,
          volumeMode: 'ltq-sum',
          // Live buckets must land on the same boundaries the history does, so
          // they anchor to the 09:15 IST open exactly like the resampler.
          sessionAnchorSec: sessionAnchor(nowSec(), this.sym.exchange),
        })
      : null
    const last = this.rawBars[this.rawBars.length - 1]
    if (this.builder && last) this.builder.seed(last)
    this.depthActive = false
    this.cumVolume = -1
    this.book = {}
    this.baseOi = undefined
    this.basePrice = undefined
    // The footprint keys off this instrument's prices and this timeframe's bar
    // times, so it starts over whenever either changes.
    this.profiles.resetTape()
    // GEX is keyed on the underlying, not the timeframe. connectLive also runs
    // on a timeframe change, and re-fetching a whole option chain (and blanking
    // the panel) because the user switched 5m to 15m would be a visible bug.
    // The starting null is also what kicks a restored layout's study into
    // polling: restore() deliberately leaves the timer stopped because the
    // instrument is not resolved yet, and this is the first point at which it is.
    const gex = this.gexInstrument()
    const gexKey = gex ? `${gex.underlying}:${gex.exchange}` : ''
    if (gexKey !== this.lastGexKey) {
      this.lastGexKey = gexKey
      this.gexLevels.instrumentChanged()
    }
    this.offLtp?.()
    this.offDepth?.()
    this.offLtp = this.ws.onLtp((e: LtpEvent) => {
      this.cb.onWsState('live')
      this.stopLtpFallback()
      this.onTick(e)
    })
    this.offDepth = this.ws.onDepth((symbol: string, _exchange: string, depth: MarketDepth) => {
      if (!this.sym || symbol !== this.sym.symbol) return
      this.depthActive = true
      this.trading.onDepth(depth)
      this.profiles.onDepth(depth)
      this.absorbBook(depth)
      if (typeof depth.ltp === 'number' && depth.ltp > 0) {
        this.cb.onWsState('live')
        this.stopLtpFallback()
        this.onTick({ ltp: depth.ltp, ltq: depth.ltq, volume: depth.volume })
      }
    })
    // One subscription per symbol: indices have no order book (LTP), tradeables
    // take Depth, whose payload embeds the LTP.
    if (this.sym.quoteOnly) {
      this.ws.subscribe('LTP', this.sym.symbol, this.sym.exchange)
    } else {
      this.ws.subscribe('Depth', this.sym.symbol, this.sym.exchange, 5)
    }
  }

  /**
   * Keep the exchange-stated fields off a depth payload, and latch the baseline
   * the OI buildup is measured from.
   *
   * Each field is stored only when present, so a broker that sends open interest
   * but not the book totals leaves those unknown rather than zero. The baseline
   * takes the first OI *and* the price from that same message, so both sides of the
   * buildup comparison start at one instant.
   */
  private absorbBook(depth: MarketDepth): void {
    if (typeof depth.oi === 'number' && depth.oi > 0) this.book.oi = depth.oi
    if (typeof depth.totalBuyQty === 'number') this.book.tbq = depth.totalBuyQty
    if (typeof depth.totalSellQty === 'number') this.book.tsq = depth.totalSellQty
    if (typeof depth.atp === 'number' && depth.atp > 0) this.book.vwap = depth.atp
    if (this.baseOi === undefined && this.book.oi !== undefined && depth.ltp > 0) {
      this.baseOi = this.book.oi
      this.basePrice = depth.ltp
    }
  }

  /**
   * The reference open interest and price a buildup is measured against.
   *
   * Indian convention reads "change in OI" against the **previous session's
   * close**, so that is what this finds: the last loaded bar before today's
   * session anchor, whose `oi` the history API carries per bar. Falling back to
   * the first value seen after connecting would measure an arbitrary window and
   * silently disagree with every OI table the user compares it to.
   *
   * Falls back in two steps: today's first bar when only today is loaded, then the
   * first live observation when history reports no open interest at all.
   */
  private oiBaseline(): { oi?: number; price?: number } {
    if (!this.sym) return {}
    const anchor = sessionAnchor(nowSec(), this.sym.exchange)
    let prevSessionClose: Bar | undefined
    let firstToday: Bar | undefined
    for (const b of this.rawBars) {
      if (b.oi === undefined) continue
      if (b.time < anchor) prevSessionClose = b
      else if (!firstToday) firstToday = b
    }
    const ref = prevSessionClose ?? firstToday
    if (ref?.oi !== undefined) return { oi: ref.oi, price: ref.close }
    return { oi: this.baseOi, price: this.basePrice }
  }

  /** True when the OI baseline came from history rather than the live session. */
  get oiBaselineIsSession(): boolean {
    return this.oiBaseline().oi !== undefined && this.rawBars.some((b) => b.oi !== undefined)
  }

  /**
   * Current market-direction verdict, assembled from the studies and the feed.
   *
   * Collection only — every rule lives in {@link readDirection}. Inputs absent for
   * this instrument (no open interest on equity, no book at all on a quote-only
   * index) are simply not supplied, and the engine reports those signals as
   * unavailable rather than guessing.
   */
  get direction(): DirectionVerdict {
    const vas = this.profiles.valueAreas()
    const tape = this.profiles.footprintTape
    const last = tape[tape.length - 1]

    let cum = 0
    const cvdSeries = tape.map((b) => (cum += b.delta))
    const base = this.oiBaseline()
    // The live feed is the freshest OI; history's last bar covers a chart opened
    // outside market hours, when no depth packet has arrived yet.
    const lastHistoryOi = [...this.rawBars].reverse().find((b) => b.oi !== undefined)?.oi

    return readDirection({
      hasOi: this.sym ? DERIVATIVE_EXCHANGES.has(this.sym.exchange) : false,
      oi: this.book.oi ?? lastHistoryOi,
      baselineOi: base.oi,
      price: this.lastLtp ?? undefined,
      baselinePrice: base.price,
      totalBuyQty: this.book.tbq,
      totalSellQty: this.book.tsq,
      vwap: this.book.vwap,
      tick: this.sym?.tick,
      valueArea: vas.current,
      prevValueArea: vas.previous,
      barDelta: last?.delta,
      barVolume: last ? last.cells.reduce((a, c) => a + c.bidVol + c.askVol, 0) : undefined,
      cvdSeries: tape.length > 0 ? cvdSeries : undefined,
    })
  }

  /** True for a charted put, where a rising premium means a falling underlying. */
  get isPut(): boolean {
    return /PE$/.test(this.sym?.symbol ?? '')
  }

  /**
   * Quantity traded since the previous message.
   *
   * `ltq` is the size of the *last* trade and is sticky — the broker repeats it
   * unchanged on every book update until the next print — so summing it counts
   * the same trade over and over. Cumulative day volume differences cleanly
   * instead, and every broker's depth payload carries it. The first message only
   * establishes the baseline, and a drop means the daily reset rather than a
   * negative quantity.
   *
   * A feed with no cumulative volume at all (an index streaming LTP only) has
   * nothing better than the sticky value, so it falls back to assuming one print
   * per message. Once a baseline exists it is never abandoned for that guess.
   */
  private tradedSince(e: { ltq?: number; volume?: number }): number {
    const cum = e.volume
    if (typeof cum !== 'number' || !(cum > 0)) return this.cumVolume < 0 ? (e.ltq ?? 0) : 0
    const prev = this.cumVolume
    this.cumVolume = cum
    return prev < 0 || cum < prev ? 0 : cum - prev
  }

  private onTick(e: {
    symbol?: string
    ltp: number
    ltq?: number
    volume?: number
    timeSec?: number
  }): void {
    if (this.destroyed || !this.sym) return
    if (e.symbol && e.symbol !== this.sym.symbol) return
    this.lastLtp = e.ltp
    this.cb.onLtp(e.ltp, this.changePct())
    this.ltpLine?.setPrice(e.ltp)
    this.trading.onLtp(e.ltp)
    const time = e.timeSec ?? nowSec()
    const qty = this.tradedSince(e)
    // Exactly one aggregator is live: the clock-bucketing candle builder, or
    // the tick/volume aggregator when the timeframe is a live-bar mode.
    const update = this.ticker
      ? this.ticker.onTick({ time, price: e.ltp, qty })
      : this.builder?.onTick({ time, price: e.ltp, ltq: qty })
    if (update) {
      this.liveBucket = update.bar.time
      if (update.isNew) this.rawBars.push(update.bar)
      else this.rawBars[this.rawBars.length - 1] = update.bar
      // The footprint buckets on the chart's own bar clock, not one of its own:
      // its columns are positioned by an exact time match against the plotted
      // bars, so any other bucketing lands on times the chart does not have and
      // is silently dropped. This also means the footprint inherits whatever the
      // chart is bucketing by, tick and volume bars included.
      this.profiles.onTrade({ time: update.bar.time, price: e.ltp, qty })
      this.applyData()
      this.indicators.onBar(update.bar, update.isNew)
      this.library.onData()
    }
    this.setLegend(this.shownBars[this.shownBars.length - 1] ?? null)
  }

  /** WS-down fallback: poll quotes so the LTP and forming candle stay live. */
  private startLtpFallback(): void {
    if (this.ltpPollTimer) return
    this.ltpPollTimer = setInterval(() => {
      void (async () => {
        if (!this.sym) return
        try {
          const j = await this.api<{ data?: { ltp?: number; bid?: number; ask?: number } }>(
            'quotes',
            { symbol: this.sym.symbol, exchange: this.sym.exchange }
          )
          const q = j.data || {}
          if (typeof q.ltp === 'number' && q.ltp > 0) {
            this.onTick({ symbol: this.sym.symbol, ltp: q.ltp, timeSec: nowSec() })
          }
          if (!this.depthActive && typeof q.bid === 'number' && typeof q.ask === 'number') {
            this.trading.onQuote(q.bid, q.ask)
          }
          this.cb.onWsState('fallback')
        } catch {
          /* next cycle retries */
        }
      })()
    }, 4000)
  }

  private stopLtpFallback(): void {
    if (!this.ltpPollTimer) return
    clearInterval(this.ltpPollTimer)
    this.ltpPollTimer = null
  }

  /** Periodically snap completed bars back to the broker's own OHLC/volume. */
  private scheduleReconcile(): void {
    if (this.reconcileTimer) clearTimeout(this.reconcileTimer)
    this.reconcileTimer = setTimeout(
      () => {
        void (async () => {
          try {
            if (this.sym && this.rawBars.length) {
              const to = nowSec()
              const fresh = await this.fetchBars(
                this.interval,
                to - Math.min(3, lookbackDays(this.interval)) * 86_400,
                to
              )
              const byTime = new Map(fresh.map((b) => [b.time, b]))
              let changed = false
              for (let i = 0; i < this.rawBars.length; i++) {
                const f = byTime.get(this.rawBars[i].time)
                if (f && (this.liveBucket == null || f.time < this.liveBucket)) {
                  this.rawBars[i] = f
                  changed = true
                }
              }
              if (changed) this.applyData()
            }
          } catch {
            /* next cycle retries */
          }
          this.scheduleReconcile()
        })()
      },
      25_000 + Math.random() * 10_000
    )
  }

  /* ── toolbar setters ───────────────────────────────────────────────────── */

  async setInterval(iv: string): Promise<void> {
    if (!this.sym) {
      this.interval = iv
      return
    }
    await this.load({ symbol: this.sym.symbol, exchange: this.sym.exchange }, iv)
    this.cb.onDirty()
  }

  setChartType(value: string): void {
    if (this.chartType === value) return
    this.chartType = value
    if (this.rawBars.length) this.buildChart()
    this.cb.onDirty()
  }

  setTransformSettings(patch: Partial<TransformSettings>): void {
    this.transform = { ...this.transform, ...patch }
    if (this.rawBars.length && chartTypeDef(this.chartType).movement) this.buildChart()
    this.cb.onDirty()
  }

  /** The box size a `sized` chart type is currently drawing with. */
  currentBoxSize(): number {
    return effectiveBoxSize(this.transform, this.refPrice(), this.tick())
  }

  setVolumeMode(mode: VolumeMode): void {
    if (this.volumeMode === mode) return
    this.volumeMode = mode
    if (this.rawBars.length) this.buildChart()
    this.cb.onDirty()
  }

  setGrid(patch: Partial<GridOptions>): void {
    this.grid = { ...this.grid, ...patch }
    this.chart?.setGridOptions(this.grid)
    this.cb.onDirty()
  }

  setTheme(): void {
    if (this.chart && this.rawBars.length) this.buildChart()
  }

  resetScale(): void {
    this.chart?.resetScale()
  }

  screenshot(): void {
    if (!this.chart || !this.sym) return
    const stamp = new Date().toISOString().slice(0, 16).replace(/[T:]/g, '-')
    this.chart.downloadScreenshot(`${this.sym.symbol}-${this.interval}-${stamp}.png`)
  }

  subscribeCrosshair(cb: (data: CrosshairData | null) => void): void {
    this.crosshairCb = cb
  }

  /** Container-local y to a tick-snapped price — the right-click order menu. */
  priceAt(localY: number): number | null {
    const p = this.chart?.coordinateToPrice(localY, 0)
    return p == null ? null : this.snap(p)
  }

  /* ── engine-backed indicators ──────────────────────────────────────────── */

  async addIndicator(
    definitionId: string,
    inputs?: Record<string, unknown>,
    styleOverrides?: StyleOverrides,
    visibility?: TimeframeVisibility
  ): Promise<void> {
    await this.indicators.add(definitionId, inputs, styleOverrides, visibility)
  }

  /**
   * Save an indicator's inputs. `setInputs` now REJECTS when the worker refuses
   * the patch (it keeps the previously committed inputs rather than advancing to
   * values the engine never took), and the settings dialog calls this from a
   * void-returning `onSave`, so the rejection is caught here — unhandled it would
   * surface only as a console rejection with the user believing the save landed.
   *
   * A worker-side refusal also reaches the host's `onError` toast; both toasts
   * occupy the same single slot, so this more specific message is what remains.
   */
  async updateIndicatorInputs(instanceId: string, inputs: Record<string, unknown>): Promise<void> {
    try {
      await this.indicators.setInputs(instanceId, inputs)
    } catch (e) {
      this.cb.onToast(`indicator settings not applied: ${this.cleanError(e)}`, 'err')
    }
  }

  updateIndicatorStyle(instanceId: string, styleOverrides: StyleOverrides): void {
    this.indicators.setStyleOverrides(instanceId, styleOverrides)
  }

  updateIndicatorVisibility(instanceId: string, visibility: TimeframeVisibility | undefined): void {
    this.indicators.setVisibility(instanceId, visibility)
  }

  async removeIndicator(instanceId: string): Promise<void> {
    const legend = this.indicatorLegends.get(instanceId)
    if (legend) {
      this.chart?.removePrimitive(legend)
      this.indicatorLegends.delete(instanceId)
    }
    const needsRebuild = await this.indicators.remove(instanceId)
    if (needsRebuild) {
      // Removing a pane shifts every pane above it, so the whole stack is
      // rebuilt from the surviving instances rather than patched in place.
      this.buildChart()
      await this.indicators.recreateSessions()
    }
  }

  /** Toggle an engine-backed indicator's plots without disposing its session. */
  setIndicatorHidden(instanceId: string, hidden: boolean): void {
    this.indicatorLegends.get(instanceId)?.setOptions({ hidden })
    this.indicators.setHidden(instanceId, hidden)
  }

  /* ── library indicators (openalgo-charts/indicators tier) ──────────────── */

  addLibraryIndicator(indicatorId: string, settings?: Record<string, unknown>): void {
    this.library.add(indicatorId, settings)
  }

  setLibraryIndicatorSettings(instanceId: string, patch: Record<string, unknown>): void {
    this.library.setSettings(instanceId, patch)
  }

  setLibraryIndicatorHidden(instanceId: string, hidden: boolean): void {
    this.library.setHidden(instanceId, hidden)
  }

  removeLibraryIndicator(instanceId: string): void {
    this.library.remove(instanceId)
  }

  /**
   * Add a saved OpenScript indicator to the workspace — the durable
   * counterpart of `previewIr`.
   *
   * Deliberately does NOT touch `previewId`. The preview is single-instance by
   * design (`previewIr` clears the previous one first), which is right for an
   * editor draft and wrong for a saved indicator: routing this through the
   * preview slot would make adding a second saved indicator delete the first,
   * and opening the editor delete both.
   *
   * `ir` is the server's authoritative IR for `script.versionId`. The caller
   * supplies it rather than this fetching it, so the reopen contract — server
   * IR, never a browser recompile — is enforced at one place, in the caller.
   */
  async addScriptIndicator(
    script: ScriptIdentity,
    ir: IRProgram,
    options?: {
      inputs?: Record<string, unknown>
      styleOverrides?: StyleOverrides
      visibility?: TimeframeVisibility
    }
  ): Promise<string> {
    return this.indicators.addIr(ir, { ...options, script })
  }

  /**
   * Add a saved OpenScript script from the picker.
   *
   * Fetches the script's current version and adds it from the SERVER's compiled
   * IR — the same rule `restoreIndicators` obeys, kept beside it so the add and
   * reopen paths cannot drift into different notions of what a saved indicator
   * is. The stored IR wins even if it disagrees with the stored source: what
   * goes on the chart has to be reproducible from what the server holds.
   *
   * Returns the new instance id, or undefined after reporting why it could not
   * be added.
   */
  async addSavedScript(scriptId: number): Promise<string | undefined> {
    let script: Awaited<ReturnType<typeof getScript>>
    try {
      script = await getScript(scriptId)
    } catch (err) {
      this.cb.onToast(`Could not add script ${scriptId} — ${errorText(err)}`, 'err')
      return undefined
    }
    if (!script) {
      this.cb.onToast(`Could not add script ${scriptId} — not found`, 'err')
      return undefined
    }
    const label = script.name || `script ${scriptId}`
    if (script.version_id === undefined) {
      // Identity is script + version. With no version to pin, the indicator
      // could be added but never restored, so it is refused up front.
      this.cb.onToast(`Could not add ${label} — it has no saved version`, 'err')
      return undefined
    }
    if (!script.compiled_ir) {
      this.cb.onToast(
        `Could not add ${label} — the server has no compiled IR for it. Check its diagnostics in the editor.`,
        'err'
      )
      return undefined
    }
    return this.addScriptIndicator(
      {
        scriptId,
        versionId: script.version_id,
        ...(script.source_hash ? { sourceHash: script.source_hash } : {}),
      },
      script.compiled_ir
    )
  }

  /**
   * Live preview of the OpenScript editor — one preview session at a time.
   *
   * `inputs` is what makes the editor's settings dialog usable at all (P4). The
   * editor recompiles on a 400 ms debounce, and this method is `clearPreview()`
   * + `addIr`: the session is torn down and rebuilt on every keystroke. Without
   * carrying the values through, an edited input reverts to its declared default
   * the moment the author types the next character — which reads as the settings
   * dialog being broken rather than as state being dropped here.
   *
   * Omitted means "use the declared defaults", which is the untouched case.
   */
  async previewIr(ir: IRProgram, inputs?: Record<string, unknown>): Promise<void> {
    await this.clearPreview()
    this.previewId = await this.indicators.addIr(ir, inputs ? { inputs } : undefined)
  }

  async clearPreview(): Promise<void> {
    if (!this.previewId) return
    const id = this.previewId
    this.previewId = null
    await this.removeIndicator(id)
  }

  /* ── snapshot / restore ────────────────────────────────────────────────── */

  /**
   * Entries from the last restore that could not be run, kept so saving the
   * layout does not erase them. Cleared and rebuilt on each restore.
   */
  private unrestoredIndicators: IndicatorSnapshotEntry[] = []

  /**
   * The live indicators plus any entry that failed to restore.
   *
   * Deduped by scriptId: once the same script is running again -- the user
   * re-added it, or a later reload found its IR -- the live entry is
   * authoritative and the carried one is dropped, so a recovered indicator can
   * never appear twice in a saved layout.
   */
  private indicatorSnapshotWithUnrestored(): IndicatorSnapshotEntry[] {
    const live = this.indicators.snapshot()
    if (this.unrestoredIndicators.length === 0) return live
    const liveIds = new Set(live.map((e) => e.script?.scriptId).filter((id) => id !== undefined))
    const carried = this.unrestoredIndicators.filter(
      (e) => e.script === undefined || !liveIds.has(e.script.scriptId)
    )
    return [...live, ...carried]
  }

  snapshot(): WorkspaceSnapshot {
    return {
      chartType: this.chartType,
      transform: { ...this.transform },
      volumeMode: this.volumeMode,
      grid: { ...this.grid },
      indicators: this.indicatorSnapshotWithUnrestored(),
      libraryIndicators: this.library.snapshot(),
      drawings: this.drawing.snapshot(),
      profiles: this.profiles.snapshot(),
      gexLevels: this.gexLevels.snapshot(),
      trading: this.trading.snapshot(),
      markers: this.markers.showing,
      ...(this.chart ? { viewport: this.chart.getVisibleLogicalRange() } : {}),
    }
  }

  /**
   * Re-apply a saved viewport. Separate from `applySnapshot` because a logical
   * range indexes bars: restoring it onto an empty chart means nothing, so it
   * has to wait until the data has landed.
   */
  restoreViewport(range: { from: number; to: number } | undefined): void {
    if (!range || !this.chart || !this.shownBars.length) return
    if (!Number.isFinite(range.from) || !Number.isFinite(range.to) || range.to <= range.from) return
    this.chart.timeScale.setVisibleLogicalRange(range)
  }

  /**
   * Apply a saved workspace. Called before the first `load()`, so it only sets
   * state — the chart is built from it on the next symbol load.
   */
  applySnapshot(snap: Partial<WorkspaceSnapshot> | undefined): void {
    if (!snap) return
    if (snap.chartType && chartTypeDef(snap.chartType).value === snap.chartType) {
      this.chartType = snap.chartType
    }
    if (snap.transform) this.transform = { ...DEFAULT_TRANSFORM_SETTINGS, ...snap.transform }
    if (snap.volumeMode) this.volumeMode = snap.volumeMode
    if (snap.grid) this.grid = { ...this.grid, ...snap.grid }
    if (snap.profiles) this.profiles.restore(snap.profiles)
    if (snap.gexLevels) this.gexLevels.restore(snap.gexLevels)
    if (snap.trading) this.trading.restore(snap.trading)
    if (snap.drawings) this.drawing.restore(snap.drawings)
    if (typeof snap.markers === 'boolean') this.markers.setEnabled(snap.markers)
  }

  /**
   * Re-add saved indicator instances once a dataset exists.
   *
   * A durable OpenScript entry is rebuilt from the SERVER's compiled IR for the
   * exact version it was saved against — never through the registry (whose
   * manifest has no `'ir'` entry, which is what used to throw
   * `unknown indicator: ir`), and never by recompiling its source in the
   * browser. Pinning the version is what keeps a reopened chart showing the
   * indicator that was saved rather than whatever the script has since become.
   *
   * Every failure is reported. A layout that cannot restore an indicator has to
   * say which one and why: silently dropping it is how custom indicators
   * disappeared from saved layouts without a word.
   *
   * Entry ORDER is placement (panes are handed out in instance order), so the
   * IR fetches run concurrently but the adds stay strictly sequential.
   */
  async restoreIndicators(snap: Partial<WorkspaceSnapshot> | undefined): Promise<void> {
    if (!snap) return
    const entries = snap.indicators ?? []
    // Rebuilt per restore: this run's failures are the ones worth carrying, and
    // keeping an older run's would resurrect entries the user has since removed.
    this.unrestoredIndicators = []
    // Resolve every durable entry's IR first, in parallel — a layout with
    // several custom indicators would otherwise pay one full round trip each.
    const resolved = await Promise.all(entries.map((item) => this.resolveRestoreIr(item)))

    const failures: string[] = []
    for (const [index, item] of entries.entries()) {
      const outcome = resolved[index] as RestoreResolution
      if (outcome.error) {
        failures.push(outcome.error)
        // The chart cannot run it, but the LAYOUT must keep it. `snapshot()` is
        // what the layout is saved from, so an entry missing there is erased on
        // the next autosave -- turning a transient failure (a version that
        // momentarily has no compiled IR, a blip mid-reload) into permanent loss
        // of the indicator along with its inputs and style overrides.
        this.unrestoredIndicators.push(item)
        continue
      }
      try {
        const id = outcome.ir
          ? await this.addScriptIndicator(item.script as ScriptIdentity, outcome.ir, {
              inputs: item.inputs,
              ...(item.styleOverrides ? { styleOverrides: item.styleOverrides } : {}),
              ...(item.visibility ? { visibility: item.visibility } : {}),
            })
          : await this.indicators.add(
              item.definitionId,
              item.inputs,
              item.styleOverrides,
              item.visibility
            )
        if (item.hidden) this.indicators.setHidden(id, true)
      } catch (err) {
        failures.push(`${describeRestoreEntry(item)}: ${errorText(err)}`)
      }
    }

    if (failures.length > 0) {
      this.cb.onToast(
        `Could not restore ${failures.length === 1 ? 'an indicator' : `${failures.length} indicators`} from this layout — ${failures.join('; ')}`,
        'err'
      )
    }
    this.library.restore(snap.libraryIndicators ?? [])
  }

  /**
   * Fetch the authoritative IR for one saved entry, or explain why it cannot be
   * restored. A registry builtin resolves to neither and is added by
   * `definitionId` further down.
   */
  private async resolveRestoreIr(item: IndicatorSnapshotEntry): Promise<RestoreResolution> {
    if (!item.script) {
      // `'ir'` with no identity is an editor preview persisted by an older
      // build. There is no version to fetch, so it can only be reported.
      if (item.definitionId === IR_DEFINITION_ID) {
        return { error: `${describeRestoreEntry(item)}: saved without a script reference` }
      }
      return {}
    }
    try {
      const version = await getVersion(item.script.scriptId, item.script.versionId)
      if (!version) {
        // The VERSION is named because a layout pins one: the script may well
        // still exist and compile fine, and "not found" without the number sends
        // people to look at the wrong thing.
        return {
          error: `${describeRestoreEntry(item)}: saved version ${item.script.versionId} not found`,
        }
      }
      if (!version.compiled_ir) {
        // The server stored this version without IR — a source it could not
        // compile. Nothing to run, and recompiling in the browser is exactly
        // what the reopen contract forbids.
        return {
          error:
            `${describeRestoreEntry(item)}: saved version ${item.script.versionId} has no compiled IR ` +
            `— it is kept in this layout and will load once that version compiles`,
        }
      }
      return { ir: version.compiled_ir }
    } catch (err) {
      return { error: `${describeRestoreEntry(item)}: ${errorText(err)}` }
    }
  }

  destroy(): void {
    this.destroyed = true
    this.offLtp?.()
    this.offDepth?.()
    this.stopLtpFallback()
    if (this.reconcileTimer) clearTimeout(this.reconcileTimer)
    this.trading.dispose()
    this.profiles.dispose()
    this.gexLevels.dispose()
    this.drawing.dispose()
    this.library.dispose()
    this.indicators.dispose()
    try {
      this.ws?.close()
    } catch {
      /* already closed */
    }
    try {
      this.chart?.destroy()
    } catch {
      /* already gone */
    }
    this.chart = null
    this.ws = null
  }
}

/* ── helpers ─────────────────────────────────────────────────────────────── */

/** Compact volume text (1.2K / 3.4M / 5.6B) for the volume legend row. */
function compact(v: number): string {
  const a = Math.abs(v)
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `${(v / 1e3).toFixed(2)}K`
  return String(Math.round(v))
}

/**
 * `20 close` — the dimmed parameter summary after an indicator's name.
 *
 * Colours and booleans are dropped: a legend row reading `close 20 #4caf50`
 * spends its width on the one value the swatch already shows.
 */
function summariseInputs(inputs: Record<string, unknown>): string {
  const parts: string[] = []
  for (const value of Object.values(inputs)) {
    if (typeof value === 'number') parts.push(String(value))
    else if (typeof value === 'string' && !/^(#|rgba?\()/.test(value)) parts.push(value)
    if (parts.length === 4) break
  }
  return parts.join(' ')
}
