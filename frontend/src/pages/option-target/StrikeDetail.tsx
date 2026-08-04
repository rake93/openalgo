import type * as PlotlyTypes from 'plotly.js'
import { useMemo } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import Plot from '@/lib/Plot2D'
import { cn } from '@/lib/utils'
import type { Candidate, LadderRow, Scenario } from '@/types/option-target'

interface StrikeDetailProps {
  candidate: Candidate
  ladder: LadderRow[]
  scenario: Scenario
  isDark: boolean
}

function formatRupees(value: number): string {
  return value.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

function pnlClass(value: number): string {
  if (value > 0) return 'text-green-500'
  if (value < 0) return 'text-red-500'
  return 'text-muted-foreground'
}

interface StatCellProps {
  label: string
  value: string
  valueClassName?: string
  title?: string
}

function StatCell({ label, value, valueClassName, title }: StatCellProps) {
  return (
    <div className="rounded-md border border-border p-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn('text-sm font-semibold tabular-nums', valueClassName)} title={title}>
        {value}
      </div>
    </div>
  )
}

const EFFECTIVE_DELTA_ABOVE_ONE_TITLE =
  'Above 1.0 because projected implied vol rises over the move, so the premium gains more than delta alone explains.'

export function StrikeDetail({ candidate, ladder, scenario, isDark }: StrikeDetailProps) {
  const themeColors = useMemo(
    () => ({
      bg: 'rgba(0,0,0,0)',
      paper: 'rgba(0,0,0,0)',
      text: isDark ? '#e0e0e0' : '#333333',
      grid: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)',
      line: isDark ? '#60a5fa' : '#2563eb',
      referenceLine: isDark ? 'rgba(255,255,255,0.6)' : 'rgba(0,0,0,0.5)',
      green: isDark ? '#22c55e' : '#16a34a',
      red: isDark ? '#f87171' : '#dc2626',
      hoverBg: isDark ? '#1e293b' : '#ffffff',
      hoverFont: isDark ? '#e0e0e0' : '#333333',
      hoverBorder: isDark ? '#475569' : '#e2e8f0',
    }),
    [isDark]
  )

  const plotConfig: Partial<PlotlyTypes.Config> = useMemo(
    () => ({
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: [
        'pan2d',
        'select2d',
        'lasso2d',
        'autoScale2d',
        'toggleSpikelines',
      ] as PlotlyTypes.ModeBarDefaultButtons[],
      responsive: true,
    }),
    []
  )

  // Card 1: premium ladder
  const ladderPlot = useMemo(() => {
    const x = ladder.map((row) => row.reference_level)
    const y = ladder.map((row) => row.premium)

    const data: PlotlyTypes.Data[] = [
      {
        x,
        y,
        type: 'scatter' as const,
        mode: 'lines+markers' as const,
        name: 'Premium',
        line: { color: themeColors.line, width: 2.5 },
        marker: { size: 4, color: themeColors.line },
        hovertemplate: 'Level: %{x:.2f}<br>Premium: %{y:.2f}<extra></extra>',
      },
    ]

    const shapes: Partial<PlotlyTypes.Shape>[] = [
      {
        type: 'line' as const,
        x0: scenario.reference_now,
        x1: scenario.reference_now,
        y0: 0,
        y1: 1,
        yref: 'paper' as const,
        line: { color: themeColors.referenceLine, width: 1.5, dash: 'dot' as const },
      },
      {
        type: 'line' as const,
        x0: scenario.reference_target,
        x1: scenario.reference_target,
        y0: 0,
        y1: 1,
        yref: 'paper' as const,
        line: { color: themeColors.referenceLine, width: 1.5, dash: 'dash' as const },
      },
    ]

    const annotations: Partial<PlotlyTypes.Annotations>[] = [
      {
        x: scenario.reference_now,
        y: 1,
        yref: 'paper' as const,
        text: `Now ${scenario.reference_now.toFixed(1)}`,
        showarrow: false,
        font: { color: themeColors.text, size: 11 },
        yanchor: 'bottom' as const,
      },
      {
        x: scenario.reference_target,
        y: 1,
        yref: 'paper' as const,
        text: `Target ${scenario.reference_target.toFixed(1)}`,
        showarrow: false,
        font: { color: themeColors.text, size: 11 },
        yanchor: 'bottom' as const,
      },
    ]

    const layout: Partial<PlotlyTypes.Layout> = {
      paper_bgcolor: themeColors.paper,
      plot_bgcolor: themeColors.bg,
      font: { color: themeColors.text, family: 'system-ui, sans-serif' },
      hovermode: 'x unified' as const,
      hoverlabel: {
        bgcolor: themeColors.hoverBg,
        font: { color: themeColors.hoverFont, size: 12 },
        bordercolor: themeColors.hoverBorder,
      },
      showlegend: false,
      margin: { l: 55, r: 20, t: 30, b: 40 },
      xaxis: {
        title: { text: `${scenario.reference} level`, font: { color: themeColors.text, size: 11 } },
        tickfont: { color: themeColors.text, size: 10 },
        gridcolor: themeColors.grid,
      },
      yaxis: {
        title: { text: 'Premium', font: { color: themeColors.text, size: 11 } },
        tickfont: { color: themeColors.text, size: 10 },
        gridcolor: themeColors.grid,
      },
      annotations,
      shapes,
    }

    return { data, layout }
  }, [ladder, scenario, themeColors])

  // Card 2: P&L attribution
  const attributionPlot = useMemo(() => {
    const attribution = candidate.attribution
    const terms: Array<[string, number]> = [
      ['Delta', attribution.delta],
      ['Gamma', attribution.gamma],
      ['Theta', attribution.theta],
      ['Vega', attribution.vega],
      ['Spread', attribution.spread],
      ['Residual', attribution.residual],
    ]

    const data: PlotlyTypes.Data[] = [
      {
        x: terms.map(([label]) => label),
        y: terms.map(([, value]) => value),
        type: 'bar' as const,
        marker: {
          color: terms.map(([, value]) => (value >= 0 ? themeColors.green : themeColors.red)),
        },
        hovertemplate: '%{x}: %{y:.2f}<extra></extra>',
      },
    ]

    const layout: Partial<PlotlyTypes.Layout> = {
      paper_bgcolor: themeColors.paper,
      plot_bgcolor: themeColors.bg,
      font: { color: themeColors.text, family: 'system-ui, sans-serif' },
      hoverlabel: {
        bgcolor: themeColors.hoverBg,
        font: { color: themeColors.hoverFont, size: 12 },
        bordercolor: themeColors.hoverBorder,
      },
      showlegend: false,
      margin: { l: 55, r: 20, t: 20, b: 40 },
      xaxis: { tickfont: { color: themeColors.text, size: 10 }, gridcolor: themeColors.grid },
      yaxis: {
        title: { text: 'P&L / lot', font: { color: themeColors.text, size: 11 } },
        tickfont: { color: themeColors.text, size: 10 },
        gridcolor: themeColors.grid,
        zerolinecolor: themeColors.grid,
      },
    }

    return { data, layout }
  }, [candidate.attribution, themeColors])

  // Card 3: partial-move outcomes
  const scenarioPlot = useMemo(() => {
    const points: Array<[string, number]> = [
      ['50%', candidate.scenario_pnl['50']],
      ['75%', candidate.scenario_pnl['75']],
      ['100%', candidate.scenario_pnl['100']],
    ]

    const data: PlotlyTypes.Data[] = [
      {
        x: points.map(([label]) => label),
        y: points.map(([, value]) => value),
        type: 'bar' as const,
        marker: {
          color: points.map(([, value]) => (value >= 0 ? themeColors.green : themeColors.red)),
        },
        hovertemplate: 'Move completed: %{x}<br>P&L/lot: %{y:.2f}<extra></extra>',
      },
    ]

    const layout: Partial<PlotlyTypes.Layout> = {
      paper_bgcolor: themeColors.paper,
      plot_bgcolor: themeColors.bg,
      font: { color: themeColors.text, family: 'system-ui, sans-serif' },
      hoverlabel: {
        bgcolor: themeColors.hoverBg,
        font: { color: themeColors.hoverFont, size: 12 },
        bordercolor: themeColors.hoverBorder,
      },
      showlegend: false,
      margin: { l: 55, r: 20, t: 20, b: 40 },
      xaxis: {
        type: 'category' as const,
        title: { text: 'Move completed', font: { color: themeColors.text, size: 11 } },
        tickfont: { color: themeColors.text, size: 10 },
        gridcolor: themeColors.grid,
      },
      yaxis: {
        title: { text: 'P&L / lot', font: { color: themeColors.text, size: 11 } },
        tickfont: { color: themeColors.text, size: 10 },
        gridcolor: themeColors.grid,
        zerolinecolor: themeColors.grid,
      },
    }

    return { data, layout }
  }, [candidate.scenario_pnl, themeColors])

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card>
        <CardContent className="p-4">
          <h3 className="font-semibold text-sm mb-2">Premium Ladder</h3>
          <Plot
            data={ladderPlot.data}
            layout={ladderPlot.layout}
            config={plotConfig}
            useResizeHandler
            style={{ width: '100%', height: '320px' }}
          />
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-4">
          <h3 className="font-semibold text-sm mb-2">P&amp;L Attribution</h3>
          <Plot
            data={attributionPlot.data}
            layout={attributionPlot.layout}
            config={plotConfig}
            useResizeHandler
            style={{ width: '100%', height: '320px' }}
          />
          <p className="text-xs text-muted-foreground mt-2">
            Net total:{' '}
            <span className={pnlClass(candidate.attribution.total)}>
              {formatRupees(candidate.attribution.total)}
            </span>
            {'. '}A large residual means the move is big enough that this attribution is indicative
            only, not exact.
          </p>
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardContent className="p-4 space-y-4">
          <h3 className="font-semibold text-sm">Partial-Move Outcomes</h3>
          <Plot
            data={scenarioPlot.data}
            layout={scenarioPlot.layout}
            config={plotConfig}
            useResizeHandler
            style={{ width: '100%', height: '280px' }}
          />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <StatCell label="Delta" value={candidate.greeks_now.delta.toFixed(3)} />
            <StatCell label="Gamma" value={candidate.greeks_now.gamma.toFixed(4)} />
            <StatCell
              label="Theta"
              value={formatRupees(candidate.greeks_now.theta)}
              valueClassName={pnlClass(candidate.greeks_now.theta)}
            />
            <StatCell label="Vega" value={candidate.greeks_now.vega.toFixed(2)} />
            <StatCell label="IV now" value={`${candidate.iv_now_pct.toFixed(1)}%`} />
            <StatCell label="IV at target" value={`${candidate.iv_target_pct.toFixed(1)}%`} />
            <StatCell
              label="Effective delta"
              value={candidate.effective_delta.toFixed(2)}
              valueClassName={
                Math.abs(candidate.effective_delta) > 1 ? 'text-amber-500' : undefined
              }
              title={
                Math.abs(candidate.effective_delta) > 1
                  ? EFFECTIVE_DELTA_ABOVE_ONE_TITLE
                  : undefined
              }
            />
            <StatCell label="Reward : risk" value={candidate.reward_risk.toFixed(2)} />
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

export default StrikeDetail
