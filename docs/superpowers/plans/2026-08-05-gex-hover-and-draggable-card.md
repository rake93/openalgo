# GEX Levels: bar hover readout and a draggable card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hovering a strike bar shows that strike's numbers on the chart, and the GEX readout card can be dragged anywhere in the pane.

**Architecture:** The existing top-layer caption primitive grows into a full overlay: it gains the strike data, implements `hitTest` over the bar rows, and draws a readout for whichever row the library reports as hovered. The card becomes a pointer-dragged element whose offset persists with the study settings.

**Tech Stack:** React 19 + TypeScript, Vitest, Biome, the `openalgo-charts` primitive API.

**Follows:** [`2026-08-05-gex-delta-exposure.md`](2026-08-05-gex-delta-exposure.md), all eight tasks shipped.

---

## Why these two, and why this shape

**Hover.** The study currently shows aggregate numbers in the card and a wall label on two lines. The per-strike profile — the thing with the most information in it — is unreadable in detail: a trader can see one bar is longer than another but cannot read either value. This is also the only place the two metrics can be compared directly. Live measurement from the chain backing these screenshots: strike 24000 is **−1318 Cr gamma but +679 Cr delta**. Under one metric it is a put-dominant strike; under the other it is call-dominant. Toggling back and forth to discover that is exactly the work a hover readout removes.

**The library supports this properly.** `PrimitiveRenderContext` carries `hoverId` — the `externalId` of the primitive hit under the pointer — and `paintTop` passes the same context to top-layer primitives that `paintBase` passes to bottom ones. So a primitive can implement `hitTest`, and on the next frame read back whether it is the thing being hovered. Seven primitives in the library already do this.

**Why the top-layer primitive owns it.** The readout must not be painted over by candles, which rules out `GexLevelsPrimitive` (`zOrder: 'bottom'`). The caption primitive is already `zOrder: 'top'` and already positioned against the bar column through the shared `gexColumnAxisX`. Giving it the strike data is a real requirement now, not the scope creep it was correctly judged to be when it only needed a boolean.

**Panning is preserved.** `chart.ts` arms a drag only when a hit is `draggable: true` or has `cursor: 'ns-resize'` with a drag callback registered. A plain hit sets hover state and nothing else, so press-and-pan over the column keeps working.

**Card drag.** The card is plain absolutely-positioned DOM inside a `relative` container — no chart-library involvement. It also retires a known defect for free: on short panes (once indicator sub-panes are added) the card can still collide with the bottom caption, and a movable card lets the user resolve that instead of us hunting a position that is safe in every layout.

---

## File structure

| File | Responsibility |
| --- | --- |
| `frontend/src/lib/charts/gex-levels-primitive.ts` | **Modify.** The top-layer primitive gains strike data, `hitTest`, and the readout drawing. Pure geometry and formatting extracted for testing. |
| `frontend/src/lib/charts/gex-levels.ts` | **Modify.** Feed strikes to the overlay primitive; add and persist the card offset. |
| `frontend/src/components/charts/workspace/GexDashboard.tsx` | **Modify.** Pointer-drag handling and a drag handle. |
| `frontend/src/pages/charts/ChartWorkspace.tsx` | **Modify.** Pass the offset and the change callback. |
| `frontend/src/api/gex.ts` | Unchanged — the payload already carries everything the readout shows. |

---

## Task 1: Hit-testing the bar rows

**Files:**
- Modify: `frontend/src/lib/charts/gex-levels-primitive.ts`
- Test: `frontend/src/lib/charts/gex-levels-primitive.test.ts`

- [ ] **Step 1: Write the failing test**

Add a `describe('gexHitTestStrike', ...)` block. The function is pure geometry — which strike row, if any, sits under a device-pixel point:

```typescript
describe('gexHitTestStrike', () => {
  const bars = [
    { strike: 24_500, y: 100, length: 60, positive: true },
    { strike: 24_600, y: 200, length: 120, positive: true },
  ]

  it('returns the strike whose row band contains the point', () => {
    // rowHeight 40 means each band is +/-20px around its y.
    expect(gexHitTestStrike(bars, 40, 300, 120, 'right', 0, 290, 195)?.strike).toBe(24_600)
  })

  it('returns null above and below every band', () => {
    expect(gexHitTestStrike(bars, 40, 300, 120, 'right', 0, 290, 150)).toBeNull()
  })

  it('returns null outside the column horizontally', () => {
    // axisX for plotWidth 300, columnWidth 120, right side is 180; the column
    // spans 60..300. A point at x=20 is in the chart body, not the column.
    expect(gexHitTestStrike(bars, 40, 300, 120, 'right', 0, 20, 195)).toBeNull()
  })

  it('picks the nearer row when two bands touch', () => {
    const touching = [
      { strike: 24_500, y: 100, length: 60, positive: true },
      { strike: 24_600, y: 140, length: 60, positive: true },
    ]
    expect(gexHitTestStrike(touching, 40, 300, 120, 'right', 0, 290, 121)?.strike).toBe(24_600)
    expect(gexHitTestStrike(touching, 40, 300, 120, 'right', 0, 290, 119)?.strike).toBe(24_500)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/lib/charts/gex-levels-primitive.test.ts`
Expected: FAIL — `gexHitTestStrike is not defined`

- [ ] **Step 3: Implement**

Export a pure function taking the already-computed bar geometry, the row height, the same placement inputs `gexColumnAxisX` takes, and a point. It returns the nearest bar whose band contains the point, or null. Reuse `gexColumnAxisX` for the horizontal extent rather than recomputing it — the column spans from the axis to `columnWidth` on both sides, because negative bars extend the opposite way.

- [ ] **Step 4: Verify and commit**

Run the suite and `npx tsc --noEmit -p tsconfig.app.json`, then:

```bash
git add frontend/src/lib/charts/gex-levels-primitive.ts frontend/src/lib/charts/gex-levels-primitive.test.ts
git commit -m "feat(gex-levels): hit-test the strike bar rows"
```

---

## Task 2: The hover readout

**Files:**
- Modify: `frontend/src/lib/charts/gex-levels-primitive.ts`, `frontend/src/lib/charts/gex-levels.ts`
- Test: both test files

The overlay primitive gains the strikes, implements `IPrimitive.hitTest` (delegating to Task 1's function and returning `externalId` of the form `gex-strike-<strike>`), and in `draw()` reads `ctx.hoverId` to decide whether to draw the readout.

**What the readout shows — both metrics, always.** The whole point is comparing them:

```
24000
GEX  -1318.1 Cr
DEX   +679.4 Cr
Put wall
```

The active metric's row is emphasised; the other is shown dimmed so it reads as context rather than as the primary number. A wall marker line appears only when the strike is the Call Wall or Put Wall. Format figures with the same Cr/L/K convention the rest of the study uses — find the existing formatter rather than writing a second one.

**Positioning:** anchor the box to the hovered row's y, inset from the column on the side with more room, clamped so it never leaves the plot. Extract the box geometry as a pure function and test the clamping at the top and bottom edges.

**When no strike is hovered, or `hasBars` is false, draw nothing.** The existing `hasBars` gate already covers the no-chain case.

Full TDD steps, exact code and commit message: follow the pattern of Task 1 — write the pure geometry and formatting tests first, then the primitive wiring, then a `draw()`-level test using the recording-`ctx` fake already established in this file.

---

## Task 3: Draggable readout card

**Files:**
- Modify: `frontend/src/lib/charts/gex-levels.ts`, `frontend/src/components/charts/workspace/GexDashboard.tsx`, `frontend/src/pages/charts/ChartWorkspace.tsx`
- Test: `frontend/src/lib/charts/gex-levels.test.ts`, `frontend/src/components/charts/workspace/GexDashboard.test.tsx`

- [ ] `GexLevelsConfig` gains `cardOffset: { x: number; y: number }`, defaulting to `{ x: 0, y: 0 }`, persisted through the existing `snapshot()` / `restore()` path. Test the round-trip and the restore-from-an-older-snapshot fill, matching the tests already written for `metric`.

- [ ] The card renders at `right-2 top-2` **translated by** the offset, so the default position is unchanged and an offset of zero is indistinguishable from today.

- [ ] Dragging is on the card header only, not the whole card — the body carries a tooltip trigger and selectable numbers, and making all of it a drag surface would break both. Use pointer events with capture so a fast drag does not lose the element.

- [ ] **Clamp so the card cannot be dragged out of reach.** Keep at least the header's height and a reasonable width inside the container bounds. Test the clamp directly as a pure function.

- [ ] The header shows a grab cursor and `aria-label`s the drag affordance. Do not add an icon.

- [ ] A double-click on the header resets the offset to zero. Cheap, and the only recovery if a layout change strands the card.

---

## Out of scope

- Click-to-pin a strike row. Hover covers the need; pinning adds a selection state to manage.
- Making the walls or Zero-Gamma draggable.
- Persisting the card offset per-symbol. It persists per saved layout, like every other study setting.
