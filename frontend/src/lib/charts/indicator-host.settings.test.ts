/**
 * Milestone 1 acceptance: an addIr indicator resolves a renderable settings
 * descriptor from its own IR, while a registry builtin still resolves through
 * registryManifest.
 *
 * This exercises the PRODUCTION `resolveSettingsEntry` (shared by
 * `IndicatorSettingsDialog`) rather than mounting the component or keeping a
 * parallel copy of the rule here — a private copy would stay green even if the
 * dialog's own resolution regressed back to the broken `definitionId` lookup.
 */

import { describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import { registryManifest } from '@openalgo/openscript/registry'
import type { IRProgram } from '@openalgo/openscript'

// `indicator-host.ts` pulls in `./engine`, which imports the wasm binary via a
// module-scope `?url` suffix — real in the browser, but Vite's test transform
// denies it here because the linked @openalgo/openscript package resolves
// outside this project's root. This test only needs the pure
// `resolveSettingsEntry` function, so `./engine` is mocked (same pattern as
// indicator-host.activation.test.ts) to keep the real engine module unloaded,
// and `indicator-host` is imported dynamically so the mock is in place first.
vi.mock('./engine', () => ({
  getEngine: () => Promise.reject(new Error('not needed for settings-metadata resolution')),
}))

const { resolveSettingsEntry } = await import('./indicator-host')

function irOf(src: string): IRProgram {
  const r = compile(`indicator("My Script")\n${src}`)
  if (!r.ir) throw new Error(`compile failed: ${JSON.stringify(r.diagnostics.map((d) => d.code))}`)
  return r.ir
}

describe('settings metadata resolution', () => {
  it('an IR instance resolves a descriptor with renderable inputs and outputs', () => {
    const ir = irOf('len = input.int(14, "Length", maxval=200)\nplot(ta.rsi(close, len), "RSI")')
    const entry = resolveSettingsEntry({ definitionId: 'ir', ir }, registryManifest)

    expect(entry).toBeDefined()
    expect(entry?.name).toBe('My Script')
    expect(entry?.inputs).toHaveLength(1)
    expect(entry?.inputs[0]?.label).toBe('Length')
    expect(entry?.outputs).toHaveLength(1)
    expect(entry?.outputs[0]?.title).toBe('RSI')
  })

  it('an IR instance resolves from its OWN IR even when definitionId also matches a manifest entry', () => {
    // Regression guard for the exact bug that was fixed: if resolution ever
    // reverts to keying off `definitionId` instead of IR ownership, this
    // instance would silently resolve the wrong (registry) entry instead of
    // its own compiled script, even though `ir` is present.
    const ir = irOf('len = input.int(14, "Length", maxval=200)\nplot(ta.rsi(close, len), "RSI")')
    const entry = resolveSettingsEntry({ definitionId: 'builtin.sma', ir }, registryManifest)

    expect(entry).toBeDefined()
    expect(entry?.name).toBe('My Script')
    expect(entry?.inputs[0]?.label).toBe('Length')
  })

  it('a registry builtin still resolves through registryManifest', () => {
    const entry = resolveSettingsEntry({ definitionId: 'builtin.sma' }, registryManifest)
    expect(entry).toBeDefined()
    expect(entry?.inputs.length).toBeGreaterThan(0)
  })
})
