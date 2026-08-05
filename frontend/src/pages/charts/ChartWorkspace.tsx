/**
 * /charts — the chart workspace.
 *
 * The canvas is the product; everything else is bezel. A 40px toolbar above, a
 * 40px tool rail on the left, a dock that opens only when asked, and a status
 * strip below that doubles as the crosshair readout — so the hovered bar's
 * O/H/L/C never floats a card over the price action.
 *
 * This page is presentation and orchestration only. Chart lifecycle, feeds,
 * indicators, drawings, studies and trading all live in
 * `ChartWorkspaceController`, which is imperative on purpose: ticks and canvas
 * repaints must not go through React's render path.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { GEXLevelsResponse } from '@/api/gex'
import {
  type ChartLayoutRecord,
  type ChartLayoutState,
  createLayout,
  deleteLayout,
  listLayouts,
  listScripts,
  type ScriptRecord,
  updateLayout,
} from '@/api/indicators'
import { DataWindow } from '@/components/charts/DataWindow'
import { IndicatorSettingsDialog } from '@/components/charts/IndicatorSettingsDialog'
import { InspectorPanel } from '@/components/charts/InspectorPanel'
import { ProfilePanel } from '@/components/charts/ProfilePanel'
import { ChartTopBar } from '@/components/charts/workspace/ChartTopBar'
import { DirectionPanel } from '@/components/charts/workspace/DirectionPanel'
import { DrawingProperties } from '@/components/charts/workspace/DrawingProperties'
import { DrawingRail } from '@/components/charts/workspace/DrawingRail'
import { GexDashboard } from '@/components/charts/workspace/GexDashboard'
import { IndicatorPicker } from '@/components/charts/workspace/IndicatorPicker'
import { Icon } from '@/components/charts/workspace/icons'
import { LayoutMenu } from '@/components/charts/workspace/LayoutMenu'
import { LibraryIndicatorDialog } from '@/components/charts/workspace/LibraryIndicatorDialog'
import { OrderContextMenu } from '@/components/charts/workspace/OrderContextMenu'
import { StudiesPanel } from '@/components/charts/workspace/StudiesPanel'
import { TradePanel } from '@/components/charts/workspace/TradePanel'
import { SymbolSearchDialog } from '@/components/trading/SymbolSearchDialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { DEFAULT_TRANSFORM_SETTINGS, type TransformSettings } from '@/lib/charts/chart-types'
import { type Drawing, POSITION_TOOLS } from '@/lib/charts/drawing'
import { DEFAULT_GEX_LEVELS_SETTINGS, type GexLevelsConfig } from '@/lib/charts/gex-levels'
import type { IndicatorInstance } from '@/lib/charts/indicator-host'
import type { LibraryIndicatorInstance } from '@/lib/charts/library-indicators'
import type { ProfileHover, ProfileSettings } from '@/lib/charts/profiles'
import { DEFAULT_PROFILE_SETTINGS } from '@/lib/charts/profiles'
import type { CtxItem, OrderSide, OrderType, TradingViewState } from '@/lib/charts/trading-layer'
import { useInspectorPin } from '@/lib/charts/use-inspector-pin'
import {
  ChartWorkspaceController,
  type CrosshairData,
  type GridOptions,
  type SearchRow,
  type SymbolView,
  type VolumeMode,
  type WorkspaceSnapshot,
} from '@/lib/charts/workspace'
import { displayDp, fmtPrice } from '@/lib/trading/format'
import type { IntervalGroup } from '@/lib/trading/intervals'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'

const LAYOUT_NAME = 'default'
/** Which saved layout to reopen; the record itself lives on the server. */
const LAST_LAYOUT_KEY = 'oa-charts-layout-id'
const DEFAULT_SYMBOL: SearchRow = { symbol: 'NIFTY', exchange: 'NSE_INDEX' }

const EMPTY_TRADING: TradingViewState = {
  qty: 1,
  product: 'MIS',
  armed: false,
  ladder: false,
  buySellButtons: true,
  orders: [],
  position: null,
  bracket: null,
  depthTop: null,
}

type Dock = 'none' | 'studies' | 'trade' | 'direction'

interface ContextMenuState {
  x: number
  y: number
  price: string
  items: CtxItem[]
}

export default function ChartWorkspace() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const controllerRef = useRef<ChartWorkspaceController | null>(null)
  const layoutIdRef = useRef<number | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const confirmResolve = useRef<((ok: boolean) => void) | null>(null)
  const mode = useThemeStore((s) => s.mode)
  const appMode = useThemeStore((s) => s.appMode)

  const [ready, setReady] = useState(false)
  const [noApiKey, setNoApiKey] = useState(false)
  const [status, setStatus] = useState('Starting the workspace…')
  const [toast, setToast] = useState<{ text: string; kind: 'ok' | 'err' | '' } | null>(null)
  const [wsState, setWsState] = useState('idle')

  const [symbol, setSymbol] = useState<SymbolView | null>(null)
  const [ltp, setLtp] = useState<number | null>(null)
  const [changePct, setChangePct] = useState<number | null>(null)
  const [interval, setIntervalValue] = useState('5m')
  const [intervalGroups, setIntervalGroups] = useState<IntervalGroup[]>([])

  const [chartType, setChartType] = useState('candlestick')
  const [transform, setTransform] = useState<TransformSettings>(DEFAULT_TRANSFORM_SETTINGS)
  const [boxSize, setBoxSize] = useState(0)
  const [volumeMode, setVolumeMode] = useState<VolumeMode>('overlay')
  const [grid, setGrid] = useState<GridOptions>({ vertLines: true, horzLines: true })

  const [indicators, setIndicators] = useState<IndicatorInstance[]>([])
  const [libraryIndicators, setLibraryIndicators] = useState<LibraryIndicatorInstance[]>([])
  const [crosshair, setCrosshair] = useState<CrosshairData | null>(null)
  const [dataWindow, setDataWindow] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  /**
   * Bar pinned for the series inspector (M8); see `useInspectorPin`.
   *
   * `i` is free on this surface. Escape is not — the page's own key handler
   * also clears the active drawing tool — so pressing it with the panel open
   * both closes the panel and clears the tool. That is the meaning Escape
   * already has here, and the panel carries its own close button, so the two
   * are left independent rather than one intercepting the other.
   */
  const { pinned, setPinned } = useInspectorPin(crosshair)

  const [rail, setRail] = useState(true)
  const [drawState, setDrawState] = useState({
    tool: null as string | null,
    selected: null as string | null,
    canUndo: false,
    canRedo: false,
  })
  const [selectedDrawing, setSelectedDrawing] = useState<Drawing | null>(null)

  const [profiles, setProfiles] = useState<ProfileSettings>(DEFAULT_PROFILE_SETTINGS)
  const [profileHover, setProfileHover] = useState<ProfileHover | null>(null)
  const [gex, setGex] = useState<GexLevelsConfig>(DEFAULT_GEX_LEVELS_SETTINGS)
  const [gexSnapshot, setGexSnapshot] = useState<GEXLevelsResponse | null>(null)
  const [trading, setTrading] = useState<TradingViewState>(EMPTY_TRADING)

  const [dock, setDock] = useState<Dock>('none')
  const [searchOpen, setSearchOpen] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [engineSettings, setEngineSettings] = useState<IndicatorInstance | null>(null)
  const [librarySettings, setLibrarySettings] = useState<LibraryIndicatorInstance | null>(null)
  const [ctxMenu, setCtxMenu] = useState<ContextMenuState | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const [layouts, setLayouts] = useState<ChartLayoutRecord[]>([])
  const [scripts, setScripts] = useState<ScriptRecord[]>([])
  const [layoutId, setLayoutId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [markers, setMarkers] = useState(true)

  const fmt = useCallback(
    (n: number) => fmtPrice(n, symbol?.tick, ltp ?? 0, displayDp(symbol?.tick, ltp ?? 0)),
    [symbol?.tick, ltp]
  )

  /* ── boot ──────────────────────────────────────────────────────────────── */

  // Runs once: it builds the controller, which owns the chart for the lifetime
  // of the page. Re-running on any dependency would tear down the live chart.
  // biome-ignore lint/correctness/useExhaustiveDependencies: mount-once bootstrap
  useEffect(() => {
    let alive = true
    let controller: ChartWorkspaceController | null = null
    ;(async () => {
      try {
        const [keyRes, cfgRes] = await Promise.all([
          fetch('/api/websocket/apikey').then((r) => r.json()),
          fetch('/api/websocket/config').then((r) => r.json()),
        ])
        if (!alive) return
        if (keyRes.status !== 'success') {
          setNoApiKey(true)
          return
        }
        if (!containerRef.current) return

        controller = new ChartWorkspaceController({
          apiKey: keyRes.api_key,
          wsUrl: cfgRes.websocket_url || 'ws://127.0.0.1:8765',
          container: containerRef.current,
          getTheme: () => {
            const s = useThemeStore.getState()
            return { mode: s.mode, appMode: s.appMode }
          },
          callbacks: {
            onStatus: setStatus,
            onToast: (text, kind) => setToast({ text, kind }),
            onWsState: setWsState,
            onIndicators: setIndicators,
            onLibraryIndicators: setLibraryIndicators,
            onSymbolLoaded: (info) => {
              setSymbol(info)
              setIntervalValue(info.interval)
            },
            onLtp: (v, pct) => {
              setLtp(v)
              setChangePct(pct)
            },
            onIntervals: (groups, iv) => {
              setIntervalGroups(groups)
              setIntervalValue(iv)
            },
            onDrawingChange: (s) => {
              setDrawState(s)
              setSelectedDrawing(controllerRef.current?.drawing.selected() ?? null)
            },
            onTrading: setTrading,
            onProfileHover: setProfileHover,
            onGexSnapshot: setGexSnapshot,
            onIndicatorSettings: (instanceId, source) => {
              if (source === 'engine') {
                const inst = controllerRef.current?.indicators
                  .list()
                  .find((i) => i.instanceId === instanceId)
                if (inst) setEngineSettings(inst)
              } else {
                const inst = controllerRef.current?.library
                  .list()
                  .find((i) => i.instanceId === instanceId)
                if (inst) setLibrarySettings(inst)
              }
            },
            confirmOrder: (summary) =>
              new Promise<boolean>((resolve) => {
                confirmResolve.current = resolve
                setConfirm(summary)
              }),
            onDirty: () => scheduleSave(),
          },
        })
        controllerRef.current = controller
        controller.subscribeCrosshair(setCrosshair)
        await controller.init()
        if (!alive) return

        // Restore the last-used layout (or the auto-saved default), then load
        // its symbol.
        let saved: ChartLayoutRecord | undefined
        try {
          const list = await listLayouts()
          if (!alive) return
          setLayouts(list)
          const lastId = Number(localStorage.getItem(LAST_LAYOUT_KEY) || '')
          saved = list.find((l) => l.id === lastId) ?? list.find((l) => l.name === LAYOUT_NAME)
        } catch {
          /* layouts API unavailable — start from defaults */
        }
        if (!alive) return
        await applyLayout(controller, saved)
        setReady(true)
      } catch (err) {
        if (alive) setStatus(err instanceof Error ? err.message : 'failed to start the workspace')
      }
    })()
    return () => {
      alive = false
      if (saveTimer.current) clearTimeout(saveTimer.current)
      controller?.destroy()
      controllerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /**
   * Load one saved layout into the controller: settings first, then the
   * symbol, then the indicator instances, and finally the viewport — which
   * indexes bars and so means nothing until the data has landed.
   */
  const applyLayout = async (
    c: ChartWorkspaceController,
    record: ChartLayoutRecord | undefined
  ) => {
    let snapshot: Partial<WorkspaceSnapshot> | undefined
    let symbol: SearchRow | null = null
    let interval: string | undefined
    if (record) {
      layoutIdRef.current = record.id
      setLayoutId(record.id)
      localStorage.setItem(LAST_LAYOUT_KEY, String(record.id))
      snapshot = record.layout?.workspace
      // A layout written before the workspace snapshot existed.
      if (!snapshot && record.layout?.indicators) {
        snapshot = { indicators: record.layout.indicators }
      }
      if (record.symbol && record.exchange) {
        symbol = { symbol: record.symbol, exchange: record.exchange }
        interval = record.timeframe || undefined
      }
    }

    c.applySnapshot(snapshot)
    syncFromController(c)

    const ok = await c.load(symbol ?? DEFAULT_SYMBOL, interval, { silent: true })
    if (!ok && symbol) await c.load(DEFAULT_SYMBOL, interval)
    await c.restoreIndicators(snapshot)
    c.restoreViewport(snapshot?.viewport)
    syncFromController(c)
  }

  /** Pull view state the controller owns back into React after a bulk change. */
  const syncFromController = (c: ChartWorkspaceController) => {
    const s = c.snapshot()
    setChartType(s.chartType)
    setTransform(s.transform)
    setVolumeMode(s.volumeMode)
    setGrid(s.grid)
    setProfiles(s.profiles)
    setGex(s.gexLevels)
    setMarkers(s.markers)
    setBoxSize(c.currentBoxSize())
  }

  // The controller reads the theme from the store itself; naming both values
  // here is what makes the canvas rebuild when either changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: mode and appMode are triggers, not reads
  useEffect(() => {
    controllerRef.current?.setTheme()
  }, [mode, appMode])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])

  /* ── persistence ───────────────────────────────────────────────────────── */

  const saveLayout = useCallback(async (asName?: string) => {
    const c = controllerRef.current
    if (!c || !c.current.symbol) return
    const layout: ChartLayoutState = {
      indicators: c.indicators.snapshot(),
      workspace: c.snapshot(),
    }
    const meta = c.current
    const payload = {
      symbol: meta.symbol,
      exchange: meta.exchange,
      timeframe: meta.interval,
      layout,
    }
    setSaving(true)
    try {
      if (asName === undefined && layoutIdRef.current) {
        await updateLayout(layoutIdRef.current, payload)
      } else {
        const created = await createLayout({ name: asName ?? LAYOUT_NAME, ...payload })
        if (created) {
          layoutIdRef.current = created.id
          setLayoutId(created.id)
          localStorage.setItem(LAST_LAYOUT_KEY, String(created.id))
        }
      }
      setLayouts(await listLayouts())
    } catch {
      /* persistence is best-effort; the chart keeps working */
    } finally {
      setSaving(false)
    }
  }, [])

  // `applyLayout` closes over the state setters, which are stable, and over the
  // controller through a ref — so it never needs to be a dependency here.
  // biome-ignore lint/correctness/useExhaustiveDependencies: applyLayout reads only refs and stable setters
  const switchLayout = useCallback(async (id: number) => {
    const c = controllerRef.current
    const record = await listLayouts()
      .then((list) => {
        setLayouts(list)
        return list.find((l) => l.id === id)
      })
      .catch(() => undefined)
    if (c && record) await applyLayout(c, record)
  }, [])

  const removeLayout = useCallback(async (id: number) => {
    try {
      await deleteLayout(id)
      const list = await listLayouts()
      setLayouts(list)
      if (layoutIdRef.current === id) {
        layoutIdRef.current = null
        setLayoutId(null)
        localStorage.removeItem(LAST_LAYOUT_KEY)
      }
    } catch {
      /* leave the list as it was */
    }
  }, [])

  const renameLayout = useCallback(async (id: number, name: string) => {
    try {
      await updateLayout(id, { name })
      setLayouts(await listLayouts())
    } catch {
      /* leave the name as it was */
    }
  }, [])

  /** Coalesce the many small changes a session makes into one write. */
  const scheduleSave = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => void saveLayout(), 1200)
  }, [saveLayout])

  /* ── keyboard ──────────────────────────────────────────────────────────── */

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      if (target?.isContentEditable) return
      const c = controllerRef.current
      if (!c) return

      if (e.key === 'Escape') {
        c.drawing.setTool(null)
        c.drawing.select(null)
        return
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (c.drawing.selected()) {
          e.preventDefault()
          c.drawing.removeSelected()
        }
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) c.drawing.redo()
        else c.drawing.undo()
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSearchOpen(true)
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        void saveLayout()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [saveLayout])

  /* ── handlers ──────────────────────────────────────────────────────────── */

  const onContextMenu = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const c = controllerRef.current
    const box = containerRef.current?.getBoundingClientRect()
    if (!c || !box) return
    const price = c.priceAt(e.clientY - box.top)
    if (price == null) return
    const menu = c.trading.contextMenu(price)
    if (!menu) return
    e.preventDefault()
    setCtxMenu({
      x: e.clientX,
      y: e.clientY,
      price: fmtPrice(menu.price, c.symbolView?.tick, price),
      items: menu.items,
    })
  }, [])

  const pickSymbol = useCallback(
    (row: SearchRow) => {
      setSearchOpen(false)
      void controllerRef.current?.load(row, undefined, {}).then(() => scheduleSave())
    },
    [scheduleSave]
  )

  const changeInterval = useCallback((iv: string) => {
    setIntervalValue(iv)
    void controllerRef.current?.setInterval(iv)
  }, [])

  const onRailPick = useCallback((toolId: string | null) => {
    controllerRef.current?.drawing.setTool(toolId)
  }, [])

  const addIndicator = useCallback((id: string, source: 'engine' | 'library' | 'script') => {
    const c = controllerRef.current
    if (!c) return
    if (source === 'engine') void c.addIndicator(id)
    // The controller fetches the script's server-compiled IR and reports any
    // reason it cannot be added, so there is nothing to handle here.
    else if (source === 'script') void c.addSavedScript(Number(id))
    else c.addLibraryIndicator(id)
  }, [])

  // Re-read on every open rather than once: a script saved in the editor since
  // the last open must be selectable without a page reload.
  const openPicker = useCallback(() => {
    setPickerOpen(true)
    void listScripts()
      .then(setScripts)
      .catch(() => setScripts([]))
  }, [])

  // The draw tier's registry is fixed once the tier has loaded, so this is read
  // straight off the controller — memoising it would only cache a constant.
  const toolNames: Record<string, string> = {}
  for (const t of controllerRef.current?.drawing.tools ?? []) toolNames[t.id] = t.name

  const studyCount =
    (profiles.volume.enabled ? 1 : 0) +
    (profiles.market.enabled ? 1 : 0) +
    (profiles.footprint.enabled ? 1 : 0)

  const libraryForms = librarySettings
    ? controllerRef.current?.library.formInputs(librarySettings.indicatorId)
    : undefined

  /* ── render ────────────────────────────────────────────────────────────── */

  if (noApiKey) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-background text-center">
        <p className="text-sm text-muted-foreground">
          The workspace needs an API key to reach market data.
        </p>
        <Link
          to="/apikey"
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
        >
          Generate an API key
        </Link>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background text-foreground">
      <ChartTopBar
        symbol={symbol?.symbol ?? ''}
        exchange={symbol?.exchange ?? ''}
        ltp={ltp}
        changePct={changePct}
        priceText={ltp == null ? '' : fmt(ltp)}
        interval={interval}
        intervalGroups={intervalGroups}
        chartType={chartType}
        transform={transform}
        boxSize={boxSize}
        volumeMode={volumeMode}
        grid={grid}
        indicatorCount={indicators.length + libraryIndicators.length}
        studyCount={studyCount}
        dock={dock}
        drawingRail={rail}
        magnet={controllerRef.current?.drawing.options.magnet ?? false}
        onOpenSearch={() => setSearchOpen(true)}
        onInterval={changeInterval}
        onChartType={(v) => {
          setChartType(v)
          controllerRef.current?.setChartType(v)
          setBoxSize(controllerRef.current?.currentBoxSize() ?? 0)
        }}
        onTransform={(patch) => {
          setTransform((t) => ({ ...t, ...patch }))
          controllerRef.current?.setTransformSettings(patch)
          setBoxSize(controllerRef.current?.currentBoxSize() ?? 0)
        }}
        onVolumeMode={(m) => {
          setVolumeMode(m)
          controllerRef.current?.setVolumeMode(m)
        }}
        onGrid={(patch) => {
          setGrid((g) => ({ ...g, ...patch }))
          controllerRef.current?.setGrid(patch)
        }}
        onOpenIndicators={openPicker}
        onDock={setDock}
        onToggleRail={() => setRail((v) => !v)}
        onMagnet={() => {
          const c = controllerRef.current
          if (c) c.drawing.setMagnet(!c.drawing.options.magnet)
        }}
        onResetScale={() => controllerRef.current?.resetScale()}
        onScreenshot={() => controllerRef.current?.screenshot()}
        markers={markers}
        onMarkers={(on) => {
          setMarkers(on)
          controllerRef.current?.setMarkersVisible(on)
        }}
        layoutMenu={
          <LayoutMenu
            layouts={layouts}
            activeId={layoutId}
            saving={saving}
            onSwitch={(id) => void switchLayout(id)}
            onSaveNow={() => void saveLayout()}
            onSaveAs={(name) => void saveLayout(name)}
            onRename={(id, name) => void renameLayout(id, name)}
            onDelete={(id) => void removeLayout(id)}
          />
        }
      />

      <div className="flex min-h-0 flex-1">
        {rail && (
          <DrawingRail
            activeTool={drawState.tool}
            canUndo={drawState.canUndo}
            canRedo={drawState.canRedo}
            hasSelection={Boolean(drawState.selected)}
            onPick={onRailPick}
            onUndo={() => controllerRef.current?.drawing.undo()}
            onRedo={() => controllerRef.current?.drawing.redo()}
            onDeleteSelected={() => controllerRef.current?.drawing.removeSelected()}
            onClearAll={() => controllerRef.current?.drawing.clear()}
          />
        )}

        {/* Chart stage */}
        <main className="relative min-w-0 flex-1">
          <div
            ref={containerRef}
            className="absolute inset-0"
            onContextMenu={onContextMenu}
            role="application"
            aria-label="Price chart"
          />

          {selectedDrawing && (
            <DrawingProperties
              drawing={selectedDrawing}
              supportsText={TEXT_TOOLS.has(selectedDrawing.tool)}
              isPosition={POSITION_TOOLS.has(selectedDrawing.tool)}
              instrument={
                symbol
                  ? {
                      symbol: symbol.symbol,
                      lotSize: symbol.lots ? symbol.lotsize : 1,
                      freezeQty: symbol.freezeQty,
                      lots: symbol.lots,
                    }
                  : null
              }
              onPositionStyle={(patch) => {
                controllerRef.current?.drawing.setPositionStyle(patch)
                setSelectedDrawing(controllerRef.current?.drawing.selected() ?? null)
              }}
              onStyle={(patch) => {
                controllerRef.current?.drawing.styleSelected(patch)
                setSelectedDrawing(controllerRef.current?.drawing.selected() ?? null)
              }}
              onToggleLock={() => {
                controllerRef.current?.drawing.updateSelected({
                  locked: !selectedDrawing.locked,
                })
                setSelectedDrawing(controllerRef.current?.drawing.selected() ?? null)
              }}
              onDuplicate={() => controllerRef.current?.drawing.duplicateSelected()}
              onDelete={() => controllerRef.current?.drawing.removeSelected()}
              onClose={() => controllerRef.current?.drawing.select(null)}
            />
          )}

          {dataWindow && <DataWindow data={crosshair} inspectHint />}

          {profileOpen && (
            <ProfilePanel
              indicators={indicators}
              profileOf={(id) => controllerRef.current?.indicators.lastProfile(id)}
              onClose={() => setProfileOpen(false)}
            />
          )}

          {pinned && (
            <InspectorPanel
              bar={pinned}
              inspect={(instanceId, outputId, barIndex) =>
                controllerRef.current
                  ? controllerRef.current.indicators.inspect(instanceId, outputId, barIndex)
                  : Promise.resolve(null)
              }
              lastEpoch={(instanceId) => controllerRef.current?.indicators.lastEpoch(instanceId)}
              onClose={() => setPinned(null)}
            />
          )}

          {gex.enabled && gex.showDashboard && (
            <GexDashboard
              data={gexSnapshot}
              stale={controllerRef.current?.gexLevels.stale ?? false}
              metric={gex.metric}
              offset={gex.cardOffset}
              onOffsetChange={(cardOffset) => {
                controllerRef.current?.gexLevels.setConfig({ cardOffset })
                setGex((s) => ({ ...s, cardOffset }))
              }}
              onHide={() => {
                controllerRef.current?.gexLevels.setConfig({ showDashboard: false })
                setGex((s) => ({ ...s, showDashboard: false }))
              }}
            />
          )}

          {!ready && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-muted-foreground">
              {status}
            </div>
          )}

          {toast && (
            <output
              className={cn(
                'absolute bottom-3 left-1/2 z-30 -translate-x-1/2 rounded-md border px-3 py-1.5 text-[12.5px] shadow-lg backdrop-blur',
                toast.kind === 'err'
                  ? 'border-destructive/50 bg-destructive/12 text-destructive'
                  : toast.kind === 'ok'
                    ? 'border-emerald-600/40 bg-emerald-500/12 text-emerald-500'
                    : 'border-border bg-popover/95 text-foreground'
              )}
            >
              {toast.text}
            </output>
          )}
        </main>

        {/* Dock */}
        {dock !== 'none' && (
          <aside className="flex w-[288px] shrink-0 flex-col border-l border-border bg-background">
            <header className="flex h-9 shrink-0 items-center justify-between border-b border-border px-3">
              <h2 className="text-[12px] font-semibold">
                {dock === 'studies' ? 'Studies' : dock === 'direction' ? 'Direction' : 'Trade'}
              </h2>
              <button
                type="button"
                onClick={() => setDock('none')}
                aria-label="Close the panel"
                className="grid h-6 w-6 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <Icon name="close" className="h-4 w-4" />
              </button>
            </header>
            <div className="min-h-0 flex-1">
              {dock === 'studies' ? (
                <StudiesPanel
                  available={controllerRef.current?.profiles.available ?? true}
                  volume={profiles.volume}
                  market={profiles.market}
                  footprint={profiles.footprint}
                  footprintBars={controllerRef.current?.profiles.footprintBarCount ?? 0}
                  interval={interval}
                  hover={profileHover}
                  onVolume={(patch) => {
                    controllerRef.current?.profiles.setVolumeConfig(patch)
                    setProfiles((s) => ({ ...s, volume: { ...s.volume, ...patch } }))
                  }}
                  onMarket={(patch) => {
                    controllerRef.current?.profiles.setMarketConfig(patch)
                    setProfiles((s) => ({ ...s, market: { ...s.market, ...patch } }))
                  }}
                  onFootprint={(patch) => {
                    controllerRef.current?.profiles.setFootprintConfig(patch)
                    setProfiles((s) => ({ ...s, footprint: { ...s.footprint, ...patch } }))
                  }}
                  gex={gex}
                  gexNotes={gexSnapshot?.quality?.notes}
                  gexAvailable={controllerRef.current?.gexAvailable}
                  onGex={(patch) => {
                    controllerRef.current?.gexLevels.setConfig(patch)
                    setGex((s) => ({ ...s, ...patch }))
                  }}
                />
              ) : dock === 'direction' ? (
                <DirectionPanel
                  verdict={
                    controllerRef.current?.direction ?? {
                      composite: 'neutral',
                      score: 0,
                      agreeing: 0,
                      participating: 0,
                      signals: [],
                    }
                  }
                  symbol={symbol?.symbol ?? ''}
                  interval={interval}
                  isPut={controllerRef.current?.isPut}
                  oiFromSession={controllerRef.current?.oiBaselineIsSession}
                />
              ) : (
                <TradePanel
                  symbol={symbol}
                  view={trading}
                  analyzer={appMode !== 'live'}
                  fmt={fmt}
                  onQty={(n) => controllerRef.current?.trading.setQty(n)}
                  onProduct={(v) => controllerRef.current?.trading.setProduct(v)}
                  onArm={(on) => controllerRef.current?.trading.setArmed(on)}
                  onLadder={(on) => controllerRef.current?.trading.setLadder(on)}
                  onBuySellButtons={(on) => controllerRef.current?.trading.setBuySellButtons(on)}
                  onMarket={(side) => void controllerRef.current?.trading.place(side, 'MARKET')}
                  onBracket={(side) => controllerRef.current?.trading.buildBracket(side)}
                  onPlaceBracket={() => void controllerRef.current?.trading.placeBracket()}
                  onCancelBracket={() => controllerRef.current?.trading.cancelBracket()}
                  onCancelAll={() => void controllerRef.current?.trading.cancelAll()}
                  onExitPosition={() => void controllerRef.current?.trading.exitPosition()}
                />
              )}
            </div>
          </aside>
        )}
      </div>

      {/* Status strip — also the crosshair readout, so nothing covers the chart */}
      <footer className="flex h-6 shrink-0 items-center gap-3 overflow-hidden border-t border-border bg-background px-3 text-[11px] text-muted-foreground">
        <span className="shrink-0 truncate">{status}</span>
        {crosshair?.bar && (
          <span className="flex shrink-0 items-center gap-2 tabular-nums">
            <Reading label="O" value={fmt(crosshair.bar.open)} />
            <Reading label="H" value={fmt(crosshair.bar.high)} />
            <Reading label="L" value={fmt(crosshair.bar.low)} />
            <Reading label="C" value={fmt(crosshair.bar.close)} />
            {crosshair.bar.volume != null && (
              <Reading label="V" value={compact(crosshair.bar.volume)} />
            )}
          </span>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-3">
          {drawState.tool && (
            <span className="text-primary">{toolNames[drawState.tool] ?? drawState.tool}</span>
          )}
          <button
            type="button"
            onClick={() => setDataWindow((v) => !v)}
            className={cn('hover:text-foreground', dataWindow && 'text-primary')}
          >
            Data window
          </button>
          <button
            type="button"
            onClick={() => setProfileOpen((v) => !v)}
            className={cn('hover:text-foreground', profileOpen && 'text-primary')}
          >
            Profile
          </button>
          <Link to="/charts/editor" className="hover:text-foreground">
            ƒx Editor
          </Link>
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                wsState === 'live' || wsState === 'open'
                  ? 'bg-emerald-500'
                  : wsState === 'closed' || wsState === 'error' || wsState === 'auth failed'
                    ? 'bg-rose-500'
                    : 'bg-amber-500'
              )}
            />
            {wsState}
          </span>
        </div>
      </footer>

      {/* Overlays */}
      <SymbolSearchDialog
        open={searchOpen}
        onOpenChange={setSearchOpen}
        search={(q, ex, limit) =>
          controllerRef.current?.search(q, ex, limit) ?? Promise.resolve([])
        }
        onPick={pickSymbol}
        initialQuery={symbol?.symbol}
      />

      <IndicatorPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        engine={controllerRef.current?.manifest ?? []}
        library={controllerRef.current?.library.catalogue ?? []}
        scripts={scripts}
        onAdd={addIndicator}
      />

      <IndicatorSettingsDialog
        instance={engineSettings}
        manifest={controllerRef.current?.manifest ?? []}
        onSave={(instanceId, inputs, styleOverrides, visibility) => {
          const c = controllerRef.current
          if (!c) return
          c.updateIndicatorStyle(instanceId, styleOverrides)
          c.updateIndicatorVisibility(instanceId, visibility)
          void c.updateIndicatorInputs(instanceId, inputs)
        }}
        onClose={() => setEngineSettings(null)}
      />

      <LibraryIndicatorDialog
        instance={librarySettings}
        inputs={libraryForms?.inputs ?? []}
        style={libraryForms?.style ?? []}
        onApply={(id, patch) => controllerRef.current?.setLibraryIndicatorSettings(id, patch)}
        onRemove={(id) => controllerRef.current?.removeLibraryIndicator(id)}
        onClose={() => setLibrarySettings(null)}
      />

      {ctxMenu && (
        <OrderContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          price={ctxMenu.price}
          items={ctxMenu.items}
          hasOrders={trading.orders.length > 0}
          railVisible={rail}
          grid={grid}
          onPick={(side: OrderSide, type: OrderType) =>
            controllerRef.current?.trading.placeFromContext(side, type)
          }
          onCancelAll={() => void controllerRef.current?.trading.cancelAll()}
          onResetScale={() => controllerRef.current?.resetScale()}
          onToggleRail={() => setRail((v) => !v)}
          onGrid={(patch) => {
            setGrid((g) => ({ ...g, ...patch }))
            controllerRef.current?.setGrid(patch)
          }}
          onClose={() => setCtxMenu(null)}
        />
      )}

      <Dialog
        open={confirm !== null}
        onOpenChange={(open) => {
          if (open) return
          confirmResolve.current?.(false)
          confirmResolve.current = null
          setConfirm(null)
        }}
      >
        <DialogContent className="w-[400px] max-w-[92vw]">
          <DialogHeader>
            <DialogTitle>
              {appMode === 'live' ? 'Send this order?' : 'Send this analyzer order?'}
            </DialogTitle>
            <DialogDescription className="pt-1 text-[13px] text-foreground">
              {confirm}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              variant="ghost"
              onClick={() => {
                confirmResolve.current?.(false)
                confirmResolve.current = null
                setConfirm(null)
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={() => {
                confirmResolve.current?.(true)
                confirmResolve.current = null
                setConfirm(null)
              }}
            >
              Send
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** Tools whose style bag renders a `text` label. */
const TEXT_TOOLS = new Set(['text', 'rectangle', 'ellipse', 'parallel-channel'])

function Reading({ label, value }: { label: string; value: string }) {
  return (
    <span>
      <span className="opacity-55">{label}</span>{' '}
      <span className="text-foreground/85">{value}</span>
    </span>
  )
}

function compact(v: number): string {
  const a = Math.abs(v)
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `${(v / 1e3).toFixed(2)}K`
  return String(Math.round(v))
}
