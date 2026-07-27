/**
 * On-chart trading for the /charts workspace.
 *
 * Two paths, deliberately:
 *
 *  - **New orders go through `OrderEngine`** (`openalgo-charts/trade`), which
 *    adds tick snapping, price-band / freeze-quantity validation, client-token
 *    idempotency, an arm/confirm gate, OCO linking for brackets, and analyzer
 *    (sandbox) mode routing.
 *  - **Existing orders are reconciled from the broker book** and modified or
 *    cancelled by broker id. A working order may have been placed anywhere —
 *    the /trading terminal, TradingView, a Python strategy — so it has no engine
 *    client id, and driving it by broker id is the only correct option.
 *
 * The visual layer is `PriceLine` pill groups (draggable, with a cancel ✕), a
 * position line carrying live P&L, an OCO bracket, `BuySellButtons` inside the
 * plot, and a `DomLadder` fed from the depth stream.
 */

import {
  BuySellButtons,
  type Chart,
  type MarketDepth,
  type OpenAlgoTradeFeed,
  type PriceLine,
} from 'openalgo-charts'
import { DomLadder, OrderEngine, riskReward, validateOrder } from 'openalgo-charts/trade'
import { asPrimitive } from './tier-compat'

export type OrderSide = 'BUY' | 'SELL'
export type OrderType = 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
export type Product = 'CNC' | 'NRML' | 'MIS'
export type ToastKind = 'ok' | 'err' | ''

const BUY_COLOR = '#26a69a'
const SELL_COLOR = '#ef5350'
/** Order and bracket lines span only the rightmost slice, broker-style. */
const TRADE_EXTENT = 0.3

/** Minimal instrument facts the layer needs; supplied by the controller. */
export interface TradingSymbol {
  symbol: string
  exchange: string
  lots: boolean
  lotsize: number
  tick: number
  freezeQty: number
  quoteOnly: boolean
  productOptions: string[]
}

/** Order shape shared by the book poll and the WebSocket order stream. */
interface LineOrder {
  id: string
  side: OrderSide
  type: OrderType
  qty: number
  price: number
  triggerPrice?: number
  status: string
}

interface OrderLineRec {
  line: PriceLine
  order: LineOrder
  dragFrom: number | null
}

interface PositionState {
  net: number
  avg: number
  product: string
}

/** An OCO bracket built on the chart before it is sent. */
export interface BracketDraft {
  side: OrderSide
  entry: number
  target: number
  stop: number
  qty: number
  /** True once the entry + OCO legs are live at the broker. */
  placed: boolean
}

/** One right-click order option. */
export interface CtxItem {
  side: OrderSide
  type: OrderType
  label: string
  enabled: boolean
}

/** Everything the React trading panels render. */
export interface TradingViewState {
  qty: number
  product: string
  armed: boolean
  ladder: boolean
  buySellButtons: boolean
  orders: { id: string; side: OrderSide; type: OrderType; qty: number; price: number }[]
  position: { net: number; avg: number; pnl: number } | null
  bracket: BracketDraft | null
  depthTop: { bid: number; ask: number } | null
}

/** Serialised trading preferences stored in the layout. */
export interface TradingSnapshot {
  qty: number
  product: string
  armed: boolean
  ladder: boolean
  buySellButtons: boolean
  ladderGroupBy: number
  ladderMaxRows: number
  /** Where the user dragged the Buy/Sell panel, offset from its docked corner. */
  buttonOffset?: { x: number; y: number }
}

export interface TradingLayerOptions {
  feed: OpenAlgoTradeFeed
  api<T>(path: string, body?: Record<string, unknown>): Promise<T>
  symbol(): TradingSymbol | null
  mode(): 'live' | 'analyze'
  marketPrice(): number | null
  snap(n: number): number
  fmt(n: number): string
  money(n: number): string
  onToast(message: string, kind: ToastKind): void
  onView(view: TradingViewState): void
  onDirty(): void
  /**
   * Top inset for the inline Buy/Sell panel, in media px. The price pane's
   * legend rows stack from the same corner and grow with every indicator, so a
   * fixed offset would eventually sit on top of one — and cover the legend's
   * hover controls.
   */
  topInset(): number
  /** Ask the host to confirm an order when the panel is not armed. */
  gate?(summary: string): Promise<boolean>
}

export class TradingLayer {
  private readonly o: TradingLayerOptions
  private chart: Chart | null = null
  private engine: OrderEngine | null = null

  private readonly orderLines = new Map<string, OrderLineRec>()
  private posLine: PriceLine | null = null
  private position: PositionState | null = null
  private bracket: BracketDraft | null = null
  private bracketLines: { entry: PriceLine; tp: PriceLine; sl: PriceLine } | null = null
  private ladder: DomLadder | null = null
  private buttons: BuySellButtons | null = null
  /**
   * Where the panel was dragged to, kept on the layer rather than only on the
   * primitive, so it survives the panel being torn down and remounted — on a
   * chart rebuild, or when the buttons are toggled off and on again.
   */
  private buttonOffset = { x: 0, y: 0 }

  private qty = 1
  private product = 'MIS'
  private armed = false
  private showLadder = false
  private showButtons = true
  private ladderGroupBy = 1
  private ladderMaxRows = 60

  private lastLtp: number | null = null
  private depthTop: { bid: number; ask: number } | null = null
  private ctxPrice = 0
  private bookTimer: ReturnType<typeof setInterval> | null = null
  private destroyed = false

  constructor(o: TradingLayerOptions) {
    this.o = o
  }

  /* ── lifecycle ─────────────────────────────────────────────────────────── */

  start(): void {
    if (this.bookTimer) clearInterval(this.bookTimer)
    this.bookTimer = setInterval(() => void this.pollBook(), 8000)
  }

  attachChart(chart: Chart): void {
    this.chart = chart
    // Every primitive belonged to the destroyed chart; the model is here, so the
    // view is rebuilt rather than migrated.
    this.orderLines.clear()
    this.posLine = null
    this.bracketLines = null
    this.ladder = null
    this.buttons = null

    const sym = this.o.symbol()
    this.engine = new OrderEngine({
      feed: this.o.feed,
      constraints: { tickSize: sym?.tick || 0.05, freezeQty: sym?.freezeQty || undefined },
      mode: this.o.mode() === 'analyze' ? 'analyzer' : 'live',
      armed: true, // the host runs its own confirm gate before calling in
      onValidationError: (reason) => this.o.onToast(reason, 'err'),
    })

    if (sym && !sym.quoteOnly && this.showButtons) this.mountButtons()
    if (sym && !sym.quoteOnly && this.showLadder) this.mountLadder()

    if (this.bracket) this.attachBracketLines()
    void this.pollBook()
    this.emit()
  }

  private mountButtons(): void {
    const chart = this.chart
    if (!chart || this.buttons) return
    this.buttons = new BuySellButtons({
      id: 'trade',
      position: 'top-left',
      margin: { x: 14, y: this.o.topInset() },
      qty: this.qtyChip(),
    })
    this.buttons.setOffset(this.buttonOffset.x, this.buttonOffset.y)
    const lp = this.o.marketPrice()
    if (lp != null) this.buttons.setMark(lp)
    chart.addPrimitive(this.buttons, 0)
  }

  private mountLadder(): void {
    const chart = this.chart
    const sym = this.o.symbol()
    if (!chart || !sym || this.ladder) return
    this.ladder = new DomLadder({
      tickSize: sym.tick || 0.05,
      groupBy: this.ladderGroupBy,
      maxRows: this.ladderMaxRows,
    })
    chart.addPrimitive(asPrimitive(this.ladder), 0)
  }

  /* ── quantity / product / toggles ──────────────────────────────────────── */

  setQty(n: number): void {
    this.qty = Math.max(1, Math.floor(n || 1))
    this.buttons?.setQty(this.qtyChip())
    this.o.onDirty()
    this.emit()
  }

  setProduct(p: string): void {
    this.product = p
    this.o.onDirty()
    this.emit()
  }

  setArmed(on: boolean): void {
    this.armed = on
    this.o.onDirty()
    this.emit()
  }

  setLadder(on: boolean): void {
    this.showLadder = on
    if (!this.chart) return
    if (on && !this.ladder) this.mountLadder()
    else if (!on && this.ladder) {
      this.chart.removePrimitive(asPrimitive(this.ladder))
      this.ladder = null
    }
    this.o.onDirty()
    this.emit()
  }

  setLadderOptions(patch: { groupBy?: number; maxRows?: number }): void {
    if (patch.groupBy) this.ladderGroupBy = Math.max(1, Math.round(patch.groupBy))
    if (patch.maxRows) this.ladderMaxRows = Math.max(5, Math.round(patch.maxRows))
    if (this.ladder && this.chart) {
      this.chart.removePrimitive(asPrimitive(this.ladder))
      this.ladder = null
      this.mountLadder()
    }
    this.o.onDirty()
    this.emit()
  }

  setBuySellButtons(on: boolean): void {
    this.showButtons = on
    if (this.chart) {
      // Add or remove the primitive in place — re-attaching would orphan every
      // order line already drawn on this same (undestroyed) chart.
      if (on) this.mountButtons()
      else if (this.buttons) {
        this.chart.removePrimitive(this.buttons)
        this.buttons = null
      }
    }
    this.o.onDirty()
    this.emit()
  }

  /** Real order quantity — lots multiplied by lotsize for derivatives. */
  private orderQty(): number {
    const sym = this.o.symbol()
    const n = Math.max(1, Math.floor(this.qty || 1))
    return sym?.lots ? n * sym.lotsize : n
  }

  private qtyChip(): string {
    const sym = this.o.symbol()
    const n = Math.max(1, Math.floor(this.qty || 1))
    return sym?.lots ? `${n}L` : String(n)
  }

  /* ── order placement ───────────────────────────────────────────────────── */

  private cleanError(e: unknown): string {
    console.error('[charts/trade]', e)
    const raw = String((e as Error)?.message || e || 'request failed')
    return (
      raw
        .replace(/^openalgo-charts:\s*/i, '')
        .replace(/^\/api\/v1\/[\w/]+\s+failed\s+\(\d+\)(:\s*)?/i, '')
        .trim() || 'request failed'
    )
  }

  /** Shared pre-trade checks that the engine cannot know about. */
  private guard(type: OrderType, price: number): string | null {
    const sym = this.o.symbol()
    if (!sym) return 'search a symbol first'
    if (sym.quoteOnly) return `${sym.exchange} is quote-only — trading is not supported`
    const qty = this.orderQty()
    if (sym.freezeQty > 1 && qty > sym.freezeQty) {
      return `qty ${qty} exceeds the freeze limit ${sym.freezeQty} — reduce lots`
    }
    if (type !== 'MARKET') {
      const check = validateOrder(price, qty, {
        tickSize: sym.tick || 0.05,
        ...(sym.freezeQty > 1 ? { freezeQty: sym.freezeQty } : {}),
      })
      if (!check.ok) return check.reason ?? 'order failed validation'
    }
    return null
  }

  async place(side: OrderSide, type: OrderType, priceIn?: number): Promise<void> {
    const sym = this.o.symbol()
    if (!sym || !this.engine) {
      this.o.onToast('search a symbol first', 'err')
      return
    }
    const price = type === 'MARKET' ? 0 : this.o.snap(priceIn ?? this.ctxPrice)
    const reason = this.guard(type, price)
    if (reason) {
      this.o.onToast(reason, 'err')
      return
    }
    const m = this.o.marketPrice()
    if (m != null && (type === 'SL' || type === 'SL-M')) {
      const wrong = side === 'BUY' ? price <= m : price >= m
      if (wrong) {
        this.o.onToast(
          `${side} stop must be ${side === 'BUY' ? 'above' : 'below'} LTP ${this.o.fmt(m)}`,
          'err'
        )
        return
      }
    }
    const qty = this.orderQty()
    const lotTxt = sym.lots ? `${qty / sym.lotsize}L (${qty})` : String(qty)
    const summary = `${side} ${type} ${lotTxt} ${sym.symbol}${type === 'MARKET' ? '' : ` @ ${this.o.fmt(price)}`} · ${this.product}`
    if (!this.armed && this.o.gate && !(await this.o.gate(summary))) return

    try {
      const r = await this.engine.placeOrder({
        symbol: sym.symbol,
        exchange: sym.exchange,
        side,
        type,
        qty,
        product: this.product as Product,
        ...(type === 'MARKET' ? {} : { price }),
        ...(type === 'SL' || type === 'SL-M' ? { triggerPrice: price } : {}),
      })
      if (!r.ok) {
        this.o.onToast(r.reason || 'order rejected', 'err')
        return
      }
      this.o.onToast(`placed ${summary}`, 'ok')
      void this.pollBook()
    } catch (e) {
      this.o.onToast(this.cleanError(e), 'err')
    }
  }

  /* ── bracket (entry + OCO target/stop) ─────────────────────────────────── */

  buildBracket(side: OrderSide): void {
    const m = this.o.marketPrice()
    if (m == null) return
    const entry = this.o.snap(m)
    this.bracket = {
      side,
      entry,
      qty: this.orderQty(),
      target: this.o.snap(side === 'BUY' ? entry * 1.012 : entry * 0.988),
      stop: this.o.snap(side === 'BUY' ? entry * 0.99 : entry * 1.01),
      placed: false,
    }
    this.attachBracketLines()
    this.emit()
  }

  private attachBracketLines(): void {
    const chart = this.chart
    const b = this.bracket
    if (!chart || !b) return
    this.detachBracketLines()
    const line = (price: number, color: string, id: string, badge: string) =>
      chart.addPriceLine(
        {
          price,
          color,
          lineWidth: id === 'bk-entry' ? 2 : 1,
          dashed: id !== 'bk-entry',
          id,
          badge,
          qty: b.qty,
          cursor: 'ns-resize',
          extentFromRight: TRADE_EXTENT,
        },
        0
      )
    this.bracketLines = {
      entry: line(b.entry, b.side === 'BUY' ? BUY_COLOR : SELL_COLOR, 'bk-entry', b.side),
      tp: line(b.target, BUY_COLOR, 'bk-tp', 'TP'),
      sl: line(b.stop, SELL_COLOR, 'bk-sl', 'SL'),
    }
    this.updateBracketLabels()
  }

  private detachBracketLines(): void {
    if (!this.bracketLines || !this.chart) return
    for (const l of Object.values(this.bracketLines)) this.chart.removePrimitive(l)
    this.bracketLines = null
  }

  /** Move one leg — dragging the entry carries the whole bracket with it. */
  setBracketPrice(which: 'entry' | 'tp' | 'sl', raw: number): void {
    const b = this.bracket
    if (!b) return
    const p = this.o.snap(raw)
    const buy = b.side === 'BUY'
    if (which === 'entry') {
      const d = p - b.entry
      b.entry = p
      b.target = this.o.snap(b.target + d)
      b.stop = this.o.snap(b.stop + d)
    } else if (which === 'tp') {
      // Keep each leg on the correct side of entry, or the bracket is invalid.
      b.target = buy ? Math.max(p, b.entry) : Math.min(p, b.entry)
    } else {
      b.stop = buy ? Math.min(p, b.entry) : Math.max(p, b.entry)
    }
    if (this.bracketLines) {
      this.bracketLines.entry.setPrice(b.entry)
      this.bracketLines.tp.setPrice(b.target)
      this.bracketLines.sl.setPrice(b.stop)
    }
    this.updateBracketLabels()
    this.emit()
  }

  private updateBracketLabels(): void {
    const b = this.bracket
    if (!b || !this.bracketLines) return
    const rr = riskReward(b.entry, b.stop, b.target)
    this.bracketLines.entry.setLeftLabel(`R:R ${rr == null ? '—' : rr.toFixed(2)}`)
    const tpPts = Math.abs(b.target - b.entry)
    const slPts = Math.abs(b.entry - b.stop)
    this.bracketLines.tp.setLeftLabel(`+${this.o.fmt(tpPts)} · ₹${this.o.money(tpPts * b.qty)}`)
    this.bracketLines.sl.setLeftLabel(`-${this.o.fmt(slPts)} · ₹${this.o.money(slPts * b.qty)}`)
  }

  cancelBracket(): void {
    this.bracket = null
    this.detachBracketLines()
    this.emit()
  }

  /** Send the bracket: entry, then the OCO target/stop pair linked in the engine. */
  async placeBracket(): Promise<void> {
    const b = this.bracket
    const sym = this.o.symbol()
    if (!b || !sym || !this.engine) return
    const exit: OrderSide = b.side === 'BUY' ? 'SELL' : 'BUY'
    const summary = `${b.side} bracket ${b.qty} ${sym.symbol} · entry ${this.o.fmt(b.entry)} · TP ${this.o.fmt(b.target)} · SL ${this.o.fmt(b.stop)}`
    if (!this.armed && this.o.gate && !(await this.o.gate(summary))) return
    try {
      const base = {
        symbol: sym.symbol,
        exchange: sym.exchange,
        qty: b.qty,
        product: this.product as Product,
      }
      const entry = await this.engine.placeOrder({
        ...base,
        side: b.side,
        type: 'LIMIT',
        price: b.entry,
      })
      if (!entry.ok) {
        this.o.onToast(entry.reason || 'entry rejected', 'err')
        return
      }
      const tp = await this.engine.placeOrder({
        ...base,
        side: exit,
        type: 'LIMIT',
        price: b.target,
      })
      const sl = await this.engine.placeOrder({
        ...base,
        side: exit,
        type: 'SL-M',
        triggerPrice: b.stop,
      })
      if (tp.ok && sl.ok && tp.clientId && sl.clientId) {
        // One fill cancels the peer — the engine owns that rule.
        this.engine.linkOco(tp.clientId, sl.clientId)
      }
      b.placed = true
      this.o.onToast(`placed ${summary}`, 'ok')
      void this.pollBook()
    } catch (e) {
      this.o.onToast(this.cleanError(e), 'err')
    }
    this.emit()
  }

  /* ── book reconciliation ───────────────────────────────────────────────── */

  private makeOrderLine(o: LineOrder): PriceLine {
    return this.chart!.addPriceLine(
      {
        price: o.triggerPrice ?? o.price,
        color: o.side === 'BUY' ? BUY_COLOR : SELL_COLOR,
        lineWidth: 1,
        dashed: true,
        id: `order:${o.id}`,
        cursor: 'ns-resize',
        extentFromRight: TRADE_EXTENT,
        closeButton: true,
        badge: o.side,
        qty: o.qty,
        leftLabel: o.type,
      },
      0
    )
  }

  async pollBook(): Promise<void> {
    const sym = this.o.symbol()
    if (!sym || !this.chart || this.destroyed) return
    try {
      const orders = await this.o.feed.getOrders()
      const seen = new Set<string>()
      for (const o of orders) {
        if (o.status !== 'working' || o.symbol !== sym.symbol) continue
        seen.add(o.id)
        const px = o.triggerPrice ?? o.price
        const rec = this.orderLines.get(o.id)
        if (rec) {
          rec.order = o as LineOrder
          rec.line.setPrice(px)
        } else {
          this.orderLines.set(o.id, {
            line: this.makeOrderLine(o as LineOrder),
            order: o as LineOrder,
            dragFrom: null,
          })
        }
      }
      for (const [id, rec] of this.orderLines) {
        if (seen.has(id)) continue
        this.chart.removePrimitive(rec.line)
        this.orderLines.delete(id)
      }
    } catch {
      /* transient — the next cycle retries */
    }
    try {
      const j = await this.o.api<{ data?: Record<string, unknown>[] }>('positionbook')
      this.renderPosition(
        (j.data || []).find(
          (p) => p.symbol === sym.symbol && p.exchange === sym.exchange && Number(p.quantity) !== 0
        )
      )
    } catch {
      /* transient */
    }
    this.emit()
  }

  private posLabel(): string {
    if (!this.position) return ''
    const mark = this.lastLtp ?? this.position.avg
    const pnl = (mark - this.position.avg) * this.position.net
    return `@ ${this.o.fmt(this.position.avg)}  ${pnl >= 0 ? '+' : '-'}₹${this.o.money(Math.abs(pnl))}`
  }

  private renderPosition(pos: Record<string, unknown> | undefined): void {
    if (this.posLine && this.chart) {
      this.chart.removePrimitive(this.posLine)
      this.posLine = null
    }
    const net = pos ? Number(pos.quantity) : 0
    this.position =
      pos && net !== 0
        ? { net, avg: Number(pos.average_price), product: String(pos.product ?? '') }
        : null
    if (!this.position || !this.chart) return
    this.posLine = this.chart.addPriceLine(
      {
        price: this.position.avg,
        color: this.position.net > 0 ? '#2e7d6b' : '#a14a52',
        lineWidth: 2,
        dashed: false,
        id: 'position',
        extentFromRight: TRADE_EXTENT,
        closeButton: true,
        badge: this.position.net > 0 ? 'LONG' : 'SHORT',
        qty: Math.abs(this.position.net),
        leftLabel: this.posLabel(),
      },
      0
    )
  }

  async exitPosition(): Promise<void> {
    const sym = this.o.symbol()
    if (!this.position || !sym || !this.engine) return
    const qty = Math.abs(this.position.net)
    const side: OrderSide = this.position.net > 0 ? 'SELL' : 'BUY'
    if (
      !this.armed &&
      this.o.gate &&
      !(await this.o.gate(`Close ${sym.symbol} — ${side} ${qty} at market`))
    ) {
      return
    }
    try {
      // Square off with a plain market order on the opposite side, never a smart order.
      await this.engine.placeOrder({
        symbol: sym.symbol,
        exchange: sym.exchange,
        side,
        type: 'MARKET',
        qty,
        product: (this.position.product || this.product) as Product,
      })
      this.o.onToast('position closed', 'ok')
      void this.pollBook()
    } catch (e) {
      this.o.onToast(this.cleanError(e), 'err')
    }
  }

  /* ── chart interaction ─────────────────────────────────────────────────── */

  onClick(externalId: string): void {
    if (externalId === 'trade:buy') {
      void this.place('BUY', 'MARKET')
      return
    }
    if (externalId === 'trade:sell') {
      void this.place('SELL', 'MARKET')
      return
    }
    if (externalId === 'position::close') {
      void this.exitPosition()
      return
    }
    if (externalId === 'bk-entry::close') {
      this.cancelBracket()
      return
    }
    if (externalId.startsWith('ladder-')) {
      // A ladder row hit-tests as `ladder-<side>:<price>`.
      const [side, price] = externalId.slice('ladder-'.length).split(':')
      const p = Number(price)
      if (Number.isFinite(p)) void this.place(side === 'bid' ? 'BUY' : 'SELL', 'LIMIT', p)
      return
    }
    if (externalId.startsWith('order:') && externalId.endsWith('::close')) {
      const oid = externalId.slice(6, -'::close'.length)
      this.o.feed
        .cancel(oid)
        .then(() => {
          this.o.onToast(`order ${oid} cancelled`, 'ok')
          void this.pollBook()
        })
        .catch((e) => this.o.onToast(this.cleanError(e), 'err'))
    }
  }

  /** Grab point of a panel drag, in plot pixels, plus the offset it started at. */
  private panelDrag: { x: number; y: number; ox: number; oy: number } | null = null

  /**
   * Convert a drag's price/time to plot pixels.
   *
   * The Buy/Sell panel is docked in screen space — it deliberately does not move
   * with pan or zoom — but the drag bus reports chart coordinates, so the two have
   * to be reconciled. Panning is suspended while a primitive drag is armed, so the
   * scales hold still and the conversion tracks the cursor exactly.
   */
  private dragPixels(price: number, time: number): { x: number; y: number } | null {
    const chart = this.chart
    const pane = chart?.panes()[0]
    if (!chart || !pane) return null
    const x = chart.timeToCoordinate(time)
    const y = pane.priceScale.priceToY(price)
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null
  }

  onDrag(externalId: string, price: number, time = 0): void {
    if (externalId === 'trade:move') {
      const p = this.dragPixels(price, time)
      const panel = this.buttons
      if (!p || !panel) return
      if (!this.panelDrag) {
        const o = panel.offset()
        this.panelDrag = { x: p.x, y: p.y, ox: o.x, oy: o.y }
        return
      }
      this.buttonOffset = {
        x: this.panelDrag.ox + (p.x - this.panelDrag.x),
        y: this.panelDrag.oy + (p.y - this.panelDrag.y),
      }
      panel.setOffset(this.buttonOffset.x, this.buttonOffset.y)
      return
    }
    if (externalId.startsWith('bk-')) {
      this.setBracketPrice(externalId.slice(3) as 'entry' | 'tp' | 'sl', price)
      return
    }
    if (!externalId.startsWith('order:') || externalId.endsWith('::close')) return
    const rec = this.orderLines.get(externalId.slice(6))
    if (!rec) return
    if (rec.dragFrom == null) {
      // A drag ghost marks where the order was, so a release always shows what changed.
      rec.dragFrom = rec.line.price
      rec.line.setDragGhost(rec.dragFrom)
    }
    rec.line.setPrice(this.o.snap(price))
  }

  onDragEnd(externalId: string, price: number, time = 0): void {
    if (externalId === 'trade:move') {
      this.onDrag(externalId, price, time)
      this.panelDrag = null
      // Persist where it was dropped, so it is still there after a reload.
      this.o.onDirty()
      return
    }
    if (externalId.startsWith('bk-')) return
    if (!externalId.startsWith('order:') || externalId.endsWith('::close')) return
    const oid = externalId.slice(6)
    const rec = this.orderLines.get(oid)
    if (!rec) return
    rec.line.setDragGhost(null)
    rec.dragFrom = null
    const px = this.o.snap(price)
    const stop = rec.order.type === 'SL' || rec.order.type === 'SL-M'
    // Modify on release, not on every pixel, so the broker is not spammed.
    this.o.feed
      .modify(oid, stop ? { triggerPrice: px } : { price: px })
      .then(() => this.pollBook())
      .catch((e) => {
        this.o.onToast(this.cleanError(e), 'err')
        void this.pollBook()
      })
  }

  /** Build the right-click menu for a price under the cursor. */
  contextMenu(price: number): { price: number; items: CtxItem[] } | null {
    const sym = this.o.symbol()
    if (!sym || sym.quoteOnly) return null
    this.ctxPrice = this.o.snap(price)
    const m = this.o.marketPrice()
    const lotTxt = sym.lots ? `${Math.max(1, Math.floor(this.qty || 1))}L` : String(this.orderQty())
    const defs: [OrderSide, OrderType][] = [
      ['BUY', 'MARKET'],
      ['BUY', 'LIMIT'],
      ['BUY', 'SL'],
      ['SELL', 'MARKET'],
      ['SELL', 'LIMIT'],
      ['SELL', 'SL'],
    ]
    const items = defs.map(([side, type]) => {
      const verb = side === 'BUY' ? 'Buy' : 'Sell'
      const label =
        type === 'MARKET'
          ? `${verb} ${lotTxt} Market`
          : type === 'LIMIT'
            ? `${verb} ${lotTxt} Limit @ ${this.o.fmt(this.ctxPrice)}`
            : `${verb} ${lotTxt} Stop @ ${this.o.fmt(this.ctxPrice)}`
      let enabled = true
      if (m != null) {
        if (type === 'SL') enabled = side === 'BUY' ? this.ctxPrice > m : this.ctxPrice < m
        else if (type === 'LIMIT') enabled = side === 'BUY' ? this.ctxPrice < m : this.ctxPrice > m
      }
      return { side, type, label, enabled }
    })
    return { price: this.ctxPrice, items }
  }

  placeFromContext(side: OrderSide, type: OrderType): void {
    void this.place(side, type, this.ctxPrice)
  }

  async cancelAll(): Promise<void> {
    const ids = [...this.orderLines.keys()]
    if (!ids.length) return
    for (const id of ids) {
      await this.o.feed.cancel(id).catch(() => undefined)
    }
    this.o.onToast(`cancelled ${ids.length} order(s)`, 'ok')
    void this.pollBook()
  }

  /* ── live feeds ────────────────────────────────────────────────────────── */

  onLtp(ltp: number): void {
    this.lastLtp = ltp
    if (this.position && this.posLine) this.posLine.setLeftLabel(this.posLabel())
    if (this.buttons && !this.depthTop) this.buttons.setMark(ltp)
  }

  onDepth(depth: MarketDepth): void {
    this.ladder?.setDepth(depth)
    const bid = depth.bids?.[0]?.price
    const ask = depth.asks?.[0]?.price
    if (typeof bid === 'number' && typeof ask === 'number' && bid > 0 && ask > 0) {
      this.depthTop = { bid, ask }
      this.buttons?.setPrices(bid, ask)
    }
  }

  onQuote(bid: number, ask: number): void {
    if (!(bid > 0 && ask > 0)) return
    this.depthTop = { bid, ask }
    this.buttons?.setPrices(bid, ask)
  }

  /** Real-time order stream: keep the on-chart lines in step with the broker. */
  onOrderUpdate(e: {
    orderId: string
    symbol: string
    action: OrderSide
    quantity: number
    price: number
    triggerPrice?: number
    pricetype: string
    status: string
    averagePrice: number
    rejectionReason: string
  }): void {
    const sym = this.o.symbol()
    if (!sym || e.symbol !== sym.symbol || !this.chart) return
    const working = e.status === 'open' || e.status === 'trigger pending' || e.status === 'pending'
    const rec = this.orderLines.get(e.orderId)
    const order: LineOrder = {
      id: e.orderId,
      side: e.action,
      type: e.pricetype as OrderType,
      qty: e.quantity,
      price: e.price,
      triggerPrice: e.triggerPrice,
      status: working ? 'working' : e.status,
    }
    if (working) {
      if (rec) {
        rec.order = order
        rec.line.setPrice(e.triggerPrice ?? e.price)
      } else {
        this.orderLines.set(e.orderId, {
          line: this.makeOrderLine(order),
          order,
          dragFrom: null,
        })
      }
    } else if (rec) {
      this.chart.removePrimitive(rec.line)
      this.orderLines.delete(e.orderId)
    }
    if (e.status === 'rejected') {
      this.o.onToast(`rejected: ${e.rejectionReason || 'see order book'}`, 'err')
    }
    if (e.status === 'complete') {
      this.engine?.onFill(e.orderId, true)
      this.o.onToast(
        `filled: ${e.action} ${e.quantity} @ ${this.o.fmt(e.averagePrice || e.price)}`,
        'ok'
      )
    }
    if (!working) void this.pollBook()
    this.emit()
  }

  /* ── persistence + teardown ────────────────────────────────────────────── */

  snapshot(): TradingSnapshot {
    return {
      qty: this.qty,
      product: this.product,
      armed: this.armed,
      ladder: this.showLadder,
      buySellButtons: this.showButtons,
      ladderGroupBy: this.ladderGroupBy,
      ladderMaxRows: this.ladderMaxRows,
      buttonOffset: { ...this.buttonOffset },
    }
  }

  restore(snap: Partial<TradingSnapshot> | undefined): void {
    if (!snap) return
    if (snap.buttonOffset) {
      this.buttonOffset = { x: snap.buttonOffset.x || 0, y: snap.buttonOffset.y || 0 }
      this.buttons?.setOffset(this.buttonOffset.x, this.buttonOffset.y)
    }
    if (snap.qty) this.qty = Math.max(1, Math.floor(snap.qty))
    if (snap.product) this.product = snap.product
    if (typeof snap.armed === 'boolean') this.armed = snap.armed
    if (typeof snap.ladder === 'boolean') this.showLadder = snap.ladder
    if (typeof snap.buySellButtons === 'boolean') this.showButtons = snap.buySellButtons
    if (snap.ladderGroupBy) this.ladderGroupBy = snap.ladderGroupBy
    if (snap.ladderMaxRows) this.ladderMaxRows = snap.ladderMaxRows
  }

  dispose(): void {
    this.destroyed = true
    if (this.bookTimer) clearInterval(this.bookTimer)
    this.bookTimer = null
    this.orderLines.clear()
    this.chart = null
    this.engine = null
  }

  private emit(): void {
    this.o.onView({
      qty: this.qty,
      product: this.product,
      armed: this.armed,
      ladder: this.showLadder,
      buySellButtons: this.showButtons,
      orders: [...this.orderLines.values()].map((r) => ({
        id: r.order.id,
        side: r.order.side,
        type: r.order.type,
        qty: r.order.qty,
        price: r.order.triggerPrice ?? r.order.price,
      })),
      position: this.position
        ? {
            net: this.position.net,
            avg: this.position.avg,
            pnl: ((this.lastLtp ?? this.position.avg) - this.position.avg) * this.position.net,
          }
        : null,
      bracket: this.bracket ? { ...this.bracket } : null,
      depthTop: this.depthTop,
    })
  }
}
