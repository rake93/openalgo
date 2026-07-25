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

const { IndicatorHost, resolveSettingsEntry } = await import('./indicator-host')

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

/** A minimal stand-in for the engine client's setInputs contract. */
interface FakeEngine {
  setInputs: (id: string, inputs: Record<string, unknown>) => Promise<{ outputs: unknown[] }>
}

/** Install a fake engine on a host without going through its real bootstrap. */
function withEngine(host: unknown, engine: FakeEngine): void {
  ;(host as { engine: FakeEngine }).engine = engine
}

/** Seed one instance straight into the host's private map (no worker session). */
function withInstance(host: unknown, instance: object): void {
  ;(host as { instances: Map<string, unknown> }).instances.set(
    (instance as { instanceId: string }).instanceId,
    instance
  )
}

// These instances carry `definitionId: 'ir'` but NO `ir` field, so
// `reconcileInputs` is deliberately not exercised — the subject here is the
// commit transaction and its serialization, not input reconciliation.
describe('IndicatorHost.setInputs — transaction', () => {
  it('leaves the committed inputs untouched when the worker rejects', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    withEngine(host, { setInputs: () => Promise.reject(new Error('OS4001: too big')) })
    const inst = { instanceId: 'i1', definitionId: 'ir', inputs: { len: 10 } }
    withInstance(host, inst)

    await expect(host.setInputs('i1', { len: 999 })).rejects.toThrow('OS4001')

    // The worker never accepted len=999, so the instance must still read 10.
    expect((inst as { inputs: Record<string, unknown> }).inputs.len).toBe(10)
    expect((inst as { error?: string }).error).toContain('OS4001')
  })

  it('commits overlapping saves in issue order', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    const seen: number[] = []
    withEngine(host, {
      setInputs: async (_id, inputs) => {
        // The FIRST call resolves SLOWER than the second, so an unserialized
        // implementation would commit them out of order.
        const len = inputs.len as number
        await new Promise((r) => setTimeout(r, len === 20 ? 30 : 1))
        seen.push(len)
        return { outputs: [] }
      },
    })
    const inst = { instanceId: 'i1', definitionId: 'ir', inputs: { len: 10 } }
    withInstance(host, inst)

    await Promise.all([host.setInputs('i1', { len: 20 }), host.setInputs('i1', { len: 30 })])

    expect(seen).toEqual([20, 30])
    expect((inst as { inputs: Record<string, unknown> }).inputs.len).toBe(30)
  })

  it('reconciles against the IR before the worker sees the patch', async () => {
    // The commit path runs `reconcileInputs` for IR-backed instances. The two
    // tests above deliberately carry no `ir`, so that branch was uncovered —
    // this is the one that exercises it, and it is the whole point of milestone
    // 1: the engine's reconciliation is what a stale saved layout passes through
    // before it can reach the worker.
    const ir = irOf('len = input.int(10, "Len", minval=2, maxval=200)\nplot(ta.sma(close, len))')
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    let sent: Record<string, unknown> | undefined
    withEngine(host, {
      setInputs: async (_id, inputs) => {
        sent = inputs
        return { outputs: [] }
      },
    })
    const inst = { instanceId: 'i1', definitionId: 'ir', ir, inputs: { len: 10 } }
    withInstance(host, inst)

    await host.setInputs('i1', { len: 5000, staleKey: 'gone' })

    // Clamped to the declared max, and the key the IR no longer declares never
    // reaches the worker at all.
    expect(sent?.len).toBe(200)
    expect('staleKey' in (sent ?? {})).toBe(false)
    expect((inst as { inputs: Record<string, unknown> }).inputs.len).toBe(200)
  })
})
