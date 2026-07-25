/**
 * Chart-type catalogue for the /charts workspace.
 *
 * Covers every renderer openalgo-charts ships: the 11 time-indexed styles from
 * the base tier plus the six movement-driven families from the transform tier
 * (Heikin Ashi, Renko, Range, Line Break, Point & Figure, Kagi). P&F and Kagi
 * have their own renderers registered by the tier import; the rest plot as
 * candlesticks over transformed bars.
 *
 * A movement-driven type re-buckets bars by price movement rather than the
 * clock, so one source bar can emit zero, one, or many elements — which is why
 * companion series (volume) must be re-bucketed onto the transformed times
 * rather than fed the raw bars.
 */

import {
  HeikinAshiTransform,
  type ISeriesTransform,
  KagiTransform,
  LineBreakTransform,
  type PointFigureBoxMode,
  PointFigureTransform,
  RangeBarsTransform,
  RenkoTransform,
} from 'openalgo-charts/transform'
import type { ReactNode } from 'react'

export type BoxMode = 'auto' | PointFigureBoxMode

/**
 * Tunables shared by the movement-driven families. Persisted with the layout so
 * a Renko box or a P&F reversal survives a reload.
 */
export interface TransformSettings {
  /** How the box / brick / range size is resolved. `auto` derives it from price. */
  boxMode: BoxMode
  /** Absolute box size for `boxMode: 'fixed'`. */
  boxSize: number
  /** Box as a percentage of price for `boxMode: 'percent'` (0.5 = 0.5%). */
  percent: number
  atrPeriod: number
  atrMultiplier: number
  /** Boxes of counter-move that start a new P&F column / Kagi reversal. */
  reversal: number
  /** Lines the close must break beyond for Line Break. */
  lineBreakLines: number
  /** P&F construction: high/low (desk standard) or close-only. */
  pfMethod: 'hl' | 'close'
}

export const DEFAULT_TRANSFORM_SETTINGS: TransformSettings = {
  boxMode: 'auto',
  boxSize: 1,
  percent: 1,
  atrPeriod: 14,
  atrMultiplier: 1,
  reversal: 3,
  lineBreakLines: 3,
  pfMethod: 'hl',
}

export interface ChartTypeDef {
  /** Stable id persisted in the layout. */
  value: string
  label: string
  iconKey: string
  /** openalgo-charts series type the result is plotted as. */
  series: string
  /** Movement-driven: the raw bars run through a transform first. */
  movement?: boolean
  /** Baseline needs a `baseValue` computed from the data. */
  baseline?: boolean
  /** Exposes the box-size / reversal controls in the toolbar. */
  sized?: boolean
}

/** Ordered groups, rendered with separators between them in the menu. */
export const CHART_TYPE_GROUPS: { label: string; items: ChartTypeDef[] }[] = [
  {
    label: 'Bars',
    items: [
      { value: 'candlestick', label: 'Candles', iconKey: 'candle', series: 'candlestick' },
      {
        value: 'hollow-candle',
        label: 'Hollow candles',
        iconKey: 'hollow',
        series: 'hollow-candle',
      },
      {
        value: 'volume-candle',
        label: 'Volume candles',
        iconKey: 'volCandle',
        series: 'volume-candle',
      },
      { value: 'bar', label: 'Bars (OHLC)', iconKey: 'bars', series: 'bar' },
      { value: 'high-low', label: 'High-Low', iconKey: 'highLow', series: 'high-low' },
    ],
  },
  {
    label: 'Lines',
    items: [
      { value: 'line', label: 'Line', iconKey: 'line', series: 'line' },
      {
        value: 'line-markers',
        label: 'Line + markers',
        iconKey: 'lineDots',
        series: 'line-markers',
      },
      { value: 'step', label: 'Step line', iconKey: 'step', series: 'step' },
      { value: 'area', label: 'Area', iconKey: 'area', series: 'area' },
      { value: 'hlc-area', label: 'HLC area', iconKey: 'hlcArea', series: 'hlc-area' },
      {
        value: 'baseline',
        label: 'Baseline',
        iconKey: 'baseline',
        series: 'baseline',
        baseline: true,
      },
      { value: 'column', label: 'Columns', iconKey: 'column', series: 'column' },
    ],
  },
  {
    label: 'Movement-driven',
    items: [
      {
        value: 'heikin-ashi',
        label: 'Heikin Ashi',
        iconKey: 'candle',
        series: 'candlestick',
        movement: true,
      },
      {
        value: 'renko',
        label: 'Renko',
        iconKey: 'bricks',
        series: 'candlestick',
        movement: true,
        sized: true,
      },
      {
        value: 'range',
        label: 'Range bars',
        iconKey: 'range',
        series: 'candlestick',
        movement: true,
        sized: true,
      },
      {
        value: 'line-break',
        label: 'Line break',
        iconKey: 'lineBreak',
        series: 'candlestick',
        movement: true,
        sized: true,
      },
      {
        value: 'point-figure',
        label: 'Point & Figure',
        iconKey: 'pnf',
        series: 'point-figure',
        movement: true,
        sized: true,
      },
      {
        value: 'kagi',
        label: 'Kagi',
        iconKey: 'kagi',
        series: 'kagi',
        movement: true,
        sized: true,
      },
    ],
  },
]

export const CHART_TYPES: Record<string, ChartTypeDef> = Object.fromEntries(
  CHART_TYPE_GROUPS.flatMap((g) => g.items).map((d) => [d.value, d])
)

export function chartTypeDef(value: string): ChartTypeDef {
  return CHART_TYPES[value] ?? CHART_TYPES.candlestick
}

/**
 * Default box size for a movement-driven type: ~0.15% of price, snapped to the
 * instrument tick. Scaling to price (rather than to the visible span) keeps one
 * setting sensible across a ₹20 stock and a ₹75,000 index.
 */
export function autoBoxSize(refPrice: number, tick: number): number {
  const t = tick > 0 ? tick : 0.05
  const raw = Math.max(t, Math.round((refPrice * 0.0015) / t) * t)
  return Number(raw.toFixed(8))
}

/** Resolve the effective box size a `sized` type should use right now. */
export function effectiveBoxSize(
  settings: TransformSettings,
  refPrice: number,
  tick: number
): number {
  if (settings.boxMode === 'fixed' && settings.boxSize > 0) return settings.boxSize
  if (settings.boxMode === 'percent') {
    return Math.max(tick, (refPrice * settings.percent) / 100)
  }
  return autoBoxSize(refPrice, tick)
}

/**
 * Build the transform for a movement-driven chart type, or null for the
 * time-indexed ones. `refPrice` scales the auto box size to the instrument.
 */
export function makeTransform(
  type: string,
  settings: TransformSettings,
  refPrice: number,
  tick: number
): ISeriesTransform | null {
  const def = chartTypeDef(type)
  if (!def.movement) return null
  const box = effectiveBoxSize(settings, refPrice, tick)
  switch (def.value) {
    case 'heikin-ashi':
      return new HeikinAshiTransform()
    case 'renko':
      return new RenkoTransform({ boxSize: box })
    case 'range':
      return new RangeBarsTransform({ range: box * 2 })
    case 'line-break':
      return new LineBreakTransform({ lines: Math.max(1, Math.round(settings.lineBreakLines)) })
    case 'kagi':
      return new KagiTransform({ reversal: box * 2 })
    case 'point-figure': {
      const reversal = Math.max(1, Math.round(settings.reversal))
      const method = settings.pfMethod
      // `percent` and `atr` re-resolve the box each time a column opens, so the
      // grid tracks price level / volatility instead of freezing at load time.
      if (settings.boxMode === 'percent') {
        return new PointFigureTransform({
          mode: 'percent',
          percent: settings.percent,
          reversal,
          method,
        })
      }
      if (settings.boxMode === 'atr') {
        return new PointFigureTransform({
          mode: 'atr',
          atrPeriod: Math.max(1, Math.round(settings.atrPeriod)),
          atrMultiplier: settings.atrMultiplier,
          reversal,
          method,
        })
      }
      return new PointFigureTransform({ mode: 'fixed', boxSize: box, reversal, method })
    }
    default:
      return null
  }
}

/* ── icons ──────────────────────────────────────────────────────────────── */

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

/** Icon for a chart type, used in the toolbar button and the menu rows. */
export function chartTypeIcon(iconKey: string): ReactNode {
  switch (iconKey) {
    case 'candle':
      return (
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <rect x="4.5" y="9" width="4" height="7" rx="1" />
          <rect x="6" y="5" width="1" height="15" rx=".5" />
          <rect x="14.5" y="7" width="4" height="6" rx="1" />
          <rect x="16" y="4" width="1" height="16" rx=".5" />
        </svg>
      )
    case 'hollow':
      return (
        <svg viewBox="0 0 24 24" {...stroke} strokeWidth={1.6} aria-hidden="true">
          <rect x="4.5" y="9" width="4" height="7" rx="1" />
          <path d="M6.5 9V5M6.5 16v3" />
          <rect x="14.5" y="7" width="4" height="6" rx="1" />
          <path d="M16.5 7V4M16.5 13v3" />
        </svg>
      )
    case 'volCandle':
      return (
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <rect x="4" y="8" width="6" height="7" rx="1" />
          <rect x="6.5" y="4" width="1" height="15" />
          <rect x="15" y="9" width="3.5" height="5" rx="1" />
          <rect x="16.3" y="6" width="1" height="11" />
        </svg>
      )
    case 'bars':
      return (
        <svg viewBox="0 0 24 24" {...stroke} strokeWidth={1.6} aria-hidden="true">
          <path d="M7 4v16M4 8h3M7 13h3M17 5v14M14 9h3M17 15h3" />
        </svg>
      )
    case 'highLow':
      return (
        <svg viewBox="0 0 24 24" {...stroke} strokeWidth={1.8} aria-hidden="true">
          <path d="M6 6v12M12 4v14M18 8v10" />
        </svg>
      )
    case 'line':
      return (
        <svg viewBox="0 0 24 24" {...stroke} strokeWidth={1.8} aria-hidden="true">
          <path d="M3 16l4-5 4 3 4-6 6 4" />
        </svg>
      )
    case 'lineDots':
      return (
        <svg viewBox="0 0 24 24" {...stroke} strokeWidth={1.5} aria-hidden="true">
          <path d="M3 16l4-5 4 3 4-6 6 4" />
          <circle cx="7" cy="11" r="1.7" fill="currentColor" stroke="none" />
          <circle cx="11" cy="14" r="1.7" fill="currentColor" stroke="none" />
          <circle cx="15" cy="8" r="1.7" fill="currentColor" stroke="none" />
        </svg>
      )
    case 'step':
      return (
        <svg viewBox="0 0 24 24" {...stroke} aria-hidden="true">
          <path d="M3 17h4v-6h5V7h4v4h1" />
        </svg>
      )
    case 'area':
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 17l4-5 4 3 4-6 6 4v6H3z" fill="currentColor" opacity=".32" />
          <path d="M3 17l4-5 4 3 4-6 6 4" {...stroke} strokeWidth={1.6} />
        </svg>
      )
    case 'hlcArea':
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 9l4-3 4 4 4-5 6 4v7l-6-3-4 4-4-3-4 3z" fill="currentColor" opacity=".28" />
          <path d="M3 15l4-4 4 3 4-5 6 4" {...stroke} strokeWidth={1.5} />
        </svg>
      )
    case 'baseline':
      return (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path
            d="M3 12h18"
            stroke="currentColor"
            strokeWidth={1}
            strokeDasharray="2 2"
            opacity=".6"
          />
          <path d="M3 13l4-5 4 2 4-5 6 4" {...stroke} strokeWidth={1.6} />
        </svg>
      )
    case 'column':
      return (
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <rect x="4" y="12" width="3" height="8" rx=".6" />
          <rect x="9" y="7" width="3" height="13" rx=".6" />
          <rect x="14" y="14" width="3" height="6" rx=".6" />
          <rect x="19" y="10" width="3" height="10" rx=".6" />
        </svg>
      )
    case 'bricks':
      return (
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <rect x="3" y="13" width="5" height="5" rx=".6" />
          <rect x="9.5" y="8.5" width="5" height="5" rx=".6" />
          <rect x="16" y="10" width="5" height="5" rx=".6" />
        </svg>
      )
    case 'range':
      return (
        <svg viewBox="0 0 24 24" {...stroke} strokeWidth={1.5} aria-hidden="true">
          <rect x="3.5" y="9" width="4" height="6" rx=".8" />
          <rect x="10" y="9" width="4" height="6" rx=".8" />
          <rect x="16.5" y="9" width="4" height="6" rx=".8" />
        </svg>
      )
    case 'lineBreak':
      return (
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <rect x="3" y="12" width="4" height="6" rx=".6" />
          <rect x="8" y="8" width="4" height="6" rx=".6" />
          <rect x="13" y="5" width="4" height="6" rx=".6" opacity=".6" />
          <rect x="18" y="9" width="4" height="6" rx=".6" opacity=".6" />
        </svg>
      )
    case 'pnf':
      return (
        <svg viewBox="0 0 24 24" {...stroke} strokeWidth={1.5} aria-hidden="true">
          <path d="M3 5l3 3M6 5l-3 3M3 11l3 3M6 11l-3 3" />
          <circle cx="12" cy="13" r="1.7" />
          <circle cx="12" cy="18" r="1.7" />
          <path d="M18 5l3 3M21 5l-3 3M18 11l3 3M21 11l-3 3" />
        </svg>
      )
    case 'kagi':
      return (
        <svg viewBox="0 0 24 24" {...stroke} aria-hidden="true">
          <path d="M4 18V9h4v7h4V5h4v9h4" strokeWidth={2} />
        </svg>
      )
    default:
      return null
  }
}
