/**
 * Durable OpenScript indicators, distinct from the editor preview.
 *
 * Until now `addIr` had exactly one production caller: `previewIr`, documented
 * as "one preview session at a time" and torn down by `clearPreview`. Every
 * compiled OpenScript program on a chart was therefore ephemeral by
 * construction — there was no such thing as an OpenScript indicator a user had
 * *added*, only one they were *previewing*.
 *
 * A durable instance differs from a preview in exactly one way that matters:
 * it carries the identity of the SAVED SCRIPT it was built from, which is what
 * lets a layout re-fetch its authoritative IR on reopen. `definitionId: 'ir'`
 * is a UI sentinel and is not identity.
 *
 * The engine gate is unchanged and must stay so: eligibility for the
 * incremental path is IR OWNERSHIP, never the sentinel and never whether the
 * instance happens to be durable. Both kinds run as `{kind:'ir'}` sessions.
 *
 * The host is driven with NO chart binding — `createRenderer` and `applyOutputs`
 * both return early without one, so sessions and the worker round trip exercise
 * normally while rendering is skipped.
 */

import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import type { Bar } from 'openalgo-charts'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { IRProgram } from '@openalgo/openscript'
import type { EngineWorkerClient, WorkerLike } from '@openalgo/openscript/worker-client'

let client: EngineWorkerClient | null = null

vi.mock('./engine', () => ({
  getEngine: () => Promise.resolve(client as EngineWorkerClient),
}))

const { IndicatorHost } = await import('./indicator-host')

/** Every `create-session` request the host sends, teed at the transport. */
interface SentSession {
  sessionId: string
  program: { kind: string }
}
const sent: SentSession[] = []

function irOf(src: string): IRProgram {
  const result = compile(`indicator("Custom", overlay=false)\n${src}`)
  if (!result.ir) {
    throw new Error(`compile failed: ${JSON.stringify(result.diagnostics.map((d) => d.code))}`)
  }
  return result.ir
}

function bars(n: number): Bar[] {
  const out: Bar[] = []
  let price = 100
  for (let i = 0; i < n; i++) {
    price += Math.sin(i / 3)
    out.push({
      time: 1_700_000_000 + i * 60,
      open: price - 0.2,
      high: price + 0.5,
      low: price - 0.5,
      close: price,
      volume: 1000 + i,
    })
  }
  return out
}

async function flush(): Promise<void> {
  for (let i = 0; i < 30; i++) await Promise.resolve()
}

beforeAll(async () => {
  const scope: {
    onmessage: ((e: { data: unknown }) => void) | null
    postMessage: (m: unknown) => void
  } = { onmessage: null, postMessage: () => {} }
  vi.stubGlobal('self', scope)

  const require = createRequire(import.meta.url)
  const distDir = dirname(require.resolve('@openalgo/openscript/oa_wasm.wasm'))
  await import('@openalgo/openscript/worker')

  const transport: WorkerLike = {
    postMessage: (message: unknown) => {
      const m = message as { type?: string; sessionId?: string; program?: { kind: string } }
      if (m.type === 'create-session' && m.program) {
        sent.push({ sessionId: m.sessionId as string, program: m.program })
      }
      scope.onmessage?.({ data: message })
    },
    onmessage: null,
  }
  scope.postMessage = (message: unknown) => {
    transport.onmessage?.({ data: message })
  }

  const { EngineWorkerClient: Client } = await import('@openalgo/openscript/worker-client')
  client = new Client(transport)
  const wasm = readFileSync(join(distDir, 'oa_wasm.wasm'))
  await client.init(
    wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength) as ArrayBuffer
  )
}, 120_000)

const SCRIPT = { scriptId: 7, versionId: 42, sourceHash: 'a'.repeat(64) }

async function hostWithData() {
  const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
  await host.setDataset(bars(120), { symbol: 'X', exchange: 'NSE', interval: '1m' })
  return host
}

describe('a durable IR indicator carries saved-script identity', () => {
  it('records the script it was built from', async () => {
    const host = await hostWithData()
    const id = await host.addIr(irOf('plot(ta.sma(close, 10), "S")'), { script: SCRIPT })
    await flush()

    const instance = host.list().find((i) => i.instanceId === id)
    expect(instance?.script).toEqual(SCRIPT)
    host.dispose()
  })

  it('an editor preview carries no script identity', async () => {
    const host = await hostWithData()
    const id = await host.addIr(irOf('plot(close, "C")'))
    await flush()

    expect(host.list().find((i) => i.instanceId === id)?.script).toBeUndefined()
    host.dispose()
  })

  it('takes its name, overlay and inputs from the IR, like a preview does', async () => {
    const host = await hostWithData()
    const ir = irOf('len = input.int(14, "Length")\nplot(ta.sma(close, len), "S")')
    const id = await host.addIr(ir, { script: SCRIPT })
    await flush()

    const instance = host.list().find((i) => i.instanceId === id)
    expect(instance?.name).toBe('Custom')
    expect(instance?.overlay).toBe(false)
    expect(instance?.inputs.len).toBe(14)
    host.dispose()
  })

  it('accepts saved inputs, styles and visibility at add time', async () => {
    const host = await hostWithData()
    const ir = irOf('len = input.int(14, "Length")\nplot(ta.sma(close, len), "S")')
    const id = await host.addIr(ir, {
      script: SCRIPT,
      inputs: { len: 30 },
      styleOverrides: { out_0: { color: '#ff0000' } },
      visibility: { '1m': false },
    })
    await flush()

    const instance = host.list().find((i) => i.instanceId === id)
    expect(instance?.inputs.len).toBe(30)
    expect(instance?.styleOverrides?.out_0?.color).toBe('#ff0000')
    expect(instance?.visibility).toEqual({ '1m': false })
    host.dispose()
  })
})

describe('durability does not change the engine gate', () => {
  it('a durable instance runs as an IR session, exactly like a preview', async () => {
    const host = await hostWithData()
    sent.length = 0
    const durableId = await host.addIr(irOf('plot(ta.sma(close, 10), "S")'), { script: SCRIPT })
    const previewId = await host.addIr(irOf('plot(close, "C")'))
    await flush()

    // IR OWNERSHIP is the eligibility rule. If durability leaked into it, one of
    // these would arrive as a builtin and silently lose the incremental path.
    expect(sent.find((s) => s.sessionId === durableId)?.program.kind).toBe('ir')
    expect(sent.find((s) => s.sessionId === previewId)?.program.kind).toBe('ir')
    host.dispose()
  })

  it('a durable instance owns its IR, so it resolves settings from it', async () => {
    const { resolveSettingsEntry } = await import('./indicator-host')
    const host = await hostWithData()
    const ir = irOf('len = input.int(14, "Length")\nplot(ta.sma(close, len), "S")')
    const id = await host.addIr(ir, { script: SCRIPT })
    await flush()

    const instance = host.list().find((i) => i.instanceId === id)
    const entry = resolveSettingsEntry(
      instance as Parameters<typeof resolveSettingsEntry>[0],
      host.manifest
    )
    expect(entry?.inputs.map((i) => i.id)).toContain('len')
    host.dispose()
  })
})

describe('durable instances and previews are independent', () => {
  it('several durable instances of the same script coexist', async () => {
    // The preview is single-instance by design. A saved indicator is not: a user
    // may want the same script twice with different inputs.
    const host = await hostWithData()
    const ir = irOf('len = input.int(14, "Length")\nplot(ta.sma(close, len), "S")')
    const first = await host.addIr(ir, { script: SCRIPT, inputs: { len: 10 } })
    const second = await host.addIr(ir, { script: SCRIPT, inputs: { len: 50 } })
    await flush()

    expect(first).not.toBe(second)
    const instances = host.list().filter((i) => i.script?.scriptId === SCRIPT.scriptId)
    expect(instances).toHaveLength(2)
    expect(instances.map((i) => i.inputs.len).sort()).toEqual([10, 50])
    host.dispose()
  })

  it('each non-overlay instance gets its own pane', async () => {
    const host = await hostWithData()
    const ir = irOf('plot(ta.sma(close, 10), "S")')
    const first = await host.addIr(ir, { script: SCRIPT })
    const second = await host.addIr(ir, { script: SCRIPT })
    await flush()

    const panes = host
      .list()
      .filter((i) => i.instanceId === first || i.instanceId === second)
      .map((i) => i.pane)
    expect(new Set(panes).size).toBe(2)
    host.dispose()
  })
})
