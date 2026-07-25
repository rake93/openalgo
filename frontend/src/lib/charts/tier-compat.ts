/**
 * Cross-tier type bridging for openalgo-charts.
 *
 * The package ships each lazy tier (`/draw`, `/profile`, `/trade`,
 * `/transform`, `/indicators`) as its own rollup bundle, and each tier's
 * generated `.d.ts` *re-declares* the shared classes it references — `Chart`,
 * `TimeScale`, `PriceScale`, `DataLayer`, `PrimitiveRenderContext` — instead of
 * importing them from the package root.
 *
 * At **runtime** there is exactly one of each: the tiers import shared state
 * from the package entry (that is what makes `chart.addSeries('point-figure')`
 * resolve after importing the transform tier), so a `Chart` handed to
 * `DrawingController` really is the same object the base bundle created.
 *
 * At **type level** TypeScript compares those re-declarations nominally,
 * because they carry `private` fields — so `Chart` from `openalgo-charts` and
 * `Chart` from `openalgo-charts/draw` are reported as different types, and a
 * tier primitive is rejected by `chart.addPrimitive` even though it is exactly
 * what that method wants.
 *
 * The bridge is unavoidable until the library's tier builds mark the root as
 * external for type generation too. Keeping it in one file means the casts are
 * explained once, are easy to find, and are trivial to delete when a future
 * openalgo-charts release fixes the declarations.
 */

import type { Chart, IPrimitive } from 'openalgo-charts'

/**
 * A tier-declared `Chart` (from `openalgo-charts/draw` and friends), viewed as
 * whatever that tier's constructor expects. Runtime-identical to the base
 * `Chart`; see the module comment.
 */
export function asTierChart<T>(chart: Chart): T {
  return chart as unknown as T
}

/**
 * A primitive built by a lazy tier (`VolumeProfile`, `MarketProfile`,
 * `Footprint`, `DomLadder`), viewed as the base tier's `IPrimitive` so it can
 * be passed to `chart.addPrimitive` / `chart.removePrimitive`.
 */
export function asPrimitive(primitive: object): IPrimitive {
  return primitive as unknown as IPrimitive
}
