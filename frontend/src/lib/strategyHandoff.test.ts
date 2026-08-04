import { describe, expect, it } from 'vitest'

import { buildHandoffSearch, MAX_HANDOFF_LEGS, parseHandoff } from './strategyHandoff'

const HANDOFF = {
  exchange: 'NFO',
  underlying: 'NIFTY',
  expiry: '11AUG26',
  legs: [
    { strike: 24600, optionType: 'CE' as const, side: 'BUY' as const, lots: 2 },
    { strike: 24700, optionType: 'CE' as const, side: 'SELL' as const, lots: 1 },
  ],
}

const parse = (search: string) => parseHandoff(new URLSearchParams(search))

describe('buildHandoffSearch', () => {
  it('encodes legs compactly enough to stay readable in the address bar', () => {
    const search = buildHandoffSearch(HANDOFF)
    expect(search).toContain('legs=24600CE%3ABUY%3A2%2C24700CE%3ASELL%3A1')
    expect(new URLSearchParams(search).get('legs')).toBe('24600CE:BUY:2,24700CE:SELL:1')
  })

  it('round-trips through the parser', () => {
    expect(parse(buildHandoffSearch(HANDOFF))).toEqual(HANDOFF)
  })

  it('round-trips a fractional strike', () => {
    const handoff = {
      ...HANDOFF,
      legs: [{ strike: 292.5, optionType: 'CE' as const, side: 'BUY' as const, lots: 1 }],
    }
    expect(parse(buildHandoffSearch(handoff))).toEqual(handoff)
  })

  it('does not emit trailing zeros on a whole-number strike', () => {
    expect(new URLSearchParams(buildHandoffSearch(HANDOFF)).get('legs')).not.toContain('24600.0')
  })
})

describe('parseHandoff', () => {
  it('parses a single leg', () => {
    const result = parse('exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=24600CE:BUY:2')
    expect(result).toEqual({
      exchange: 'NFO',
      underlying: 'NIFTY',
      expiry: '11AUG26',
      legs: [{ strike: 24600, optionType: 'CE', side: 'BUY', lots: 2 }],
    })
  })

  it('normalises case', () => {
    const result = parse('exchange=nfo&underlying=nifty&expiry=11aug26&legs=24600ce:buy:2')
    expect(result).toEqual({
      exchange: 'NFO',
      underlying: 'NIFTY',
      expiry: '11AUG26',
      legs: [{ strike: 24600, optionType: 'CE', side: 'BUY', lots: 2 }],
    })
  })

  it('returns null when there is no handoff at all', () => {
    expect(parse('load=7')).toBeNull()
  })

  it.each([
    'exchange',
    'underlying',
    'expiry',
  ])('returns null when %s is missing, since the legs cannot be resolved without it', (missing) => {
    const params = new URLSearchParams(
      'exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=24600CE:BUY:2'
    )
    params.delete(missing)
    expect(parseHandoff(params)).toBeNull()
  })

  it('rejects the whole handoff when any single leg is malformed', () => {
    // Silently dropping a leg would hand the builder a DIFFERENT strategy with
    // a different payoff, which is worse than refusing the handoff.
    expect(parse('exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=24600CE:BUY:2,garbage')).toBe(
      null
    )
  })

  it.each([
    ['an unknown option type', '24600XX:BUY:2'],
    ['an unknown side', '24600CE:HOLD:2'],
    ['a missing option type', '24600:BUY:2'],
    ['a zero strike', '0CE:BUY:2'],
    ['a negative strike', '-100CE:BUY:2'],
    ['a non-numeric strike', 'ABCCE:BUY:2'],
    ['zero lots', '24600CE:BUY:0'],
    ['negative lots', '24600CE:BUY:-1'],
    ['fractional lots', '24600CE:BUY:1.5'],
    ['a missing field', '24600CE:BUY'],
    ['an extra field', '24600CE:BUY:2:99'],
    ['an empty leg list', ''],
  ])('rejects %s', (_label, legs) => {
    expect(parse(`exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=${legs}`)).toBeNull()
  })

  it('rejects more legs than a real strategy would carry', () => {
    const legs = Array.from({ length: MAX_HANDOFF_LEGS + 1 }, (_, i) => `${24000 + i * 50}CE:BUY:1`)
    expect(parse(`exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=${legs.join(',')}`)).toBeNull()
  })

  it('accepts exactly the maximum number of legs', () => {
    const legs = Array.from({ length: MAX_HANDOFF_LEGS }, (_, i) => `${24000 + i * 50}CE:BUY:1`)
    const result = parse(`exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=${legs.join(',')}`)
    expect(result?.legs).toHaveLength(MAX_HANDOFF_LEGS)
  })

  it('preserves leg order, which decides the payoff', () => {
    const result = parse(
      'exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=24700CE:SELL:1,24600CE:BUY:2'
    )
    expect(result?.legs.map((l) => l.strike)).toEqual([24700, 24600])
  })
})
