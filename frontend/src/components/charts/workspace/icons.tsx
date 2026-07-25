/**
 * Icon set for the chart workspace chrome.
 *
 * One geometry language across the whole surface: a 20x20 box, 1.6px strokes,
 * round caps and joins, no fills except where a glyph reads better solid. Drawn
 * here rather than pulled from an icon pack because the drawing tools have no
 * standard equivalents — a parallel channel or a fib retracement has to look
 * like the thing it places.
 */

import type { SVGProps } from 'react'

const base: SVGProps<SVGSVGElement> = {
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

const PATHS: Record<string, string> = {
  /* toolbar */
  search: '<circle cx="9" cy="9" r="5.5"/><path d="M13.2 13.2 17 17"/>',
  chevron: '<path d="M6 8.5 10 12.5 14 8.5"/>',
  indicators: '<path d="M2.5 15 6 8.5l3 3 3-7 2.5 5L17 7.5"/>',
  studies:
    '<path d="M3 4v13"/><path d="M5.5 6h7"/><path d="M5.5 9h10"/><path d="M5.5 12h5.5"/><path d="M5.5 15h8"/>',
  trade: '<path d="M3 12.5h5.5V17H3z"/><path d="M11.5 6H17v11h-5.5z"/><path d="M5.75 12.5V9"/>',
  camera:
    '<rect x="2.5" y="5.5" width="15" height="10.5" rx="2"/><circle cx="10" cy="11" r="3.1"/><path d="M7 5.5 8.2 3.5h3.6L13 5.5"/>',
  reset:
    '<path d="M3.5 7.5v-3h3"/><path d="M16.5 12.5v3h-3"/><path d="M4 8a6.2 6.2 0 0 1 11-2"/><path d="M16 12a6.2 6.2 0 0 1-11 2"/>',
  grid: '<path d="M2.5 7.5h15M2.5 12.5h15M7.5 2.5v15M12.5 2.5v15"/>',
  magnet: '<path d="M5.5 3.5v6a4.5 4.5 0 0 0 9 0v-6"/><path d="M5.5 3.5h3v6M11.5 3.5h3v6"/>',
  layout: '<rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M2.5 8h15M8.5 8v8.5"/>',
  save: '<path d="M4 3.5h9L16.5 7v9.5H4z"/><path d="M6.5 3.5v4h6v-4M6.5 16.5v-5h7v5"/>',
  eye: '<path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z"/><circle cx="10" cy="10" r="2.2"/>',
  gear: '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.5v2M10 15.5v2M17.5 10h-2M4.5 10h-2M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4M15.3 15.3l-1.4-1.4M6.1 6.1 4.7 4.7"/>',
  close: '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/>',
  plus: '<path d="M10 4.5v11M4.5 10h11"/>',
  panelRight: '<rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M12.5 3.5v13"/>',
  alert:
    '<path d="M10 3a4.5 4.5 0 0 0-4.5 4.5c0 4-1.5 5-1.5 5h12s-1.5-1-1.5-5A4.5 4.5 0 0 0 10 3Z"/><path d="M8.6 15.5a1.6 1.6 0 0 0 2.8 0"/>',

  /* drawing rail */
  cursor: '<path d="M4.5 2.5 15 10.2l-4.4.6-1.9 4.2z"/>',
  trend:
    '<path d="M4.5 14.5 14.5 5"/><circle cx="4.5" cy="14.5" r="1.6"/><circle cx="14.5" cy="5" r="1.6"/>',
  hline: '<path d="M2.5 10h15"/><circle cx="7" cy="10" r="1.6"/>',
  vline: '<path d="M10 2.5v15"/><circle cx="10" cy="7" r="1.6"/>',
  rect: '<rect x="3" y="5.5" width="14" height="9" rx="1.2"/>',
  ellipse: '<ellipse cx="10" cy="10" rx="7" ry="4.6"/>',
  channel:
    '<path d="M3 13.5 13 4.5M6 16.5 16 7.5"/><circle cx="3" cy="13.5" r="1.3"/><circle cx="16" cy="7.5" r="1.3"/>',
  fib: '<path d="M3 4.5h14M3 8h14M3 11.5h14M3 15h14"/>',
  position:
    '<rect x="3" y="4" width="14" height="4.5" rx="1"/><rect x="3" y="11.5" width="14" height="4.5" rx="1"/>',
  measure: '<path d="M3 13.5 8 8.5l3 3 6-6"/><path d="M3 9.5v4h4"/>',
  text: '<path d="M4 5h12"/><path d="M10 5v11"/>',
  path: '<path d="M2.5 14c3-8 6 4 9-3.5s4 2 6 1"/>',
  trash: '<path d="M3 5.5h14"/><path d="M7.5 5.5v-2h5v2"/><path d="M5 5.5 6 17h8l1-11.5"/>',
  undo: '<path d="M6.5 6.5h6a4 4 0 0 1 0 8H8"/><path d="M9 3.5 6 6.5l3 3"/>',
  redo: '<path d="M13.5 6.5h-6a4 4 0 0 0 0 8H12"/><path d="M11 3.5l3 3-3 3"/>',
  lock: '<rect x="4.5" y="9" width="11" height="7.5" rx="1.6"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>',
  unlock:
    '<rect x="4.5" y="9" width="11" height="7.5" rx="1.6"/><path d="M7 9V6.5a3 3 0 0 1 5.6-1.4"/>',
  clone:
    '<rect x="6.5" y="6.5" width="10" height="10" rx="1.6"/><path d="M4 12.5V4a.5.5 0 0 1 .5-.5H12"/>',
}

export type WorkspaceIconName = keyof typeof PATHS | string

/** Render one glyph by name. Unknown names render nothing rather than throwing. */
export function Icon({ name, className }: { name: WorkspaceIconName; className?: string }) {
  const d = PATHS[name]
  if (!d) return null
  return (
    // biome-ignore lint/security/noDangerouslySetInnerHtml: glyphs are literals in this module
    <svg {...base} className={className} dangerouslySetInnerHTML={{ __html: d }} />
  )
}
