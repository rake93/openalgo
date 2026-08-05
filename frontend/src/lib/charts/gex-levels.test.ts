import { describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_GEX_LEVELS_SETTINGS,
  type GexLevelsCallbacks,
  GexLevelsManager,
} from './gex-levels'

function make(overrides: Partial<GexLevelsCallbacks> = {}) {
  const onChange = vi.fn()
  const fetchLevels = vi.fn().mockResolvedValue({ status: 'success' })
  const manager = new GexLevelsManager({
    onChange,
    fetchLevels,
    instrument: () => ({ underlying: 'NIFTY', exchange: 'NFO' }),
    ...overrides,
  })
  return { manager, onChange, fetchLevels }
}

describe('GexLevelsManager settings', () => {
  it('starts disabled, like every other study', () => {
    expect(DEFAULT_GEX_LEVELS_SETTINGS.enabled).toBe(false)
  })

  it('defaults to open-interest weighting', () => {
    expect(DEFAULT_GEX_LEVELS_SETTINGS.weightBy).toBe('oi')
  })

  it('defaults the strike bars on', () => {
    expect(DEFAULT_GEX_LEVELS_SETTINGS.showBars).toBe(true)
  })

  it('applies a patch and notifies', () => {
    const { manager, onChange } = make()
    manager.setConfig({ weightBy: 'volume' })
    expect(manager.config.weightBy).toBe('volume')
    expect(onChange).toHaveBeenCalled()
  })

  it('round-trips through snapshot and restore', () => {
    const { manager } = make()
    manager.setConfig({ enabled: true, weightBy: 'volume', showBars: false, refreshSeconds: 30 })
    const snap = manager.snapshot()

    const { manager: restored } = make()
    restored.restore(snap)
    expect(restored.config).toEqual(manager.config)
  })

  it('carries the card offset through a snapshot round-trip', () => {
    const { manager } = make()
    expect(manager.config.cardOffset).toEqual({ x: 0, y: 0 })

    manager.setConfig({ cardOffset: { x: -240, y: 120 } })
    const { manager: restored } = make()
    restored.restore(manager.snapshot())
    expect(restored.config.cardOffset).toEqual({ x: -240, y: 120 })
  })

  it('fills the card offset from the defaults for a layout saved before it existed', () => {
    const { manager } = make()
    manager.restore({ enabled: true })
    expect(manager.config.cardOffset).toEqual({ x: 0, y: 0 })
  })

  it('fills unknown keys from the defaults when restoring a partial snapshot', () => {
    const { manager } = make()
    manager.restore({ enabled: true })
    expect(manager.config.enabled).toBe(true)
    expect(manager.config.weightBy).toBe('oi')
  })

  it('does not mutate its settings through the config getter', () => {
    const { manager } = make()
    const config = manager.config
    config.weightBy = 'volume'
    expect(manager.config.weightBy).toBe('oi')
  })

  it('defaults to gamma and carries the metric through a snapshot round-trip', () => {
    const { manager } = make()
    expect(DEFAULT_GEX_LEVELS_SETTINGS.metric).toBe('gamma')
    expect(manager.config.metric).toBe('gamma')

    manager.setConfig({ metric: 'delta' })
    expect(manager.config.metric).toBe('delta')

    const { manager: restored } = make()
    restored.restore(manager.snapshot())
    expect(restored.config.metric).toBe('delta')
  })

  it('fills the metric from the defaults when restoring a snapshot saved before it existed', () => {
    const { manager } = make()
    manager.restore({ enabled: true })
    expect(manager.config.metric).toBe('gamma')
  })
})
