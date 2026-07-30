/**
 * `i`-to-pin for the series inspector (M8, roadmap Phase 2 §13.2).
 *
 * Shared by /charts and the script editor so the gesture and its guards cannot
 * drift apart between the two surfaces that offer it.
 *
 * WHY A PIN AND NOT A HOVER PANEL. The data window follows the crosshair, so a
 * control rendered in it is unreachable by definition: moving the pointer to
 * the control drags the crosshair onto a different bar, and the panel would
 * answer about a bar the user was not looking at. `i` captures whatever is under
 * the pointer at that instant, and the panel then stays put.
 */

import { useEffect, useRef, useState } from 'react'
import type { CrosshairData } from './workspace'
import type { DataWindowRow } from './indicator-host'

/** The bar an inspector panel is pinned to. */
export interface PinnedBar {
  index: number
  time: number | null
  rows: DataWindowRow[]
}

export interface InspectorPin {
  pinned: PinnedBar | null
  setPinned: (bar: PinnedBar | null) => void
}

export function useInspectorPin(crosshair: CrosshairData | null): InspectorPin {
  const [pinned, setPinned] = useState<PinnedBar | null>(null)

  /**
   * The listener is bound once, so it must not close over `crosshair` — it would
   * capture the value from the first render and pin a bar the pointer left long
   * ago. Synced after every commit instead, which is always before the next key
   * event can be delivered.
   */
  const latest = useRef<CrosshairData | null>(crosshair)
  useEffect(() => {
    latest.current = crosshair
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      // Both surfaces put a text editor or a symbol search beside the chart.
      // Without this, `i` is silently stolen from whatever the user is typing.
      const typing =
        el?.isContentEditable === true ||
        /^(INPUT|TEXTAREA|SELECT)$/.test(el?.tagName ?? '') ||
        el?.closest?.('.cm-editor') != null
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return

      if (e.key === 'Escape') {
        setPinned(null)
        return
      }
      if (e.key !== 'i' && e.key !== 'I') return

      const c = latest.current
      if (c) setPinned({ index: c.index, time: c.time, rows: c.rows })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return { pinned, setPinned }
}
