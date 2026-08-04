import { useEffect, useMemo, useState } from 'react'
import { optionTargetApi, toCompactExpiry } from '@/api/option-target'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
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
import { Switch } from '@/components/ui/switch'
import { useOptionTarget } from '@/hooks/useOptionTarget'
import ScenarioPanel, { type ScenarioState } from '@/pages/option-target/ScenarioPanel'
import { StrikeDetail } from '@/pages/option-target/StrikeDetail'
import { StrikeTable } from '@/pages/option-target/StrikeTable'
import { useAuthStore } from '@/stores/authStore'
import type { Candidate, Objective, OptionTargetRequest } from '@/types/option-target'

const NEAREST_EXPIRY_VALUE = '__nearest__'

const DEFAULT_SCENARIO: ScenarioState = {
  reference: 'FUT',
  targetPrice: '',
  holdValue: '15',
  holdUnit: 'minutes',
  ivModel: 'smile_slide',
  volBeta: 'auto',
  volShift: '0',
  dayCount: 'calendar',
  lots: '1',
}

function formatPrice(value: number): string {
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

/** Days-to-expiry in whichever unit is legible at that scale. */
function formatDte(days: number): string {
  if (!Number.isFinite(days) || days < 0) return '-'
  const minutes = days * 24 * 60
  if (minutes < 90) return `${minutes.toFixed(0)} min`
  if (days < 1) return `${(days * 24).toFixed(1)} h`
  return `${days.toFixed(2)} d`
}

export default function OptionTargetCalculator() {
  const { apiKey } = useAuthStore()

  const [underlyingInput, setUnderlyingInput] = useState('NIFTY')
  const [underlying, setUnderlying] = useState('NIFTY')
  const [expiries, setExpiries] = useState<string[]>([])
  const [expiry, setExpiry] = useState('')
  const [scenario, setScenario] = useState<ScenarioState>(DEFAULT_SCENARIO)
  const [objective, setObjective] = useState<Objective>('balanced')
  const [frozen, setFrozen] = useState(false)
  const [selected, setSelected] = useState<Candidate | null>(null)

  const isDark = document.documentElement.classList.contains('dark')

  // Fetch expiries when underlying changes; leave expiry '' so the backend
  // resolves the nearest live expiry until the user picks one explicitly.
  useEffect(() => {
    if (!apiKey || !underlying) {
      setExpiries([])
      return
    }
    setExpiry('')
    setSelected(null)

    let cancelled = false
    const fetchExpiries = async () => {
      try {
        const response = await optionTargetApi.getExpiries(apiKey, underlying, 'NFO', 'options')
        if (cancelled) return
        if (response.status === 'success' && response.data.length > 0) {
          setExpiries(response.data.map(toCompactExpiry))
        } else {
          setExpiries([])
        }
      } catch {
        if (!cancelled) setExpiries([])
      }
    }
    fetchExpiries()
    return () => {
      cancelled = true
    }
  }, [apiKey, underlying])

  // A new expiry selection can leave a previously selected strike orphaned.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only expiry should retrigger this reset
  useEffect(() => {
    setSelected(null)
  }, [expiry])

  const commitUnderlying = () => {
    const next = underlyingInput.trim().toUpperCase()
    if (next && next !== underlying) {
      setUnderlying(next)
    } else {
      setUnderlyingInput(underlying)
    }
  }

  const targetPriceValue = Number.parseFloat(scenario.targetPrice)
  const hasTarget = Number.isFinite(targetPriceValue) && targetPriceValue > 0

  const request = useMemo<OptionTargetRequest | null>(() => {
    if (!apiKey || !underlying) return null
    const parsedTarget = Number.parseFloat(scenario.targetPrice)
    if (!Number.isFinite(parsedTarget) || parsedTarget <= 0) return null

    const req: OptionTargetRequest = {
      apikey: apiKey,
      underlying,
      exchange: 'NFO',
      target_price: parsedTarget,
      reference: scenario.reference,
      iv_model: scenario.ivModel,
      vol_beta: scenario.volBeta,
      day_count: scenario.dayCount,
      objective,
    }

    if (expiry) req.expiry_date = expiry

    const volShift = Number.parseFloat(scenario.volShift)
    if (Number.isFinite(volShift)) req.vol_shift = volShift

    const lots = Number.parseInt(scenario.lots, 10)
    if (Number.isFinite(lots) && lots > 0) req.lots = lots

    const holdValue = Number.parseFloat(scenario.holdValue)
    if (Number.isFinite(holdValue) && holdValue > 0) {
      if (scenario.holdUnit === 'days') {
        req.hold_days = holdValue
      } else {
        req.hold_minutes = holdValue
      }
    }

    return req
  }, [apiKey, underlying, expiry, scenario, objective])

  const { data, error, isLoading, updatedAt, refetch } = useOptionTarget({
    apiKey,
    request,
    frozen,
  })

  // Keep the selection alive across polls by resolving the latest candidate
  // with the same strike; fall back to the backend's recommended strike.
  const activeCandidate = useMemo<Candidate | null>(() => {
    if (!data) return null
    if (selected) {
      const stillPresent = data.candidates.find((c) => c.strike === selected.strike)
      if (stillPresent) return stillPresent
    }
    return data.candidates.find((c) => c.strike === data.recommended_strike) ?? null
  }, [data, selected])

  const snapshot = data?.snapshot ?? null

  return (
    <div className="py-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Option Target Calculator</h1>
      </div>

      {/* Header: underlying / expiry / live snapshot */}
      <Card>
        <CardContent className="p-4 space-y-3">
          <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col gap-1">
                <Label htmlFor="ot-underlying" className="text-xs text-muted-foreground">
                  Underlying
                </Label>
                <Input
                  id="ot-underlying"
                  value={underlyingInput}
                  onChange={(e) => setUnderlyingInput(e.target.value.toUpperCase())}
                  onBlur={commitUnderlying}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') e.currentTarget.blur()
                  }}
                  placeholder="NIFTY"
                  className="w-28 uppercase"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs text-muted-foreground">Expiry</Label>
                <Select
                  value={expiry === '' ? NEAREST_EXPIRY_VALUE : expiry}
                  onValueChange={(value) => setExpiry(value === NEAREST_EXPIRY_VALUE ? '' : value)}
                >
                  <SelectTrigger className="w-[160px]">
                    <SelectValue placeholder="Expiry" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NEAREST_EXPIRY_VALUE}>Nearest (auto)</SelectItem>
                    {expiries.map((exp) => (
                      <SelectItem key={exp} value={exp}>
                        {exp}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {snapshot && data && (
                <div className="flex flex-wrap items-center gap-2 pl-3 border-l border-border">
                  <Badge variant="secondary">Spot {formatPrice(snapshot.spot)}</Badge>
                  <Badge variant="secondary">Forward {formatPrice(snapshot.forward)}</Badge>
                  <Badge variant="secondary">
                    Basis {snapshot.basis >= 0 ? '+' : ''}
                    {formatPrice(snapshot.basis)}
                  </Badge>
                  <Badge variant="secondary">ATM {snapshot.atm_strike}</Badge>
                  <Badge variant="secondary">ATM IV {snapshot.atm_iv_pct.toFixed(1)}%</Badge>
                  <Badge variant="secondary">DTE {formatDte(snapshot.days_to_expiry)}</Badge>
                  <Badge variant={data.scenario.forward_mode === 'exact' ? 'secondary' : 'outline'}>
                    {data.scenario.forward_mode === 'exact'
                      ? 'Forward: Exact'
                      : 'Forward: Basis-modelled'}
                  </Badge>
                  {snapshot.is_zero_dte && (
                    <Badge variant="destructive" className="font-semibold">
                      0DTE
                    </Badge>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground">
                {updatedAt ? `Updated ${updatedAt.toLocaleTimeString()}` : 'Not updated yet'}
              </span>
              {isLoading && (
                <span className="text-xs text-muted-foreground animate-pulse">Loading...</span>
              )}
              <div className="flex items-center gap-2">
                <Switch id="ot-freeze" checked={frozen} onCheckedChange={setFrozen} />
                <Label htmlFor="ot-freeze" className="text-xs">
                  Freeze
                </Label>
              </div>
              <button
                type="button"
                onClick={refetch}
                disabled={!request}
                className="text-xs text-primary hover:underline disabled:opacity-50 disabled:cursor-not-allowed disabled:no-underline"
              >
                Refresh
              </button>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {data?.warnings.map((warning) => (
        <Alert key={warning} variant="warning">
          <AlertDescription>{warning}</AlertDescription>
        </Alert>
      ))}

      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
        <ScenarioPanel
          state={scenario}
          referenceNow={data?.scenario.reference_now ?? 0}
          scenario={data?.scenario ?? null}
          onChange={setScenario}
        />

        <div className="space-y-4">
          {!hasTarget && (
            <Card>
              <CardContent className="p-8 text-center text-muted-foreground">
                Enter a target price in the scenario panel to project strike outcomes.
              </CardContent>
            </Card>
          )}

          {hasTarget && !data && (
            <Card>
              <CardContent className="p-8 text-center text-muted-foreground">
                {isLoading ? 'Computing projections...' : 'Waiting for data...'}
              </CardContent>
            </Card>
          )}

          {hasTarget && data && (
            <>
              <StrikeTable
                candidates={data.candidates}
                objective={objective}
                selectedStrike={activeCandidate?.strike ?? null}
                onObjectiveChange={setObjective}
                onSelect={setSelected}
              />

              {activeCandidate && (
                <StrikeDetail
                  candidate={activeCandidate}
                  ladder={data.ladder}
                  scenario={data.scenario}
                  isDark={isDark}
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
