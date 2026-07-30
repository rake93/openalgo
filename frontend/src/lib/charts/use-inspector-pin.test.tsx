/**
 * `useInspectorPin` (M8) — the `i`-to-pin gesture shared by /charts and the
 * script editor.
 *
 * Pinning rather than following the crosshair is the whole point: a panel that
 * tracked the pointer could not host a control, because reaching for one drags
 * the crosshair onto a different bar.
 *
 * The typing guard is the part most worth pinning down. Both surfaces render a
 * text editor or a symbol search beside the chart, and a bare window-level `i`
 * listener would silently type into them.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { CrosshairData } from './workspace'
import { useInspectorPin } from './use-inspector-pin'

function crosshairAt(index: number, outputId = 'out_0'): CrosshairData {
  return {
    time: 1_700_000_000 + index * 60,
    index,
    bar: { time: 1_700_000_000 + index * 60, open: 1, high: 2, low: 0, close: 1.5 },
    rows: [
      {
        instanceId: 'inst-1',
        name: 'My Script',
        values: [{ id: outputId, title: 'Plot', value: 1.5, color: '#0f0' }],
      },
    ],
  } as CrosshairData
}

function Harness({ crosshair }: { crosshair: CrosshairData | null }) {
  const { pinned } = useInspectorPin(crosshair)
  return (
    <div>
      <input aria-label="symbol" />
      <div data-testid="pinned">{pinned ? `bar ${pinned.index}` : 'none'}</div>
    </div>
  )
}

describe('useInspectorPin', () => {
  it('pins the bar under the crosshair when `i` is pressed', () => {
    render(<Harness crosshair={crosshairAt(42)} />)
    expect(screen.getByTestId('pinned')).toHaveTextContent('none')

    fireEvent.keyDown(document.body, { key: 'i' })

    expect(screen.getByTestId('pinned')).toHaveTextContent('bar 42')
  })

  it('pins the CURRENT bar, not the one present when the listener was bound', () => {
    const { rerender } = render(<Harness crosshair={crosshairAt(42)} />)
    rerender(<Harness crosshair={crosshairAt(99)} />)

    fireEvent.keyDown(document.body, { key: 'i' })

    // A stale closure over the first crosshair would report bar 42 here.
    expect(screen.getByTestId('pinned')).toHaveTextContent('bar 99')
  })

  it('ignores `i` typed into an input, so it never steals a character', () => {
    render(<Harness crosshair={crosshairAt(42)} />)

    fireEvent.keyDown(screen.getByLabelText('symbol'), { key: 'i' })

    expect(screen.getByTestId('pinned')).toHaveTextContent('none')
  })

  it('ignores `i` typed into the script editor', () => {
    render(
      <div className="cm-editor">
        <Harness crosshair={crosshairAt(42)} />
      </div>
    )

    fireEvent.keyDown(screen.getByTestId('pinned'), { key: 'i' })

    expect(screen.getByTestId('pinned')).toHaveTextContent('none')
  })

  it('ignores `i` when a modifier is held, leaving browser shortcuts alone', () => {
    render(<Harness crosshair={crosshairAt(42)} />)

    fireEvent.keyDown(document.body, { key: 'i', ctrlKey: true })

    expect(screen.getByTestId('pinned')).toHaveTextContent('none')
  })

  it('does nothing when there is no crosshair to pin', () => {
    render(<Harness crosshair={null} />)

    fireEvent.keyDown(document.body, { key: 'i' })

    expect(screen.getByTestId('pinned')).toHaveTextContent('none')
  })

  it('Escape clears a pin', () => {
    render(<Harness crosshair={crosshairAt(42)} />)
    fireEvent.keyDown(document.body, { key: 'i' })
    expect(screen.getByTestId('pinned')).toHaveTextContent('bar 42')

    fireEvent.keyDown(document.body, { key: 'Escape' })

    expect(screen.getByTestId('pinned')).toHaveTextContent('none')
  })
})
