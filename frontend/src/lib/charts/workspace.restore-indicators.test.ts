/**
 * Restoring a saved layout's indicators.
 *
 * `restoreIndicators` re-added every entry through the REGISTRY:
 * `this.indicators.add(item.definitionId, ...)`. For a custom OpenScript entry
 * `definitionId` is the sentinel `'ir'`, which the manifest does not contain, so
 * the lookup threw `unknown indicator: ir` — straight into `.catch(() =>
 * undefined)`. A layout with custom indicators reopened without them and
 * without a word to the user.
 *
 * Three things are fixed here and each is asserted separately:
 *
 *   5. a durable entry restores through `addIr`, rebuilt from the server's
 *      compiled IR for the exact version that was saved — never a registry
 *      lookup, and never a browser recompile;
 *   6. saved inputs are reconciled against that IR's declarations, so a stale
 *      or out-of-range value cannot ride back onto the chart;
 *   7. anything that cannot be restored is reported. Silence is the bug.
 *
 * The engine is stubbed: what is under test is which path restore takes and
 * what it does when that path fails, not what the worker computes.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { IRProgram } from '@openalgo/openscript'
import type { IndicatorInstance } from './indicator-host'

const getVersion = vi.fn()

vi.mock('@/api/indicators', () => ({
  getVersion: (scriptId: number, versionId: number) => getVersion(scriptId, versionId),
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

const SAVED_IR = irOf(
  'Saved',
  'len = input.int(14, "Length", minval=5, maxval=50)\nplot(ta.sma(close, len), "S")'
)
const OTHER_IR = irOf('Other', 'plot(ta.ema(close, 20), "E")')

/** A successful version fetch, shaped like the scripts API returns it. */
function versionWith(ir: IRProgram | null) {
  return { id: 42, version_number: 1, source_code: '', compiled_ir: ir, diagnostics: [] }
}

let controller: InstanceType<typeof ChartWorkspaceController>
let latest: IndicatorInstance[] = []
let toasts: { message: string; kind: string }[] = []

beforeEach(() => {
  latest = []
  toasts = []
  getVersion.mockReset()
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

describe('a durable entry restores through the IR path', () => {
  it('rebuilds the indicator from the server IR', async () => {
    getVersion.mockResolvedValue(versionWith(SAVED_IR))

    await controller.restoreIndicators({
      indicators: [{ definitionId: 'ir', inputs: { len: 21 }, script: { scriptId: 7, versionId: 42 } }],
    })

    expect(latest).toHaveLength(1)
    expect(latest[0]?.ir).toBeDefined()
    expect(latest[0]?.name).toBe('Saved')
    expect(errors()).toEqual([])
  })

  it('fetches the pinned version, not whatever the script is now', async () => {
    // Restoring the CURRENT version would silently change a saved chart when the
    // script has been edited since.
    getVersion.mockResolvedValue(versionWith(SAVED_IR))

    await controller.restoreIndicators({
      indicators: [{ definitionId: 'ir', inputs: {}, script: { scriptId: 7, versionId: 42 } }],
    })

    expect(getVersion).toHaveBeenCalledWith(7, 42)
  })

  it('the restored instance keeps its script identity, so it can be saved again', async () => {
    getVersion.mockResolvedValue(versionWith(SAVED_IR))
    const script = { scriptId: 7, versionId: 42, sourceHash: 'b'.repeat(64) }

    await controller.restoreIndicators({ indicators: [{ definitionId: 'ir', inputs: {}, script }] })

    expect(latest[0]?.script).toEqual(script)
    expect(controller.snapshot().indicators[0]?.script).toEqual(script)
  })

  it('restores styles, visibility and hidden', async () => {
    getVersion.mockResolvedValue(versionWith(SAVED_IR))

    await controller.restoreIndicators({
      indicators: [
        {
          definitionId: 'ir',
          inputs: {},
          script: { scriptId: 7, versionId: 42 },
          styleOverrides: { out_0: { color: '#123456' } },
          visibility: { '1m': false } as never,
          hidden: true,
        },
      ],
    })

    expect(latest[0]?.styleOverrides).toEqual({ out_0: { color: '#123456' } })
    expect(latest[0]?.visibility).toEqual({ '1m': false })
    expect(latest[0]?.hidden).toBe(true)
  })
})

describe('registry builtins are unaffected', () => {
  it('still restores from the manifest', async () => {
    await controller.restoreIndicators({
      indicators: [{ definitionId: 'builtin.sma', inputs: { period: 9 } }],
    })

    expect(latest).toHaveLength(1)
    expect(latest[0]?.definitionId).toBe('builtin.sma')
    expect(getVersion).not.toHaveBeenCalled()
    expect(errors()).toEqual([])
  })

  it('order is preserved across a mixed layout', async () => {
    getVersion.mockResolvedValue(versionWith(SAVED_IR))

    await controller.restoreIndicators({
      indicators: [
        { definitionId: 'builtin.sma', inputs: {} },
        { definitionId: 'ir', inputs: {}, script: { scriptId: 7, versionId: 42 } },
        { definitionId: 'builtin.ema', inputs: {} },
      ],
    })

    expect(latest.map((i) => i.definitionId)).toEqual(['builtin.sma', 'ir', 'builtin.ema'])
  })
})

describe('saved inputs are reconciled against the IR declarations', () => {
  beforeEach(() => {
    getVersion.mockResolvedValue(versionWith(SAVED_IR))
  })

  const restoreWith = (inputs: Record<string, unknown>) =>
    controller.restoreIndicators({
      indicators: [{ definitionId: 'ir', inputs, script: { scriptId: 7, versionId: 42 } }],
    })

  it('clamps a value outside the declared range', async () => {
    await restoreWith({ len: 9999 })

    expect(latest[0]?.inputs.len).toBe(50)
  })

  it('replaces a value of the wrong type with the declared default', async () => {
    await restoreWith({ len: 'not a number' })

    expect(latest[0]?.inputs.len).toBe(14)
  })

  it('fills in an input the layout never saved', async () => {
    await restoreWith({})

    expect(latest[0]?.inputs.len).toBe(14)
  })

  it('drops a key the script no longer declares', async () => {
    await restoreWith({ len: 20, removedSetting: 'stale' })

    expect(latest[0]?.inputs).toEqual({ len: 20 })
  })
})

describe('a failed restore is reported, never swallowed', () => {
  it('reports a version that no longer exists', async () => {
    getVersion.mockRejectedValue(new Error('Request failed with status code 404'))

    await controller.restoreIndicators({
      indicators: [{ definitionId: 'ir', inputs: {}, script: { scriptId: 7, versionId: 42 } }],
    })

    expect(latest).toHaveLength(0)
    expect(errors()).toHaveLength(1)
    expect(errors()[0]).toContain('7')
  })

  it('reports a version the server could not compile', async () => {
    // The real case: `request.security` compiles in the browser and not on the
    // server, so the stored version has no IR at all.
    getVersion.mockResolvedValue(versionWith(null))

    await controller.restoreIndicators({
      indicators: [{ definitionId: 'ir', inputs: {}, script: { scriptId: 7, versionId: 42 } }],
    })

    expect(latest).toHaveLength(0)
    expect(errors()).toHaveLength(1)
  })

  it('reports a legacy entry that has no script identity', async () => {
    // Written by a build that persisted editor previews. There is no version to
    // fetch, so it cannot be restored — but it must not vanish quietly either.
    await controller.restoreIndicators({ indicators: [{ definitionId: 'ir', inputs: {} }] })

    expect(latest).toHaveLength(0)
    expect(errors()).toHaveLength(1)
    expect(getVersion).not.toHaveBeenCalled()
  })

  it('reports an unknown builtin', async () => {
    await controller.restoreIndicators({
      indicators: [{ definitionId: 'builtin.doesnotexist', inputs: {} }],
    })

    expect(latest).toHaveLength(0)
    expect(errors()).toHaveLength(1)
  })

  it('keeps restoring the entries that do work', async () => {
    // One bad entry must not cost the user the rest of their layout.
    getVersion.mockRejectedValue(new Error('boom'))

    await controller.restoreIndicators({
      indicators: [
        { definitionId: 'builtin.sma', inputs: {} },
        { definitionId: 'ir', inputs: {}, script: { scriptId: 7, versionId: 42 } },
        { definitionId: 'builtin.ema', inputs: {} },
      ],
    })

    expect(latest.map((i) => i.definitionId)).toEqual(['builtin.sma', 'builtin.ema'])
    expect(errors()).toHaveLength(1)
  })

  it('names every failure when several entries fail', async () => {
    getVersion.mockRejectedValue(new Error('boom'))

    await controller.restoreIndicators({
      indicators: [
        { definitionId: 'ir', inputs: {}, script: { scriptId: 7, versionId: 42 } },
        { definitionId: 'ir', inputs: {}, script: { scriptId: 8, versionId: 43 } },
      ],
    })

    const reported = errors().join(' ')
    expect(reported).toContain('7')
    expect(reported).toContain('8')
  })
})

describe('a restored layout can be saved again unchanged', () => {
  it('round-trips identity, reconciled inputs and order', async () => {
    getVersion.mockImplementation((scriptId: number) =>
      Promise.resolve(versionWith(scriptId === 7 ? SAVED_IR : OTHER_IR))
    )
    const saved = [
      { definitionId: 'ir', inputs: { len: 20 }, script: { scriptId: 7, versionId: 42 } },
      { definitionId: 'ir', inputs: {}, script: { scriptId: 8, versionId: 43 } },
    ]

    await controller.restoreIndicators({ indicators: saved })

    expect(controller.snapshot().indicators).toEqual(saved.map((s) => ({ ...s, inputs: s.inputs })))
  })
})

/**
 * A FAILED restore must not delete the indicator from the saved layout.
 *
 * The chart drops what it cannot run — correctly, since there is nothing to
 * draw. But `snapshot()` is what the layout is saved from, and an entry missing
 * there is an entry ERASED on the next autosave. So a transient failure (a
 * version that momentarily has no compiled IR, a server blip mid-reload) turns
 * into permanent data loss: the indicator is gone from the layout and the user
 * has to find and re-add it, losing its inputs and style overrides with it.
 *
 * Observed for real: a script saved in a state the server could not compile
 * vanished from the chart, and re-adding it was the only recovery.
 */
describe('an unrestorable entry survives in the layout', () => {
  it('keeps the entry in snapshot() so the next reload can retry it', async () => {
    getVersion.mockResolvedValue({ compiled_ir: null })
    const saved = [
      { definitionId: 'ir', inputs: { len: 20 }, script: { scriptId: 7, versionId: 42 } },
    ]

    await controller.restoreIndicators({ indicators: saved })

    // Nothing runs on the chart...
    expect(latest).toHaveLength(0)
    // ...but the layout still carries it, inputs and identity intact.
    expect(controller.snapshot().indicators).toEqual(saved)
  })

  it('does not duplicate the entry once the same script restores successfully', async () => {
    getVersion.mockResolvedValue({ compiled_ir: null })
    const saved = [
      { definitionId: 'ir', inputs: {}, script: { scriptId: 7, versionId: 42 } },
    ]
    await controller.restoreIndicators({ indicators: saved })
    expect(controller.snapshot().indicators).toHaveLength(1)

    // Second reload, this time the server has the IR.
    getVersion.mockResolvedValue(versionWith(SAVED_IR))
    await controller.restoreIndicators({ indicators: saved })

    const entries = controller.snapshot().indicators
    expect(entries).toHaveLength(1)
    expect(entries[0]?.script?.scriptId).toBe(7)
  })
})
