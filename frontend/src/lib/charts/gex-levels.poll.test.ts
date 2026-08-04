import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GEXLevelsResponse } from '@/api/gex'
import { GexLevelsManager } from './gex-levels'

function make(
  instrument: { underlying: string; exchange: string } | null = {
    underlying: 'NIFTY',
    exchange: 'NFO',
  }
) {
  const fetchLevels = vi.fn().mockResolvedValue({ status: 'success', call_wall: 24800 })
  const onSnapshot = vi.fn()
  const manager = new GexLevelsManager({
    onChange: vi.fn(),
    instrument: () => instrument,
    fetchLevels,
    onSnapshot,
  })
  return { manager, fetchLevels, onSnapshot }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('GexLevelsManager refresh loop', () => {
  it('does not fetch while the study is off', () => {
    const { fetchLevels } = make()
    vi.advanceTimersByTime(120_000)
    expect(fetchLevels).not.toHaveBeenCalled()
  })

  it('fetches immediately on enable rather than waiting a full interval', () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true })
    expect(fetchLevels).toHaveBeenCalledTimes(1)
  })

  it('refetches on the configured interval', async () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true, refreshSeconds: 30 })
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchLevels).toHaveBeenCalledTimes(2)
  })

  it('stops fetching when disabled', async () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true })
    manager.setConfig({ enabled: false })
    await vi.advanceTimersByTimeAsync(300_000)
    expect(fetchLevels).toHaveBeenCalledTimes(1)
  })

  it('never starts for an instrument with no option chain', () => {
    const { manager, fetchLevels } = make(null)
    manager.setConfig({ enabled: true })
    expect(fetchLevels).not.toHaveBeenCalled()
  })

  it('clears its timer on dispose', async () => {
    const { manager, fetchLevels } = make()
    manager.setConfig({ enabled: true })
    manager.dispose()
    await vi.advanceTimersByTimeAsync(300_000)
    expect(fetchLevels).toHaveBeenCalledTimes(1)
  })

  it('keeps the last good snapshot when a refresh fails', async () => {
    const fetchLevels = vi
      .fn()
      .mockResolvedValueOnce({ status: 'success', call_wall: 24800 })
      .mockRejectedValueOnce(new Error('broker down'))
    const manager = new GexLevelsManager({
      onChange: vi.fn(),
      instrument: () => ({ underlying: 'NIFTY', exchange: 'NFO' }),
      fetchLevels,
      onSnapshot: vi.fn(),
    })

    manager.setConfig({ enabled: true, refreshSeconds: 30 })
    await vi.advanceTimersByTimeAsync(30_000)

    expect(manager.lastSnapshot?.call_wall).toBe(24800)
    expect(manager.stale).toBe(true)
  })

  it('discards a response issued for a previous instrument', async () => {
    let instrument = { underlying: 'NIFTY', exchange: 'NFO' }
    const onSnapshot = vi.fn()
    const fetchLevels = vi.fn().mockResolvedValue({ status: 'success', call_wall: 24800 })
    const manager = new GexLevelsManager({
      onChange: vi.fn(),
      instrument: () => instrument,
      fetchLevels,
      onSnapshot,
    })

    manager.setConfig({ enabled: true })
    instrument = { underlying: 'BANKNIFTY', exchange: 'NFO' }
    manager.instrumentChanged()
    await vi.runOnlyPendingTimersAsync()

    // The in-flight NIFTY response must never be published as BANKNIFTY's.
    expect(manager.lastSnapshot === null || fetchLevels.mock.calls.length > 1).toBe(true)
  })

  it('never publishes a late response for an instrument the user has already left', async () => {
    // The test above is weak: since the mock resolves both requests
    // immediately, it cannot distinguish "the old response was discarded"
    // from "the old response never had a chance to arrive". This one holds
    // the first (NIFTY) request open with a manually-resolved promise, lets
    // the second (BANKNIFTY) request resolve and publish first, and only then
    // resolves the stale NIFTY request - proving it is dropped on arrival
    // rather than merely never completing in time.
    let instrument = { underlying: 'NIFTY', exchange: 'NFO' }
    const onSnapshot = vi.fn()
    let resolveNifty: (value: GEXLevelsResponse) => void = () => {}
    const niftyPromise = new Promise<GEXLevelsResponse>((resolve) => {
      resolveNifty = resolve
    })
    const fetchLevels = vi
      .fn()
      .mockReturnValueOnce(niftyPromise)
      .mockResolvedValue({ status: 'success', call_wall: 51000 })
    const manager = new GexLevelsManager({
      onChange: vi.fn(),
      instrument: () => instrument,
      fetchLevels,
      onSnapshot,
    })

    // Issues the NIFTY request; it stays pending.
    manager.setConfig({ enabled: true })
    instrument = { underlying: 'BANKNIFTY', exchange: 'NFO' }
    // Issues the BANKNIFTY request under the new epoch, which resolves right away.
    manager.instrumentChanged()
    await vi.runOnlyPendingTimersAsync()

    expect(manager.lastSnapshot?.call_wall).toBe(51000)
    onSnapshot.mockClear()

    // The slow NIFTY response finally lands, under the old epoch.
    resolveNifty({ status: 'success', call_wall: 24800 })
    await Promise.resolve()
    await Promise.resolve()

    expect(onSnapshot).not.toHaveBeenCalled()
    expect(manager.lastSnapshot?.call_wall).toBe(51000)
  })
})
