/**
 * The workspace-level separation between a saved OpenScript indicator and the
 * editor preview.
 *
 * `previewIr` is single-instance by design: it calls `clearPreview` first, so
 * each recompile in the editor replaces the last one. That is right for a
 * preview and fatal for a saved indicator — if adding one went through the same
 * path, adding a second would silently delete the first, and opening the editor
 * would delete both.
 *
 * These drive the real `ChartWorkspaceController` with no chart bound. The
 * engine is stubbed out entirely: what is under test is the controller's
 * bookkeeping of which instance is the preview, not anything the worker does.
 * The host skips `createSession` while no dataset is loaded, so no worker is
 * needed to observe it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { IRProgram } from '@openalgo/openscript'
import type { IndicatorInstance } from './indicator-host'

vi.mock('./engine', () => ({
  getEngine: () => Promise.reject(new Error('no engine in this test')),
}))

const { ChartWorkspaceController } = await import('./workspace')

/**
 * Overlay scripts throughout. Removing a PANED indicator rebuilds the chart,
 * and `createChart` needs a canvas jsdom does not provide — so a non-overlay
 * fixture here would fail on chart construction rather than on anything these
 * tests are about. Pane assignment is covered where it belongs, against the
 * host, in `indicator-host.durable.test.ts`.
 */
function irOf(name: string, src: string): IRProgram {
  const result = compile(`indicator("${name}", overlay=true)\n${src}`)
  if (!result.ir) {
    throw new Error(`compile failed: ${JSON.stringify(result.diagnostics.map((d) => d.code))}`)
  }
  return result.ir
}

const SAVED = irOf('Saved', 'plot(ta.sma(close, 10), "S")')
const OTHER = irOf('Other', 'plot(ta.ema(close, 20), "E")')
const DRAFT = irOf('Draft', 'plot(close, "C")')

const SCRIPT_A = { scriptId: 1, versionId: 11 }
const SCRIPT_B = { scriptId: 2, versionId: 22 }

let controller: InstanceType<typeof ChartWorkspaceController>
/** Latest instance list, captured through the production `onIndicators`
 *  callback — the same channel the React layer reads, rather than a public
 *  accessor added for the benefit of a test. */
let latest: IndicatorInstance[] = []

beforeEach(() => {
  latest = []
  controller = new ChartWorkspaceController({
    apiKey: 'test',
    // Removing a paned indicator rebuilds the chart, which clears its
    // container — so one has to exist even though no chart is ever built here.
    container: document.createElement('div'),
    callbacks: {
      onIndicators: (list: IndicatorInstance[]) => {
        latest = list
      },
    },
  } as unknown as ConstructorParameters<typeof ChartWorkspaceController>[0])
})

/** Instances the host is holding, in add order. */
function instances(): IndicatorInstance[] {
  return latest
}

describe('adding a saved script indicator', () => {
  it('adds a durable instance carrying its script identity', async () => {
    const id = await controller.addScriptIndicator(SCRIPT_A, SAVED)

    const instance = instances().find((i) => i.instanceId === id)
    expect(instance?.script).toEqual(SCRIPT_A)
    expect(instance?.ir).toBeDefined()
  })

  it('does not consume the preview slot', async () => {
    // If the add went through `previewIr`, the next editor compile would replace
    // the saved indicator instead of the draft.
    await controller.addScriptIndicator(SCRIPT_A, SAVED)
    await controller.previewIr(DRAFT)

    expect(instances()).toHaveLength(2)
  })

  it('adding a second saved indicator keeps the first', async () => {
    await controller.addScriptIndicator(SCRIPT_A, SAVED)
    await controller.addScriptIndicator(SCRIPT_B, OTHER)

    expect(instances().map((i) => i.script?.scriptId)).toEqual([1, 2])
  })

  it('carries saved inputs, styles and visibility through', async () => {
    const ir = irOf('Params', 'len = input.int(14, "Length")\nplot(ta.sma(close, len), "S")')
    const id = await controller.addScriptIndicator(SCRIPT_A, ir, {
      inputs: { len: 30 },
      styleOverrides: { out_0: { color: '#00ff00' } },
      visibility: { '5m': false },
    })

    const instance = instances().find((i) => i.instanceId === id)
    expect(instance?.inputs.len).toBe(30)
    expect(instance?.styleOverrides?.out_0?.color).toBe('#00ff00')
    expect(instance?.visibility).toEqual({ '5m': false })
  })
})

describe('what a saved layout carries', () => {
  it('a saved indicator reaches the workspace snapshot with its identity', async () => {
    await controller.addScriptIndicator(SCRIPT_A, SAVED)

    const entries = controller.snapshot().indicators
    expect(entries).toHaveLength(1)
    expect(entries[0]?.script).toEqual(SCRIPT_A)
  })

  it('the editor preview never reaches it', async () => {
    // The whole point: a layout saved while the editor happens to be open must
    // not gain an entry that cannot be restored.
    await controller.addScriptIndicator(SCRIPT_A, SAVED)
    await controller.previewIr(DRAFT)

    const entries = controller.snapshot().indicators
    expect(entries).toHaveLength(1)
    expect(entries[0]?.script).toEqual(SCRIPT_A)
  })

  it('preserves order across several saved indicators', async () => {
    await controller.addScriptIndicator(SCRIPT_A, SAVED)
    await controller.addScriptIndicator(SCRIPT_B, OTHER)

    expect(controller.snapshot().indicators.map((e) => e.script?.scriptId)).toEqual([1, 2])
  })
})

describe('the editor preview stays separate', () => {
  it('clearing the preview leaves saved indicators alone', async () => {
    const savedId = await controller.addScriptIndicator(SCRIPT_A, SAVED)
    await controller.previewIr(DRAFT)
    await controller.clearPreview()

    expect(instances().map((i) => i.instanceId)).toEqual([savedId])
  })

  it('recompiling in the editor replaces only the preview', async () => {
    const savedId = await controller.addScriptIndicator(SCRIPT_A, SAVED)
    await controller.previewIr(DRAFT)
    const firstPreview = instances().find((i) => i.instanceId !== savedId)?.instanceId
    await controller.previewIr(OTHER)

    const after = instances()
    expect(after).toHaveLength(2)
    expect(after.map((i) => i.instanceId)).toContain(savedId)
    expect(after.map((i) => i.instanceId)).not.toContain(firstPreview)
  })

  it('a preview is never given script identity', async () => {
    await controller.previewIr(DRAFT)

    expect(instances()[0]?.script).toBeUndefined()
  })

  it('clearing the preview when a saved indicator was added first removes nothing else', async () => {
    // Guards the ordering trap: `clearPreview` must key off the recorded preview
    // id, not "the most recently added IR instance".
    await controller.previewIr(DRAFT)
    const savedId = await controller.addScriptIndicator(SCRIPT_A, SAVED)
    await controller.clearPreview()

    expect(instances().map((i) => i.instanceId)).toEqual([savedId])
  })
})
