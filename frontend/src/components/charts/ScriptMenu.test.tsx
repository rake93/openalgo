/**
 * The script actions menu. Covers the Delete entry specifically: it is the one
 * destructive action in the dropdown, and the one whose gating matters — an
 * unsaved buffer has no script id, so there is nothing on the server to delete.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ScriptRecord } from '@/api/indicators'
import { ScriptMenu } from './ScriptMenu'

const SCRIPTS = [
  { id: 1, name: 'My RSI', updated_at: null },
  { id: 2, name: 'My MACD', updated_at: null },
] as unknown as ScriptRecord[]

function setup(overrides: Partial<Parameters<typeof ScriptMenu>[0]> = {}) {
  const handlers = {
    onToggle: vi.fn(),
    onClose: vi.fn(),
    onMakeCopy: vi.fn(),
    onRename: vi.fn(),
    onVersionHistory: vi.fn(),
    onDelete: vi.fn(),
    onCreateNew: vi.fn(),
    onOpen: vi.fn(),
  }
  render(
    <ScriptMenu open scripts={SCRIPTS} currentScriptId={1} canManage {...handlers} {...overrides} />
  )
  return handlers
}

describe('ScriptMenu delete action', () => {
  it('offers a delete entry', () => {
    setup()
    expect(screen.getByRole('button', { name: /delete script/i })).toBeInTheDocument()
  })

  it('asks the parent to delete, and closes the menu', () => {
    // The menu never deletes anything itself - it opens the parent's confirm.
    const handlers = setup()
    fireEvent.click(screen.getByRole('button', { name: /delete script/i }))

    expect(handlers.onDelete).toHaveBeenCalledTimes(1)
    expect(handlers.onClose).toHaveBeenCalledTimes(1)
  })

  it('is disabled until the script has been saved', () => {
    // An unsaved buffer has no server-side script, so there is nothing to
    // delete - the same gate the other manage actions use.
    setup({ canManage: false })
    expect(screen.getByRole('button', { name: /delete script/i })).toBeDisabled()
  })

  it('leaves the non-destructive actions enabled alongside it', () => {
    setup()
    expect(screen.getByRole('button', { name: /make a copy/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /create new/i })).toBeEnabled()
  })
})
