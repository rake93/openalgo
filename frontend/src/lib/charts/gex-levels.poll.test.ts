import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GEXLevelsResponse } from '@/api/gex'
import { GexLevelsManager } from './gex-levels'
import { GexLevelsPrimitive } from './gex-levels-primitive'

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

describe('GexLevelsManager primitive lifecycle', () => {
  function chartDouble() {
    return { addPrimitive: vi.fn(), removePrimitive: vi.fn() }
  }

  it('adds the primitive when the study is enabled', () => {
    const chart = chartDouble()
    const { manager } = make()
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true })
    expect(chart.addPrimitive).toHaveBeenCalledTimes(1)
  })

  it('removes the primitive when the study is disabled', () => {
    const chart = chartDouble()
    const { manager } = make()
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true })
    manager.setConfig({ enabled: false })
    expect(chart.removePrimitive).toHaveBeenCalledTimes(1)
  })

  it('does not add the primitive twice for repeated enables', () => {
    const chart = chartDouble()
    const { manager } = make()
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true })
    manager.setConfig({ enabled: true })
    expect(chart.addPrimitive).toHaveBeenCalledTimes(1)
  })

  it('re-adds to a rebuilt chart without removing from the destroyed one', () => {
    const first = chartDouble()
    const second = chartDouble()
    const { manager } = make()
    manager.attachChart(first as never)
    manager.setConfig({ enabled: true })
    manager.attachChart(second as never)
    expect(second.addPrimitive).toHaveBeenCalledTimes(1)
    // The old chart is already destroyed - calling into it would throw.
    expect(first.removePrimitive).not.toHaveBeenCalled()
  })

  it('survives a chart that throws on removePrimitive', () => {
    const chart = {
      addPrimitive: vi.fn(),
      removePrimitive: vi.fn(() => {
        throw new Error('chart already disposed')
      }),
    }
    const { manager } = make()
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true })
    expect(() => manager.setConfig({ enabled: false })).not.toThrow()
  })

  it('pushes a snapshot held before attachChart into the freshly created primitive', async () => {
    const chart = chartDouble()
    const { manager } = make()
    const setDataSpy = vi.spyOn(GexLevelsPrimitive.prototype, 'setData')

    // Enable and let the fetch resolve with no chart attached yet - e.g. the
    // study was on before the chart finished mounting, or survived a rebuild
    // that raced the poll response.
    manager.setConfig({ enabled: true })
    await Promise.resolve()
    await Promise.resolve()
    expect(manager.lastSnapshot).not.toBeNull()

    manager.attachChart(chart as never)

    // The primitive did not exist when the snapshot arrived, so the only way
    // it can show it without waiting out a full refresh interval is if
    // attachChart pushes the held snapshot into the freshly created primitive.
    expect(setDataSpy).toHaveBeenCalledWith(manager.lastSnapshot)
    setDataSpy.mockRestore()
  })

  it('applies the volume profile inset to the column', () => {
    const chart = chartDouble()
    const setOptionsSpy = vi.spyOn(GexLevelsPrimitive.prototype, 'setOptions')
    const manager = new GexLevelsManager({
      onChange: vi.fn(),
      instrument: () => ({ underlying: 'NIFTY', exchange: 'NFO' }),
      fetchLevels: vi.fn().mockResolvedValue({ status: 'success' }),
      volumeProfileWidthOnSide: (side) => (side === 'right' ? 150 : 0),
    })
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true, side: 'right' })
    const primitive = chart.addPrimitive.mock.calls[0][0]
    // The constructed primitive is a real GexLevelsPrimitive, and syncPrimitive
    // always re-pushes options after creation - so the setOptions spy is the
    // honest way to assert the inset actually reached the primitive, rather
    // than just checking that some object was returned.
    expect(primitive).toBeInstanceOf(GexLevelsPrimitive)
    expect(setOptionsSpy).toHaveBeenCalledWith(expect.objectContaining({ columnInset: 150 }))
    setOptionsSpy.mockRestore()
  })

  it('updates the inset on the existing primitive when the side flips', () => {
    const chart = chartDouble()
    const setOptionsSpy = vi.spyOn(GexLevelsPrimitive.prototype, 'setOptions')
    const manager = new GexLevelsManager({
      onChange: vi.fn(),
      instrument: () => ({ underlying: 'NIFTY', exchange: 'NFO' }),
      fetchLevels: vi.fn().mockResolvedValue({ status: 'success' }),
      volumeProfileWidthOnSide: (side) => (side === 'right' ? 150 : 0),
    })
    manager.attachChart(chart as never)
    manager.setConfig({ enabled: true, side: 'right' })
    setOptionsSpy.mockClear()
    manager.setConfig({ side: 'left' })
    expect(setOptionsSpy).toHaveBeenLastCalledWith(expect.objectContaining({ columnInset: 0 }))
    setOptionsSpy.mockRestore()
  })

  it('propagates the metric setting to the primitive - a select that updates state but never reaches primitiveOptions() would pass every other test while doing nothing on screen', () => {
    const chart = chartDouble()
    const setOptionsSpy = vi.spyOn(GexLevelsPrimitive.prototype, 'setOptions')
    const { manager } = make()
    manager.attachChart(chart as never)

    // Default (gamma) must reach the primitive on creation, not just live in
    // manager.config.
    manager.setConfig({ enabled: true })
    expect(setOptionsSpy).toHaveBeenLastCalledWith(expect.objectContaining({ metric: 'gamma' }))

    // And an explicit change to delta must reach it too.
    manager.setConfig({ metric: 'delta' })
    expect(setOptionsSpy).toHaveBeenLastCalledWith(expect.objectContaining({ metric: 'delta' }))

    setOptionsSpy.mockRestore()
  })
})
