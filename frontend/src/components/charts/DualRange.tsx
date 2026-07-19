/**
 * Dual-thumb range slider (min/max) for the indicator Visibility tab — two
 * overlaid native range inputs sharing one track, so each thumb stays draggable
 * while a filled bar shows the selected span. Chromium-targeted (the app runs in
 * Chromium/Electron); values are also editable via the adjacent number inputs.
 */

const THUMB =
  '[&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 ' +
  '[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full ' +
  '[&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-primary [&::-webkit-slider-thumb]:bg-background ' +
  '[&::-moz-range-thumb]:pointer-events-auto [&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3 ' +
  '[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border [&::-moz-range-thumb]:border-primary [&::-moz-range-thumb]:bg-background'

export interface DualRangeProps {
  min: number
  max: number
  low: number
  high: number
  disabled?: boolean
  onChange: (low: number, high: number) => void
}

export function DualRange({ min, max, low, high, disabled, onChange }: DualRangeProps) {
  const span = max - min || 1
  const lp = ((Math.max(min, Math.min(low, max)) - min) / span) * 100
  const hp = ((Math.max(min, Math.min(high, max)) - min) / span) * 100

  return (
    <div className={`relative h-4 w-full ${disabled ? 'opacity-40' : ''}`}>
      <div className="absolute top-1/2 h-1 w-full -translate-y-1/2 rounded bg-muted" />
      <div
        className="absolute top-1/2 h-1 -translate-y-1/2 rounded bg-primary"
        style={{ left: `${lp}%`, width: `${Math.max(0, hp - lp)}%` }}
      />
      <input
        type="range"
        min={min}
        max={max}
        value={low}
        disabled={disabled}
        aria-label="Minimum"
        onChange={(e) => onChange(Math.min(Number(e.target.value), high), high)}
        className={`pointer-events-none absolute inset-0 h-4 w-full appearance-none bg-transparent ${THUMB}`}
      />
      <input
        type="range"
        min={min}
        max={max}
        value={high}
        disabled={disabled}
        aria-label="Maximum"
        onChange={(e) => onChange(low, Math.max(Number(e.target.value), low))}
        className={`pointer-events-none absolute inset-0 h-4 w-full appearance-none bg-transparent ${THUMB}`}
      />
    </div>
  )
}
