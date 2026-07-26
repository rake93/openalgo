/**
 * Runtime consumability of server-produced IR.
 *
 * The reopen contract says a saved indicator is rebuilt from the SERVER's
 * `compiled_ir` and never recompiled in the browser. Two guards already sit
 * under that: the diagnostic conformance suite (the two front ends agree on
 * what is an error) and the IR conformance suite (they agree on the graph they
 * build). Neither proves the last step — that the TS runtime will actually RUN
 * what the Python compiler emitted and produce the same numbers.
 *
 * That gap is not theoretical. `ProgramRef` types `ir` as `IRProgram`, but the
 * type is erased: `create-session` executes whatever JSON it is handed. A
 * structural mismatch between the two compilers would be accepted silently and
 * render a subtly different indicator.
 *
 * So this test:
 *   1. compiles each source with the SERVER's Python compiler, by spawning it —
 *      the IR here is genuinely Python-produced, not reconstructed JSON and not
 *      TS compiler output;
 *   2. runs it through the BUILT worker (`@openalgo/openscript/worker`, i.e.
 *      `dist/`), which is the same artifact the browser loads;
 *   3. runs the TS-compiled equivalent through the same worker;
 *   4. requires identical outputs, value for value, over historical bars.
 *
 * Coverage is chosen against the contract rather than for convenience: inputs
 * (including a non-default value), history references, and TA kernels each
 * appear. `request.security`/HTF is absent deliberately — the Python port does
 * not implement it, which `test_openscript_ir_conformance.py` pins as a
 * recorded compiler asymmetry, so there is no server IR for an HTF script to
 * consume.
 */

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Bar } from 'openalgo-charts'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { compile } from '@openalgo/openscript/compiler'
import { datasetFromBars, toDatasetBuffers } from '@openalgo/openscript'
import type { IndicatorOutput, IRProgram } from '@openalgo/openscript'
import type { EngineWorkerClient, WorkerLike } from '@openalgo/openscript/worker-client'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..')

/**
 * Sources exercised on both compilers. Each names the contract element it is
 * here for; a script that covers nothing new is not worth the worker round
 * trip.
 */
const CASES: { name: string; source: string; inputs?: Record<string, unknown> }[] = [
  {
    name: 'ta-kernel',
    source: 'indicator("RSI", overlay=false)\nplot(ta.rsi(close, 14), "RSI")\n',
  },
  {
    name: 'history-reference',
    // `close[1]` and a two-bar-back offset — the `hist` node the incremental
    // path treats specially.
    source:
      'indicator("Hist", overlay=true)\n' +
      'plot(close - close[1], "Delta")\n' +
      'plot(ta.sma(close[2], 5), "Lagged")\n',
  },
  {
    name: 'declared-inputs',
    source:
      'indicator("Inputs", overlay=false)\n' +
      'len = input.int(14, "Length")\n' +
      'mult = input.float(2.0, "Mult")\n' +
      'src = input.source(close, "Source")\n' +
      'plot(ta.sma(src, len) * mult, "Scaled")\n',
    // Deliberately NOT the declared defaults: input plumbing that silently fell
    // back to defaults would pass with them.
    inputs: { len: 30, mult: 3.5, src: 'high' },
  },
  {
    name: 'multi-output-and-crossover',
    source:
      'indicator("Cross", overlay=true)\n' +
      'f = ta.ema(close, 9)\n' +
      's = ta.ema(close, 21)\n' +
      'plot(f, "Fast")\n' +
      'plot(s, "Slow")\n' +
      'plotshape(ta.crossover(f, s), "Up", location=location.belowbar, shape=shape.triangleup, color=color.green)\n',
  },
  {
    name: 'recurrence',
    // `var` + `:=` lowers to a scan node — single-lane recurrence, the shape
    // most sensitive to a wiring difference between the two compilers.
    source:
      'indicator("Scan", overlay=false)\n' +
      'var acc = 0.0\n' +
      'acc := acc + (close > open ? 1.0 : -1.0)\n' +
      'plot(acc, "Accumulator")\n',
  },
]

let client: EngineWorkerClient | null = null
let pythonIR: Record<string, { ir: IRProgram | null; diagnostics: { code: string }[] }> = {}

function bars(n: number): Bar[] {
  const out: Bar[] = []
  let price = 100
  for (let i = 0; i < n; i++) {
    // Deterministic and non-monotonic: a flat or purely rising series hides
    // crossover, recurrence and comparison differences.
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

async function flush(): Promise<void> {
  for (let i = 0; i < 50; i++) await Promise.resolve()
}

function tsIR(source: string): IRProgram {
  const result = compile(source)
  if (!result.ir) {
    throw new Error(`TS compile failed: ${JSON.stringify(result.diagnostics.map((d) => d.code))}`)
  }
  return result.ir
}

beforeAll(async () => {
  // Compile every case with the server's own compiler, in one subprocess.
  const request = JSON.stringify(CASES.map(({ name, source }) => ({ name, source })))
  const stdout = execFileSync('uv', ['run', 'python', 'test/helpers/emit_python_ir.py'], {
    cwd: repoRoot,
    input: request,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  pythonIR = JSON.parse(stdout)

  // The built worker entry binds `self.onmessage` AT IMPORT TIME, so the scope
  // stub has to exist before the dynamic import below.
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
}, 120_000)

/** Run one IR to completion on a shared dataset and return its outputs. */
async function outputsFor(
  sessionId: string,
  datasetKey: string,
  ir: IRProgram,
  inputs: Record<string, unknown>
): Promise<IndicatorOutput[]> {
  const engine = client as EngineWorkerClient
  // `createSession` resolves with the session's initial full outputs, so the
  // result is read from the returned event rather than from the transport tee —
  // no ambiguity about which session's outputs these are.
  const event = await engine.createSession({
    sessionId,
    datasetKey,
    program: { kind: 'ir', ir },
    inputs,
    mode: 'historical',
    meta: { symbol: 'X', exchange: 'NSE', timeframe: '1m' },
  })
  await flush()
  return event.outputs
}

describe('server-compiled IR runs in the built worker', () => {
  const datasetKey = 'consumability'
  const data = bars(300)

  beforeAll(async () => {
    const engine = client as EngineWorkerClient
    await engine.setDataset(datasetKey, toDatasetBuffers(datasetFromBars(data)))
    await flush()
  })

  it('the Python compiler produced IR for every case', () => {
    for (const testCase of CASES) {
      const compiled = pythonIR[testCase.name]
      expect(compiled, `${testCase.name}: missing from the Python compiler output`).toBeDefined()
      expect(
        compiled?.ir,
        `${testCase.name}: server compile failed with ${JSON.stringify(compiled?.diagnostics.map((d) => d.code))}`
      ).not.toBeNull()
    }
  })

  for (const testCase of CASES) {
    it(`${testCase.name}: server IR yields the same outputs as browser IR`, async () => {
      const serverIR = pythonIR[testCase.name]?.ir as IRProgram
      const inputs = testCase.inputs ?? {}

      const fromServer = await outputsFor(`py-${testCase.name}`, datasetKey, serverIR, inputs)
      const fromBrowser = await outputsFor(
        `ts-${testCase.name}`,
        datasetKey,
        tsIR(testCase.source),
        inputs
      )

      expect(fromServer).toEqual(fromBrowser)
    })
  }

  it('the comparison would notice a different program', async () => {
    // Mutation proof. Without this, a bug that returned empty outputs for both
    // sessions would make every assertion above pass vacuously.
    const baseline = await outputsFor(
      'mutation-baseline',
      datasetKey,
      tsIR('indicator("M", overlay=false)\nplot(ta.sma(close, 5), "S")\n'),
      {}
    )
    const altered = await outputsFor(
      'mutation-altered',
      datasetKey,
      tsIR('indicator("M", overlay=false)\nplot(ta.sma(close, 9), "S")\n'),
      {}
    )
    expect(baseline).not.toEqual(altered)
    expect(baseline.length).toBeGreaterThan(0)
  })

  it('server IR carrying a non-default input actually uses it', async () => {
    // Guards the inputs case above: if the runtime ignored the supplied inputs
    // and fell back to declared defaults, both sides would still agree with
    // each other and prove nothing about input plumbing.
    const serverIR = pythonIR['declared-inputs']?.ir as IRProgram
    const atDefaults = await outputsFor('py-inputs-default', datasetKey, serverIR, {})
    const atOverrides = await outputsFor(
      'py-inputs-override',
      datasetKey,
      serverIR,
      CASES.find((c) => c.name === 'declared-inputs')?.inputs as Record<string, unknown>
    )
    expect(atDefaults).not.toEqual(atOverrides)
  })
})
