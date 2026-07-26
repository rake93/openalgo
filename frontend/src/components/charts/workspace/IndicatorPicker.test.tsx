/**
 * The picker's saved-script source.
 *
 * The picker offered engine builtins and chart-library builtins only. There was
 * no row type for a script the user had written and saved, which is why a saved
 * OpenScript indicator could not be put on a chart at all — the journey ended in
 * the editor.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { IndicatorManifestEntry } from '@openalgo/openscript'
import type { IndicatorDescriptor } from 'openalgo-charts'
import { describe, expect, it, vi } from 'vitest'
import { IndicatorPicker, type PickerScript } from './IndicatorPicker'

const ENGINE = [
  {
    id: 'builtin.sma',
    name: 'Simple Moving Average',
    shortName: 'SMA',
    category: 'Moving Averages',
    overlay: true,
    inputs: [],
  },
] as unknown as IndicatorManifestEntry[]

const LIBRARY = [
  { id: 'lib.vwap', name: 'VWAP', category: 'Volume', placement: 'onchart' },
] as unknown as IndicatorDescriptor[]

const SCRIPTS: PickerScript[] = [
  { id: 7, name: 'Momentum Burst', description: 'my own thing' },
  { id: 8, name: 'Range Compression', description: null },
]

function renderPicker(onAdd = vi.fn()) {
  render(
    <IndicatorPicker
      open
      onOpenChange={() => {}}
      engine={ENGINE}
      library={LIBRARY}
      scripts={SCRIPTS}
      onAdd={onAdd}
    />
  )
  return onAdd
}

describe('saved scripts appear in the picker', () => {
  it('lists them alongside the builtins', () => {
    renderPicker()

    expect(screen.getByText('Momentum Burst')).toBeInTheDocument()
    expect(screen.getByText('Range Compression')).toBeInTheDocument()
    expect(screen.getByText('Simple Moving Average')).toBeInTheDocument()
  })

  it('adds one by script id, tagged as a script', async () => {
    // The id must be the SCRIPT id, not a manifest key — it is what the
    // controller fetches the authoritative IR with.
    const onAdd = renderPicker()

    await userEvent.click(screen.getByText('Momentum Burst'))

    expect(onAdd).toHaveBeenCalledWith('7', 'script')
  })

  it('marks the row so a trader can tell it from a builtin', () => {
    renderPicker()

    const row = screen.getByText('Momentum Burst').closest('button')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText(/script/i)).toBeInTheDocument()
  })
})

describe('finding a saved script', () => {
  it('matches on its name', async () => {
    renderPicker()

    await userEvent.type(screen.getByPlaceholderText('Search indicators'), 'momentum')

    expect(screen.getByText('Momentum Burst')).toBeInTheDocument()
    expect(screen.queryByText('Simple Moving Average')).not.toBeInTheDocument()
  })

  it('matches on its description, so a script named opaquely is still findable', async () => {
    renderPicker()

    await userEvent.type(screen.getByPlaceholderText('Search indicators'), 'my own thing')

    expect(screen.getByText('Momentum Burst')).toBeInTheDocument()
    expect(screen.queryByText('Range Compression')).not.toBeInTheDocument()
  })

  it('can be filtered down to scripts alone', async () => {
    renderPicker()

    await userEvent.click(screen.getByRole('button', { name: 'Scripts' }))

    expect(screen.getByText('Momentum Burst')).toBeInTheDocument()
    expect(screen.queryByText('Simple Moving Average')).not.toBeInTheDocument()
    expect(screen.queryByText('VWAP')).not.toBeInTheDocument()
  })

  it('the existing group filters still exclude scripts', async () => {
    renderPicker()

    await userEvent.click(screen.getByRole('button', { name: 'Engine' }))

    expect(screen.getByText('Simple Moving Average')).toBeInTheDocument()
    expect(screen.queryByText('Momentum Burst')).not.toBeInTheDocument()
  })
})

describe('with no saved scripts', () => {
  it('renders the builtins unchanged', () => {
    render(
      <IndicatorPicker
        open
        onOpenChange={() => {}}
        engine={ENGINE}
        library={LIBRARY}
        scripts={[]}
        onAdd={vi.fn()}
      />
    )

    expect(screen.getByText('Simple Moving Average')).toBeInTheDocument()
    expect(screen.getByText('VWAP')).toBeInTheDocument()
  })
})
