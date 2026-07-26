/**
 * The product chain, end to end:
 *
 *     saved script -> picker -> authoritative IR -> durable instance
 *                  -> configure -> snapshot -> restore -> same numbers
 *
 * Every link is covered on its own elsewhere. That is exactly why this file
 * exists: the audit that started this work found each step individually
 * plausible while the chain between them was absent, so a suite of link tests is
 * not evidence that the chain holds. This runs the whole thing once.
 *
 * What is real here, and what is not:
 *
 *   REAL — the server. `openscript_chain_server.py` persists the script through
 *   `blueprints/indicators.py`, compiles it with the server's own Python
 *   compiler, and serializes it with the real `_script_row`. The payloads below
 *   are that output verbatim, not hand-written fixtures.
 *
 *   REAL — the browser. The production `ChartWorkspaceController`, its
 *   `IndicatorHost`, and the BUILT worker from `@openalgo/openscript/worker`
 *   (i.e. `dist/`), which is the artifact the browser actually loads.
 *
 *   STUBBED — the HTTP transport only. `@/api/indicators` hands back the
 *   payloads the server produced rather than standing a server up on a port.
 *   The routes themselves are covered by
 *   `test/test_indicator_script_endpoints.py`; what this proves is that the
 *   client half consumes what the server half emits.
 *
 * NOT exercised: rendering. No chart is bound — `createChart` needs a canvas
 * jsdom does not provide — so `applyOutputs` returns before it reaches a
 * renderer and the crosshair cache stays empty. Values are therefore read where
 * the host itself receives them, at the worker boundary. That makes this a proof
 * of computation and wiring, not of pixels; seeing the indicator drawn correctly
 * remains part of the live demonstration.
 */

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Bar } from 'openalgo-charts'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import type { IRProgram } from '@openalgo/openscript'
import type { EngineWorkerClient, WorkerLike } from '@openalgo/openscript/worker-client'
import type { PickerScript } from '@/components/charts/workspace/IndicatorPicker'
import type { IndicatorInstance } from './indicator-host'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..')

/** The script the user writes. Deliberately exercises an input, a TA kernel and
 *  a history reference, so a broken link shows up as wrong NUMBERS. */
const SOURCE =
  'indicator("Chain", overlay=true)\n' +
  'len = input.int(14, "Length", minval=2, maxval=200)\n' +
  'plot(ta.sma(close, len) + close[1], "Signal")\n'

interface ServerPayloads {
  list: { id: number; name: string; description: string | null }[]
  script: {
    id: number
    name: string
    version_id: number
    source_hash: string
    compiled_ir: IRProgram | null
  }
  version: { id: number; compiled_ir: IRProgram | null }
}

let server: ServerPayloads
/** The same script, whose stored IR had its header stripped before the reads —
 *  a version compiled before the header existed. */
let staleServer: ServerPayloads
let client: EngineWorkerClient | null = null

const getScript = vi.fn()
const getVersion = vi.fn()

vi.mock('@/api/indicators', () => ({
  getScript: (id: number) => getScript(id),
  getVersion: (scriptId: number, versionId: number) => getVersion(scriptId, versionId),
}))

vi.mock('./engine', () => ({
  getEngine: () => Promise.resolve(client as EngineWorkerClient),
}))

const { ChartWorkspaceController } = await import('./workspace')

function bars(n: number): Bar[] {
  const out: Bar[] = []
  let price = 100
  for (let i = 0; i < n; i++) {
    price += Math.sin(i / 3) * 1.5 + Math.cos(i / 7)
    out.push({
      time: 1_700_000_000 + i * 60,
      open: price - 0.3,
      high: price + 0.6,
      low: price - 0.6,
      close: price,
      volume: 1000 + (i % 17) * 13,
    })
  }
  return out
}

const DATA = bars(240)
const META = { symbol: 'NIFTY', exchange: 'NSE', interval: '1m' }

/**
 * Every `session-outputs` the worker publishes, teed at the transport. The
 * host's own session id IS the instance id, so this reads per indicator.
 */
const published = new Map<string, number[]>()

/** The plotted series most recently computed for an instance. */
function readings(instanceId: string): number[] {
  return published.get(instanceId) ?? []
}

async function flush(): Promise<void> {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

beforeAll(async () => {
  // ── the server half ──────────────────────────────────────────────────────
  const runServer = (stale: boolean): ServerPayloads =>
    JSON.parse(
      execFileSync('uv', ['run', 'python', 'test/helpers/openscript_chain_server.py'], {
        cwd: repoRoot,
        input: JSON.stringify({ name: 'Chain Test', source: SOURCE, stale }),
        encoding: 'utf8',
        maxBuffer: 64 * 1024 * 1024,
      })
    ) as ServerPayloads

  server = runServer(false)
  staleServer = runServer(true)
  getScript.mockImplementation(() => Promise.resolve(server.script))
  getVersion.mockImplementation(() => Promise.resolve(server.version))

  // ── the browser half ─────────────────────────────────────────────────────
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
      scope.onmessage?.({ data: message })
    },
    onmessage: null,
  }
  scope.postMessage = (message: unknown) => {
    const m = message as {
      type?: string
      sessionId?: string
      outputs?: { kind: string; values?: Float64Array }[]
    }
    if (m.type === 'session-outputs' && m.outputs) {
      const line = m.outputs.find((o) => o.kind === 'line')
      if (line?.values) published.set(m.sessionId as string, [...line.values])
    }
    transport.onmessage?.({ data: message })
  }

  const { EngineWorkerClient: Client } = await import('@openalgo/openscript/worker-client')
  client = new Client(transport)
  const wasm = readFileSync(join(distDir, 'oa_wasm.wasm'))
  await client.init(
    wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength) as ArrayBuffer
  )
}, 180_000)

/** A workspace with the dataset loaded. `indicators` is the same handle
 *  `ChartWorkspace.tsx` uses to snapshot and to read the data window. */
async function workspace() {
  let instances: IndicatorInstance[] = []
  const toasts: string[] = []
  const controller = new ChartWorkspaceController({
    apiKey: 'test',
    container: document.createElement('div'),
    callbacks: {
      onIndicators: (list: IndicatorInstance[]) => {
        instances = list
      },
      onToast: (message: string, kind: string) => {
        if (kind === 'err') toasts.push(message)
      },
    },
  } as unknown as ConstructorParameters<typeof ChartWorkspaceController>[0])
  await controller.indicators.setDataset(DATA, META)
  await flush()
  return { controller, errors: () => toasts, instances: () => instances }
}

describe('the chain from a saved script to a reopened chart indicator', () => {
  it('the server compiled and stored the script', () => {
    expect(server.script.compiled_ir).not.toBeNull()
    expect(server.script.version_id).toBeGreaterThan(0)
    expect(server.script.source_hash).toHaveLength(64)
  })

  it('the picker can offer it', () => {
    // The list payload has to carry what a picker row needs. Typed against the
    // production row so a field the picker depends on cannot quietly vanish.
    const rows: PickerScript[] = server.list.map((s) => ({
      id: s.id,
      name: s.name,
      description: s.description,
    }))
    expect(rows).toHaveLength(1)
    expect(rows[0]?.name).toBe('Chain Test')
    expect(rows[0]?.id).toBe(server.script.id)
  })

  it('runs the whole chain and reopens to the same numbers', async () => {
    // 1. ADD, as the picker does.
    const first = await workspace()
    const addedId = await first.controller.addSavedScript(server.script.id)
    await flush()
    expect(first.errors()).toEqual([])
    expect(addedId).toBeDefined()

    const atDefaults = readings(addedId as string)
    expect(atDefaults.length).toBeGreaterThan(0)
    expect(atDefaults.some((v) => Number.isFinite(v))).toBe(true)

    // 2. CONFIGURE — a non-default input, so the reopen has something to lose.
    await first.controller.updateIndicatorInputs(addedId as string, { len: 30 })
    await flush()
    const configured = readings(addedId as string)
    expect(configured).not.toEqual(atDefaults)

    // 3. SAVE — exactly what `ChartWorkspace.tsx` persists into layout_json,
    //    through a JSON round trip because that is how it is stored.
    const layout = JSON.parse(JSON.stringify(first.controller.snapshot()))
    expect(layout.indicators).toHaveLength(1)
    expect(layout.indicators[0].script).toEqual({
      scriptId: server.script.id,
      versionId: server.script.version_id,
      sourceHash: server.script.source_hash,
    })
    expect(layout.indicators[0].inputs.len).toBe(30)

    // 4. REOPEN into a fresh workspace, as a page reload does.
    getScript.mockClear()
    getVersion.mockClear()
    const second = await workspace()
    await second.controller.restoreIndicators(layout)
    await flush()

    expect(second.errors()).toEqual([])
    expect(second.instances()).toHaveLength(1)

    // The reopen went through the VERSION endpoint — the version that was
    // saved — and never re-read "whatever the script is now".
    expect(getVersion).toHaveBeenCalledWith(server.script.id, server.script.version_id)
    expect(getScript).not.toHaveBeenCalled()

    // 5. NO BROWSER RECOMPILE. The restored instance runs the server's stored
    //    IR, byte for byte, not something compiled from the source.
    const restored = second.instances()[0] as IndicatorInstance
    expect(restored.ir).toEqual(server.version.compiled_ir)

    // 6. SAME NUMBERS, and the configured input survived.
    expect(restored.inputs.len).toBe(30)
    expect(readings(restored.instanceId)).toEqual(configured)

    // 7. And it can be saved again unchanged, so the cycle is closed rather
    //    than merely surviving once.
    expect(second.controller.snapshot().indicators).toEqual(layout.indicators)

    first.controller.destroy()
    second.controller.destroy()
  }, 120_000)

  it('holds for a script whose stored IR predates the header', async () => {
    // Not hypothetical: this is what was in a running instance's database, and
    // it made every affected script unaddable with IR_MAJOR_MISMATCH and its
    // layouts unrestorable. The fetch routes repair such a version from its own
    // source, so the chain has to complete over it exactly as over a fresh one.
    getScript.mockImplementation(() => Promise.resolve(staleServer.script))
    getVersion.mockImplementation(() => Promise.resolve(staleServer.version))
    try {
      const first = await workspace()
      const addedId = await first.controller.addSavedScript(staleServer.script.id)
      await flush()
      expect(first.errors()).toEqual([])
      expect(addedId).toBeDefined()

      const added = readings(addedId as string)
      expect(added.some((v) => Number.isFinite(v))).toBe(true)

      const layout = JSON.parse(JSON.stringify(first.controller.snapshot()))
      const second = await workspace()
      await second.controller.restoreIndicators(layout)
      await flush()

      expect(second.errors()).toEqual([])
      const restored = second.instances()[0] as IndicatorInstance
      expect(readings(restored.instanceId)).toEqual(added)

      first.controller.destroy()
      second.controller.destroy()
    } finally {
      getScript.mockImplementation(() => Promise.resolve(server.script))
      getVersion.mockImplementation(() => Promise.resolve(server.version))
    }
  }, 120_000)

  it('the repaired payload is what makes that possible', () => {
    // Guards the leg above from passing for the wrong reason: if the routes
    // stopped repairing, the payload would arrive header-less and the assertion
    // would be that a broken IR still works.
    expect(staleServer.script.compiled_ir).not.toBeNull()
    expect((staleServer.script.compiled_ir as IRProgram).header?.major).toBe(1)
    expect((staleServer.version.compiled_ir as IRProgram).header?.major).toBe(1)
  })

  it('the comparison would notice a different indicator', async () => {
    // Mutation proof for the chain's own assertions. Without it, a bug that
    // produced empty readings for every instance would make step 6 pass
    // vacuously.
    const w = await workspace()
    const id = await w.controller.addSavedScript(server.script.id)
    await flush()
    const before = readings(id as string)

    await w.controller.updateIndicatorInputs(id as string, { len: 50 })
    await flush()

    expect(readings(id as string)).not.toEqual(before)
    expect(before.length).toBeGreaterThan(0)
    w.controller.destroy()
  })
})
