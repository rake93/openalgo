/**
 * Indicator-engine worker bootstrap (one shared worker + WASM init per app).
 *
 * The worker entry and wasm binary come from the local @openalgo/openscript
 * package; Vite bundles the worker via the `?worker` suffix and serves the wasm
 * as an asset URL. Excluded from optimizeDeps so both survive pre-bundling.
 */

import wasmUrl from '@openalgo/openscript/oa_wasm.wasm?url'
import EngineWorker from '@openalgo/openscript/worker?worker'
import { EngineWorkerClient, type WorkerLike } from '@openalgo/openscript/worker-client'

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
