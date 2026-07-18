/**
 * Indicator-engine worker bootstrap (one shared worker + WASM init per app).
 *
 * The worker entry and wasm binary come from the local @openalgo/indicator-engine
 * package; Vite bundles the worker via the `?worker` suffix and serves the wasm
 * as an asset URL. Excluded from optimizeDeps so both survive pre-bundling.
 */

import { EngineWorkerClient, type WorkerLike } from '@openalgo/indicator-engine/worker-client'
import EngineWorker from '@openalgo/indicator-engine/worker?worker'
import wasmUrl from '@openalgo/indicator-engine/oa_wasm.wasm?url'

let clientPromise: Promise<EngineWorkerClient> | null = null

export function getEngine(): Promise<EngineWorkerClient> {
  if (!clientPromise) {
    clientPromise = (async () => {
      // DOM Worker's onmessage declares `this: Worker`; the client only uses
      // the postMessage/onmessage surface, so the structural cast is safe.
      const client = new EngineWorkerClient(new EngineWorker() as unknown as WorkerLike)
      await client.init(wasmUrl)
      return client
    })()
  }
  return clientPromise
}
