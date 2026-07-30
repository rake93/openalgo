/**
 * M8 series inspector, through the PLATFORM stack.
 *
 * Same harness as `indicator-host.activation.test.ts`: the real `IndicatorHost`,
 * the real `EngineWorkerClient`, and the BUILT worker entry resolved through
 * `@openalgo/openscript/worker` — so a stale `dist/` fails here rather than
 * silently serving an inspector-less worker to the browser (trap T1).
 *
 * The load-bearing case is the `na` one. An indicator that plots nothing at the
 * crosshair bar is exactly what the inspector exists to explain, and until this
 * change the data window dropped those rows entirely — leaving the user nothing
 * to click on precisely when they needed it.
 *
 * See openalgo-openscript/docs/openscript-phase2-series-inspector-design.md.
 */

import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import type { Bar } from 'openalgo-charts'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import type { EngineWorkerClient, WorkerLike } from '@openalgo/openscript/worker-client'
import type { IRProgram } from '@openalgo/openscript'

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

/** A host with one IR indicator seeded over `n` bars. */
async function hosted(src: string, n = 240) {
  const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
  await host.setDataset(bars(n), { symbol: 'X', exchange: 'NSE', interval: '1m' })
  const instanceId = await host.addIr(irOf(src))
  await flush()
  return { host, instanceId }
}

describe('data window — na rows', () => {
  it('keeps a row for an output that is na at the bar, so it can be inspected', async () => {
    const { host } = await hosted('plot(ta.sma(close, 20), "SMA")')

    // Bar 5 is inside the sma's warmup: the series has no value there.
    const rows = host.valuesAtIndex(5)

    expect(rows).toHaveLength(1)
    const value = rows[0]?.values[0]
    expect(value?.title).toBe('SMA')
    expect(value?.value).toBeNull()
  })

  it('still reports a real number where there is one', async () => {
    const { host } = await hosted('plot(ta.sma(close, 20), "SMA")')

    const value = host.valuesAtIndex(200)[0]?.values[0]

    expect(typeof value?.value).toBe('number')
    expect(Number.isNaN(value?.value)).toBe(false)
  })
})

describe('IndicatorHost.inspect', () => {
  it('answers with the value at the bar for a live IR indicator', async () => {
    const { host, instanceId } = await hosted('plot(close, "C")')
    const outputId = host.valuesAtIndex(100)[0]?.values[0]?.id as string

    const answer = await host.inspect(instanceId, outputId, 100)

    expect(answer?.result.ok).toBe(true)
    if (!answer?.result.ok) return
    expect(typeof answer.result.roots[0]?.value).toBe('number')
  })

  it('explains an na bar: the origin is the warmup of the operator underneath', async () => {
    const { host, instanceId } = await hosted('plot(ta.sma(close, 20) * 2 + 1, "S")')
    const outputId = host.valuesAtIndex(5)[0]?.values[0]?.id as string

    const answer = await host.inspect(instanceId, outputId, 5)

    expect(answer?.result.ok).toBe(true)
    if (!answer?.result.ok) return
    const root = answer.result.roots[0]
    expect(root?.value).toBeNull()
    expect(root?.origins[0]?.reason).toBe('warmup')
    // Non-vacuity: the origin must be BELOW the root, or the walk did nothing.
    expect(root?.origins[0]?.nodeId).not.toBe(root?.nodeId)
  })

  it('carries the epoch the answer was computed under', async () => {
    const { host, instanceId } = await hosted('plot(close, "C")')
    const outputId = host.valuesAtIndex(100)[0]?.values[0]?.id as string

    const answer = await host.inspect(instanceId, outputId, 100)

    expect(typeof answer?.epoch).toBe('number')
  })

  it('returns null for an output id it cannot map, rather than inspecting output 0', async () => {
    const { host, instanceId } = await hosted('plot(close, "C")')

    expect(await host.inspect(instanceId, 'not-an-output-id', 100)).toBeNull()
  })

  it('returns null for an unknown instance', async () => {
    const { host } = await hosted('plot(close, "C")')

    expect(await host.inspect('nope', 'out_0', 100)).toBeNull()
  })
})
