/**
 * GexDashboard behaviour.
 *
 * jest-dom matchers are registered globally in `src/test/setup.ts`, so no
 * per-file import is needed here.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { GEXLevelsResponse, GEXQuality } from '@/api/gex'
import { GexDashboard } from './GexDashboard'

function makeQuality(overrides: Partial<GEXQuality> = {}): GEXQuality {
  return {
    verdict: 'good',
    may_draw: true,
    strikes_used: 20,
    strikes_priced: 20,
    both_sides: true,
    wall_at_edge: false,
    notes: [],
    ...overrides,
  }
}

function makeData(overrides: Partial<GEXLevelsResponse> = {}): GEXLevelsResponse {
  return {
    status: 'success',
    underlying: 'NIFTY',
    exchange: 'NFO',
    expiry_date: '28AUG25',
    weight_by: 'oi',
    spot_price: 24610.5,
    forward_price: 24650.2,
    atm_strike: 24600,
    lot_size: 75,
    dte_days: 7,
    strikes: [],
    call_wall: 24800,
    put_wall: 24400,
    zero_gamma: 24550,
    total_call_gex: 125000000,
    total_put_gex: -98000000,
    net_gex: 27000000,
    regime: 'suppressive',
    quality: makeQuality(),
    ...overrides,
  }
}

describe('GexDashboard', () => {
  it('renders the walls and the zero-gamma level', () => {
    render(<GexDashboard data={makeData()} stale={false} />)
    expect(screen.getByText('24,800')).toBeInTheDocument()
    expect(screen.getByText('24,400')).toBeInTheDocument()
    expect(screen.getByText('24,550')).toBeInTheDocument()
  })

  it('reads Suppressive for positive net gamma and Amplifying for negative — never bullish/bearish', () => {
    const { rerender } = render(
      <GexDashboard data={makeData({ regime: 'suppressive' })} stale={false} />
    )
    expect(screen.getByText('Suppressive')).toBeInTheDocument()

    rerender(<GexDashboard data={makeData({ regime: 'amplifying' })} stale={false} />)
    expect(screen.getByText('Amplifying')).toBeInTheDocument()

    const seen = document.body.textContent?.toLowerCase() ?? ''
    expect(seen).not.toContain('bullish')
    expect(seen).not.toContain('bearish')
  })

  it('renders "No local cross" when zero_gamma is null, not a dash or an error', () => {
    render(<GexDashboard data={makeData({ zero_gamma: null })} stale={false} />)
    expect(screen.getByText('No local cross')).toBeInTheDocument()
  })

  it('reports strikes_priced of strikes_used in the data-status row', () => {
    render(
      <GexDashboard
        data={makeData({ quality: makeQuality({ strikes_used: 24, strikes_priced: 19 }) })}
        stale={false}
      />
    )
    expect(screen.getByText('19 of 24 strikes')).toBeInTheDocument()
  })

  it('surfaces quality notes verbatim for a degraded verdict', () => {
    const note = 'Only one side of the chain priced cleanly — walls may be unreliable.'
    render(
      <GexDashboard
        data={makeData({
          quality: makeQuality({
            verdict: 'degraded',
            strikes_used: 20,
            strikes_priced: 6,
            both_sides: false,
            wall_at_edge: true,
            notes: [note],
          }),
        })}
        stale={false}
      />
    )
    expect(screen.getByText(note)).toBeInTheDocument()
  })

  it('shows the stale caveat while still showing the numbers underneath', () => {
    render(<GexDashboard data={makeData()} stale={true} />)
    expect(screen.getByText(/previous snapshot/i)).toBeInTheDocument()
    // The whole point of "stale" is that the old numbers stay visible.
    expect(screen.getByText('24,800')).toBeInTheDocument()
    expect(screen.getByText('24,400')).toBeInTheDocument()
    expect(screen.getByText('Suppressive')).toBeInTheDocument()
  })

  it('does not show the stale caveat when the refresh succeeded', () => {
    render(<GexDashboard data={makeData()} stale={false} />)
    expect(screen.queryByText(/previous snapshot/i)).not.toBeInTheDocument()
  })

  it('renders nothing when data is null', () => {
    const { container } = render(<GexDashboard data={null} stale={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when status is not success', () => {
    const { container } = render(
      <GexDashboard data={makeData({ status: 'error', message: 'boom' })} stale={false} />
    )
    expect(container).toBeEmptyDOMElement()
  })
})
