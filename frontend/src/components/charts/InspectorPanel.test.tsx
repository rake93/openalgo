/**
 * InspectorPanel selection lifecycle (M8).
 *
 * The regression this exists for: an editor preview recompiles into a NEW
 * engine session, so the held selection points at an instance that no longer
 * exists. The host correctly refuses to answer for it — but the panel then sat
 * on "Select an output above" until the user clicked a chip, which reads as the
 * inspector being broken right after the edit that prompted the inspection.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { InspectResult } from '@openalgo/openscript'
import { InspectorPanel, type PinnedBar } from './InspectorPanel'

function okResult(value: number): InspectResult {
  return {
    ok: true,
    repaintSources: [],
    roots: [
      {
        name: 'value',
        nodeId: 0,
        value,
        origins: [],
        tree: {
          nodeId: 0,
          label: 'close',
          value,
          span: { start: 0, end: 5, line: 1, column: 1 },
          sharedCount: 1,
          children: [],
        },
      },
    ],
  }
}

function barWith(instanceId: string, outputIds: string[], index = 10): PinnedBar {
  return {
    index,
    time: 1_700_000_000,
    rows: [
      {
        instanceId,
        name: 'My Script',
        values: outputIds.map((id, i) => ({
          id,
          title: `Plot ${i}`,
          value: 1 + i,
          color: '#0f0',
        })),
      },
    ],
  }
}

describe('InspectorPanel selection', () => {
  it('inspects the first output as soon as it opens', async () => {
    const inspect = vi.fn().mockResolvedValue({ epoch: 1, result: okResult(1) })

    render(<InspectorPanel bar={barWith('inst-1', ['out_0'])} inspect={inspect} onClose={() => {}} />)

    await waitFor(() => expect(inspect).toHaveBeenCalledWith('inst-1', 'out_0', 10))
  })

  it('re-selects when the session was replaced, instead of stalling on a dead instance', async () => {
    const inspect = vi.fn().mockResolvedValue({ epoch: 1, result: okResult(1) })
    const { rerender } = render(
      <InspectorPanel bar={barWith('inst-1', ['out_0'])} inspect={inspect} onClose={() => {}} />
    )
    await waitFor(() => expect(inspect).toHaveBeenCalledWith('inst-1', 'out_0', 10))
    inspect.mockClear()

    // A recompile: same script, brand-new session id.
    rerender(
      <InspectorPanel bar={barWith('inst-2', ['out_0'], 11)} inspect={inspect} onClose={() => {}} />
    )

    await waitFor(() => expect(inspect).toHaveBeenCalledWith('inst-2', 'out_0', 11))
    expect(inspect).not.toHaveBeenCalledWith('inst-1', 'out_0', 11)
  })

  it('keeps the chosen output across bars while it still exists', async () => {
    const inspect = vi.fn().mockResolvedValue({ epoch: 1, result: okResult(1) })
    const { rerender } = render(
      <InspectorPanel bar={barWith('inst-1', ['out_0', 'out_1'])} inspect={inspect} onClose={() => {}} />
    )
    await waitFor(() => expect(inspect).toHaveBeenCalled())

    // Pick the second output, then pin a different bar.
    screen.getByText('Plot 1').click()
    await waitFor(() => expect(inspect).toHaveBeenCalledWith('inst-1', 'out_1', 10))
    inspect.mockClear()

    rerender(
      <InspectorPanel
        bar={barWith('inst-1', ['out_0', 'out_1'], 20)}
        inspect={inspect}
        onClose={() => {}}
      />
    )

    // Re-deriving unconditionally would snap back to out_0 and lose the user's choice.
    await waitFor(() => expect(inspect).toHaveBeenCalledWith('inst-1', 'out_1', 20))
    expect(inspect).not.toHaveBeenCalledWith('inst-1', 'out_0', 20)
  })

  it('falls back to the first output when the chosen one disappears', async () => {
    const inspect = vi.fn().mockResolvedValue({ epoch: 1, result: okResult(1) })
    const { rerender } = render(
      <InspectorPanel bar={barWith('inst-1', ['out_0', 'out_1'])} inspect={inspect} onClose={() => {}} />
    )
    await waitFor(() => expect(inspect).toHaveBeenCalled())
    screen.getByText('Plot 1').click()
    await waitFor(() => expect(inspect).toHaveBeenCalledWith('inst-1', 'out_1', 10))
    inspect.mockClear()

    // The script lost a plot on recompile.
    rerender(
      <InspectorPanel bar={barWith('inst-1', ['out_0'], 11)} inspect={inspect} onClose={() => {}} />
    )

    await waitFor(() => expect(inspect).toHaveBeenCalledWith('inst-1', 'out_0', 11))
  })
})
