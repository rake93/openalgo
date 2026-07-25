/**
 * Shared chrome primitives for the chart workspace.
 *
 * The whole surface runs on one rhythm: 28px controls, 6px gaps, hairline
 * dividers, and no card surfaces — the chrome reads as instrument bezel around
 * the canvas rather than as stacked web panels. Every control here is quiet at
 * rest and states itself only on hover or when active, so nothing competes with
 * the chart for attention.
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** Toolbar button: ghost at rest, tinted when active. */
export function TBtn({
  active,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-md border border-transparent px-2 text-[13px] leading-none',
        'text-foreground/85 transition-colors',
        'hover:border-border hover:bg-accent hover:text-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        'disabled:pointer-events-none disabled:opacity-40',
        active && 'border-primary/40 bg-primary/12 text-primary hover:bg-primary/15',
        className
      )}
    >
      {children}
    </button>
  )
}

/** Square icon-only variant of {@link TBtn}. */
export function IBtn({
  active,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <TBtn active={active} {...rest} className={cn('w-7 justify-center px-0', className)}>
      {children}
    </TBtn>
  )
}

/** Hairline vertical divider between toolbar clusters. */
export function VDivider({ className }: { className?: string }) {
  return <span className={cn('mx-0.5 h-5 w-px shrink-0 bg-border', className)} aria-hidden="true" />
}

/**
 * Segmented pill group — the timeframe switcher and any other small
 * mutually-exclusive choice. One recessed track, the active item raised.
 */
export function Pills({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'inline-flex items-center gap-0.5 rounded-lg border border-border/70 bg-muted/40 p-0.5',
        className
      )}
    >
      {children}
    </div>
  )
}

export function Pill({
  active,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className={cn(
        'h-6 rounded-[5px] px-2 text-[11px] font-semibold uppercase leading-none tracking-[0.03em]',
        'text-muted-foreground transition-colors hover:text-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        active && 'bg-background text-primary shadow-sm',
        className
      )}
    >
      {children}
    </button>
  )
}

/** Small-caps section label used inside the side panels. */
export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'text-[10px] font-semibold uppercase leading-none tracking-[0.09em] text-muted-foreground',
        className
      )}
    >
      {children}
    </div>
  )
}

/** One labelled control row in a settings panel. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="grid grid-cols-[1fr_auto] items-center gap-3">
      <span className="min-w-0">
        <span className="block truncate text-[12px] text-foreground/85">{label}</span>
        {hint ? (
          <span className="block truncate text-[10.5px] text-muted-foreground">{hint}</span>
        ) : null}
      </span>
      <span className="justify-self-end">{children}</span>
    </label>
  )
}

/** Compact numeric/text input sized for the panels. */
export function TinyInput({ className, ...rest }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...rest}
      className={cn(
        'h-7 w-[86px] rounded-md border border-border bg-background px-2 text-right text-[12px] tabular-nums',
        'outline-none transition-colors focus:border-primary/60 focus:ring-2 focus:ring-ring/40',
        className
      )}
    />
  )
}

/** Compact select sized for the panels. */
export function TinySelect({
  className,
  children,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...rest}
      className={cn(
        'h-7 w-[128px] rounded-md border border-border bg-background px-1.5 text-[12px]',
        'outline-none transition-colors focus:border-primary/60 focus:ring-2 focus:ring-ring/40',
        className
      )}
    >
      {children}
    </select>
  )
}
