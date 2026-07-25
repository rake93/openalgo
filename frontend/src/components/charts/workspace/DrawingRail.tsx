/**
 * Left drawing rail.
 *
 * One button per tool family, not per tool: clicking a family arms its first
 * tool and clicking again cycles through the rest, so eighteen tools fit in
 * nine buttons without a flyout to chase. The armed tool is named in the
 * tooltip and echoed in the status bar, so the cycle is never guesswork.
 */

import { DRAWING_RAIL, type RailGroup } from '@/lib/charts/drawing'
import { cn } from '@/lib/utils'
import { Icon } from './icons'

export interface DrawingRailProps {
  activeTool: string | null
  canUndo: boolean
  canRedo: boolean
  hasSelection: boolean
  /** Display names by tool id, for tooltips that say what is armed. */
  toolNames: Record<string, string>
  onPick(group: RailGroup): void
  onUndo(): void
  onRedo(): void
  onDeleteSelected(): void
  onClearAll(): void
}

export function DrawingRail(p: DrawingRailProps) {
  return (
    <nav
      aria-label="Drawing tools"
      className="flex w-10 shrink-0 flex-col items-center gap-0.5 overflow-y-auto border-r border-border bg-background/95 py-1.5"
    >
      {DRAWING_RAIL.map((entry, i) => {
        if ('separator' in entry) {
          return <span key={`sep${i}`} className="my-1 h-px w-5 bg-border" aria-hidden="true" />
        }
        const armed =
          entry.tools.length === 0
            ? p.activeTool === null
            : p.activeTool !== null && entry.tools.includes(p.activeTool)
        const current = armed && p.activeTool ? p.toolNames[p.activeTool] : null
        return (
          <RailButton
            key={entry.id}
            icon={entry.iconKey}
            active={armed}
            title={current ? `${current} — click to cycle` : entry.title}
            onClick={() => p.onPick(entry)}
          />
        )
      })}

      <span className="my-1 h-px w-5 bg-border" aria-hidden="true" />

      <RailButton icon="undo" title="Undo (Ctrl+Z)" disabled={!p.canUndo} onClick={p.onUndo} />
      <RailButton
        icon="redo"
        title="Redo (Ctrl+Shift+Z)"
        disabled={!p.canRedo}
        onClick={p.onRedo}
      />
      <RailButton
        icon="close"
        title="Delete the selected drawing (Del)"
        disabled={!p.hasSelection}
        onClick={p.onDeleteSelected}
      />
      <RailButton icon="trash" title="Remove every drawing" onClick={p.onClearAll} />
    </nav>
  )
}

function RailButton({
  icon,
  title,
  active,
  disabled,
  onClick,
}: {
  icon: string
  title: string
  active?: boolean
  disabled?: boolean
  onClick(): void
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'grid h-7 w-7 shrink-0 place-items-center rounded-md border border-transparent text-muted-foreground',
        'transition-colors hover:border-border hover:bg-accent hover:text-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        'disabled:pointer-events-none disabled:opacity-30',
        active && 'border-primary/40 bg-primary/12 text-primary'
      )}
    >
      <Icon name={icon} className="h-[17px] w-[17px]" />
    </button>
  )
}
