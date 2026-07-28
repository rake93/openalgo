/**
 * Rail behaviour.
 *
 * The rail this replaced armed a family's first tool and advanced on repeat
 * clicks, so reaching a group's sixth tool cost six clicks. These tests pin the
 * replacement's contract: one click arms, the caret reveals, and the workspace's
 * own action buttons still gate on what is actually available.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DrawingRail, type DrawingRailProps } from './DrawingRail'

function setup(over: Partial<DrawingRailProps> = {}) {
  const props: DrawingRailProps = {
    activeTool: null,
    canUndo: false,
    canRedo: false,
    hasSelection: false,
    onPick: vi.fn(),
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onDeleteSelected: vi.fn(),
    onClearAll: vi.fn(),
    ...over,
  }
  render(<DrawingRail {...props} />)
  return props
}

describe('DrawingRail', () => {
  it('arms a group’s first tool on a plain click, with no menu to chase', async () => {
    const p = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Shapes' }))
    expect(p.onPick).toHaveBeenCalledWith('rectangle')
  })

  it('re-arms the tool that group last had armed, not its first', async () => {
    // Triangle is armed, so the Shapes button should return to triangle rather
    // than resetting to rectangle — that is the whole point of the bare click.
    const p = setup({ activeTool: 'triangle' })
    await userEvent.click(screen.getByRole('button', { name: 'Shapes' }))
    expect(p.onPick).toHaveBeenCalledWith('triangle')
  })

  it('reaches any tool in two clicks via the caret', async () => {
    const p = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Shapes menu' }))
    await userEvent.click(await screen.findByText('Rotated rectangle'))
    expect(p.onPick).toHaveBeenCalledWith('rotated-rectangle')
  })

  it('offers the tools that had no button before the port', async () => {
    setup()
    await userEvent.click(screen.getByRole('button', { name: 'Arrows & notes menu' }))
    for (const label of ['Callout', 'Flag mark', 'Brush', 'Highlighter']) {
      expect(await screen.findByText(label)).toBeTruthy()
    }
  })

  it('disarms from the cursor button', async () => {
    const p = setup({ activeTool: 'trend-line' })
    await userEvent.click(screen.getByRole('button', { name: 'Cursor' }))
    expect(p.onPick).toHaveBeenCalledWith(null)
  })

  it('marks the owning group active, so you can see what is armed', () => {
    setup({ activeTool: 'gann-box' })
    // Gann lives under Fibonacci & Gann, not under Shapes.
    expect(screen.getByRole('button', { name: 'Fibonacci & Gann' }).className).toContain('primary')
    expect(screen.getByRole('button', { name: 'Shapes' }).className).not.toContain('primary')
  })

  it('gates undo, redo and delete on what is actually available', async () => {
    const p = setup({ canUndo: true, canRedo: false, hasSelection: false })
    const undo = screen.getByRole('button', { name: 'Undo (Ctrl+Z)' })
    const redo = screen.getByRole('button', { name: 'Redo (Ctrl+Shift+Z)' })
    const del = screen.getByRole('button', { name: 'Delete selected (Del)' })

    expect(undo.hasAttribute('disabled')).toBe(false)
    expect(redo.hasAttribute('disabled')).toBe(true)
    expect(del.hasAttribute('disabled')).toBe(true)

    await userEvent.click(undo)
    expect(p.onUndo).toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Clear all drawings' }))
    expect(p.onClearAll).toHaveBeenCalled()
  })
})
