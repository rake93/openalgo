/**
 * Host retention of run telemetry (M8 §13.3), through the real worker.
 *
 * `perf` crossed the worker boundary on every run and the host dropped it — it
 * appeared zero times in production `indicator-host.ts`. This asserts it is kept,
 * and that the badge predicate reads correctly against telemetry the ENGINE
 * actually produced rather than hand-built fixtures.
 *
 * Same harness as `indicator-host.inspect.test.ts`: real host, real client, BUILT
 * worker entry, so a stale dist fails here rather than in the browser (trap T1).
 */

import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import type { Bar } from 'openalgo-charts'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { EngineWorkerClient, WorkerLike } from '@openalgo/openscript/worker-client'
import type { IRProgram } from '@openalgo/openscript'
import { isSilentFallback } from './indicator-profile'

let client: EngineWorkerClient | null = null

vi.mock('./engine', () => ({
  getEngine: () => Promise.resolve(client as EngineWorkerClient),
}))

const { IndicatorHost } = await import('./indicator-host')

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

async function flush(): Promise<void> {
  for (let i = 0; i < 20; i++) await Promise.resolve()
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
})

describe('IndicatorHost profile retention', () => {
  it('keeps the seed run telemetry the host used to discard', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    const data = bars(240)
    await host.setDataset(data, { symbol: 'X', exchange: 'NSE', interval: '1m' })
    const id = await host.addIr(irOf('plot(ta.ema(close, 10))'))
    await flush()

    const profile = host.lastProfile(id)

    expect(profile).toBeDefined()
    expect(profile?.scope).toBe('full')
    expect(profile?.perf.recompute).toBe('full')
    expect(profile?.perf.bars).toBe(240)
    expect(profile?.perf.peakBytes).toBeGreaterThan(0)
    // A seed is never incremental — it must not be badged.
    expect(isSilentFallback(profile!)).toBe(false)
  })

  it('an IR indicator goes incremental on a tick and is not flagged', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    const data = bars(240)
    await host.setDataset(data, { symbol: 'X', exchange: 'NSE', interval: '1m' })
    const id = await host.addIr(irOf('plot(ta.ema(close, 10))'))
    await flush()

    const last = data[data.length - 1] as Bar
    host.onBar({ ...last, close: last.close + 0.25 }, false)
    await flush()

    const profile = host.lastProfile(id)
    expect(profile?.scope).toBe('update')
    expect(profile?.perf.recompute).toBe('incremental')
    expect(isSilentFallback(profile!)).toBe(false)
  })

  it('a registry builtin stays full on a tick and is still NOT flagged', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    const data = bars(240)
    await host.setDataset(data, { symbol: 'X', exchange: 'NSE', interval: '1m' })
    const id = await host.add('builtin.sma', { source: 'close', period: 5 })
    await flush()

    const last = data[data.length - 1] as Bar
    host.onBar({ ...last, close: last.close + 0.25 }, false)
    await flush()

    const profile = host.lastProfile(id)
    // Non-vacuity: the run really IS full with the builtin reason, and the
    // predicate must still return false. This is the case a naive badge breaks on.
    expect(profile?.scope).toBe('update')
    expect(profile?.perf.recompute).toBe('full')
    expect(profile?.perf.fallbackReason).toBe('builtin-no-ir')
    expect(isSilentFallback(profile!)).toBe(false)
  })

  it('retains the drawing churn a structural tick produces (M2)', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    const data = bars(240)
    await host.setDataset(data, { symbol: 'X', exchange: 'NSE', interval: '1m' })
    // Spawns a level on EVERY bar with a small retention cap, so a confirmed
    // append MUST add a new object and evict the oldest — churn produced by the
    // real worker, not a hand-built fixture.
    const id = await host.addIr(irOf('plotlevel(close > 0, close, max_kept=3)'))
    await flush()

    // The seed is the diff BASELINE — its whole object list is not churn.
    expect(host.lastProfile(id)?.drawings).toBeUndefined()

    const last = data[data.length - 1] as Bar
    host.onBar({ ...last, time: (last.time as number) + 60, close: last.close + 0.25 }, true)
    await flush()

    const profile = host.lastProfile(id)
    expect(profile?.scope).toBe('update')
    const churn = profile?.drawings
    expect(churn).toBeDefined()
    // Non-vacuity: the append really did change the list structurally.
    expect(churn!.added).toBeGreaterThan(0)
  })

  it('returns nothing for an instance it does not have', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })

    expect(host.lastProfile('nope')).toBeUndefined()
  })
})
