/**
 * The destructive confirm for deleting a saved indicator.
 *
 * What is worth pinning here is the wording, not the plumbing. The dialog has
 * to distinguish three states that a naive implementation collapses into one:
 * "no alerts affected", "N alerts affected", and "could not find out". The
 * third must never render as the first - that is the reassuring answer, and it
 * would appear exactly when the server is unreachable.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DeleteScriptDialog } from './DeleteScriptDialog'

function setup(overrides: Partial<Parameters<typeof DeleteScriptDialog>[0]> = {}) {
  const onConfirm = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <DeleteScriptDialog
      open
      onOpenChange={onOpenChange}
      scriptName="My RSI"
      affectedAlerts={0}
      busy={false}
      onConfirm={onConfirm}
      {...overrides}
    />
  )
  return { onConfirm, onOpenChange }
}

describe('DeleteScriptDialog', () => {
  it('names the script being deleted', () => {
    // Confirming against a name rather than "this script" is the whole point of
    // a confirm on a menu that can be opened from any script.
    setup()
    expect(screen.getByText('My RSI')).toBeInTheDocument()
  })

  it('falls back to a placeholder for an unnamed script', () => {
    setup({ scriptName: '   ' })
    expect(screen.getByText('Untitled indicator')).toBeInTheDocument()
  })

  it('says every version goes too', () => {
    setup()
    expect(screen.getByText(/every saved version/i)).toBeInTheDocument()
    expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument()
  })

  it('warns with a count when alerts are built on the script', () => {
    setup({ affectedAlerts: 3 })
    expect(
      screen.getByText(/3 alerts built on this indicator will stop working/i)
    ).toBeInTheDocument()
  })

  it('uses the singular for exactly one alert', () => {
    setup({ affectedAlerts: 1 })
    expect(
      screen.getByText(/^1 alert built on this indicator will stop working/i)
    ).toBeInTheDocument()
  })

  it('stays quiet about alerts when there are none', () => {
    setup({ affectedAlerts: 0 })
    expect(screen.queryByText(/will stop working/i)).not.toBeInTheDocument()
  })

  it('still warns when the alert count could not be determined', () => {
    // THE test in this file. null is "unknown", not "zero" - rendering the
    // no-alerts case here would reassure the user precisely when the lookup
    // failed.
    setup({ affectedAlerts: null })
    expect(
      screen.getByText(/any alerts built on this indicator will stop working/i)
    ).toBeInTheDocument()
  })

  it('confirms through the parent rather than closing itself', () => {
    // Deletion is async and can fail; the dialog must not dismiss on click or a
    // failed delete would read as a success.
    const { onConfirm, onOpenChange } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('locks both buttons while the delete is in flight', () => {
    setup({ busy: true })
    expect(screen.getByRole('button', { name: 'Deleting…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Keep' })).toBeDisabled()
  })
})
