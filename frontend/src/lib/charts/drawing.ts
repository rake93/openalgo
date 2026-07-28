/**
 * Drawing-tools manager for the /charts workspace.
 *
 * `openalgo-charts/draw` is deliberately headless — it owns the model and the
 * interactions, the host supplies the buttons. This class is the bridge: it
 * keeps one `DrawingController` alive across chart rebuilds (the model lives
 * here, not on the canvas), exposes the tool catalogue the rail renders, and
 * serialises drawings into the workspace layout.
 *
 * Anchors are `{ time, price }`, never pixels, so a drawing survives zoom,
 * collapsed session gaps, and a chart-type switch.
 */

import type { Chart } from 'openalgo-charts'
import {
  type Drawing,
  DrawingController,
  type DrawingStyle,
  registeredDrawingTools,
} from 'openalgo-charts/draw'
import { asTierChart } from './tier-compat'

/** The `Chart` shape the draw tier's own declarations expect. */
type DrawChart = ConstructorParameters<typeof DrawingController>[0]

export type { Drawing, DrawingStyle } from 'openalgo-charts/draw'

/** Serialised drawing state stored in the layout. */
export interface DrawingSnapshot {
  drawings: Drawing[]
  magnet: boolean
  stayInDrawingMode: boolean
  defaultStyle: DrawingStyle
}

export interface DrawingState {
  tool: string | null
  selected: string | null
  canUndo: boolean
  canRedo: boolean
}

export interface DrawingManagerCallbacks {
  onChange(state: DrawingState): void
}

/** TradingView-style swatch grid for the properties bar. */
export const DRAWING_SWATCHES = [
  '#ffffff',
  '#d1d4dc',
  '#b2b5be',
  '#9598a1',
  '#787b86',
  '#5d606b',
  '#434651',
  '#2a2e39',
  '#1e222d',
  '#000000',
  '#f23645',
  '#ff9800',
  '#ffe100',
  '#4caf50',
  '#089981',
  '#00bcd4',
  '#2962ff',
  '#673ab7',
  '#9c27b0',
  '#e91e63',
  '#fccbcd',
  '#ffe0b2',
  '#fff9c4',
  '#c8e6c9',
  '#b2dfdb',
  '#b2ebf2',
  '#bbdefb',
  '#d1c4e9',
  '#e1bee7',
  '#f8bbd0',
  '#faa1a4',
  '#ffcc80',
  '#fff176',
  '#a5d6a7',
  '#80cbc4',
  '#80deea',
  '#90caf9',
  '#b39ddb',
  '#ce93d8',
  '#f48fb1',
  '#f77c80',
  '#ffb74d',
  '#ffee58',
  '#81c784',
  '#4db6ac',
  '#4dd0e1',
  '#64b5f6',
  '#9575cd',
  '#ba68c8',
  '#f06292',
  '#b22833',
  '#e65100',
  '#fbc02d',
  '#388e3c',
  '#00695c',
  '#0097a7',
  '#1565c0',
  '#4527a0',
  '#6a1b9a',
  '#ad1457',
]

const DEFAULT_STYLE: DrawingStyle = {
  color: '#2962ff',
  lineWidth: 2,
  lineStyle: 'solid',
  fill: true,
  fillOpacity: 0.12,
  showLabels: true,
  // Position-tool sizing. `lotSize` and `maxQty` are replaced per instrument
  // when a symbol loads; the capital base and risk budget are the trader's.
  accountSize: 100_000,
  risk: 1,
  lotSize: 1,
  currencyPrefix: '₹',
}

/** The style keys the long/short position tools read. */
export type PositionStyle = Pick<
  DrawingStyle,
  'accountSize' | 'risk' | 'lotSize' | 'maxQty' | 'currencyPrefix'
>

/** Tools whose readout is driven by {@link PositionStyle}. */
export const POSITION_TOOLS = new Set(['long-position', 'short-position'])

export class DrawingManager {
  private readonly cb: DrawingManagerCallbacks
  private controller: DrawingController | null = null
  /** The model, kept here so it survives every chart rebuild. */
  private drawings: Drawing[] = []
  private magnet = false
  private stayInDrawingMode = false
  private defaultStyle: DrawingStyle = { ...DEFAULT_STYLE }
  private readonly offs: (() => void)[] = []

  constructor(cb: DrawingManagerCallbacks) {
    this.cb = cb
  }

  /** Tool descriptors registered by the tier, for a picker or tooltip. */
  get tools() {
    return registeredDrawingTools().map((t) => ({ id: t.id, name: t.name, points: t.points }))
  }

  get options() {
    return {
      magnet: this.magnet,
      stayInDrawingMode: this.stayInDrawingMode,
      defaultStyle: { ...this.defaultStyle },
    }
  }

  attachChart(chart: Chart): void {
    this.detach()
    // The chart carries the drawing payload in its own state slot, so seeding it
    // before constructing the controller is what makes the model reappear on a
    // freshly built chart.
    chart.setDrawingState(this.drawings)
    this.controller = new DrawingController(asTierChart<DrawChart>(chart), {
      magnet: this.magnet,
      stayInDrawingMode: this.stayInDrawingMode,
      defaultStyle: this.defaultStyle,
    })
    for (const ev of ['draw:add', 'draw:update', 'draw:remove', 'draw:select', 'draw:tool']) {
      this.offs.push(
        chart.on(ev, () => {
          this.drawings = this.controller ? [...this.controller.toJSON()] : this.drawings
          this.emit()
        })
      )
    }
    this.emit()
  }

  private detach(): void {
    for (const off of this.offs.splice(0)) off()
    try {
      // The controller's layers live on the previous chart, which the caller
      // has usually destroyed already; tearing them down is then a throwing
      // no-op. The model is held here, so nothing is lost either way.
      this.controller?.destroy()
    } catch {
      /* previous chart already destroyed */
    }
    this.controller = null
  }

  /* ── tool + selection ──────────────────────────────────────────────────── */

  activeTool(): string | null {
    return this.controller?.activeTool() ?? null
  }

  setTool(toolId: string | null): void {
    this.controller?.setTool(toolId)
    this.emit()
  }

  selected(): Drawing | null {
    const id = this.controller?.selected()
    return id ? (this.controller?.get(id) ?? null) : null
  }

  select(id: string | null): void {
    this.controller?.select(id)
    this.emit()
  }

  updateSelected(patch: Partial<Pick<Drawing, 'points' | 'style' | 'locked' | 'visible'>>): void {
    const id = this.controller?.selected()
    if (!id) return
    this.controller?.update(id, patch)
    this.drawings = this.controller ? [...this.controller.toJSON()] : this.drawings
    this.emit()
  }

  /** Patch the selected drawing's style bag (colour, width, text, ...). */
  styleSelected(patch: DrawingStyle): void {
    const current = this.selected()
    if (!current) return
    this.updateSelected({ style: { ...current.style, ...patch } })
  }

  duplicateSelected(): void {
    const d = this.selected()
    if (!d || !this.controller) return
    // Offset the copy by one bar-ish so it is visibly a second object.
    const span = d.points.length > 1 ? Math.abs(d.points[1].time - d.points[0].time) || 60 : 60
    this.controller.add({
      tool: d.tool,
      points: d.points.map((p) => ({ ...p, time: p.time + span * 0.15 })),
      style: { ...d.style },
      paneIndex: d.paneIndex,
      locked: false,
      visible: true,
    })
    this.drawings = [...this.controller.toJSON()]
    this.emit()
  }

  removeSelected(): void {
    const id = this.controller?.selected()
    if (!id) return
    this.controller?.remove(id)
    this.drawings = this.controller ? [...this.controller.toJSON()] : this.drawings
    this.emit()
  }

  clear(): void {
    this.controller?.clear()
    this.drawings = []
    this.emit()
  }

  undo(): void {
    this.controller?.undo()
    this.drawings = this.controller ? [...this.controller.toJSON()] : this.drawings
    this.emit()
  }

  redo(): void {
    this.controller?.redo()
    this.drawings = this.controller ? [...this.controller.toJSON()] : this.drawings
    this.emit()
  }

  setMagnet(on: boolean): void {
    this.magnet = on
    this.controller?.setOptions({ magnet: on })
    this.emit()
  }

  setStayInDrawingMode(on: boolean): void {
    this.stayInDrawingMode = on
    this.controller?.setOptions({ stayInDrawingMode: on })
    this.emit()
  }

  setDefaultStyle(patch: DrawingStyle): void {
    this.defaultStyle = { ...this.defaultStyle, ...patch }
    this.controller?.setOptions({ defaultStyle: this.defaultStyle })
    this.emit()
  }

  /** The position-sizing defaults new long/short drawings inherit. */
  get positionStyle(): PositionStyle {
    const s = this.defaultStyle
    return {
      accountSize: s.accountSize,
      risk: s.risk,
      lotSize: s.lotSize,
      maxQty: s.maxQty,
      currencyPrefix: s.currencyPrefix,
    }
  }

  /**
   * Bind sizing to the loaded instrument. Cash trades in single units, so its
   * lot size is 1; derivatives trade in indivisible lots and are additionally
   * capped per order by the exchange freeze quantity.
   *
   * Existing position drawings are updated too — leaving a NIFTY size on the
   * chart after switching to a stock would be quietly wrong.
   */
  bindInstrument(instrument: { lotSize: number; freezeQty: number }): void {
    const lotSize = Math.max(1, Math.floor(instrument.lotSize || 1))
    const maxQty = instrument.freezeQty > 1 ? instrument.freezeQty : undefined
    this.setDefaultStyle({ lotSize, maxQty })
    if (!this.controller) return
    for (const d of this.controller.drawings()) {
      if (!POSITION_TOOLS.has(d.tool)) continue
      // A drawing restored from a layout carries the style bag it was saved
      // with, which predates these keys — so the currency prefix is stamped
      // here too rather than only on newly placed ones.
      this.controller.update(d.id, {
        style: {
          currencyPrefix: this.defaultStyle.currencyPrefix,
          ...d.style,
          lotSize,
          maxQty,
        },
      })
    }
    this.drawings = [...this.controller.toJSON()]
    this.emit()
  }

  /** Apply sizing settings to the selected position drawing and to new ones. */
  setPositionStyle(patch: PositionStyle): void {
    this.setDefaultStyle(patch)
    const selected = this.selected()
    if (selected && POSITION_TOOLS.has(selected.tool)) {
      this.styleSelected(patch)
    }
  }

  /** All drawings, for an objects-tree panel. */
  all(): readonly Drawing[] {
    return this.controller?.drawings() ?? this.drawings
  }

  /* ── persistence ───────────────────────────────────────────────────────── */

  snapshot(): DrawingSnapshot {
    return {
      drawings: this.controller ? [...this.controller.toJSON()] : this.drawings,
      magnet: this.magnet,
      stayInDrawingMode: this.stayInDrawingMode,
      defaultStyle: { ...this.defaultStyle },
    }
  }

  restore(snap: Partial<DrawingSnapshot>): void {
    if (Array.isArray(snap.drawings)) this.drawings = snap.drawings
    if (typeof snap.magnet === 'boolean') this.magnet = snap.magnet
    if (typeof snap.stayInDrawingMode === 'boolean') {
      this.stayInDrawingMode = snap.stayInDrawingMode
    }
    if (snap.defaultStyle) this.defaultStyle = { ...DEFAULT_STYLE, ...snap.defaultStyle }
    if (this.controller) {
      this.controller.fromJSON(this.drawings)
      this.controller.setOptions({
        magnet: this.magnet,
        stayInDrawingMode: this.stayInDrawingMode,
        defaultStyle: this.defaultStyle,
      })
      this.emit()
    }
  }

  dispose(): void {
    this.detach()
  }

  private emit(): void {
    this.cb.onChange({
      tool: this.controller?.activeTool() ?? null,
      selected: this.controller?.selected() ?? null,
      canUndo: this.controller?.canUndo() ?? false,
      canRedo: this.controller?.canRedo() ?? false,
    })
  }
}
