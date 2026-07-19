/**
 * Color + opacity picker for the indicator settings dialog — a swatch button
 * that opens a popover with a preset palette, a custom color input, and an
 * opacity slider. Modeled on TradingView's plot-color picker.
 */

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

const PALETTE: string[] = [
  '#ffffff',
  '#e0e0e0',
  '#bdbdbd',
  '#9e9e9e',
  '#757575',
  '#616161',
  '#424242',
  '#212121',
  '#000000',
  '#f44336',
  '#e91e63',
  '#9c27b0',
  '#673ab7',
  '#3f51b5',
  '#2196f3',
  '#03a9f4',
  '#00bcd4',
  '#009688',
  '#4caf50',
  '#8bc34a',
  '#cddc39',
  '#ffeb3b',
  '#ffc107',
  '#ff9800',
  '#ff5722',
  '#795548',
  '#607d8b',
  '#ef9a9a',
  '#f48fb1',
  '#ce93d8',
  '#b39ddb',
  '#9fa8da',
  '#90caf9',
  '#81d4fa',
  '#80deea',
  '#80cbc4',
  '#a5d6a7',
  '#c5e1a5',
  '#e6ee9c',
  '#fff59d',
  '#ffe082',
  '#ffcc80',
  '#ffab91',
  '#bcaaa4',
  '#b0bec5',
]

/** hex (#rgb/#rrggbb) + alpha → rgba() for the swatch preview. */
function rgba(color: string, alpha: number): string {
  if (alpha >= 1 || !color.startsWith('#')) return color
  let hex = color.slice(1)
  if (hex.length === 3) hex = hex.replace(/./g, (c) => c + c)
  if (hex.length < 6) return color
  const r = Number.parseInt(hex.slice(0, 2), 16)
  const g = Number.parseInt(hex.slice(2, 4), 16)
  const b = Number.parseInt(hex.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha))})`
}

export interface ColorPickerProps {
  color: string
  /** 0..1 */
  opacity: number
  onChange: (color: string, opacity: number) => void
  className?: string
}

export function ColorPicker({ color, opacity, onChange, className }: ColorPickerProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Choose color"
          className={cn(
            'h-6 w-8 rounded border border-input shadow-sm',
            'bg-[linear-gradient(45deg,#0002_25%,transparent_25%,transparent_75%,#0002_75%),linear-gradient(45deg,#0002_25%,transparent_25%,transparent_75%,#0002_75%)]',
            '[background-size:8px_8px] [background-position:0_0,4px_4px]',
            className
          )}
        >
          <span
            className="block h-full w-full rounded-[3px]"
            style={{ backgroundColor: rgba(color, opacity) }}
          />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-3" align="start">
        <div className="grid grid-cols-9 gap-1">
          {PALETTE.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => onChange(c, opacity)}
              title={c}
              aria-label={c}
              className={cn(
                'h-4 w-4 rounded-sm border border-border/60',
                c.toLowerCase() === color.toLowerCase() &&
                  'ring-2 ring-primary ring-offset-1 ring-offset-background'
              )}
              style={{ backgroundColor: c }}
            />
          ))}
        </div>

        <div className="mt-3 flex items-center gap-2">
          <input
            type="color"
            value={color.startsWith('#') && color.length >= 7 ? color.slice(0, 7) : '#000000'}
            onChange={(e) => onChange(e.target.value, opacity)}
            className="h-7 w-7 cursor-pointer rounded border border-input bg-transparent p-0.5"
            aria-label="Custom color"
          />
          <input
            type="text"
            value={color}
            onChange={(e) => onChange(e.target.value, opacity)}
            spellCheck={false}
            className="h-7 flex-1 rounded border border-input bg-background px-2 font-mono text-xs outline-none focus:border-primary"
          />
        </div>

        <div className="mt-3">
          <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
            <span>Opacity</span>
            <span>{Math.round(opacity * 100)}%</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(opacity * 100)}
            onChange={(e) => onChange(color, Number(e.target.value) / 100)}
            className="w-full accent-primary"
            aria-label="Opacity"
          />
        </div>
      </PopoverContent>
    </Popover>
  )
}
