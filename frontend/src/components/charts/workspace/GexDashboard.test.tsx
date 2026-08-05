/**
 * GexDashboard behaviour.
 *
 * jest-dom matchers are registered globally in `src/test/setup.ts`, so no
 * per-file import is needed here.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { GEXLevelsResponse, GEXQuality, GEXSentiment } from '@/api/gex'
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

function makeSentiment(overrides: Partial<GEXSentiment> = {}): GEXSentiment {
  return {
    bias: 'bullish',
    score: 0.67,
    agreeing: 2,
    participating: 3,
    signals: [
      {
        key: 'walls',
        label: 'Wall position',
        detail: 'Spot 24750 above the call wall 24700',
        bias: 'bullish',
        why: 'Spot 24750 is above the call wall 24700',
        weight: 2,
      },
      {
        key: 'pcr',
        label: 'Put-call ratio',
        detail: 'PCR 1.34 by open interest',
        bias: 'bullish',
        why: '1.34 is at or above the 1.20 bullish threshold',
        weight: 1,
      },
      {
        key: 'skew',
        label: 'IV skew',
        detail: 'puts 14.2% vs calls 12.4%',
        bias: 'neutral',
        why: '+0.8 vol points, inside the +/-1.5 band',
        weight: 1,
      },
    ],
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
    render(<GexDashboard data={makeData()} stale={false} metric="gamma" />)
    expect(screen.getByText('24,800')).toBeInTheDocument()
    expect(screen.getByText('24,400')).toBeInTheDocument()
    expect(screen.getByText('24,550')).toBeInTheDocument()
  })

  it('formats Call/Put/Net GEX in crore, the shared formatter this card and the canvas hover readout both now use', () => {
    // total_call_gex: 125000000 -> 12.50 Cr, total_put_gex: -98000000 ->
    // -9.80 Cr, net_gex: 27000000 -> 2.70 Cr. This is the one place that
    // fails if a change motivated by the canvas readout (formatGexMoney is
    // now shared - see gex-levels-primitive.ts) alters what the sidebar
    // card itself shows.
    render(<GexDashboard data={makeData()} stale={false} metric="gamma" />)
    expect(screen.getByText('12.50 Cr')).toBeInTheDocument()
    expect(screen.getByText('-9.80 Cr')).toBeInTheDocument()
    expect(screen.getByText('2.70 Cr')).toBeInTheDocument()
  })

  it('reads Suppressive for positive net gamma and Amplifying for negative — never bullish/bearish', () => {
    const { rerender } = render(
      <GexDashboard data={makeData({ regime: 'suppressive' })} stale={false} metric="gamma" />
    )
    expect(screen.getByText('Suppressive')).toBeInTheDocument()

    rerender(
      <GexDashboard data={makeData({ regime: 'amplifying' })} stale={false} metric="gamma" />
    )
    expect(screen.getByText('Amplifying')).toBeInTheDocument()

    const seen = document.body.textContent?.toLowerCase() ?? ''
    expect(seen).not.toContain('bullish')
    expect(seen).not.toContain('bearish')
  })

  it('renders "No local cross" when zero_gamma is null, not a dash or an error', () => {
    render(<GexDashboard data={makeData({ zero_gamma: null })} stale={false} metric="gamma" />)
    expect(screen.getByText('No local cross')).toBeInTheDocument()
  })

  it('reports strikes_priced of strikes_used in the data-status row', () => {
    render(
      <GexDashboard
        data={makeData({ quality: makeQuality({ strikes_used: 24, strikes_priced: 19 }) })}
        stale={false}
        metric="gamma"
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
        metric="gamma"
      />
    )
    expect(screen.getByText(note)).toBeInTheDocument()
  })

  it('shows the stale caveat while still showing the numbers underneath', () => {
    render(<GexDashboard data={makeData()} stale={true} metric="gamma" />)
    expect(screen.getByText(/previous snapshot/i)).toBeInTheDocument()
    // The whole point of "stale" is that the old numbers stay visible.
    expect(screen.getByText('24,800')).toBeInTheDocument()
    expect(screen.getByText('24,400')).toBeInTheDocument()
    expect(screen.getByText('Suppressive')).toBeInTheDocument()
  })

  it('does not show the stale caveat when the refresh succeeded', () => {
    render(<GexDashboard data={makeData()} stale={false} metric="gamma" />)
    expect(screen.queryByText(/previous snapshot/i)).not.toBeInTheDocument()
  })

  it('renders Sentiment alongside Regime, as two distinct rows', () => {
    render(
      <GexDashboard
        data={makeData({ regime: 'suppressive', sentiment: makeSentiment({ bias: 'bullish' }) })}
        stale={false}
        metric="gamma"
      />
    )
    expect(screen.getByText('Suppressive')).toBeInTheDocument()
    expect(screen.getByText('Bullish 2/3')).toBeInTheDocument()
  })

  it('renders a bullish Sentiment in green and shows the agreeing/participating count', () => {
    render(
      <GexDashboard
        data={makeData({
          sentiment: makeSentiment({ bias: 'bullish', agreeing: 2, participating: 3 }),
        })}
        stale={false}
        metric="gamma"
      />
    )
    const value = screen.getByText('Bullish 2/3')
    expect(value.className).toContain('emerald')
  })

  it('renders a bearish Sentiment in red', () => {
    render(
      <GexDashboard
        data={makeData({
          sentiment: makeSentiment({ bias: 'bearish', agreeing: 3, participating: 3 }),
        })}
        stale={false}
        metric="gamma"
      />
    )
    const value = screen.getByText('Bearish 3/3')
    expect(value.className).toContain('red')
  })

  it('renders a neutral Sentiment muted, not green or red', () => {
    render(
      <GexDashboard
        data={makeData({
          sentiment: makeSentiment({ bias: 'neutral', agreeing: 1, participating: 3 }),
        })}
        stale={false}
        metric="gamma"
      />
    )
    const value = screen.getByText('Neutral 1/3')
    expect(value.className).not.toContain('emerald')
    expect(value.className).not.toContain('red')
  })

  it('makes the Sentiment value a tooltip trigger with a help cursor', () => {
    render(
      <GexDashboard
        data={makeData({
          sentiment: makeSentiment({ bias: 'neutral', agreeing: 3, participating: 3 }),
        })}
        stale={false}
        metric="gamma"
      />
    )
    const value = screen.getByText('Neutral 3/3')
    // pointer-events-auto is load-bearing: the card's <aside> is
    // pointer-events-none so it never steals a click meant for the chart, so
    // hover would silently never fire on this row without the override.
    expect(value.className).toContain('cursor-help')
    expect(value.className).toContain('pointer-events-auto')
    expect(value.getAttribute('data-slot')).toBe('tooltip-trigger')
  })

  it('reveals every signal detail and why on hover, plus the trailing count explanation', async () => {
    // Radix's Tooltip.Content mounts twice once open: a visible popper-positioned
    // copy, plus a visually-hidden `role="tooltip"` clone it wires to the trigger
    // via aria-describedby for screen readers. Both carry identical children, so
    // every string below legitimately has two matches - assert with findAllByText
    // rather than the singular query, and require at least one hit.
    const user = userEvent.setup()
    render(
      <GexDashboard
        data={makeData({
          sentiment: makeSentiment({ bias: 'neutral', agreeing: 3, participating: 3 }),
        })}
        stale={false}
        metric="gamma"
      />
    )
    await user.hover(screen.getByText('Neutral 3/3'))

    expect(
      (await screen.findAllByText('Spot 24750 is above the call wall 24700')).length
    ).toBeGreaterThan(0)
    expect(
      screen.getAllByText('1.34 is at or above the 1.20 bullish threshold').length
    ).toBeGreaterThan(0)
    expect(screen.getAllByText('+0.8 vol points, inside the +/-1.5 band').length).toBeGreaterThan(0)
    expect(
      screen.getAllByText(/The count is how many signals agree with the verdict/).length
    ).toBeGreaterThan(0)
  })

  it('does not crash and shows no Sentiment row when sentiment is absent (an older cached response)', () => {
    render(<GexDashboard data={makeData({ sentiment: undefined })} stale={false} metric="gamma" />)
    expect(screen.getByText('Suppressive')).toBeInTheDocument()
    expect(screen.queryByText(/Bullish|Bearish|Neutral \d/)).not.toBeInTheDocument()
  })

  it('renders nothing when data is null', () => {
    const { container } = render(<GexDashboard data={null} stale={false} metric="gamma" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when status is not success', () => {
    const { container } = render(
      <GexDashboard
        data={makeData({ status: 'error', message: 'boom' })}
        stale={false}
        metric="gamma"
      />
    )
    expect(container).toBeEmptyDOMElement()
  })
})

describe('GexDashboard metric', () => {
  it('reads the Bars row from the metric prop, not from the snapshot', () => {
    // The dashboard has no metric field of its own in the response - it is
    // purely a chart-workspace setting - so this has to come from the prop,
    // and rerendering with a different prop against the exact same data must
    // flip the row without a new fetch.
    const { rerender } = render(<GexDashboard data={makeData()} stale={false} metric="gamma" />)
    expect(screen.getByText('Gamma (GEX)')).toBeInTheDocument()

    rerender(<GexDashboard data={makeData()} stale={false} metric="delta" />)
    expect(screen.getByText('Delta (DEX)')).toBeInTheDocument()
    expect(screen.queryByText('Gamma (GEX)')).not.toBeInTheDocument()
  })

  it('shows no delta caveat under gamma', () => {
    render(<GexDashboard data={makeData()} stale={false} metric="gamma" />)
    expect(screen.queryByText(/stay gamma/i)).not.toBeInTheDocument()
  })

  it('shows the delta caveat under delta, naming what stays gamma', () => {
    render(<GexDashboard data={makeData()} stale={false} metric="delta" />)
    const note = screen.getByText(/stay gamma/i)
    expect(note).toBeInTheDocument()
    expect(note.textContent).toMatch(/Walls, Zero-Gamma and Regime/)
  })

  it('shows both the stale caveat and the delta caveat together, as two separate lines', () => {
    render(<GexDashboard data={makeData()} stale={true} metric="delta" />)
    expect(screen.getByText(/previous snapshot/i)).toBeInTheDocument()
    expect(screen.getByText(/stay gamma/i)).toBeInTheDocument()
  })
})

describe('GexDashboard hide control', () => {
  it('renders no close control when onHide is omitted', () => {
    render(<GexDashboard data={makeData()} stale={false} metric="gamma" />)
    expect(screen.queryByLabelText(/hide the gex levels card/i)).not.toBeInTheDocument()
  })

  it('calls onHide when the close control is pressed', async () => {
    const onHide = vi.fn()
    render(<GexDashboard data={makeData()} stale={false} metric="gamma" onHide={onHide} />)
    await userEvent.click(screen.getByLabelText(/hide the gex levels card/i))
    expect(onHide).toHaveBeenCalledTimes(1)
  })
})
