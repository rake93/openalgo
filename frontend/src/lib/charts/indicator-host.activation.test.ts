/**
 * Phase 5.1 platform activation gate.
 *
 * The engine's own suite proves `EngineWorkerCore` routes an IR-owning session
 * onto the incremental path. This asserts the same thing through the PLATFORM's
 * stack — the real `IndicatorHost`, the real `EngineWorkerClient`, and the BUILT
 * worker entry resolved through `@openalgo/openscript/worker` (i.e. `dist/`) —
 * so three classes of problem the engine tests cannot see are caught here:
 *
 *   1. a STALE DIST: everything imported below comes from the linked package's
 *      built output, not the engine's TypeScript sources, so a `dist/` that
 *      predates Phase 5.1 fails this file rather than silently serving the old
 *      always-full worker to the browser;
 *   2. HOST ROUTING: `IndicatorHost.addIr` must send `{kind:'ir'}` and
 *      `IndicatorHost.add` must send `{kind:'builtin'}` — the eligibility rule
 *      is IR ownership, and the host is what decides which shape is sent;
 *   3. SESSION LIFECYCLE: the seed/recreate path must leave a session able to go
 *      incremental on the next tick rather than reseeding every time.
 *
 * Agreed success criteria: an IR-owned indicator reports a full seed and then
 * `incremental` with NO `fallbackReason`; a registry builtin stays `full` with
 * `builtin-no-ir`. This verifies ACTIVATION, not a controlled speedup — timings
 * here are meaningless, and no assertion depends on them.
 *
 * The host is driven with NO chart binding. `createRenderer` and `applyOutputs`
 * both return early without one, so sessions, the worker round-trip, and perf
 * telemetry all exercise normally while rendering is skipped.
 */

import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import type { Bar } from 'openalgo-charts'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { EngineWorkerClient, WorkerLike } from '@openalgo/openscript/worker-client'
import type { IRProgram } from '@openalgo/openscript'
import type { PerfStats } from '@openalgo/openscript'

/** Set by `beforeAll`; the mocked `getEngine` hands this to the host. */
let client: EngineWorkerClient | null = null

// The host resolves its engine through this module. Replacing it is what lets a
// node test drive the real host without Vite's `?worker` / `?url` transforms.
vi.mock('./engine', () => ({
  getEngine: () => Promise.resolve(client as EngineWorkerClient),
}))

const { IndicatorHost } = await import('./indicator-host')

/** Every `session-outputs` the worker posts, teed at the transport. */
interface Seen {
  sessionId: string
  scope: 'full' | 'update'
  perf: PerfStats
}
const seen: Seen[] = []

function irOf(src: string): IRProgram {
  const r = compile(`indicator("x")\n${src}`)
  if (!r.ir) throw new Error(`compile failed: ${JSON.stringify(r.diagnostics.map((d) => d.code))}`)
  return r.ir
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

/** Let the synchronous transport's promise chain settle. */
async function flush(): Promise<void> {
  for (let i = 0; i < 20; i++) await Promise.resolve()
}

beforeAll(async () => {
  // The built worker entry binds `self.onmessage` AT IMPORT TIME, so the scope
  // stub has to exist before the dynamic import below.
  const scope: {
    onmessage: ((e: { data: unknown }) => void) | null
    postMessage: (m: unknown) => void
  } = { onmessage: null, postMessage: () => {} }
  vi.stubGlobal('self', scope)

  // Resolve the LINKED PACKAGE's built artifacts — this is what makes a stale
  // dist fail here instead of in the browser. `./package.json` is not in the
  // exports map, so resolve the wasm subpath (which is) and take its directory.
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
    const m = message as { type?: string; sessionId?: string; scope?: string; perf?: PerfStats }
    if (m.type === 'session-outputs' && m.perf) {
      seen.push({
        sessionId: m.sessionId as string,
        scope: m.scope as 'full' | 'update',
        perf: m.perf,
      })
    }
    transport.onmessage?.({ data: message })
  }

  const { EngineWorkerClient: Client } = await import('@openalgo/openscript/worker-client')
  client = new Client(transport)
  const wasm = readFileSync(join(distDir, 'oa_wasm.wasm'))
  await client.init(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength) as ArrayBuffer)
})

describe('Phase 5.1 activation through IndicatorHost', () => {
  it('an IR indicator seeds full then goes incremental; a builtin stays full', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    const data = bars(240)

    await host.setDataset(data, { symbol: 'X', exchange: 'NSE', interval: '1m' })

    const irId = await host.addIr(irOf('plot(ta.sma(close, 20) + close[1])'))
    const builtinId = await host.add('builtin.sma', { source: 'close', period: 5 })
    await flush()

    // The seed. Every session starts with a full recompute by design — `reset`
    // is never incremental, so this is the correct expectation, not a fallback.
    const irSeed = seen.filter((s) => s.sessionId === irId && s.scope === 'full')
    expect(irSeed.length).toBeGreaterThan(0)
    expect(irSeed[irSeed.length - 1]?.perf.recompute).toBe('full')

    seen.length = 0

    // A forming tick on the live bar — `isNew: false` is the shape the candle
    // builder emits between bar closes, and the one the 16 ms budget is about.
    const last = data[data.length - 1] as Bar
    host.onBar({ ...last, close: last.close + 0.25 }, false)
    await flush()

    const irUpdate = seen.find((s) => s.sessionId === irId && s.scope === 'update')
    expect(irUpdate, 'no update-scope outputs for the IR session').toBeDefined()
    // THE GATE: compiled OpenScript now takes the dirty-range path in the platform.
    expect(irUpdate?.perf.recompute).toBe('incremental')
    expect(irUpdate?.perf.fallbackReason).toBeUndefined()

    const builtinUpdate = seen.find((s) => s.sessionId === builtinId && s.scope === 'update')
    expect(builtinUpdate, 'no update-scope outputs for the builtin session').toBeDefined()
    // A registry builtin is imperative TypeScript with no analyzable IR graph, so
    // it stays full FOREVER. That is architectural, not an unfinished feature.
    expect(builtinUpdate?.perf.recompute).toBe('full')
    expect(builtinUpdate?.perf.fallbackReason).toBe('builtin-no-ir')

    host.dispose()
  })
})
