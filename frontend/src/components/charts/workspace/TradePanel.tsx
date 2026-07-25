/**
 * Trading dock: order entry, working orders, position, and the depth ladder.
 *
 * The panel is deliberately blunt about risk. "Arm" is off by default, and
 * while it is off every order asks for confirmation first; turning it on says
 * so in plain words rather than with an icon. Analyzer mode is shown as a badge
 * whenever it is active, so nobody sends a live order believing it is a
 * simulation, or the reverse.
 */

import { Switch } from '@/components/ui/switch'
import type { BracketDraft, TradingViewState } from '@/lib/charts/trading-layer'
import type { SymbolView } from '@/lib/charts/workspace'
import { cn } from '@/lib/utils'
import { Icon } from './icons'
import { Eyebrow, Field, TinySelect } from './primitives'

export interface TradePanelProps {
  symbol: SymbolView | null
  view: TradingViewState
  analyzer: boolean
  fmt(n: number): string
  onQty(n: number): void
  onProduct(p: string): void
  onArm(on: boolean): void
  onLadder(on: boolean): void
  onBuySellButtons(on: boolean): void
  onMarket(side: 'BUY' | 'SELL'): void
  onBracket(side: 'BUY' | 'SELL'): void
  onPlaceBracket(): void
  onCancelBracket(): void
  onCancelAll(): void
  onExitPosition(): void
}

export function TradePanel(p: TradePanelProps) {
  const sym = p.symbol
  const v = p.view

  if (!sym) {
    return (
      <p className="px-3 py-6 text-center text-[13px] text-muted-foreground">
        Pick a symbol to trade it from the chart.
      </p>
    )
  }
  if (sym.quoteOnly) {
    return (
      <p className="px-3 py-6 text-center text-[13px] leading-relaxed text-muted-foreground">
        {sym.exchange} is quote-only.
        <br />
        Trade the futures or options contract instead.
      </p>
    )
  }

  const lots = sym.lots
  const realQty = lots ? v.qty * sym.lotsize : v.qty
  const freezeHit = sym.freezeQty > 1 && realQty > sym.freezeQty

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-0 overflow-y-auto">
        {/* Order entry */}
        <section className="space-y-2.5 border-b border-border px-3 py-3">
          <div className="flex items-center justify-between">
            <Eyebrow>Order</Eyebrow>
            {p.analyzer && (
              <span className="rounded bg-primary/12 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-primary">
                Analyzer
              </span>
            )}
          </div>

          <Field
            label={lots ? 'Lots' : 'Quantity'}
            hint={lots ? `${sym.lotsize} per lot` : undefined}
          >
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Decrease"
                onClick={() => p.onQty(v.qty - 1)}
                className="grid h-7 w-7 place-items-center rounded-md border border-border hover:bg-accent"
              >
                −
              </button>
              <input
                type="number"
                min={1}
                value={v.qty}
                onChange={(e) => p.onQty(Number(e.target.value))}
                className="h-7 w-16 rounded-md border border-border bg-background px-2 text-center text-[12px] tabular-nums outline-none focus:border-primary/60"
              />
              <button
                type="button"
                aria-label="Increase"
                onClick={() => p.onQty(v.qty + 1)}
                className="grid h-7 w-7 place-items-center rounded-md border border-border hover:bg-accent"
              >
                +
              </button>
            </div>
          </Field>

          {lots && (
            <p
              className={cn(
                'text-[11px] tabular-nums',
                freezeHit ? 'text-destructive' : 'text-muted-foreground'
              )}
            >
              {v.qty} × {sym.lotsize} = {realQty} qty
              {freezeHit && ` — over the ${sym.freezeQty} freeze limit`}
            </p>
          )}

          <Field label="Product">
            <TinySelect value={v.product} onChange={(e) => p.onProduct(e.target.value)}>
              {sym.productOptions.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </TinySelect>
          </Field>

          {v.depthTop && (
            <div className="grid grid-cols-2 gap-2 text-[12px] tabular-nums">
              <div className="rounded-md border border-border px-2 py-1">
                <div className="text-[10px] uppercase tracking-[0.06em] text-muted-foreground">
                  Bid
                </div>
                <div className="text-emerald-500">{p.fmt(v.depthTop.bid)}</div>
              </div>
              <div className="rounded-md border border-border px-2 py-1 text-right">
                <div className="text-[10px] uppercase tracking-[0.06em] text-muted-foreground">
                  Ask
                </div>
                <div className="text-rose-500">{p.fmt(v.depthTop.ask)}</div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => p.onMarket('BUY')}
              className="h-9 rounded-md bg-emerald-600 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Buy
            </button>
            <button
              type="button"
              onClick={() => p.onMarket('SELL')}
              className="h-9 rounded-md bg-rose-600 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Sell
            </button>
          </div>

          <label className="flex items-center justify-between gap-3 rounded-md border border-border px-2.5 py-2">
            <span className="min-w-0">
              <span className="block text-[12px]">Skip confirmation</span>
              <span className="block text-[10.5px] leading-snug text-muted-foreground">
                {v.armed ? 'Orders send the moment you click' : 'Every order asks first'}
              </span>
            </span>
            <Switch checked={v.armed} onCheckedChange={p.onArm} />
          </label>
        </section>

        {/* Bracket */}
        <section className="space-y-2.5 border-b border-border px-3 py-3">
          <Eyebrow>Bracket</Eyebrow>
          {v.bracket ? (
            <BracketCard bracket={v.bracket} fmt={p.fmt} />
          ) : (
            <p className="text-[11.5px] leading-snug text-muted-foreground">
              Places an entry with an OCO target and stop. Drag any leg on the chart; the entry
              carries the whole bracket with it.
            </p>
          )}
          {v.bracket ? (
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={p.onPlaceBracket}
                className="h-8 rounded-md bg-primary text-[12.5px] font-semibold text-primary-foreground hover:brightness-110"
              >
                Send bracket
              </button>
              <button
                type="button"
                onClick={p.onCancelBracket}
                className="h-8 rounded-md border border-border text-[12.5px] hover:bg-accent"
              >
                Discard
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => p.onBracket('BUY')}
                className="h-8 rounded-md border border-emerald-600/40 text-[12.5px] font-medium text-emerald-500 hover:bg-emerald-500/10"
              >
                Long setup
              </button>
              <button
                type="button"
                onClick={() => p.onBracket('SELL')}
                className="h-8 rounded-md border border-rose-600/40 text-[12.5px] font-medium text-rose-500 hover:bg-rose-500/10"
              >
                Short setup
              </button>
            </div>
          )}
        </section>

        {/* Position */}
        <section className="space-y-2 border-b border-border px-3 py-3">
          <Eyebrow>Position</Eyebrow>
          {v.position ? (
            <>
              <div className="flex items-baseline justify-between tabular-nums">
                <span
                  className={cn(
                    'text-[13px] font-semibold',
                    v.position.net > 0 ? 'text-emerald-500' : 'text-rose-500'
                  )}
                >
                  {v.position.net > 0 ? 'Long' : 'Short'} {Math.abs(v.position.net)}
                </span>
                <span className="text-[12px] text-muted-foreground">@ {p.fmt(v.position.avg)}</span>
              </div>
              <div
                className={cn(
                  'text-[15px] font-semibold tabular-nums',
                  v.position.pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'
                )}
              >
                {v.position.pnl >= 0 ? '+' : '−'}₹{Math.abs(v.position.pnl).toFixed(2)}
              </div>
              <button
                type="button"
                onClick={p.onExitPosition}
                className="h-8 w-full rounded-md border border-border text-[12.5px] hover:bg-accent"
              >
                Close at market
              </button>
            </>
          ) : (
            <p className="text-[12px] text-muted-foreground">Flat.</p>
          )}
        </section>

        {/* Working orders */}
        <section className="space-y-2 border-b border-border px-3 py-3">
          <div className="flex items-center justify-between">
            <Eyebrow>Working orders</Eyebrow>
            {v.orders.length > 0 && (
              <button
                type="button"
                onClick={p.onCancelAll}
                className="text-[11px] text-muted-foreground underline-offset-2 hover:text-destructive hover:underline"
              >
                Cancel all
              </button>
            )}
          </div>
          {v.orders.length === 0 ? (
            <p className="text-[12px] text-muted-foreground">
              None. Right-click the chart to place one at a price.
            </p>
          ) : (
            <ul className="space-y-1">
              {v.orders.map((o) => (
                <li
                  key={o.id}
                  className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5 text-[12px] tabular-nums"
                >
                  <span
                    className={cn(
                      'rounded px-1 py-0.5 text-[10px] font-bold',
                      o.side === 'BUY'
                        ? 'bg-emerald-500/15 text-emerald-500'
                        : 'bg-rose-500/15 text-rose-500'
                    )}
                  >
                    {o.side}
                  </span>
                  <span className="text-muted-foreground">{o.type}</span>
                  <span className="ml-auto">{o.qty}</span>
                  <span className="w-16 text-right">{p.fmt(o.price)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* On-chart tools */}
        <section className="space-y-2 px-3 py-3">
          <Eyebrow>On the chart</Eyebrow>
          <label className="flex items-center justify-between gap-3 text-[12px]">
            <span className="flex items-center gap-2">
              <Icon name="trade" className="h-4 w-4 text-muted-foreground" />
              Depth ladder
            </span>
            <Switch checked={v.ladder} onCheckedChange={p.onLadder} />
          </label>
          <label className="flex items-center justify-between gap-3 text-[12px]">
            <span className="flex items-center gap-2">
              <Icon name="plus" className="h-4 w-4 text-muted-foreground" />
              Buy / sell buttons
            </span>
            <Switch checked={v.buySellButtons} onCheckedChange={p.onBuySellButtons} />
          </label>
        </section>
      </div>
    </div>
  )
}

function BracketCard({ bracket, fmt }: { bracket: BracketDraft; fmt(n: number): string }) {
  const risk = Math.abs(bracket.entry - bracket.stop)
  const reward = Math.abs(bracket.target - bracket.entry)
  const rr = risk > 0 ? reward / risk : null
  return (
    <div className="space-y-1 rounded-md border border-border px-2.5 py-2 text-[12px] tabular-nums">
      <Row label="Target" value={fmt(bracket.target)} tone="up" />
      <Row label="Entry" value={fmt(bracket.entry)} />
      <Row label="Stop" value={fmt(bracket.stop)} tone="down" />
      <div className="mt-1 flex items-center justify-between border-t border-border/60 pt-1.5 text-[11px] text-muted-foreground">
        <span>Risk : reward</span>
        <span className="text-foreground">{rr == null ? '—' : `1 : ${rr.toFixed(2)}`}</span>
      </div>
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>Risk on {bracket.qty}</span>
        <span className="text-foreground">₹{(risk * bracket.qty).toFixed(2)}</span>
      </div>
    </div>
  )
}

function Row({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn(tone === 'up' && 'text-emerald-500', tone === 'down' && 'text-rose-500')}>
        {value}
      </span>
    </div>
  )
}
