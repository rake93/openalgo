# Flyout drawing rail for the /charts workspace

Date: 2026-07-28
Status: approved, implementing

## Problem

`/charts` reaches 18 of the drawing tier's 43 registered tools. The gap is
entirely in the rail: `DrawingManager` already builds its tool list from
`registeredDrawingTools()`, so the engine can drive all 43 today. What is stale
is `DRAWING_RAIL`, a hand-written constant listing 12 groups over 18 tools.

The 25 tools with no button: Forecast, Price range, Date range, Circle, Triangle,
Polyline, Arc, Curve, Rotated rectangle, Double curve, Arrow up, Arrow down,
Highlighter, Brush, Fib channel, Fib time zone, Fib speed fan, Gann fan, Gann
box, Cyclic lines, Time cycles, Sine line, Price label, Callout, Flag mark.

`/trading` already solved this: `DRAW_GROUPS` covers exactly all 43 (verified —
no missing ids, no unregistered ones) behind a flyout rail.

## Decisions

**Flyout, not click-to-cycle.** `/charts` currently cycles a group's tools on
repeat clicks. That was reasonable for four tools per family; it does not survive
groups of six, where reaching the last tool costs six clicks with no menu to show
what is coming. `/trading`'s flyout behaviour is adopted so the two surfaces
behave identically.

**Copy the catalogue, do not share it.** `/charts` gets its own
`lib/charts/drawTools.tsx` rather than importing `/trading`'s. This duplicates
the group definitions, and the cost is real — a tool added to the library must be
added twice or the rails drift. It is accepted so `/charts` can regroup and
relabel without any risk to `/trading`, which is reference-only. The drift risk is
handled by a test rather than by discipline (see Testing).

**`/charts` keeps its own rail buttons.** Undo, redo, delete-selected and
clear-all stay as they are. `/trading` expresses removal as `onRemove(all: boolean)`;
`/charts` has two distinct buttons and keeps them, so the rails differ below the
separator by design.

## What is not ported

- **`portalHost`** — `/trading` portals its flyouts to a caller-supplied element
  because its panes use the Fullscreen API, whose top layer hides anything
  portalled to `document.body`. `/charts` has no fullscreen, so the prop would be
  dead weight.
- **`DrawStats`** — `/charts` already tracks the equivalent in `drawState`
  (`tool`, `canUndo`, `canRedo`, `selected`), so the rail keeps its current props.

## Components

**`lib/charts/drawTools.tsx`** — pure data plus glyphs. `DrawGroupDef` /
`DrawToolDef` and a `drawToolIcon(key)` lookup. Knows what a tool is called and
which glyph to show; nothing about how it behaves.

**`components/charts/workspace/DrawingRail.tsx`** — rewritten. One button per
group: a plain click arms that group's last-used tool, the caret opens a flyout
of the group's tools, and picking one arms it and becomes the group's new
default. Cursor disarms. Below a separator, the existing undo / redo / delete /
clear buttons are unchanged.

**`pages/charts/ChartWorkspace.tsx`** — `onPick(group: RailGroup)` becomes
`onPick(toolId: string | null)`, calling the already-present
`DrawingManager.setTool(toolId)` instead of `cycleGroup(group)`.

**`lib/charts/drawing.ts`** — `DRAWING_RAIL`, `RailGroup` and `cycleGroup()` are
deleted. They exist only to serve the cycling rail and have no other callers.

## Testing

The rail is presentation over a data table, so the tests worth writing are on the
data and the wiring.

1. **Registry coverage** — every id from `registeredDrawingTools()` appears
   exactly once across the groups, and no group names an unregistered id. This is
   the test that matters: the rail fell to 18 of 43 precisely because a
   hand-written list silently drifted from the library, and only a test that
   compares the two can stop it happening again on the next rebase.
2. **Icons** — every tool resolves to a glyph rather than falling through to a
   blank.
3. **Rail behaviour** — clicking a group arms its last-used tool; picking from
   the flyout arms that tool and updates the group's default; the cursor button
   disarms.

## Out of scope

Changing any drawing tool's behaviour, the drawing properties panel, or
`/trading` in any way.
