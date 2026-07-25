/**
 * Settings for a library (openalgo-charts) indicator.
 *
 * The form is generated from the descriptor: `inputs` are what the indicator
 * declares, and `indicatorStyleInputs` derives colour, opacity, thickness and
 * line style for every plot. Nothing here is written per indicator, so an
 * indicator registered later gets a working dialog for free — the same rule the
 * engine-backed dialog follows.
 */

import type { IndicatorInput } from 'openalgo-charts'
import { INDICATOR_SOURCES } from 'openalgo-charts'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { LibraryIndicatorInstance } from '@/lib/charts/library-indicators'

export interface LibraryIndicatorDialogProps {
  instance: LibraryIndicatorInstance | null
  inputs: IndicatorInput[]
  style: IndicatorInput[]
  onApply(instanceId: string, patch: Record<string, unknown>): void
  onRemove(instanceId: string): void
  onClose(): void
}

export function LibraryIndicatorDialog({
  instance,
  inputs,
  style,
  onApply,
  onRemove,
  onClose,
}: LibraryIndicatorDialogProps) {
  const [draft, setDraft] = useState<Record<string, unknown>>({})

  useEffect(() => {
    setDraft(instance ? { ...instance.settings } : {})
  }, [instance])

  if (!instance) return null

  const set = (key: string, value: unknown) => setDraft((d) => ({ ...d, [key]: value }))

  const defaults = () => {
    const next: Record<string, unknown> = {}
    for (const i of [...inputs, ...style]) next[i.key] = i.default
    setDraft(next)
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-[420px] max-w-[92vw] gap-0 p-0">
        <DialogHeader className="border-b border-border px-4 pb-3 pt-4">
          <DialogTitle className="text-[15px]">{instance.name}</DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="inputs">
          <TabsList className="mx-4 mt-3 grid w-[calc(100%-2rem)] grid-cols-2">
            <TabsTrigger value="inputs">Inputs</TabsTrigger>
            <TabsTrigger value="style">Style</TabsTrigger>
          </TabsList>

          <TabsContent
            value="inputs"
            className="max-h-[52vh] space-y-2.5 overflow-y-auto px-4 py-3"
          >
            {inputs.length === 0 && (
              <p className="py-6 text-center text-[13px] text-muted-foreground">
                This indicator has no inputs to tune.
              </p>
            )}
            {inputs.map((i) => (
              <InputRow key={i.key} input={i} value={draft[i.key]} onChange={set} />
            ))}
          </TabsContent>

          <TabsContent value="style" className="max-h-[52vh] space-y-2.5 overflow-y-auto px-4 py-3">
            {style.map((i) => (
              <InputRow key={i.key} input={i} value={draft[i.key]} onChange={set} />
            ))}
          </TabsContent>
        </Tabs>

        <DialogFooter className="gap-2 border-t border-border px-4 py-3 sm:justify-between">
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => {
              onRemove(instance.instanceId)
              onClose()
            }}
          >
            Remove
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={defaults}>
              Defaults
            </Button>
            <Button
              size="sm"
              onClick={() => {
                onApply(instance.instanceId, draft)
                onClose()
              }}
            >
              Apply
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function InputRow({
  input,
  value,
  onChange,
}: {
  input: IndicatorInput
  value: unknown
  onChange(key: string, value: unknown): void
}) {
  const control = () => {
    switch (input.type) {
      case 'number':
        return (
          <input
            type="number"
            value={Number(value ?? input.default)}
            min={input.min}
            max={input.max}
            step={input.step ?? 1}
            onChange={(e) => onChange(input.key, Number(e.target.value))}
            className="h-7 w-[132px] rounded-md border border-border bg-background px-2 text-right text-[12px] tabular-nums outline-none focus:border-primary/60"
          />
        )
      case 'boolean':
        return (
          <input
            type="checkbox"
            checked={Boolean(value ?? input.default)}
            onChange={(e) => onChange(input.key, e.target.checked)}
            className="h-4 w-4 accent-primary"
          />
        )
      case 'color':
        return (
          <input
            type="color"
            value={String(value ?? input.default)}
            onChange={(e) => onChange(input.key, e.target.value)}
            className="h-7 w-[132px] cursor-pointer rounded-md border border-border bg-background p-0.5"
          />
        )
      case 'select':
        return (
          <select
            value={String(value ?? input.default)}
            onChange={(e) => onChange(input.key, e.target.value)}
            className="h-7 w-[132px] rounded-md border border-border bg-background px-1.5 text-[12px] outline-none focus:border-primary/60"
          >
            {input.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        )
      case 'source':
        return (
          <select
            value={String(value ?? input.default)}
            onChange={(e) => onChange(input.key, e.target.value)}
            className="h-7 w-[132px] rounded-md border border-border bg-background px-1.5 text-[12px] outline-none focus:border-primary/60"
          >
            {INDICATOR_SOURCES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        )
      default:
        return (
          <input
            type="text"
            value={String(value ?? input.default)}
            onChange={(e) => onChange(input.key, e.target.value)}
            className="h-7 w-[132px] rounded-md border border-border bg-background px-2 text-[12px] outline-none focus:border-primary/60"
          />
        )
    }
  }

  return (
    <label className="grid grid-cols-[1fr_auto] items-center gap-3">
      <span className="truncate text-[12.5px] text-foreground/85">{input.label}</span>
      <span className="justify-self-end">{control()}</span>
    </label>
  )
}
