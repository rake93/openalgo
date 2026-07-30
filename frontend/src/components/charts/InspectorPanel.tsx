/**
 * Series inspector panel (M8, roadmap Phase 2 §13.2).
 *
 * Answers the last step of the authoring loop — *it looks wrong → why?* — for a
 * bar pinned on the chart. Every number here is READ from what the engine
 * already computed for that session; nothing is recalculated on this side.
 *
 * Pinned rather than crosshair-following on purpose: reaching a control on a
 * hover-following panel drags the crosshair with it, so the panel would answer
 * about a different bar than the one the user was looking at.
 *
 * See openalgo-openscript/docs/openscript-phase2-series-inspector-design.md.
 */

import { useCallback, useEffect, useState } from 'react'
import type { InspectNode, InspectResult, InspectRoot } from '@openalgo/openscript'
import type { DataWindowRow } from '@/lib/charts/indicator-host'

export interface PinnedBar {
  index: number
  time: number | null
  rows: DataWindowRow[]
}

export type InspectFn = (
  instanceId: string,
  outputId: string,
  barIndex: number
) => Promise<{ epoch: number; result: InspectResult } | null>

/** Why an inspect could not be answered, in words a chart user can act on. */
const REFUSALS: Record<string, string> = {
  'no-such-session': 'That indicator is no longer running on this chart.',
  'builtin-no-ir': 'Built-in indicators have no script graph to inspect.',
  'no-such-output': 'That output is not part of the compiled script.',
  'bar-out-of-range': 'That bar is outside the loaded history.',
  'no-session-state': 'The indicator has no computed state — it may have failed to run.',
}

function fmt(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return 'na'
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 })
}

function whenLabel(time: number | null): string {
  if (!time) return ''
  const d = new Date(time * 1000)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

function TreeRow({
  node,
  depth,
  originIds,
  onPickSpan,
}: {
  node: InspectNode
  depth: number
  originIds: Set<number>
  onPickSpan?: ((span: { start: number; end: number; line: number }) => void) | undefined
}) {
  const isOrigin = originIds.has(node.nodeId)
  return (
    <>
      <div
        className={`flex items-baseline justify-between gap-2 rounded px-1 py-0.5 tabular-nums ${
          isOrigin ? 'bg-destructive/10' : ''
        } ${onPickSpan ? 'cursor-pointer hover:bg-accent' : ''}`}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
        onClick={onPickSpan ? () => onPickSpan(node.span) : undefined}
        onKeyDown={
          onPickSpan
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') onPickSpan(node.span)
              }
            : undefined
        }
        role={onPickSpan ? 'button' : undefined}
        tabIndex={onPickSpan ? 0 : undefined}
      >
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span className="truncate font-mono">{node.label}</span>
          {isOrigin && <span className="shrink-0 text-[10px] text-destructive">origin</span>}
          {node.sharedCount > 1 && (
            <span
              className="shrink-0 text-[10px] text-muted-foreground"
              title={`Read in ${node.sharedCount} places in this script. The line shown is where the compiler recorded this node — and if the same expression is written more than once, the compiler collapses them into one node, so that line is the first occurrence.`}
            >
              {node.sharedCount} refs
            </span>
          )}
          {node.htf && (
            <span className="shrink-0 text-[10px] text-muted-foreground">
              {node.htf.timeframe} bucket {node.htf.bucketIndex}
              {node.htf.closed ? '' : ' (forming)'}
            </span>
          )}
          {node.span.line > 0 && (
            <span className="shrink-0 text-[10px] text-muted-foreground">L{node.span.line}</span>
          )}
        </span>
        <span className={node.value === null ? 'text-muted-foreground' : ''}>
          {fmt(node.value)}
        </span>
      </div>
      {node.children.map((c) => (
        <TreeRow
          key={c.nodeId}
          node={c}
          depth={depth + 1}
          originIds={originIds}
          onPickSpan={onPickSpan}
        />
      ))}
      {node.elided ? (
        <div
          className="px-1 py-0.5 text-[10px] text-muted-foreground"
          style={{ paddingLeft: `${(depth + 1) * 12 + 4}px` }}
        >
          + {node.elided} more not shown
        </div>
      ) : null}
    </>
  )
}

function RootBlock({
  root,
  onPickSpan,
}: {
  root: InspectRoot
  onPickSpan?: ((span: { start: number; end: number; line: number }) => void) | undefined
}) {
  const originIds = new Set(root.origins.map((o) => o.nodeId))
  return (
    <div className="mt-2 first:mt-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-muted-foreground">{root.name}</span>
        <span className="tabular-nums">{fmt(root.value)}</span>
      </div>

      {root.origins.map((o) => (
        <div key={o.nodeId} className="mt-1 rounded bg-muted/50 px-1.5 py-1 text-[11px]">
          <span className="font-medium">na originates here</span>
          <span className="ml-1 text-muted-foreground">({o.reason})</span>
          {o.detail && <div className="text-muted-foreground">{o.detail}</div>}
        </div>
      ))}

      <div className="mt-1 max-h-64 overflow-auto rounded border border-border/60 text-[11px]">
        <TreeRow node={root.tree} depth={0} originIds={originIds} onPickSpan={onPickSpan} />
      </div>
    </div>
  )
}

export function InspectorPanel({
  bar,
  inspect,
  lastEpoch,
  onClose,
  onPickSpan,
}: {
  bar: PinnedBar
  inspect: InspectFn
  /** Current run counter for an instance, so a superseded answer says so. */
  lastEpoch?: (instanceId: string) => number | undefined
  onClose: () => void
  /** Reveal a node's source span in the editor. */
  onPickSpan?: (span: { start: number; end: number; line: number }) => void
}) {
  const first = bar.rows[0]
  const [selected, setSelected] = useState<{ instanceId: string; outputId: string } | null>(
    first?.values[0] ? { instanceId: first.instanceId, outputId: first.values[0].id } : null
  )
  const [state, setState] = useState<
    { kind: 'idle' } | { kind: 'loading' } | { kind: 'done'; epoch: number; result: InspectResult }
  >({ kind: 'idle' })

  const load = useCallback(
    async (pick: { instanceId: string; outputId: string }) => {
      setState({ kind: 'loading' })
      const answer = await inspect(pick.instanceId, pick.outputId, bar.index)
      // `null` means the host could not map the request at all (unknown instance
      // or unrecognised output id) — distinct from a NAMED engine refusal, which
      // arrives inside `result` and is worth showing.
      if (!answer) {
        setState({ kind: 'idle' })
        return
      }
      setState({ kind: 'done', epoch: answer.epoch, result: answer.result })
    },
    [inspect, bar.index]
  )

  useEffect(() => {
    if (selected) void load(selected)
  }, [selected, load])

  const stale =
    state.kind === 'done' &&
    selected &&
    lastEpoch?.(selected.instanceId) !== undefined &&
    lastEpoch(selected.instanceId) !== state.epoch

  return (
    <div className="absolute right-2 top-2 z-30 flex max-h-[calc(100%-1rem)] w-80 flex-col rounded-md border border-border bg-card/95 text-xs shadow-lg backdrop-blur">
      <div className="flex items-center justify-between gap-2 border-b border-border px-2.5 py-1.5">
        <span className="font-medium">
          Inspect · bar {bar.index}
          {bar.time ? ` · ${whenLabel(bar.time)}` : ''}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded px-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Close inspector"
        >
          ✕
        </button>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-border px-2 py-1.5">
        {bar.rows.flatMap((row) =>
          row.values.map((v) => {
            const on = selected?.instanceId === row.instanceId && selected?.outputId === v.id
            return (
              <button
                key={`${row.instanceId}:${v.id}`}
                type="button"
                onClick={() => setSelected({ instanceId: row.instanceId, outputId: v.id })}
                className={`flex items-center gap-1 rounded border px-1.5 py-0.5 ${
                  on ? 'border-primary bg-primary/10' : 'border-border hover:bg-accent'
                }`}
                title={row.name}
              >
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: v.color }}
                />
                <span className="max-w-28 truncate">{v.title}</span>
                {v.value === null && <span className="text-muted-foreground">na</span>}
              </button>
            )
          })
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-2.5 py-2">
        {state.kind === 'loading' && <div className="text-muted-foreground">Reading…</div>}

        {state.kind === 'idle' && (
          <div className="text-muted-foreground">
            {bar.rows.length === 0
              ? 'No indicator on this chart to inspect.'
              : 'Select an output above.'}
          </div>
        )}

        {state.kind === 'done' && !state.result.ok && (
          <div className="text-muted-foreground">
            {REFUSALS[state.result.reason] ?? state.result.reason}
          </div>
        )}

        {state.kind === 'done' && state.result.ok && (
          <>
            {stale && (
              <div className="mb-2 rounded bg-muted px-1.5 py-1 text-[11px]">
                The indicator has recomputed since this reading.
                <button
                  type="button"
                  className="ml-1 underline"
                  onClick={() => selected && void load(selected)}
                >
                  refresh
                </button>
              </div>
            )}

            {state.result.finality && (
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-muted-foreground">finality</span>
                <span>{state.result.finality}</span>
              </div>
            )}
            {state.result.repaintSources.map((s) => (
              <div key={s.nodeId} className="text-[11px] text-muted-foreground">
                may repaint via <span className="font-mono">{s.operator}</span> (line {s.span.line})
              </div>
            ))}

            {state.result.roots.length === 0 && (
              <div className="mt-1 text-muted-foreground">
                This output has no computed expression — its value is a constant in the script.
              </div>
            )}

            {state.result.roots.map((root) => (
              <RootBlock key={root.nodeId} root={root} onPickSpan={onPickSpan} />
            ))}

            {state.result.truncated && (
              <div className="mt-2 text-[10px] text-muted-foreground">
                {state.result.truncated.nodes} node
                {state.result.truncated.nodes === 1 ? '' : 's'} hidden beyond depth{' '}
                {state.result.truncated.maxDepth}.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
