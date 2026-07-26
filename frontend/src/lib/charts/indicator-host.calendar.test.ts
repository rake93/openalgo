/**
 * G7 gap: the host must not discard the calendar the worker resolves.
 *
 * `calendarForInstrument` resolves a `CalendarResolution` once, on the
 * `create-session` response only (design 6.4) — never on update / set-inputs /
 * set-dataset. The design's rule is that a fallback must never be SILENT: an
 * unrecognized exchange still resolves a usable (IST) calendar so the chart
 * never breaks, but that guess must be observable, not indistinguishable from
 * a real mapping. This is what makes it observable on the platform side —
 * recorded on the host and warned to the console when it is not `'mapped'`.
 *
 * A second, independent rule: the persisted layout snapshot must stay
 * calendar-free (see `IndicatorSnapshotEntry`'s doc comment). A calendar in a
 * saved layout would freeze one instrument's day boundary into every chart
 * the layout is later restored onto.
 *
 * Follows the `indicator-host.settings.test.ts` / `indicator-host.snapshot.test.ts`
 * fake-engine harness rather than standing up a real worker: `./engine` is
 * mocked purely to satisfy `indicator-host.ts`'s module-scope `?url` import (see
 * those files' comments), and the engine actually driving the session is
 * installed directly on the host, bypassing `ensureEngine()`/`getEngine()`
 * entirely — the same reach-into-privates style `withEngine`/`withInstance`
 * already use there.
 */

import { describe, expect, it, vi } from 'vitest'
import { calendarForInstrument } from '@openalgo/openscript'
import type { CalendarResolution } from '@openalgo/openscript'

vi.mock('./engine', () => ({
  getEngine: () => Promise.reject(new Error('not needed — the engine is injected directly')),
}))

const { IndicatorHost } = await import('./indicator-host')

/** A minimal stand-in for the engine client's create-session contract. */
interface FakeEngine {
  disposeSession: (id: string) => Promise<void>
  createSession: (opts: unknown) => Promise<{ outputs: unknown[]; calendar?: CalendarResolution }>
}

/** Install a fake engine on a host without going through its real bootstrap
 *  (mirrors `indicator-host.settings.test.ts`'s helper of the same name). */
function withEngine(host: unknown, engine: FakeEngine): void {
  ;(host as { engine: FakeEngine }).engine = engine
}

/** Make the host believe a dataset is already loaded, so `add`/`addIr` take
 *  the `createSession` branch instead of skipping it. `setDataset` itself is
 *  not exercised here — the subject is calendar bookkeeping, not dataset load. */
function withCurrentKey(host: unknown, key: string): void {
  ;(host as { currentKey: string }).currentKey = key
}

const MAPPED = calendarForInstrument({ exchange: 'NSE', symbol: 'SBIN' })
const FALLBACK = calendarForInstrument({ exchange: 'NOT_A_REAL_EXCHANGE', symbol: 'X' })

function fakeEngine(calendar: CalendarResolution): FakeEngine {
  return {
    disposeSession: async () => {},
    createSession: async () => ({ outputs: [], calendar }),
  }
}

describe('IndicatorHost records the calendar resolved at create-session', () => {
  it('records a mapped resolution — semanticKey and provenance', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    withCurrentKey(host, 'k1')
    withEngine(host, fakeEngine(MAPPED))

    expect(host.calendarResolution()).toBeUndefined()
    await host.add('builtin.sma', { period: 5 })

    expect(host.calendarResolution()?.semanticKey).toBe(MAPPED.semanticKey)
    expect(host.calendarResolution()?.provenance).toBe('mapped')
    expect(host.calendarResolution()?.warningCode).toBeUndefined()
  })

  it('records a non-mapped resolution WITH its warningCode', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    withCurrentKey(host, 'k1')
    withEngine(host, fakeEngine(FALLBACK))

    await host.add('builtin.sma', { period: 5 })

    expect(host.calendarResolution()?.provenance).not.toBe('mapped')
    expect(host.calendarResolution()?.warningCode).toBe(FALLBACK.warningCode)
    expect(host.calendarResolution()?.warningCode).toBeDefined()
  })

  it('warns to the console when the resolution is not a real mapping', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    withCurrentKey(host, 'k1')
    withEngine(host, fakeEngine(FALLBACK))

    await host.add('builtin.sma', { period: 5 })

    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls[0]?.[0]).toContain(FALLBACK.warningCode)
    warn.mockRestore()
  })

  it('does not warn when the resolution is a real mapping', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    withCurrentKey(host, 'k1')
    withEngine(host, fakeEngine(MAPPED))

    await host.add('builtin.sma', { period: 5 })

    expect(warn).not.toHaveBeenCalled()
    warn.mockRestore()
  })
})

describe('the layout snapshot stays calendar-free', () => {
  it('carries no calendar field, however it is spelled', async () => {
    const host = new IndicatorHost({ onIndicators: () => {}, onError: () => {} })
    withCurrentKey(host, 'k1')
    withEngine(host, fakeEngine(FALLBACK))

    await host.add('builtin.sma', { period: 5 })
    expect(host.calendarResolution()).toBeDefined() // sanity: recording did happen

    const serialized = JSON.stringify(host.snapshot())
    expect(serialized).not.toContain('calendar')
    expect(serialized).not.toContain('semanticKey')
    expect(serialized).not.toContain('utcOffset')
  })
})
