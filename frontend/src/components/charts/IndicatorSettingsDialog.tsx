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

import type { IndicatorManifestEntry, IRSettingsInput } from '@openalgo/openscript'
import { parseSessionString } from '@openalgo/openscript'
import { Info } from 'lucide-react'
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
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type {
  IndicatorInstance,
  OutputStyleOverride,
  RangeVisibility,
  StyleOverrides,
  TimeframeVisibility,
} from '@/lib/charts/indicator-host'
import { DEFAULT_TF_VISIBILITY, resolveSettingsEntry } from '@/lib/charts/indicator-host'

const SOURCES = ['open', 'high', 'low', 'close', 'volume', 'hl2', 'hlc3', 'ohlc4', 'hlcc4']
const STYLABLE = new Set(['line', 'hline', 'histogram', 'fill'])
const DEFAULT_COLOR = '#2196f3'
/** Common OpenAlgo interval strings offered by `input.timeframe`'s select. */
const COMMON_TIMEFRAMES = ['1', '3', '5', '15', '30', '60', '120', '240', 'D', 'W', 'M']

/** Info affordance for `input.tooltip` — renders nothing when unset. */
function InputTooltip({ text }: { text?: string }) {
  if (!text) return null
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Info className="h-3.5 w-3.5 shrink-0 cursor-help text-muted-foreground" />
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  )
}

// `resolveSettingsEntry` returns EITHER a registry manifest entry OR an
// IR-derived descriptor (`descriptorFromIR`) — so this input's actual type is
// their union, not `IndicatorManifestEntry['inputs'][number]` alone. `session`
// (session-surface plan Task 7) only exists on the IR side (`IRSettingsInput`);
// without this widening, `entry.inputs` fails to typecheck the moment an
// IR-backed script declares `input.session`.
type InputDef = IndicatorManifestEntry['inputs'][number] | IRSettingsInput

/** One rendered row: inputs sharing an `inline` key render side by side. */
interface InputRow {
  rowKey: string
  inputs: InputDef[]
}

/** One rendered section: inputs sharing a `group` render under one header, in
 *  declaration order; ungrouped inputs each get their own headerless section. */
interface InputSection {
  group?: string
  rows: InputRow[]
}

/** Bucket inputs into group sections, then inline rows within each section —
 *  both in first-seen declaration order (P4.4). */
function groupInputs(inputs: readonly InputDef[]): InputSection[] {
  const sections: InputSection[] = []
  const sectionByGroup = new Map<string, InputSection>()
  for (const input of inputs) {
    let section = input.group ? sectionByGroup.get(input.group) : undefined
    if (!section) {
      section = { group: input.group, rows: [] }
      sections.push(section)
      if (input.group) sectionByGroup.set(input.group, section)
    }
    const rowKey = input.inline ?? input.id
    const row = input.inline ? section.rows.find((r) => r.rowKey === rowKey) : undefined
    if (row) {
      row.inputs.push(input)
    } else {
      section.rows.push({ rowKey, inputs: [input] })
    }
  }
  return sections
}

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
  // See resolveSettingsEntry's doc comment for why IR ownership is the gate.
  const entry = instance ? resolveSettingsEntry(instance, manifest) : undefined
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

  // Derived (not stored) — recomputed from `values` every render, same source
  // `renderControl`'s own `session` case reads, so the two can never disagree
  // on which inputs are currently invalid. Blocks Apply/Ok; it does NOT stop
  // the invalid text from being typed or shown — only from being committed.
  const hasSessionError = entry.inputs.some(
    (i) => i.type === 'session' && 'error' in parseSessionString(String(values[i.id] ?? i.defaultValue))
  )

  /** The bare control for one input (no label) — `compact` sizes it for an
   *  inline row shared with other inputs. */
  const renderControl = (input: InputDef, compact = false) => {
    const value = values[input.id] ?? input.defaultValue
    switch (input.type) {
      case 'integer':
      case 'float':
        return (
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
            className={compact ? 'h-8 w-20' : 'h-8'}
          />
        )
      case 'bool':
        return (
          <Switch
            id={`ind-${input.id}`}
            checked={Boolean(value)}
            onCheckedChange={(checked) => setInput(input.id, checked)}
          />
        )
      case 'enum':
      case 'source': {
        const options = input.type === 'enum' ? input.options : SOURCES
        return (
          <Select value={String(value)} onValueChange={(v) => setInput(input.id, v)}>
            <SelectTrigger className={compact ? 'h-8 w-28' : 'h-8'}>
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
        )
      }
      case 'string': {
        // input.string(options=[...]) renders a select; a bare input.string
        // (no options) falls back to free text.
        if (input.options && input.options.length > 0) {
          const options = input.options
          return (
            <Select value={String(value)} onValueChange={(v) => setInput(input.id, v)}>
              <SelectTrigger className={compact ? 'h-8 w-28' : 'h-8'}>
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
          )
        }
        return (
          <Input
            id={`ind-${input.id}`}
            type="text"
            value={String(value)}
            onChange={(e) => setInput(input.id, e.target.value)}
            className={compact ? 'h-8 w-28' : 'h-8'}
          />
        )
      }
      case 'timeframe': {
        // A select of common intervals; the current value is appended when
        // it isn't one of the presets so a saved custom timeframe is never lost.
        const current = String(value)
        const options = COMMON_TIMEFRAMES.includes(current)
          ? COMMON_TIMEFRAMES
          : [current, ...COMMON_TIMEFRAMES]
        return (
          <Select value={current} onValueChange={(v) => setInput(input.id, v)}>
            <SelectTrigger className={compact ? 'h-8 w-20' : 'h-8'}>
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
        )
      }
      case 'session': {
        // Free-text control, same shape as the option-less `string` case above.
        // `parseSessionString` is the SAME grammar the engine's executor runs
        // at bind time (OS4005) — this is client-side feedback only, never a
        // second source of truth: an invalid value still compiles and saves
        // fine as far as THIS check is concerned, it is the engine's bind-time
        // check that is the actual authority and will reject it at run time.
        // Showing the error here (and disabling Apply below) just gives the
        // user the same reason immediately instead of after a round trip.
        const raw = String(value)
        const parsed = parseSessionString(raw)
        const error = 'error' in parsed ? parsed.error : undefined
        return (
          <div className={compact ? 'w-28' : undefined}>
            <Input
              id={`ind-${input.id}`}
              type="text"
              value={raw}
              aria-invalid={error ? true : undefined}
              onChange={(e) => setInput(input.id, e.target.value)}
              className={compact ? 'h-8 w-28' : 'h-8'}
            />
            {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
          </div>
        )
      }
      case 'color': {
        const c = splitColor(String(value))
        return (
          <ColorPicker
            color={c.color}
            opacity={c.opacity}
            onChange={(color, opacity) => setInput(input.id, joinColor(color, opacity))}
          />
        )
      }
      default:
        return null
    }
  }

  /** One row: a single input (label | control), or several `inline`-sharing
   *  inputs laid out side by side. */
  const renderInputRow = (row: InputRow) => {
    if (row.inputs.length === 1) {
      const input = row.inputs[0] as InputDef
      const label = (
        <span className="flex items-center gap-1.5">
          <Label htmlFor={`ind-${input.id}`}>{input.label}</Label>
          <InputTooltip text={input.tooltip} />
        </span>
      )
      if (input.type === 'bool') {
        return (
          <div key={row.rowKey} className="flex items-center justify-between">
            {label}
            {renderControl(input)}
          </div>
        )
      }
      return (
        <div key={row.rowKey} className="grid grid-cols-2 items-center gap-2">
          {label}
          {renderControl(input)}
        </div>
      )
    }
    return (
      <div key={row.rowKey} className="flex flex-wrap items-center gap-3">
        {row.inputs.map((input) => (
          <div key={input.id} className="flex items-center gap-1.5">
            <span className="flex items-center gap-1 text-sm text-muted-foreground">
              {input.label}
              <InputTooltip text={input.tooltip} />
            </span>
            {renderControl(input, true)}
          </div>
        ))}
      </div>
    )
  }

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
              {groupInputs(entry.inputs).map((section, i) =>
                section.group ? (
                  <div key={section.group} className="grid gap-2">
                    <div className="pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {section.group}
                    </div>
                    {section.rows.map((row) => renderInputRow(row))}
                  </div>
                ) : (
                  // Ungrouped inputs render directly into the outer grid (no header).
                  <div key={`ungrouped-${i}`} className="contents">
                    {section.rows.map((row) => renderInputRow(row))}
                  </div>
                )
              )}
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
              disabled={hasSessionError}
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
