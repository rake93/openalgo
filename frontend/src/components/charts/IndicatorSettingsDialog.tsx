/**
 * Indicator settings dialog — form generated from IndicatorInputDefinition
 * metadata (doc §5: no per-indicator forms). Shared by /trading panes and the
 * /charts workspace.
 */

import { useEffect, useState } from 'react'
import type { IndicatorManifestEntry } from '@openalgo/indicator-engine'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { IndicatorInstance } from '@/lib/charts/indicator-host'

const SOURCES = ['open', 'high', 'low', 'close', 'volume', 'hl2', 'hlc3', 'ohlc4', 'hlcc4']

export interface IndicatorSettingsDialogProps {
  instance: IndicatorInstance | null
  manifest: readonly IndicatorManifestEntry[]
  onSave: (instanceId: string, inputs: Record<string, unknown>) => void | Promise<void>
  onClose: () => void
}

export function IndicatorSettingsDialog({
  instance,
  manifest,
  onSave,
  onClose,
}: IndicatorSettingsDialogProps) {
  const entry = instance ? manifest.find((m) => m.id === instance.definitionId) : undefined
  const [values, setValues] = useState<Record<string, unknown>>({})

  useEffect(() => {
    setValues(instance ? { ...instance.inputs } : {})
  }, [instance])

  if (!instance || !entry) {
    return null
  }

  const set = (id: string, value: unknown) => setValues((v) => ({ ...v, [id]: value }))

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{entry.name}</DialogTitle>
        </DialogHeader>
        <div className="grid gap-3 py-1">
          {entry.inputs.length === 0 && (
            <p className="text-sm text-muted-foreground">This indicator has no settings.</p>
          )}
          {entry.inputs.map((input) => {
            const value = values[input.id] ?? input.defaultValue
            switch (input.type) {
              case 'integer':
              case 'float':
                return (
                  <div key={input.id} className="grid grid-cols-2 items-center gap-2">
                    <Label htmlFor={`ind-${input.id}`}>{input.label}</Label>
                    <Input
                      id={`ind-${input.id}`}
                      type="number"
                      value={String(value)}
                      min={input.min}
                      max={input.max}
                      step={input.step ?? (input.type === 'integer' ? 1 : 0.1)}
                      onChange={(e) =>
                        set(
                          input.id,
                          input.type === 'integer'
                            ? Math.round(Number(e.target.value))
                            : Number(e.target.value)
                        )
                      }
                      className="h-8"
                    />
                  </div>
                )
              case 'bool':
                return (
                  <div key={input.id} className="flex items-center justify-between">
                    <Label htmlFor={`ind-${input.id}`}>{input.label}</Label>
                    <Switch
                      id={`ind-${input.id}`}
                      checked={Boolean(value)}
                      onCheckedChange={(checked) => set(input.id, checked)}
                    />
                  </div>
                )
              case 'enum':
              case 'source': {
                const options = input.type === 'enum' ? input.options : SOURCES
                return (
                  <div key={input.id} className="grid grid-cols-2 items-center gap-2">
                    <Label>{input.label}</Label>
                    <Select value={String(value)} onValueChange={(v) => set(input.id, v)}>
                      <SelectTrigger className="h-8">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {options.map((o) => (
                          <SelectItem key={o} value={o}>
                            {o}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )
              }
              case 'color':
                return (
                  <div key={input.id} className="grid grid-cols-2 items-center gap-2">
                    <Label htmlFor={`ind-${input.id}`}>{input.label}</Label>
                    <input
                      id={`ind-${input.id}`}
                      type="color"
                      value={String(value)}
                      onChange={(e) => set(input.id, e.target.value)}
                      className="h-8 w-full cursor-pointer rounded border border-input bg-transparent"
                    />
                  </div>
                )
              default:
                return null
            }
          })}
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={() => {
              void onSave(instance.instanceId, values)
              onClose()
            }}
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
