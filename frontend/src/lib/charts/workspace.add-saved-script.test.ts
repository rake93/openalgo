/**
 * Adding a saved OpenScript indicator from the picker.
 *
 * This is the step that had no implementation at all: `addIr`'s only production
 * caller was the editor preview, so a script you had written and saved could not
 * be put on `/charts`.
 *
 * The fetch lives on the controller rather than in the picker component for two
 * reasons. It is the same rule restore obeys — take the SERVER's compiled IR,
 * never recompile in the browser — and keeping both in one file is what stops
 * the two paths drifting into different notions of what a saved indicator is.
 * It also means the rule is testable without mounting React.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { IRProgram } from '@openalgo/openscript'
import type { IndicatorInstance } from './indicator-host'

const getScript = vi.fn()

vi.mock('@/api/indicators', () => ({
  getScript: (id: number) => getScript(id),
  getVersion: vi.fn(),
}))

vi.mock('./engine', () => ({
  getEngine: () => Promise.reject(new Error('no engine in this test')),
}))

const { ChartWorkspaceController } = await import('./workspace')

function irOf(name: string, src: string): IRProgram {
  const result = compile(`indicator("${name}", overlay=true)\n${src}`)
  if (!result.ir) {
    throw new Error(`compile failed: ${JSON.stringify(result.diagnostics.map((d) => d.code))}`)
  }
  return result.ir
}

const SAVED_IR = irOf('My Script', 'len = input.int(14, "Length")\nplot(ta.sma(close, len), "S")')

/** A script fetch, shaped like the scripts API returns it. */
function scriptWith(ir: IRProgram | null) {
  return {
    id: 7,
    name: 'My Script',
    version_id: 42,
    version_number: 3,
    source_hash: 'c'.repeat(64),
    compiler_version: 'openscript-1.0',
    compiled_ir: ir,
    source: '',
    diagnostics: [],
  }
}

let controller: InstanceType<typeof ChartWorkspaceController>
let latest: IndicatorInstance[] = []
let toasts: { message: string; kind: string }[] = []

beforeEach(() => {
  latest = []
  toasts = []
  getScript.mockReset()
  controller = new ChartWorkspaceController({
    apiKey: 'test',
    container: document.createElement('div'),
    callbacks: {
      onIndicators: (list: IndicatorInstance[]) => {
        latest = list
      },
      onToast: (message: string, kind: string) => {
        toasts.push({ message, kind })
      },
    },
  } as unknown as ConstructorParameters<typeof ChartWorkspaceController>[0])
})

const errors = () => toasts.filter((t) => t.kind === 'err').map((t) => t.message)

describe('adding a saved script', () => {
  it('puts it on the chart as a durable indicator', async () => {
    getScript.mockResolvedValue(scriptWith(SAVED_IR))

    const id = await controller.addSavedScript(7)

    expect(id).toBeDefined()
    expect(latest).toHaveLength(1)
    expect(latest[0]?.name).toBe('My Script')
    expect(latest[0]?.ir).toBeDefined()
    expect(errors()).toEqual([])
  })

  it('records the identity needed to reopen it', async () => {
    getScript.mockResolvedValue(scriptWith(SAVED_IR))

    await controller.addSavedScript(7)

    expect(latest[0]?.script).toEqual({
      scriptId: 7,
      versionId: 42,
      sourceHash: 'c'.repeat(64),
    })
  })

  it('survives a save and reopen round trip', async () => {
    // The point of the whole slice: what the picker adds must be what a layout
    // can store.
    getScript.mockResolvedValue(scriptWith(SAVED_IR))

    await controller.addSavedScript(7)

    const entry = controller.snapshot().indicators[0]
    expect(entry?.script).toEqual({ scriptId: 7, versionId: 42, sourceHash: 'c'.repeat(64) })
    expect(entry?.inputs).toEqual({ len: 14 })
  })

  it('uses the server IR rather than compiling the source in the browser', async () => {
    // The stored IR wins even when it disagrees with the source: reopen must be
    // reproducible from what the server holds, not from a browser recompile.
    const different = irOf('Divergent', 'plot(ta.ema(close, 5), "E")')
    getScript.mockResolvedValue({ ...scriptWith(different), source: 'indicator("X")\nplot(close)' })

    await controller.addSavedScript(7)

    expect(latest[0]?.name).toBe('Divergent')
  })
})

describe('a script that cannot be added says why', () => {
  it('reports a script the server could not compile', async () => {
    // The `request.security` case: compiles in the editor, not on the server.
    getScript.mockResolvedValue(scriptWith(null))

    const id = await controller.addSavedScript(7)

    expect(id).toBeUndefined()
    expect(latest).toHaveLength(0)
    expect(errors()).toHaveLength(1)
    expect(errors()[0]).toContain('My Script')
  })

  it('reports a script that no longer exists', async () => {
    getScript.mockRejectedValue(new Error('Request failed with status code 404'))

    const id = await controller.addSavedScript(7)

    expect(id).toBeUndefined()
    expect(errors()).toHaveLength(1)
  })

  it('reports a script the API returned nothing for', async () => {
    getScript.mockResolvedValue(undefined)

    const id = await controller.addSavedScript(7)

    expect(id).toBeUndefined()
    expect(errors()).toHaveLength(1)
  })

  it('reports a script with no resolvable version', async () => {
    // Identity is script + version. Without a version id there is nothing to
    // pin, so the indicator could never be restored.
    getScript.mockResolvedValue({ ...scriptWith(SAVED_IR), version_id: undefined })

    const id = await controller.addSavedScript(7)

    expect(id).toBeUndefined()
    expect(errors()).toHaveLength(1)
  })

  it('does not disturb indicators already on the chart', async () => {
    getScript.mockResolvedValueOnce(scriptWith(SAVED_IR))
    await controller.addSavedScript(7)
    getScript.mockRejectedValueOnce(new Error('boom'))

    await controller.addSavedScript(8)

    expect(latest).toHaveLength(1)
  })
})
