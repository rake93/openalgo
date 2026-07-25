/**
 * Milestone 1 acceptance: an addIr indicator resolves a renderable settings
 * descriptor from its own IR, while a registry builtin still resolves through
 * registryManifest.
 *
 * This asserts the RESOLUTION rule the dialog uses rather than mounting the
 * component, because the failure being fixed is a metadata lookup returning
 * undefined — not a rendering bug.
 */

import { describe, expect, it } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import { descriptorFromIR } from '@openalgo/openscript'
import { registryManifest } from '@openalgo/openscript/registry'
import type { IRProgram } from '@openalgo/openscript'

function irOf(src: string): IRProgram {
  const r = compile(`indicator("My Script")\n${src}`)
  if (!r.ir) throw new Error(`compile failed: ${JSON.stringify(r.diagnostics.map((d) => d.code))}`)
  return r.ir
}

/** The resolution rule the dialog applies: IR ownership first, manifest second. */
function resolveEntry(instance: { definitionId: string; ir?: IRProgram }) {
  if (instance.ir) return descriptorFromIR(instance.ir)
  return registryManifest.find((m) => m.id === instance.definitionId)
}

describe('settings metadata resolution', () => {
  it('an IR instance resolves a descriptor with renderable inputs and outputs', () => {
    const ir = irOf('len = input.int(14, "Length", maxval=200)\nplot(ta.rsi(close, len), "RSI")')
    const entry = resolveEntry({ definitionId: 'ir', ir })

    expect(entry).toBeDefined()
    expect(entry?.name).toBe('My Script')
    expect(entry?.inputs).toHaveLength(1)
    expect(entry?.inputs[0]?.label).toBe('Length')
    expect(entry?.outputs).toHaveLength(1)
    expect(entry?.outputs[0]?.title).toBe('RSI')
  })

  it('the pre-fix lookup would have returned undefined for the same instance', () => {
    // Pins the bug being fixed: definitionId 'ir' has no registryManifest entry,
    // which is why the dialog rendered nothing.
    expect(registryManifest.find((m) => m.id === 'ir')).toBeUndefined()
  })

  it('a registry builtin still resolves through registryManifest', () => {
    const entry = resolveEntry({ definitionId: 'builtin.sma' })
    expect(entry).toBeDefined()
    expect(entry?.inputs.length).toBeGreaterThan(0)
  })
})
