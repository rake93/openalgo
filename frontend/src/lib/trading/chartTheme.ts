/**
 * Bridges the app's shadcn CSS theme tokens into an openalgo-charts `ChartTheme`
 * so the canvas chrome (background, grid, axes, crosshair) matches whatever
 * theme the app is in — live light, live dark, or the analyzer violet palette.
 *
 * Tokens are oklch, which the canvas color parser doesn't read; we resolve each
 * to a plain rgb() string by painting it onto a 1×1 canvas and reading the pixel
 * back — the same rasterize trick the standalone page used, so pill-text
 * contrast and axis colors stay correct on every browser.
 */
import { type ChartTheme, darkTheme, lightTheme } from 'openalgo-charts'
import type { AppMode, ThemeMode } from '@/stores/themeStore'

let probe: HTMLSpanElement | null = null
let ctx: CanvasRenderingContext2D | null = null

function rasterize(cssColor: string): string {
  if (!probe) {
    probe = document.createElement('span')
    probe.style.display = 'none'
    document.body.appendChild(probe)
    const cnv = document.createElement('canvas')
    cnv.width = cnv.height = 1
    ctx = cnv.getContext('2d', { willReadFrequently: true })
  }
  if (!ctx) return cssColor
  // Resolve the variable through the DOM first (applies the active theme class),
  // then rasterize whatever format comes back (oklch / hsl / rgb) to rgb.
  probe.style.color = cssColor
  const resolved = getComputedStyle(probe).color || '#000'
  ctx.clearRect(0, 0, 1, 1)
  ctx.fillStyle = '#000'
  ctx.fillStyle = resolved // invalid values keep #000
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return `rgb(${r},${g},${b})`
}

const token = (name: string) => rasterize(`var(${name})`)

/** True when the app is in live light mode (analyzer is always a dark palette). */
export function isLightTheme(mode: ThemeMode, appMode: AppMode): boolean {
  return appMode === 'live' && mode === 'light'
}

/**
 * `ChartTheme` plus a full-contrast foreground text colour.
 *
 * openalgo-charts' `ChartTheme` only carries `axisText` (bound to
 * `--muted-foreground` below) for text it draws itself - axis labels and the
 * crosshair tag, both meant to read as passive scale furniture. It has no
 * field for on-canvas content this app's own primitives draw that must NOT
 * read that way - the GEX Levels metric caption is the first example - so
 * this extends the theme this app builds with one, bound to `--foreground`,
 * the same token the surrounding DOM chrome uses for regular text.
 */
export interface AppChartTheme extends ChartTheme {
  text: string
}

/** Build the canvas theme from the base palette + the app's live token colors. */
export function buildChartTheme(mode: ThemeMode, appMode: AppMode): AppChartTheme {
  const base = isLightTheme(mode, appMode) ? lightTheme : darkTheme
  return {
    ...base,
    background: token('--background'),
    grid: token('--card'),
    axisText: token('--muted-foreground'),
    axisLine: token('--border'),
    crosshair: token('--muted-foreground'),
    text: token('--foreground'),
  }
}

/** Volume-histogram color that reads well against the current theme. */
export function volumeColor(mode: ThemeMode, appMode: AppMode): string {
  return isLightTheme(mode, appMode) ? '#d4d4d8' : '#33415e'
}
