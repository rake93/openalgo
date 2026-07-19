/**
 * Crosshair data window — a floating OHLC + indicator-values panel, shown while
 * the pointer hovers the chart (openalgo-charts `subscribeCrosshairMove`).
 * Mirrors TradingView's Data Window. Positioned by the caller over the chart;
 * pointer-events are disabled so it never intercepts chart interaction.
 */

import type { CrosshairData } from '@/lib/charts/workspace'

function fmt(n: number): string {
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

function whenLabel(time: number | null): string {
  if (!time) return ''
  const d = new Date(time * 1000)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

export function DataWindow({ data }: { data: CrosshairData | null }) {
  if (!data?.bar) return null
  const { bar, rows } = data
  const up = bar.close >= bar.open

  return (
    <div className="pointer-events-none absolute left-2 top-2 z-20 min-w-40 max-w-56 rounded-md border border-border bg-card/90 px-2.5 py-2 text-xs shadow-md backdrop-blur">
      <div className="mb-1 font-medium text-muted-foreground">{whenLabel(data.time)}</div>
      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 tabular-nums">
        <span className="text-muted-foreground">O</span>
        <span className="text-right">{fmt(bar.open)}</span>
        <span className="text-muted-foreground">H</span>
        <span className="text-right">{fmt(bar.high)}</span>
        <span className="text-muted-foreground">L</span>
        <span className="text-right">{fmt(bar.low)}</span>
        <span className="text-muted-foreground">C</span>
        <span className={`text-right ${up ? 'text-green-500' : 'text-destructive'}`}>
          {fmt(bar.close)}
        </span>
        {bar.volume != null && (
          <>
            <span className="text-muted-foreground">V</span>
            <span className="text-right">{fmt(bar.volume)}</span>
          </>
        )}
      </div>

      {rows.length > 0 && (
        <div className="mt-2 space-y-1.5 border-t border-border/60 pt-2">
          {rows.map((row) => (
            <div key={row.instanceId}>
              <div className="truncate font-medium">{row.name}</div>
              {row.values.map((v) => (
                <div key={v.id} className="flex items-center justify-between gap-2 tabular-nums">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: v.color }}
                    />
                    <span className="truncate text-muted-foreground">{v.title}</span>
                  </span>
                  <span>{fmt(v.value)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
