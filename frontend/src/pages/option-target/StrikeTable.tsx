import { Fragment } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import type { Candidate, Objective } from '@/types/option-target'

interface StrikeTableProps {
  candidates: Candidate[]
  objective: Objective
  selectedStrike: number | null
  onObjectiveChange: (objective: Objective) => void
  onSelect: (candidate: Candidate) => void
  onBuy: (candidate: Candidate) => void
}

const OBJECTIVE_OPTIONS: { value: Objective; label: string }[] = [
  { value: 'balanced', label: 'Balanced score' },
  { value: 'max_pnl', label: 'Max rupees per lot' },
  { value: 'max_return', label: 'Max % return' },
  { value: 'max_rr', label: 'Best reward-to-risk' },
  { value: 'max_robust', label: 'Best partial-move average' },
]

const COLUMN_COUNT = 16

function formatRupees(value: number): string {
  return value.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

function pnlClass(value: number): string {
  if (value > 0) return 'text-green-500'
  if (value < 0) return 'text-red-500'
  return 'text-muted-foreground'
}

const EFFECTIVE_DELTA_ABOVE_ONE_TITLE =
  'Above 1.0 because projected implied vol rises over the move, so the premium gains more than delta alone explains.'

export function StrikeTable({
  candidates,
  objective,
  selectedStrike,
  onObjectiveChange,
  onSelect,
  onBuy,
}: StrikeTableProps) {
  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <h3 className="font-semibold text-sm">Strike Ladder</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Rank by</span>
            <Select
              value={objective}
              onValueChange={(value) => onObjectiveChange(value as Objective)}
            >
              <SelectTrigger className="w-[220px]">
                <SelectValue placeholder="Objective" />
              </SelectTrigger>
              <SelectContent>
                {OBJECTIVE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Strike</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">Projected</TableHead>
                <TableHead className="text-right">P&amp;L/lot</TableHead>
                <TableHead className="text-right">Return %</TableHead>
                <TableHead className="text-right">50% move</TableHead>
                <TableHead className="text-right">75% move</TableHead>
                <TableHead className="text-right">Robust avg</TableHead>
                <TableHead className="text-right">Eff delta</TableHead>
                <TableHead className="text-right">Theta cost</TableHead>
                <TableHead className="text-right">Adverse</TableHead>
                <TableHead className="text-right">R:R</TableHead>
                <TableHead className="text-right">IV now / target</TableHead>
                <TableHead className="text-right">Spread %</TableHead>
                <TableHead className="text-right">OI</TableHead>
                <TableHead className="text-right">Trade</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidates.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={COLUMN_COUNT}
                    className="text-center text-muted-foreground py-6"
                  >
                    No candidate strikes returned
                  </TableCell>
                </TableRow>
              )}
              {candidates.map((candidate) => {
                const isSelected = !candidate.excluded && candidate.strike === selectedStrike
                return (
                  <Fragment key={candidate.symbol}>
                    <TableRow
                      onClick={() => {
                        if (!candidate.excluded) onSelect(candidate)
                      }}
                      className={cn(
                        'font-mono tabular-nums text-xs',
                        candidate.excluded ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
                        candidate.recommended && !candidate.excluded && 'bg-primary/10',
                        isSelected && 'bg-muted ring-1 ring-inset ring-primary/50'
                      )}
                    >
                      <TableCell className="font-sans">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-semibold">{candidate.strike}</span>
                          <span className="text-muted-foreground">{candidate.option_type}</span>
                          <Badge variant="outline" className="text-[10px]">
                            {candidate.label}
                          </Badge>
                          {candidate.recommended && (
                            <Badge className="text-[10px]">Recommended</Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        {formatRupees(candidate.entry_cost)}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatRupees(candidate.projected_premium)}
                      </TableCell>
                      <TableCell
                        className={cn('text-right font-semibold', pnlClass(candidate.pnl_per_lot))}
                      >
                        {formatRupees(candidate.pnl_per_lot)}
                      </TableCell>
                      <TableCell className={cn('text-right', pnlClass(candidate.return_pct))}>
                        {candidate.return_pct.toFixed(1)}%
                      </TableCell>
                      <TableCell
                        className={cn('text-right', pnlClass(candidate.scenario_pnl['50']))}
                      >
                        {formatRupees(candidate.scenario_pnl['50'])}
                      </TableCell>
                      <TableCell
                        className={cn('text-right', pnlClass(candidate.scenario_pnl['75']))}
                      >
                        {formatRupees(candidate.scenario_pnl['75'])}
                      </TableCell>
                      <TableCell
                        className={cn('text-right', pnlClass(candidate.robust_pnl_per_lot))}
                      >
                        {formatRupees(candidate.robust_pnl_per_lot)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          'text-right',
                          Math.abs(candidate.effective_delta) > 1 && 'font-semibold text-amber-500'
                        )}
                        title={
                          Math.abs(candidate.effective_delta) > 1
                            ? EFFECTIVE_DELTA_ABOVE_ONE_TITLE
                            : undefined
                        }
                      >
                        {candidate.effective_delta.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {formatRupees(candidate.theta_cost_per_lot)}
                      </TableCell>
                      <TableCell
                        className={cn('text-right', pnlClass(candidate.adverse_pnl_per_lot))}
                      >
                        {formatRupees(candidate.adverse_pnl_per_lot)}
                      </TableCell>
                      <TableCell className="text-right">
                        {candidate.reward_risk.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {candidate.iv_now_pct.toFixed(1)}% / {candidate.iv_target_pct.toFixed(1)}%
                      </TableCell>
                      <TableCell className="text-right">
                        {candidate.spread_pct.toFixed(1)}%
                      </TableCell>
                      <TableCell className="text-right">{formatRupees(candidate.oi)}</TableCell>
                      <TableCell className="text-right">
                        {!candidate.excluded && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-6 px-2 text-[11px] font-sans"
                            // The row itself selects; buying must not also be a
                            // side effect of that click.
                            onClick={(e) => {
                              e.stopPropagation()
                              onBuy(candidate)
                            }}
                          >
                            Buy
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                    {(candidate.recommended || candidate.excluded) && (
                      <TableRow className="border-b-0 hover:bg-transparent">
                        <TableCell
                          colSpan={COLUMN_COUNT}
                          className={cn(
                            'pt-0 pb-2 text-xs font-sans',
                            candidate.excluded ? 'text-muted-foreground' : 'text-primary'
                          )}
                        >
                          {candidate.excluded
                            ? `Excluded: ${candidate.exclude_reason}`
                            : candidate.recommend_reason}
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}

export default StrikeTable
