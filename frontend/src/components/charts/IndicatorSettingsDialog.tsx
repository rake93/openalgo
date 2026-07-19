/**
 * Indicator settings dialog — Inputs / Style / Visibility tabs, mirroring
 * TradingView's indicator settings (architecture doc §5: no per-indicator
 * forms — everything is generated from the manifest).
 *
 * - Inputs: form generated from IndicatorInputDefinition metadata.
 * - Style: per-output color (+opacity), line width, and line style overrides.
 * - Visibility: per-output show/hide.
 *
 * Style/Visibility edits become per-output `StyleOverrides` (keyed by output id)
 * applied on the main thread by IndicatorHost — no worker recompute. Shared by
 * the /charts workspace and the /trading terminal.
 */

import type { IndicatorManifestEntry } from '@openalgo/indicator-engine'
import { useEffect, useState } from 'react'
import { ColorPicker } from '@/components/charts/ColorPicker'
import { DualRange } from '@/components/charts/DualRange'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type {
  IndicatorInstance,
  OutputStyleOverride,
  RangeVisibility,
  StyleOverrides,
  TimeframeVisibility,
} from '@/lib/charts/indicator-host'
import { DEFAULT_TF_VISIBILITY } from '@/lib/charts/indicator-host'

const SOURCES = ['open', 'high', 'low', 'close', 'volume', 'hl2', 'hlc3', 'ohlc4', 'hlcc4']
const STYLABLE = new Set(['line', 'hline', 'histogram', 'fill'])
const DEFAULT_COLOR = '#2196f3'

/** Split a stored color input value into a hex color + 0..1 opacity for the picker. */
function splitColor(value: string): { color: string; opacity: number } {
  const m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*\)/i.exec(
    value
  )
  if (m) {
    const toHex = (n: number) =>
      Math.max(0, Math.min(255, Math.round(Number(n))))
        .toString(16)
        .padStart(2, '0')
    return {
      color: `#${toHex(Number(m[1]))}${toHex(Number(m[2]))}${toHex(Number(m[3]))}`,
      opacity: m[4] != null ? Number(m[4]) : 1,
    }
  }
  return { color: value, opacity: 1 }
}

/** Fold a hex color + opacity back into a single stored value (rgba when < 1). */
function joinColor(color: string, opacity: number): string {
  if (opacity >= 1 || !color.startsWith('#')) return color
  let hex = color.slice(1)
  if (hex.length === 3) hex = hex.replace(/./g, (c) => c + c)
  if (hex.length < 6) return color
  const r = Number.parseInt(hex.slice(0, 2), 16)
  const g = Number.parseInt(hex.slice(2, 4), 16)
  const b = Number.parseInt(hex.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, opacity))})`
}

type OutputDef = IndicatorManifestEntry['outputs'][number]

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))

/** Timeframe categories that carry a min/max range, with their upper limit. */
const RANGE_ROWS: { key: keyof TimeframeVisibility; label: string; limit: number }[] = [
  { key: 'seconds', label: 'Seconds', limit: 59 },
  { key: 'minutes', label: 'Minutes', limit: 59 },
  { key: 'hours', label: 'Hours', limit: 24 },
  { key: 'days', label: 'Days', limit: 366 },
  { key: 'weeks', label: 'Weeks', limit: 52 },
  { key: 'months', label: 'Months', limit: 12 },
]

/** One checkbox-only timeframe row (Ticks / Ranges). */
function TfToggle({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <Checkbox checked={checked} onCheckedChange={(c) => onChange(c === true)} />
      <span className="text-sm">{label}</span>
    </div>
  )
}

/** One ranged timeframe row: checkbox + min + slider + max. */
function TfRange({
  label,
  limit,
  value,
  onChange,
}: {
  label: string
  limit: number
  value: RangeVisibility
  onChange: (v: RangeVisibility) => void
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex w-24 shrink-0 items-center gap-2 text-sm">
        <Checkbox
          checked={value.on}
          onCheckedChange={(c) => onChange({ ...value, on: c === true })}
        />
        {label}
      </span>
      <Input
        type="number"
        min={1}
        max={limit}
        value={String(value.min)}
        disabled={!value.on}
        onChange={(e) => onChange({ ...value, min: clamp(Number(e.target.value), 1, value.max) })}
        className="h-7 w-14 shrink-0 px-1.5 text-center"
      />
      <DualRange
        min={1}
        max={limit}
        low={value.min}
        high={value.max}
        disabled={!value.on}
        onChange={(low, high) => onChange({ ...value, min: low, max: high })}
      />
      <Input
        type="number"
        min={1}
        max={limit}
        value={String(value.max)}
        disabled={!value.on}
        onChange={(e) =>
          onChange({ ...value, max: clamp(Number(e.target.value), value.min, limit) })
        }
        className="h-7 w-14 shrink-0 px-1.5 text-center"
      />
    </div>
  )
}

export interface IndicatorSettingsDialogProps {
  instance: IndicatorInstance | null
  manifest: readonly IndicatorManifestEntry[]
  onSave: (
    instanceId: string,
    inputs: Record<string, unknown>,
    styleOverrides: StyleOverrides,
    visibility: TimeframeVisibility | undefined
  ) => void | Promise<void>
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
  const [overrides, setOverrides] = useState<StyleOverrides>({})
  const [visibility, setVisibility] = useState<TimeframeVisibility>(DEFAULT_TF_VISIBILITY)

  useEffect(() => {
    setValues(instance ? { ...instance.inputs } : {})
    setOverrides(instance?.styleOverrides ? { ...instance.styleOverrides } : {})
    setVisibility(instance?.visibility ? { ...instance.visibility } : DEFAULT_TF_VISIBILITY)
  }, [instance])

  if (!instance || !entry) {
    return null
  }

  const setInput = (id: string, value: unknown) => setValues((v) => ({ ...v, [id]: value }))

  const patch = (outputId: string, p: OutputStyleOverride) =>
    setOverrides((o) => ({ ...o, [outputId]: { ...o[outputId], ...p } }))

  const effective = (output: OutputDef) => {
    const def = output.defaultStyle ?? {}
    const ov = overrides[output.id] ?? {}
    return {
      color: ov.color ?? def.color ?? DEFAULT_COLOR,
      opacity: ov.opacity ?? def.opacity ?? 1,
      lineWidth: ov.lineWidth ?? def.lineWidth ?? 1,
      lineStyle: ov.lineStyle ?? def.lineStyle ?? 'solid',
      visible: ov.visible !== false,
    }
  }

  const resetDefaults = () => {
    setValues(Object.fromEntries(entry.inputs.map((i) => [i.id, i.defaultValue])))
    setOverrides({})
    setVisibility(DEFAULT_TF_VISIBILITY)
  }

  const setRange = (key: keyof TimeframeVisibility, value: RangeVisibility) =>
    setVisibility((v) => ({ ...v, [key]: value }))

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{entry.name}</DialogTitle>
        </DialogHeader>

        <Tabs defaultValue="inputs">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="inputs">Inputs</TabsTrigger>
            <TabsTrigger value="style">Style</TabsTrigger>
            <TabsTrigger value="visibility">Visibility</TabsTrigger>
          </TabsList>

          {/* Inputs */}
          <TabsContent value="inputs" className="max-h-[50vh] overflow-y-auto">
            <div className="grid gap-3 py-1">
              {entry.inputs.length === 0 && (
                <p className="text-sm text-muted-foreground">This indicator has no inputs.</p>
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
                            setInput(
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
                          onCheckedChange={(checked) => setInput(input.id, checked)}
                        />
                      </div>
                    )
                  case 'enum':
                  case 'source': {
                    const options = input.type === 'enum' ? input.options : SOURCES
                    return (
                      <div key={input.id} className="grid grid-cols-2 items-center gap-2">
                        <Label>{input.label}</Label>
                        <Select value={String(value)} onValueChange={(v) => setInput(input.id, v)}>
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
                  case 'color': {
                    const c = splitColor(String(value))
                    return (
                      <div key={input.id} className="grid grid-cols-2 items-center gap-2">
                        <Label>{input.label}</Label>
                        <div>
                          <ColorPicker
                            color={c.color}
                            opacity={c.opacity}
                            onChange={(color, opacity) =>
                              setInput(input.id, joinColor(color, opacity))
                            }
                          />
                        </div>
                      </div>
                    )
                  }
                  default:
                    return null
                }
              })}
            </div>
          </TabsContent>

          {/* Style — one row per plot: show/hide + color/width/line-style */}
          <TabsContent value="style" className="max-h-[50vh] overflow-y-auto">
            <div className="grid gap-3 py-1">
              {entry.outputs.length === 0 && (
                <p className="text-sm text-muted-foreground">This indicator has no plots.</p>
              )}
              {entry.outputs.map((output) => {
                const s = effective(output)
                const canStyle = STYLABLE.has(output.type)
                const isLine = output.type === 'line' || output.type === 'hline'
                return (
                  <div key={output.id} className="flex items-center gap-2">
                    <Checkbox
                      checked={s.visible}
                      onCheckedChange={(c) => patch(output.id, { visible: c === true })}
                    />
                    <span className="flex-1 truncate text-sm">{output.title}</span>
                    {canStyle && (
                      <ColorPicker
                        color={s.color}
                        opacity={s.opacity}
                        onChange={(color, opacity) => patch(output.id, { color, opacity })}
                      />
                    )}
                    {isLine && (
                      <>
                        <Select
                          value={String(s.lineWidth)}
                          onValueChange={(v) => patch(output.id, { lineWidth: Number(v) })}
                        >
                          <SelectTrigger className="h-7 w-14">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {[1, 2, 3, 4].map((w) => (
                              <SelectItem key={w} value={String(w)}>
                                {w}px
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Select
                          value={s.lineStyle}
                          onValueChange={(v) =>
                            patch(output.id, { lineStyle: v as OutputStyleOverride['lineStyle'] })
                          }
                        >
                          <SelectTrigger className="h-7 w-24">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="solid">Solid</SelectItem>
                            <SelectItem value="dashed">Dashed</SelectItem>
                            <SelectItem value="dotted">Dotted</SelectItem>
                          </SelectContent>
                        </Select>
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </TabsContent>

          {/* Visibility — which chart timeframes the indicator renders on */}
          <TabsContent value="visibility" className="max-h-[50vh] overflow-y-auto">
            <div className="grid gap-2.5 py-1">
              <TfToggle
                label="Ticks"
                checked={visibility.ticks}
                onChange={(c) => setVisibility((v) => ({ ...v, ticks: c }))}
              />
              {RANGE_ROWS.map((row) => (
                <TfRange
                  key={row.key}
                  label={row.label}
                  limit={row.limit}
                  value={visibility[row.key] as RangeVisibility}
                  onChange={(r) => setRange(row.key, r)}
                />
              ))}
              <TfToggle
                label="Ranges"
                checked={visibility.ranges}
                onChange={(c) => setVisibility((v) => ({ ...v, ranges: c }))}
              />
            </div>
          </TabsContent>
        </Tabs>

        <DialogFooter className="items-center sm:justify-between">
          <Button variant="ghost" size="sm" onClick={resetDefaults}>
            Defaults
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => {
                const vis =
                  JSON.stringify(visibility) === JSON.stringify(DEFAULT_TF_VISIBILITY)
                    ? undefined
                    : visibility
                void onSave(instance.instanceId, values, overrides, vis)
                onClose()
              }}
            >
              Ok
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
