/**
 * The editor preview carries its input values across a recompile (P4).
 *
 * P4 was recorded as "the indicator settings dialog does not open". Investigating
 * it found something different and in two layers: `IndicatorSettingsDialog` is
 * never mounted in `ChartEditor` at all, AND `previewIr` took no inputs. So even
 * with the dialog mounted, every edited value would be discarded by the next
 * keystroke — the editor recompiles on a 400 ms debounce and `previewIr` is
 * `clearPreview()` + `addIr(ir)`, which tears the session down and rebuilds it
 * from defaults.
 *
 * That second half is the one worth a test. A missing dialog is visible the
 * moment you look for it; a dialog whose values silently reset as you type is
 * the kind of defect that gets blamed on the engine.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { IRProgram } from '@openalgo/openscript'
import type { IndicatorInstance } from './indicator-host'

vi.mock('@/api/indicators', () => ({ getScript: vi.fn(), getVersion: vi.fn() }))
vi.mock('./engine', () => ({ getEngine: () => Promise.reject(new Error('no engine in this test')) }))

const { ChartWorkspaceController } = await import('./workspace')

function irOf(src: string): IRProgram {
  const result = compile(`indicator("Preview", overlay=true)\n${src}`)
  if (!result.ir) throw new Error(JSON.stringify(result.diagnostics.map((d) => d.code)))
  return result.ir
}

const IR = irOf('len = input.int(14, "Length", maxval=200)\nplot(ta.sma(close, len), "S")')

let controller: InstanceType<typeof ChartWorkspaceController>
let addIrCalls: { ir: IRProgram; options?: { inputs?: Record<string, unknown> } }[]

beforeEach(() => {
  addIrCalls = []
  controller = new ChartWorkspaceController({
    apiKey: 'test',
    container: document.createElement('div'),
    callbacks: {
      onIndicators: (_list: IndicatorInstance[]) => {},
      onToast: () => {},
    },
  } as never)
  // Stand in for the engine-backed host: record what `previewIr` forwards.
  ;(controller as never as { indicators: unknown }).indicators = {
    addIr: (ir: IRProgram, options?: { inputs?: Record<string, unknown> }) => {
      addIrCalls.push({ ir, options })
      return Promise.resolve(`inst_${addIrCalls.length}`)
    },
    remove: () => Promise.resolve(),
  }
  ;(controller as never as { removeIndicator: unknown }).removeIndicator = () => Promise.resolve()
})

describe('previewIr carries input values', () => {
  it('forwards inputs to the new preview session', async () => {
    await controller.previewIr(IR, { len: 50 })
    expect(addIrCalls).toHaveLength(1)
    expect(addIrCalls[0]?.options?.inputs).toEqual({ len: 50 })
  })

  it('survives a recompile — the case the editor hits on every keystroke', async () => {
    await controller.previewIr(IR, { len: 50 })
    await controller.previewIr(IR, { len: 50 })
    expect(addIrCalls).toHaveLength(2)
    // Without this the second session starts from the DECLARED default (14) and
    // the user watches their setting revert while typing.
    expect(addIrCalls[1]?.options?.inputs).toEqual({ len: 50 })
  })

  it('omitting inputs is still valid — an untouched preview uses declared defaults', () => {
    return controller.previewIr(IR).then(() => {
      expect(addIrCalls[0]?.options?.inputs).toBeUndefined()
    })
  })
})
