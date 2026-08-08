/**
 * Session input control in the settings dialog (session-surface plan Task 7,
 * B1/B2). `input.session` only exists on IR-backed (custom OpenScript)
 * indicators, so the fixture always carries a compiled `ir`, mirroring
 * `indicator-host.settings.test.ts`'s own `irOf` helper.
 *
 * `IndicatorSettingsDialog` pulls in `@/lib/charts/indicator-host`, which
 * pulls in `./engine`, which imports the wasm binary via a module-scope
 * `?url` suffix — real in the browser, but Vite's test transform denies it
 * here because the linked @openalgo/openscript package resolves outside this
 * project's root. Same issue `indicator-host.settings.test.ts` documents and
 * works around: `./engine` is mocked before the dialog is imported, and the
 * dialog is imported dynamically so the mock is in place first.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import { parseSessionString } from '@openalgo/openscript'
import type { IRProgram } from '@openalgo/openscript'
import type { IndicatorInstance } from '@/lib/charts/indicator-host'

vi.mock('@/lib/charts/engine', () => ({
  getEngine: () => Promise.reject(new Error('not needed for settings dialog rendering')),
}))

const { IndicatorSettingsDialog } = await import('./IndicatorSettingsDialog')

function irOf(src: string): IRProgram {
  const r = compile(`indicator("Session Script")\n${src}`)
  if (!r.ir) throw new Error(`compile failed: ${JSON.stringify(r.diagnostics.map((d) => d.code))}`)
  return r.ir
}

function instanceOf(ir: IRProgram, inputs: Record<string, unknown> = {}): IndicatorInstance {
  return {
    instanceId: 'i1',
    definitionId: 'ir',
    name: 'Session Script',
    overlay: true,
    inputs,
    ir,
  }
}

const SESSION_SRC = 'sess = input.session("0915-1530:23456", title="Session")\nplot(close)'

describe('IndicatorSettingsDialog — session input', () => {
  it('renders a session input as a text control', () => {
    const instance = instanceOf(irOf(SESSION_SRC))
    render(
      <IndicatorSettingsDialog
        instance={instance}
        manifest={[]}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />
    )

    const control = screen.getByLabelText('Session')
    expect(control).toBeInstanceOf(HTMLInputElement)
    expect((control as HTMLInputElement).type).toBe('text')
    expect((control as HTMLInputElement).value).toBe('0915-1530:23456')
  })

  it('applies a valid value on Ok', () => {
    const onSave = vi.fn()
    const instance = instanceOf(irOf(SESSION_SRC))
    render(
      <IndicatorSettingsDialog
        instance={instance}
        manifest={[]}
        onSave={onSave}
        onClose={vi.fn()}
      />
    )

    fireEvent.change(screen.getByLabelText('Session'), { target: { value: '0930-1200:23456' } })
    expect(screen.getByRole('button', { name: 'Ok' })).not.toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Ok' }))

    expect(onSave).toHaveBeenCalledTimes(1)
    const [, values] = onSave.mock.calls[0] as [string, Record<string, unknown>]
    expect(values.sess).toBe('0930-1200:23456')
  })

  it('shows the parser\'s own reason for an invalid value and blocks Ok — the engine bind-time OS4005 check remains the authority', () => {
    const onSave = vi.fn()
    const instance = instanceOf(irOf(SESSION_SRC))
    render(
      <IndicatorSettingsDialog
        instance={instance}
        manifest={[]}
        onSave={onSave}
        onClose={vi.fn()}
      />
    )

    fireEvent.change(screen.getByLabelText('Session'), { target: { value: 'abc' } })

    const parsed = parseSessionString('abc')
    const reason = 'error' in parsed ? parsed.error : undefined
    expect(reason).toBeTruthy()
    expect(screen.getByText(reason as string)).toBeInTheDocument()

    const ok = screen.getByRole('button', { name: 'Ok' })
    expect(ok).toBeDisabled()

    fireEvent.click(ok)
    expect(onSave).not.toHaveBeenCalled()
  })
})
