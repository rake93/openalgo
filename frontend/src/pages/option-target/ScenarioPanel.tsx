import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { DayCount, IvModel, Reference, Scenario } from '@/types/option-target'

export interface ScenarioState {
  reference: Reference
  targetPrice: string
  holdValue: string
  holdUnit: 'minutes' | 'days'
  ivModel: IvModel
  volBeta: string
  volShift: string
  dayCount: DayCount
  lots: string
}

interface Props {
  state: ScenarioState
  referenceNow: number
  scenario: Scenario | null
  onChange: (next: ScenarioState) => void
  /** True when the selected exchange prices its options off a future with no
   *  spot instrument at all (MCX and other commodity exchanges). Disables the
   *  Spot option in the Reference selector rather than letting the user pick
   *  a reference the backend will reject. */
  spotDisabled?: boolean
}

const QUICK_SET_PERCENTS = [0.25, 0.5, 1] as const

function quickSetPrice(referenceNow: number, pct: number): string {
  return (referenceNow * (1 + pct / 100)).toFixed(2)
}

function formatForwardModeLine(scenario: Scenario): string {
  return scenario.forward_mode === 'exact'
    ? 'Forward is exact — a same-expiry future was found, so no basis is assumed.'
    : 'Forward is basis-modelled — no same-expiry future was found; the basis is estimated.'
}

function formatVolBetaLine(scenario: Scenario): string {
  const { beta, source, reason, r_squared: rSquared, samples } = scenario.vol_beta
  const magnitude = `Using ${beta.toFixed(2)} vol pts per 1%`
  switch (source) {
    case 'estimated':
      return `${magnitude} (measured, R² ${rSquared.toFixed(2)}, n=${samples})`
    case 'fallback':
      return `${magnitude} (fallback: ${reason})`
    case 'preset':
      return `${magnitude} (preset)`
    case 'manual':
      return `${magnitude} (manual)`
    default:
      return magnitude
  }
}

export default function ScenarioPanel({
  state,
  referenceNow,
  scenario,
  onChange,
  spotDisabled = false,
}: Props) {
  const update = <K extends keyof ScenarioState>(key: K, value: ScenarioState[K]) => {
    onChange({ ...state, [key]: value })
  }

  const quickSetDisabled = referenceNow <= 0

  return (
    <Card>
      <CardContent className="p-4 space-y-4">
        {/* Reference */}
        <div className="space-y-1">
          <Label htmlFor="ot-reference">Reference</Label>
          <Select
            value={state.reference}
            onValueChange={(value) => update('reference', value as Reference)}
          >
            <SelectTrigger id="ot-reference">
              <SelectValue placeholder="Reference" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="FUT">Futures</SelectItem>
              <SelectItem value="SPOT" disabled={spotDisabled}>
                Spot
              </SelectItem>
            </SelectContent>
          </Select>
          {spotDisabled && (
            <p className="text-xs text-muted-foreground">
              Commodity options are written on futures and have no spot instrument, so the reference
              is locked to Futures.
            </p>
          )}
          {scenario && (
            <p className="text-xs text-muted-foreground">{formatForwardModeLine(scenario)}</p>
          )}
        </div>

        {/* Target price */}
        <div className="space-y-1">
          <Label htmlFor="ot-target-price">Target price</Label>
          <Input
            id="ot-target-price"
            type="number"
            inputMode="decimal"
            value={state.targetPrice}
            onChange={(e) => update('targetPrice', e.target.value)}
            placeholder="Target price"
          />
          <div className="flex flex-wrap gap-1.5 pt-1">
            {QUICK_SET_PERCENTS.slice()
              .reverse()
              .map((pct) => (
                <Button
                  key={`-${pct}`}
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={quickSetDisabled}
                  onClick={() => update('targetPrice', quickSetPrice(referenceNow, -pct))}
                >
                  -{pct}%
                </Button>
              ))}
            {QUICK_SET_PERCENTS.map((pct) => (
              <Button
                key={`+${pct}`}
                type="button"
                variant="outline"
                size="sm"
                disabled={quickSetDisabled}
                onClick={() => update('targetPrice', quickSetPrice(referenceNow, pct))}
              >
                +{pct}%
              </Button>
            ))}
          </div>
        </div>

        {/* Hold time */}
        <div className="space-y-1">
          <Label htmlFor="ot-hold-value">Hold time</Label>
          <div className="flex gap-2">
            <Input
              id="ot-hold-value"
              type="number"
              inputMode="decimal"
              value={state.holdValue}
              onChange={(e) => update('holdValue', e.target.value)}
              placeholder="Hold time"
              className="flex-1"
            />
            <Select
              value={state.holdUnit}
              onValueChange={(value) => update('holdUnit', value as 'minutes' | 'days')}
            >
              <SelectTrigger className="w-[120px]">
                <SelectValue placeholder="Unit" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="minutes">Minutes</SelectItem>
                <SelectItem value="days">Days</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Lots */}
        <div className="space-y-1">
          <Label htmlFor="ot-lots">Lots</Label>
          <Input
            id="ot-lots"
            type="number"
            inputMode="numeric"
            value={state.lots}
            onChange={(e) => update('lots', e.target.value)}
            placeholder="Lots"
          />
        </div>

        {/* IV model */}
        <div className="space-y-1">
          <Label htmlFor="ot-iv-model">IV model</Label>
          <Select
            value={state.ivModel}
            onValueChange={(value) => update('ivModel', value as IvModel)}
          >
            <SelectTrigger id="ot-iv-model">
              <SelectValue placeholder="IV model" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="smile_slide">Smile slide</SelectItem>
              <SelectItem value="sticky_strike">Sticky strike</SelectItem>
            </SelectContent>
          </Select>
          {scenario?.iv_model_overridden && (
            <p className="text-xs text-amber-500">
              Requested smile slide was overridden to sticky-strike because the smile fit was
              unreliable.
            </p>
          )}
        </div>

        {/* Vol response */}
        <div className="space-y-1">
          <Label htmlFor="ot-vol-beta">Vol response</Label>
          <Select value={state.volBeta} onValueChange={(value) => update('volBeta', value)}>
            <SelectTrigger id="ot-vol-beta">
              <SelectValue placeholder="Vol response" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Auto (measured)</SelectItem>
              <SelectItem value="off">Off</SelectItem>
              <SelectItem value="calm">Calm</SelectItem>
              <SelectItem value="normal">Normal</SelectItem>
              <SelectItem value="panic">Panic</SelectItem>
            </SelectContent>
          </Select>
          {scenario && (
            <p className="text-xs text-muted-foreground">{formatVolBetaLine(scenario)}</p>
          )}
        </div>

        {/* Manual vol shift */}
        <div className="space-y-1">
          <Label htmlFor="ot-vol-shift">Manual vol shift (vol points)</Label>
          <Input
            id="ot-vol-shift"
            type="number"
            inputMode="decimal"
            value={state.volShift}
            onChange={(e) => update('volShift', e.target.value)}
            placeholder="0"
          />
        </div>

        {/* Day count */}
        <div className="space-y-1">
          <Label htmlFor="ot-day-count">Day count</Label>
          <Select
            value={state.dayCount}
            onValueChange={(value) => update('dayCount', value as DayCount)}
          >
            <SelectTrigger id="ot-day-count">
              <SelectValue placeholder="Day count" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="calendar">Calendar (365)</SelectItem>
              <SelectItem value="trading">Trading days (252)</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </CardContent>
    </Card>
  )
}
