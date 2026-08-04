import { Check, ChevronsUpDown } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { gexApi } from '@/api/gex'
import { optionTargetApi, toCompactExpiry } from '@/api/option-target'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useOptionTarget } from '@/hooks/useOptionTarget'
import { useSupportedExchanges } from '@/hooks/useSupportedExchanges'
import ScenarioPanel, { type ScenarioState } from '@/pages/option-target/ScenarioPanel'
import { StrikeDetail } from '@/pages/option-target/StrikeDetail'
import { StrikeTable } from '@/pages/option-target/StrikeTable'
import { useAuthStore } from '@/stores/authStore'
import type { Candidate, Objective, OptionTargetRequest } from '@/types/option-target'

const NEAREST_EXPIRY_VALUE = '__nearest__'

// Mirrors services.pricing_underlying.FUTURES_UNDERLYING_EXCHANGES: exchanges
// with no spot instrument at all, where options are written on a future.
// Purely a UI-side "which reference is even meaningful" set - the backend
// remains the sole owner of *which* future a given underlying resolves to.
const FUTURES_UNDERLYING_EXCHANGES = new Set(['MCX', 'NCDEX', 'NCO'])

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
  const { toolsFnoExchanges, defaultToolsFnoExchange, defaultUnderlyings } = useSupportedExchanges()

  const [exchange, setExchange] = useState(defaultToolsFnoExchange)
  const [underlyings, setUnderlyings] = useState<string[]>(
    defaultUnderlyings[defaultToolsFnoExchange] || []
  )
  const [underlyingOpen, setUnderlyingOpen] = useState(false)
  const [underlying, setUnderlying] = useState(
    defaultUnderlyings[defaultToolsFnoExchange]?.[0] || ''
  )
  const [expiries, setExpiries] = useState<string[]>([])
  const [expiry, setExpiry] = useState('')
  const [scenario, setScenario] = useState<ScenarioState>(DEFAULT_SCENARIO)
  const [objective, setObjective] = useState<Objective>('balanced')
  const [frozen, setFrozen] = useState(false)
  const [selected, setSelected] = useState<Candidate | null>(null)

  const isDark = document.documentElement.classList.contains('dark')

  // Re-sync exchange when broker capabilities load asynchronously. Resets to
  // defaultToolsFnoExchange, not defaultFnoExchange -- the latter is the
  // first of ALL F&O exchanges the broker supports and can itself be one
  // this page excludes (CDS), which would set `exchange` to a value that
  // isn't in `toolsFnoExchanges` and can never be selected in the dropdown.
  useEffect(() => {
    setExchange((prev) =>
      prev && toolsFnoExchanges.some((ex) => ex.value === prev) ? prev : defaultToolsFnoExchange
    )
  }, [defaultToolsFnoExchange, toolsFnoExchanges])

  // Commodity exchanges have no spot instrument at all (see
  // FUTURES_UNDERLYING_EXCHANGES above); the backend rejects a SPOT reference
  // for them with 400. Force the reference to Futures on switching into one
  // of these exchanges so the request is never sent with a reference the
  // backend is guaranteed to reject.
  useEffect(() => {
    if (FUTURES_UNDERLYING_EXCHANGES.has(exchange)) {
      setScenario((prev) => (prev.reference === 'SPOT' ? { ...prev, reference: 'FUT' } : prev))
    }
  }, [exchange])

  // Fetch underlyings when the exchange changes. Seed from the static
  // defaults first so the picker is never empty, then replace with the
  // broker's live list on success; on failure keep the seed rather than
  // surfacing an error, since a missing list must not block the page.
  // Resetting underlying here also guarantees a stale underlying from a
  // previously selected exchange can never be submitted.
  useEffect(() => {
    const defaults = defaultUnderlyings[exchange] || []
    setUnderlyings(defaults)
    setUnderlying(defaults[0] || '')

    let cancelled = false
    const fetchUnderlyings = async () => {
      try {
        const response = await gexApi.getUnderlyings(exchange)
        if (cancelled) return
        if (response.status === 'success' && response.underlyings.length > 0) {
          setUnderlyings(response.underlyings)
          if (!response.underlyings.includes(defaults[0])) {
            setUnderlying(response.underlyings[0])
          }
        }
      } catch {
        // Keep the seeded defaults
      }
    }
    fetchUnderlyings()
    return () => {
      cancelled = true
    }
  }, [exchange, defaultUnderlyings])

  // Fetch expiries when underlying or exchange changes; leave expiry '' so the
  // backend resolves the nearest live expiry until the user picks one
  // explicitly. Resetting expiry/expiries here also guarantees a stale expiry
  // from a previously selected exchange can never be submitted.
  useEffect(() => {
    setExpiry('')
    setExpiries([])
    setSelected(null)

    if (!apiKey || !underlying) {
      return
    }

    let cancelled = false
    const fetchExpiries = async () => {
      try {
        const response = await optionTargetApi.getExpiries(apiKey, underlying, exchange, 'options')
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
  }, [apiKey, underlying, exchange])

  // A new expiry selection can leave a previously selected strike orphaned.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only expiry should retrigger this reset
  useEffect(() => {
    setSelected(null)
  }, [expiry])

  const targetPriceValue = Number.parseFloat(scenario.targetPrice)
  const hasTarget = Number.isFinite(targetPriceValue) && targetPriceValue > 0

  const request = useMemo<OptionTargetRequest | null>(() => {
    if (!apiKey || !underlying) return null
    const parsedTarget = Number.parseFloat(scenario.targetPrice)
    if (!Number.isFinite(parsedTarget) || parsedTarget <= 0) return null

    const req: OptionTargetRequest = {
      apikey: apiKey,
      underlying,
      exchange,
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
  }, [apiKey, underlying, exchange, expiry, scenario, objective])

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
                <Label className="text-xs text-muted-foreground">Exchange</Label>
                <Select value={exchange} onValueChange={setExchange}>
                  <SelectTrigger className="w-[100px]">
                    <SelectValue placeholder="Exchange" />
                  </SelectTrigger>
                  <SelectContent>
                    {toolsFnoExchanges.map((ex) => (
                      <SelectItem key={ex.value} value={ex.value}>
                        {ex.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1">
                <Label className="text-xs text-muted-foreground">Underlying</Label>
                <Popover open={underlyingOpen} onOpenChange={setUnderlyingOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      role="combobox"
                      aria-expanded={underlyingOpen}
                      className="w-[160px] justify-between"
                    >
                      {underlying || 'Underlying'}
                      <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-48 p-0" align="start">
                    <Command>
                      <CommandInput placeholder="Search underlying..." />
                      <CommandList>
                        <CommandEmpty>No underlying found.</CommandEmpty>
                        <CommandGroup>
                          {underlyings.map((u) => (
                            <CommandItem
                              key={u}
                              value={u}
                              onSelect={() => {
                                setUnderlying(u)
                                setUnderlyingOpen(false)
                              }}
                            >
                              <Check
                                className={`mr-2 h-4 w-4 ${underlying === u ? 'opacity-100' : 'opacity-0'}`}
                              />
                              {u}
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                  </PopoverContent>
                </Popover>
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
                  {snapshot.underlying_ref?.kind === 'FUTURE' ? (
                    // No spot instrument exists for this underlying, so `basis` is
                    // meaningless and the backend reports it as null. The carry-bound
                    // plausibility styling doesn't apply either - there's no carry
                    // bound against a non-existent spot - so this badge is always
                    // plain, unlike the basis badge below.
                    <Badge variant="secondary">
                      Parity vs future{' '}
                      {snapshot.parity_vs_underlying != null && snapshot.parity_vs_underlying >= 0
                        ? '+'
                        : ''}
                      {snapshot.parity_vs_underlying != null
                        ? formatPrice(snapshot.parity_vs_underlying)
                        : '-'}
                    </Badge>
                  ) : (
                    <Badge
                      variant={snapshot.basis_plausible ? 'secondary' : 'destructive'}
                      className={snapshot.basis_plausible ? undefined : 'font-semibold'}
                      title={
                        snapshot.basis_plausible
                          ? undefined
                          : 'This basis is larger than carry over the time to expiry can explain. The at-the-money quotes driving put-call parity are probably stale or wide.'
                      }
                    >
                      Basis {snapshot.basis != null && snapshot.basis >= 0 ? '+' : ''}
                      {snapshot.basis != null ? formatPrice(snapshot.basis) : '-'}
                    </Badge>
                  )}
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
                  {!snapshot.market_open && (
                    <Badge variant="outline" className="font-semibold">
                      Market closed
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
          spotDisabled={FUTURES_UNDERLYING_EXCHANGES.has(exchange)}
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
