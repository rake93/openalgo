/**
 * Compact URL encoding for handing option legs to the Strategy Builder.
 *
 * Deliberately carries only the legs' SEMANTIC identity — underlying, expiry,
 * strike, type, side, lots — and not their prices, IV, lot size or broker
 * symbol. The builder resolves all of those from its own live option chain,
 * which is both fresher than the sending page's snapshot and safer: some
 * brokers do not follow the standard BASE[DDMMMYY][STRIKE][CE|PE]
 * concatenation, so a locally constructed symbol can be invalid
 * (see StrategyBuilder's handleAddManualLeg).
 *
 * Format: ?exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=24600CE:BUY:2,24700CE:SELL:1
 */

export const MAX_HANDOFF_LEGS = 10

export type HandoffOptionType = 'CE' | 'PE'
export type HandoffSide = 'BUY' | 'SELL'

export interface HandoffLeg {
  strike: number
  optionType: HandoffOptionType
  side: HandoffSide
  lots: number
}

export interface LegHandoff {
  exchange: string
  underlying: string
  expiry: string
  legs: HandoffLeg[]
}

/** strike, then CE/PE, then side, then whole lots. */
const LEG_PATTERN = /^(\d+(?:\.\d+)?)(CE|PE):(BUY|SELL):(\d+)$/

function parseLeg(token: string): HandoffLeg | null {
  const match = LEG_PATTERN.exec(token.trim().toUpperCase())
  if (!match) return null

  const strike = Number(match[1])
  const lots = Number(match[4])
  if (!Number.isFinite(strike) || strike <= 0) return null
  if (!Number.isInteger(lots) || lots < 1) return null

  return {
    strike,
    optionType: match[2] as HandoffOptionType,
    side: match[3] as HandoffSide,
    lots,
  }
}

export function buildHandoffSearch(handoff: LegHandoff): string {
  const params = new URLSearchParams({
    exchange: handoff.exchange,
    underlying: handoff.underlying,
    expiry: handoff.expiry,
    // String() rather than toFixed() so 24600 stays "24600" and 292.5 stays
    // "292.5" — a padded "24600.00" would not match a chain strike on sight.
    legs: handoff.legs
      .map((l) => `${String(l.strike)}${l.optionType}:${l.side}:${l.lots}`)
      .join(','),
  })
  return `?${params.toString()}`
}

export function parseHandoff(params: URLSearchParams): LegHandoff | null {
  const raw = params.get('legs')
  if (!raw) return null

  const exchange = params.get('exchange')?.trim().toUpperCase()
  const underlying = params.get('underlying')?.trim().toUpperCase()
  const expiry = params.get('expiry')?.trim().toUpperCase()
  if (!exchange || !underlying || !expiry) return null

  const tokens = raw.split(',')
  if (tokens.length > MAX_HANDOFF_LEGS) return null

  const legs: HandoffLeg[] = []
  for (const token of tokens) {
    const leg = parseLeg(token)
    // Refuse the whole handoff rather than drop one leg: a strategy missing a
    // leg is a different strategy with a different payoff, and it would arrive
    // looking perfectly valid.
    if (!leg) return null
    legs.push(leg)
  }

  return { exchange, underlying, expiry, legs }
}
