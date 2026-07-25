/**
 * The workspace toolbar.
 *
 * Left to right it follows the order a trader actually works in: what am I
 * looking at (symbol, last price), at what resolution (timeframe), drawn how
 * (chart type), with what on it (indicators, studies), and then the things that
 * act on the whole view (trade panel, layout, screenshot). Everything is one
 * click from the chart and nothing covers it.
 */

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  CHART_TYPE_GROUPS,
  chartTypeDef,
  chartTypeIcon,
  type TransformSettings,
} from '@/lib/charts/chart-types'
import {
  LIVE_BAR_PRESETS,
  type LiveBarSpec,
  liveBarLabel,
  parseLiveBar,
} from '@/lib/charts/live-bars'
import type { GridOptions, VolumeMode } from '@/lib/charts/workspace'
import type { IntervalGroup } from '@/lib/trading/intervals'
import { cn } from '@/lib/utils'
import { Icon } from './icons'
import { IBtn, Pill, Pills, TBtn, VDivider } from './primitives'

/** Timeframes promoted to always-visible pills; the rest live in the menu. */
const QUICK = ['1m', '5m', '15m', '1h', 'D']

export interface ChartTopBarProps {
  symbol: string
  exchange: string
  ltp: number | null
  changePct: number | null
  priceText: string
  interval: string
  intervalGroups: IntervalGroup[]
  chartType: string
  transform: TransformSettings
  boxSize: number
  volumeMode: VolumeMode
  grid: GridOptions
  indicatorCount: number
  studyCount: number
  dock: 'none' | 'studies' | 'trade'
  drawingRail: boolean
  magnet: boolean
  markers: boolean
  /** The saved-layout menu, rendered by the page which owns the records. */
  layoutMenu: ReactNode
  onMarkers(on: boolean): void
  onOpenSearch(): void
  onInterval(iv: string): void
  onChartType(v: string): void
  onTransform(patch: Partial<TransformSettings>): void
  onVolumeMode(mode: VolumeMode): void
  onGrid(patch: Partial<GridOptions>): void
  onOpenIndicators(): void
  onDock(next: 'none' | 'studies' | 'trade'): void
  onToggleRail(): void
  onMagnet(): void
  onResetScale(): void
  onScreenshot(): void
}

export function ChartTopBar(p: ChartTopBarProps) {
  const def = chartTypeDef(p.chartType)
  const up = (p.changePct ?? 0) >= 0
  const allIntervals = p.intervalGroups.flatMap((g) => g.items)
  const quick = QUICK.filter((iv) => allIntervals.includes(iv))
  // Whatever is selected is always a pill — a resolution picked from the menu,
  // or a tick/volume bar, stays one click away and reads as active.
  const pills = quick.includes(p.interval) ? quick : [...quick, p.interval]

  return (
    <header className="flex h-10 shrink-0 items-center gap-1.5 overflow-x-auto border-b border-border bg-background/95 px-2 backdrop-blur">
      {/* Instrument */}
      <TBtn onClick={p.onOpenSearch} className="h-7 shrink-0 gap-2 pl-1.5 pr-2.5">
        <Icon name="search" className="h-4 w-4 opacity-70" />
        <span className="font-semibold tracking-[0.01em]">{p.symbol || 'Search'}</span>
        <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-foreground">
          {p.exchange}
        </span>
      </TBtn>

      {p.ltp != null && (
        <div className="flex shrink-0 items-baseline gap-1.5 pr-1 tabular-nums">
          <span
            className={cn('text-[13px] font-semibold', up ? 'text-emerald-500' : 'text-rose-500')}
          >
            {p.priceText}
          </span>
          {p.changePct != null && (
            <span className={cn('text-[11px]', up ? 'text-emerald-500/80' : 'text-rose-500/80')}>
              {up ? '+' : ''}
              {p.changePct.toFixed(2)}%
            </span>
          )}
        </div>
      )}

      <VDivider />

      {/* Timeframe */}
      <Pills className="shrink-0">
        {pills.map((iv) => (
          <Pill key={iv} active={p.interval === iv} onClick={() => p.onInterval(iv)}>
            {iv}
          </Pill>
        ))}
      </Pills>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IBtn title="All timeframes" aria-label="All timeframes">
            <Icon name="chevron" className="h-4 w-4" />
          </IBtn>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-[70vh] w-56 overflow-auto">
          {p.intervalGroups.map((g) => (
            <div key={g.label}>
              <DropdownMenuLabel className="text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
                {g.label}
              </DropdownMenuLabel>
              <div className="grid grid-cols-4 gap-1 px-1 pb-1.5">
                {g.items.map((iv) => (
                  <button
                    key={iv}
                    type="button"
                    onClick={() => p.onInterval(iv)}
                    className={cn(
                      'h-7 rounded-md text-[11px] font-medium tabular-nums transition-colors hover:bg-accent',
                      p.interval === iv && 'bg-primary/12 text-primary'
                    )}
                  >
                    {iv}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
            Live bars
          </DropdownMenuLabel>
          <div className="grid grid-cols-4 gap-1 px-1">
            {LIVE_BAR_PRESETS.map((preset) => (
              <button
                key={preset.value}
                type="button"
                title={liveBarLabel(parseLiveBar(preset.value) as LiveBarSpec)}
                onClick={() => p.onInterval(preset.value)}
                className={cn(
                  'h-7 rounded-md text-[11px] font-medium tabular-nums transition-colors hover:bg-accent',
                  p.interval === preset.value && 'bg-primary/12 text-primary'
                )}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <p className="px-2 pb-1.5 pt-1 text-[10.5px] leading-snug text-muted-foreground">
            Tick and volume bars need individual prints, which history does not carry. They start
            empty and build from now.
          </p>
        </DropdownMenuContent>
      </DropdownMenu>

      <VDivider />

      {/* Chart type */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <TBtn className="shrink-0" title="Chart type">
            <span className="h-4 w-4 [&>svg]:h-4 [&>svg]:w-4">{chartTypeIcon(def.iconKey)}</span>
            <span className="hidden sm:inline">{def.label}</span>
            <Icon name="chevron" className="h-3.5 w-3.5 opacity-60" />
          </TBtn>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-60">
          {CHART_TYPE_GROUPS.map((group, gi) => (
            <div key={group.label}>
              {gi > 0 && <DropdownMenuSeparator />}
              <DropdownMenuLabel className="text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
                {group.label}
              </DropdownMenuLabel>
              {group.items.map((t) => (
                <DropdownMenuItem
                  key={t.value}
                  onSelect={() => p.onChartType(t.value)}
                  className="gap-2.5 text-[13px]"
                >
                  <span className="h-4 w-4 shrink-0 [&>svg]:h-4 [&>svg]:w-4">
                    {chartTypeIcon(t.iconKey)}
                  </span>
                  <span className="flex-1">{t.label}</span>
                  {p.chartType === t.value && <span className="text-primary">✓</span>}
                </DropdownMenuItem>
              ))}
            </div>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Box sizing — only for the movement-driven types that have one */}
      {def.sized && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <TBtn className="shrink-0 tabular-nums" title="Box / reversal settings">
              <span className="text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
                box
              </span>
              <span>{p.transform.boxMode === 'auto' ? 'Auto' : p.transform.boxMode}</span>
              <span className="text-muted-foreground">{p.boxSize.toFixed(2)}</span>
              <Icon name="chevron" className="h-3.5 w-3.5 opacity-60" />
            </TBtn>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56 p-2">
            <DropdownMenuLabel className="px-0 text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
              Box sizing
            </DropdownMenuLabel>
            <div className="grid grid-cols-4 gap-1 py-1">
              {(['auto', 'fixed', 'percent', 'atr'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => p.onTransform({ boxMode: m })}
                  className={cn(
                    'h-7 rounded-md text-[11px] font-medium capitalize transition-colors hover:bg-accent',
                    p.transform.boxMode === m && 'bg-primary/12 text-primary'
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
            <div className="space-y-1.5 pt-1">
              {p.transform.boxMode === 'fixed' && (
                <NumRow
                  label="Box size"
                  value={p.transform.boxSize}
                  step={0.05}
                  onChange={(v) => p.onTransform({ boxSize: v })}
                />
              )}
              {p.transform.boxMode === 'percent' && (
                <NumRow
                  label="Percent"
                  value={p.transform.percent}
                  step={0.1}
                  onChange={(v) => p.onTransform({ percent: v })}
                />
              )}
              {p.transform.boxMode === 'atr' && (
                <>
                  <NumRow
                    label="ATR length"
                    value={p.transform.atrPeriod}
                    step={1}
                    onChange={(v) => p.onTransform({ atrPeriod: v })}
                  />
                  <NumRow
                    label="ATR multiple"
                    value={p.transform.atrMultiplier}
                    step={0.1}
                    onChange={(v) => p.onTransform({ atrMultiplier: v })}
                  />
                </>
              )}
              {(p.chartType === 'point-figure' || p.chartType === 'kagi') && (
                <NumRow
                  label="Reversal"
                  value={p.transform.reversal}
                  step={1}
                  onChange={(v) => p.onTransform({ reversal: v })}
                />
              )}
              {p.chartType === 'line-break' && (
                <NumRow
                  label="Lines"
                  value={p.transform.lineBreakLines}
                  step={1}
                  onChange={(v) => p.onTransform({ lineBreakLines: v })}
                />
              )}
              {p.chartType === 'point-figure' && (
                <div className="grid grid-cols-[1fr_auto] items-center gap-3">
                  <span className="text-[12px]">Method</span>
                  <div className="flex gap-1">
                    {(['hl', 'close'] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => p.onTransform({ pfMethod: m })}
                        className={cn(
                          'h-7 rounded-md px-2 text-[11px] uppercase transition-colors hover:bg-accent',
                          p.transform.pfMethod === m && 'bg-primary/12 text-primary'
                        )}
                      >
                        {m === 'hl' ? 'High/Low' : 'Close'}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      <VDivider />

      <TBtn onClick={p.onOpenIndicators} className="shrink-0" title="Add an indicator">
        <Icon name="indicators" className="h-4 w-4" />
        <span className="hidden md:inline">Indicators</span>
        {p.indicatorCount > 0 && (
          <span className="rounded bg-muted px-1 text-[10.5px] tabular-nums text-muted-foreground">
            {p.indicatorCount}
          </span>
        )}
      </TBtn>

      <TBtn
        onClick={() => p.onDock(p.dock === 'studies' ? 'none' : 'studies')}
        active={p.dock === 'studies'}
        className="shrink-0"
        title="Volume profile, market profile and order flow"
      >
        <Icon name="studies" className="h-4 w-4" />
        <span className="hidden lg:inline">Studies</span>
        {p.studyCount > 0 && (
          <span className="rounded bg-muted px-1 text-[10.5px] tabular-nums text-muted-foreground">
            {p.studyCount}
          </span>
        )}
      </TBtn>

      <TBtn
        onClick={() => p.onDock(p.dock === 'trade' ? 'none' : 'trade')}
        active={p.dock === 'trade'}
        className="shrink-0"
        title="Trading panel and depth ladder"
      >
        <Icon name="trade" className="h-4 w-4" />
        <span className="hidden lg:inline">Trade</span>
      </TBtn>

      <Link
        to="/charts/editor"
        title="Write a custom indicator in OpenScript"
        className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-transparent px-2 text-[13px] leading-none text-foreground/85 transition-colors hover:border-border hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="font-serif text-[15px] italic leading-none">ƒx</span>
        <span className="hidden lg:inline">Editor</span>
      </Link>

      <div className="ml-auto flex shrink-0 items-center gap-1">
        <IBtn
          onClick={p.onToggleRail}
          active={p.drawingRail}
          title="Drawing tools"
          aria-label="Drawing tools"
        >
          <Icon name="trend" className="h-4 w-4" />
        </IBtn>
        <IBtn onClick={p.onMagnet} active={p.magnet} title="Magnet: snap to O/H/L/C">
          <Icon name="magnet" className="h-4 w-4" />
        </IBtn>

        <VDivider />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IBtn title="View options" aria-label="View options">
              <Icon name="grid" className="h-4 w-4" />
            </IBtn>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuLabel className="text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
              Grid
            </DropdownMenuLabel>
            <DropdownMenuCheckboxItem
              checked={p.grid.vertLines}
              onCheckedChange={(v) => p.onGrid({ vertLines: Boolean(v) })}
            >
              Vertical lines
            </DropdownMenuCheckboxItem>
            <DropdownMenuCheckboxItem
              checked={p.grid.horzLines}
              onCheckedChange={(v) => p.onGrid({ horzLines: Boolean(v) })}
            >
              Horizontal lines
            </DropdownMenuCheckboxItem>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="text-[10px] uppercase tracking-[0.09em] text-muted-foreground">
              Volume
            </DropdownMenuLabel>
            {(
              [
                ['overlay', 'On the price pane'],
                ['pane', 'Separate pane'],
                ['off', 'Hidden'],
              ] as const
            ).map(([mode, label]) => (
              <DropdownMenuItem
                key={mode}
                onSelect={() => p.onVolumeMode(mode)}
                className="text-[13px]"
              >
                <span className="flex-1">{label}</span>
                {p.volumeMode === mode && <span className="text-primary">✓</span>}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuCheckboxItem
              checked={p.markers}
              onCheckedChange={(v) => p.onMarkers(Boolean(v))}
            >
              My fills and expiry
            </DropdownMenuCheckboxItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <IBtn onClick={p.onResetScale} title="Reset the view (double-click the chart)">
          <Icon name="reset" className="h-4 w-4" />
        </IBtn>
        <IBtn onClick={p.onScreenshot} title="Save a PNG of the chart">
          <Icon name="camera" className="h-4 w-4" />
        </IBtn>
        {p.layoutMenu}
      </div>
    </header>
  )
}

function NumRow({
  label,
  value,
  step,
  onChange,
}: {
  label: string
  value: number
  step: number
  onChange(v: number): void
}) {
  return (
    <label className="grid grid-cols-[1fr_auto] items-center gap-3">
      <span className="text-[12px]">{label}</span>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => {
          const v = Number(e.target.value)
          if (Number.isFinite(v)) onChange(v)
        }}
        className="h-7 w-[86px] rounded-md border border-border bg-background px-2 text-right text-[12px] tabular-nums outline-none focus:border-primary/60"
      />
    </label>
  )
}
