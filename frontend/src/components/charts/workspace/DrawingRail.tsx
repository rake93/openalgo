/**
 * Left drawing rail.
 *
 * One button per tool family. A plain click re-arms whatever that family last
 * had armed, and the caret opens a flyout listing the family's tools — so 43
 * tools fit in eight buttons and any of them is two clicks away.
 *
 * This replaced a click-to-cycle rail, which arms a family's first tool and
 * advances on repeat clicks. That reads well for a family of four and breaks
 * down past it: reaching the sixth tool of a group cost six clicks with nothing
 * on screen to say what was coming next.
 *
 * Below the separator sit this workspace's own actions — undo, redo, delete the
 * selection, clear everything. `/trading`'s rail collapses the last two into one
 * `onRemove(all)`; these stay separate here.
 */

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { DRAW_GROUPS, drawToolIcon } from '@/lib/charts/drawTools'
import { cn } from '@/lib/utils'
import { Icon } from './icons'

export interface DrawingRailProps {
  activeTool: string | null
  canUndo: boolean
  canRedo: boolean
  hasSelection: boolean
  onPick(toolId: string | null): void
  onUndo(): void
  onRedo(): void
  onDeleteSelected(): void
  onClearAll(): void
}

/** Which group owns a tool id, so the rail can highlight the active button. */
function groupOf(toolId: string | null): string | null {
  if (toolId === null) return null
  for (const g of DRAW_GROUPS) {
    for (const sec of g.sections) {
      if (sec.tools.some((t) => t.id === toolId)) return g.key
    }
  }
  return null
}

const BTN =
  'flex h-8 w-8 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-30'
const ON = 'border-primary/40 bg-primary/15 text-primary'

export function DrawingRail(p: DrawingRailProps) {
  // Last tool armed per group, so the bare button re-arms it rather than making
  // you reopen the flyout every time. Rendered from `activeTool` rather than
  // held in state: the controller is the source of truth for what is armed.
  const last: Record<string, string> = {}
  const activeGroup = groupOf(p.activeTool)
  if (p.activeTool && activeGroup) last[activeGroup] = p.activeTool

  return (
    <nav
      aria-label="Drawing tools"
      className="flex w-10 shrink-0 flex-col items-center gap-0.5 overflow-y-auto border-r border-border bg-background/95 py-1.5"
    >
      <button
        type="button"
        title="Cursor (Esc)"
        aria-label="Cursor"
        onClick={() => p.onPick(null)}
        className={cn(BTN, p.activeTool === null && ON)}
      >
        <span className="h-[18px] w-[18px]">{drawToolIcon('cursor')}</span>
      </button>

      <span className="my-1 h-px w-5 bg-border" aria-hidden="true" />

      {DRAW_GROUPS.map((g) => (
        <GroupButton
          key={g.key}
          group={g}
          armed={activeGroup === g.key}
          lastTool={last[g.key]}
          onPick={p.onPick}
        />
      ))}

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
        title="Delete selected (Del)"
        disabled={!p.hasSelection}
        onClick={p.onDeleteSelected}
      />
      <RailButton icon="trash" title="Clear all drawings" onClick={p.onClearAll} />
    </nav>
  )
}

function GroupButton({
  group,
  armed,
  lastTool,
  onPick,
}: {
  group: (typeof DRAW_GROUPS)[number]
  armed: boolean
  lastTool: string | undefined
  onPick(toolId: string | null): void
}) {
  const first = group.sections[0]?.tools[0]?.id ?? null
  return (
    <DropdownMenu>
      <div className="relative">
        <button
          type="button"
          title={group.label}
          aria-label={group.label}
          // With nothing armed yet, fall to the group's first tool rather than
          // forcing the flyout open — one click should always draw something.
          onClick={() => onPick(lastTool ?? first)}
          className={cn(BTN, armed && ON)}
        >
          <span className="h-[18px] w-[18px]">{drawToolIcon(group.iconKey)}</span>
        </button>
        {/* Caret opens the flyout without changing what is armed. */}
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={`${group.label} menu`}
            className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-sm text-muted-foreground/60 hover:text-foreground"
          >
            <svg viewBox="0 0 10 10" className="h-3 w-3" aria-hidden="true">
              <path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth={1.6} />
            </svg>
          </button>
        </DropdownMenuTrigger>
      </div>

      <DropdownMenuContent side="right" align="start" className="w-56">
        {group.sections.map((sec, si) => (
          <div key={sec.head ?? `s${si}`}>
            {sec.head && (
              <div className="px-2 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                {sec.head}
              </div>
            )}
            {sec.tools.map((t) => (
              <DropdownMenuItem
                key={t.id ?? 'cursor'}
                onSelect={() => onPick(t.id)}
                className="gap-2 text-[13px]"
              >
                <span className="h-4 w-4 shrink-0 text-muted-foreground">
                  {drawToolIcon(t.iconKey)}
                </span>
                {t.label}
              </DropdownMenuItem>
            ))}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
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
      disabled={disabled}
      onClick={onClick}
      className={cn(BTN, active && ON)}
    >
      <Icon name={icon} className="h-[18px] w-[18px]" />
    </button>
  )
}
