/**
 * What `IndicatorHost.snapshot()` persists into a saved layout.
 *
 * Previously it recorded `definitionId`, `inputs`, `styleOverrides`,
 * `visibility` and `hidden` — and for a custom OpenScript indicator
 * `definitionId` was the literal string `'ir'`. That is not identity: it names
 * the KIND of instance, not WHICH script is running, so the entry could never
 * be rebuilt and restore threw `unknown indicator: ir` into a swallowing catch.
 *
 * A durable entry now carries the saved script's identity instead. Two
 * properties matter and are asserted here rather than assumed:
 *
 *   - a durable indicator persists enough to re-fetch its authoritative IR;
 *   - an editor PREVIEW persists nothing at all. It is built from an unsaved
 *     buffer, so there is no version to re-fetch and no way to restore it. A
 *     preview that reached a layout would be an entry guaranteed to fail on
 *     reopen — the exact failure this work exists to remove.
 *
 * No engine is needed: with no dataset loaded the host skips `createSession`,
 * and snapshotting is pure model state.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { IRProgram } from '@openalgo/openscript'

vi.mock('./engine', () => ({
  getEngine: () => Promise.reject(new Error('no engine in this test')),
}))

const { IndicatorHost } = await import('./indicator-host')

function irOf(name: string, src: string, overlay = false): IRProgram {
  const result = compile(`indicator("${name}", overlay=${overlay})\n${src}`)
  if (!result.ir) {
    throw new Error(`compile failed: ${JSON.stringify(result.diagnostics.map((d) => d.code))}`)
  }
  return result.ir
}

const SAVED = irOf('Saved', 'len = input.int(14, "Length")\nplot(ta.sma(close, len), "S")')
const OTHER = irOf('Other', 'plot(ta.ema(close, 20), "E")')
const DRAFT = irOf('Draft', 'plot(close, "C")')

const SCRIPT_A = { scriptId: 7, versionId: 42, sourceHash: 'a'.repeat(64) }
const SCRIPT_B = { scriptId: 8, versionId: 43 }

let host: InstanceType<typeof IndicatorHost>

beforeEach(() => {
  host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
})

describe('a durable OpenScript indicator is persisted by script identity', () => {
  it('records the script and version it was built from', async () => {
    await host.addIr(SAVED, { script: SCRIPT_A })

    expect(host.snapshot()).toHaveLength(1)
    expect(host.snapshot()[0]?.script).toEqual(SCRIPT_A)
  })

  it('persists inputs, styles, visibility and hidden', async () => {
    const id = await host.addIr(SAVED, {
      script: SCRIPT_A,
      inputs: { len: 30 },
      styleOverrides: { out_0: { color: '#ff0000' } },
      visibility: { '1m': false },
    })
    host.setHidden(id, true)

    const entry = host.snapshot()[0]
    expect(entry?.inputs).toEqual({ len: 30 })
    expect(entry?.styleOverrides).toEqual({ out_0: { color: '#ff0000' } })
    expect(entry?.visibility).toEqual({ '1m': false })
    expect(entry?.hidden).toBe(true)
  })

  it('does not persist the IR itself', async () => {
    // A layout carrying its own copy of the program would be a second source of
    // truth that silently drifts from the server's stored IR. Reopen re-fetches
    // by identity instead.
    await host.addIr(SAVED, { script: SCRIPT_A })

    expect(host.snapshot()[0]).not.toHaveProperty('ir')
    expect(JSON.stringify(host.snapshot())).not.toContain('"nodes"')
  })

  it('is JSON-serializable, since a layout is stored as JSON', async () => {
    await host.addIr(SAVED, { script: SCRIPT_A, inputs: { len: 21 } })

    const roundTripped = JSON.parse(JSON.stringify(host.snapshot()))
    expect(roundTripped).toEqual(host.snapshot())
  })
})

describe('an editor preview is never persisted', () => {
  it('is omitted from the snapshot', async () => {
    await host.addIr(DRAFT)

    expect(host.snapshot()).toEqual([])
  })

  it('is omitted while durable indicators around it are kept', async () => {
    await host.addIr(SAVED, { script: SCRIPT_A })
    await host.addIr(DRAFT)
    await host.addIr(OTHER, { script: SCRIPT_B })

    const entries = host.snapshot()
    expect(entries).toHaveLength(2)
    expect(entries.map((e) => e.script?.scriptId)).toEqual([7, 8])
  })
})

describe('registry builtins are unaffected', () => {
  it('is still persisted by definitionId, with no script identity', async () => {
    await host.add('builtin.sma', { source: 'close', period: 5 })

    const entry = host.snapshot()[0]
    expect(entry?.definitionId).toBe('builtin.sma')
    expect(entry?.script).toBeUndefined()
    expect(entry?.inputs).toMatchObject({ period: 5 })
  })

  it('coexists with durable indicators in one snapshot', async () => {
    await host.add('builtin.sma', { period: 5 })
    await host.addIr(SAVED, { script: SCRIPT_A })

    expect(host.snapshot().map((e) => e.definitionId)).toEqual(['builtin.sma', 'ir'])
  })
})

describe('placement survives as instance order', () => {
  /**
   * Pane numbers are NOT persisted, deliberately. `attachChart` hands panes out
   * in instance order, so the stack order IS the instance order — a stored pane
   * index would be a second, conflicting source of truth, and it would leave a
   * gap in the stack whenever one entry failed to restore. Order is the thing
   * that has to round-trip.
   */
  it('snapshot order follows add order', async () => {
    await host.addIr(SAVED, { script: SCRIPT_A })
    await host.addIr(OTHER, { script: SCRIPT_B })

    expect(host.snapshot().map((e) => e.script?.scriptId)).toEqual([7, 8])
  })

  it('snapshot order follows a pane move', async () => {
    await host.addIr(SAVED, { script: SCRIPT_A })
    const second = await host.addIr(OTHER, { script: SCRIPT_B })

    expect(host.movePane(second, -1)).toBe(true)
    expect(host.snapshot().map((e) => e.script?.scriptId)).toEqual([8, 7])
  })

  it('does not persist a pane index', async () => {
    await host.addIr(SAVED, { script: SCRIPT_A })

    expect(host.snapshot()[0]).not.toHaveProperty('pane')
  })
})
