/**
 * The rail catalogue must stay level with the drawing tier.
 *
 * `/charts` keeps its own copy of the tool groups so it can regroup without
 * touching `/trading`, which means nothing structural stops the copy falling
 * behind the library. That is exactly what happened before: the rail offered 18
 * of 43 registered tools, because tools kept being added to `openalgo-charts`
 * and the hand-written list did not follow. These tests are what makes the
 * duplication safe — they fail the moment the two disagree.
 */

import { registeredDrawingTools } from 'openalgo-charts/draw'
import { describe, expect, it } from 'vitest'
import { DRAW_GROUPS, drawToolIcon } from './drawTools'

/** Every tool the rail offers, flattened, in rail order. */
const railTools = DRAW_GROUPS.flatMap((g) => g.sections.flatMap((s) => s.tools))
const railIds = railTools.map((t) => t.id).filter((id): id is string => id !== null)
const registeredIds = registeredDrawingTools().map((t) => t.id)

describe('drawing rail catalogue', () => {
  it('offers every tool the drawing tier registers', () => {
    const missing = registeredIds.filter((id) => !railIds.includes(id))
    expect(
      missing,
      `tools registered but unreachable from the rail: ${missing.join(', ')}`
    ).toEqual([])
  })

  it('offers nothing the drawing tier does not register', () => {
    const unknown = railIds.filter((id) => !registeredIds.includes(id))
    expect(unknown, `rail names tools the tier cannot arm: ${unknown.join(', ')}`).toEqual([])
  })

  it('lists each tool exactly once, so no tool has two buttons', () => {
    const seen = new Set<string>()
    const duplicated: string[] = []
    for (const id of railIds) {
      if (seen.has(id)) duplicated.push(id)
      seen.add(id)
    }
    expect(duplicated).toEqual([])
  })

  it('gives every tool a label and a glyph', () => {
    for (const t of railTools) {
      expect(t.label.trim().length, `${t.id} has no label`).toBeGreaterThan(0)
      // A missing glyph falls through the switch to null and renders an empty
      // button, which reads as a broken tool rather than a missing icon.
      expect(drawToolIcon(t.iconKey), `${t.id} (${t.iconKey}) has no glyph`).not.toBeNull()
    }
  })

  it('gives every group a label and a glyph for its rail button', () => {
    expect(DRAW_GROUPS.length).toBeGreaterThan(0)
    for (const g of DRAW_GROUPS) {
      expect(g.label.trim().length, `group ${g.key} has no label`).toBeGreaterThan(0)
      expect(drawToolIcon(g.iconKey), `group ${g.key} has no glyph`).not.toBeNull()
      expect(g.sections.flatMap((s) => s.tools).length, `group ${g.key} is empty`).toBeGreaterThan(
        0
      )
    }
  })

  it('keys groups uniquely, since the key stores that group’s last-used tool', () => {
    const keys = DRAW_GROUPS.map((g) => g.key)
    expect(new Set(keys).size).toBe(keys.length)
  })
})
