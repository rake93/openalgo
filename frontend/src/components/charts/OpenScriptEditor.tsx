/**
 * CodeMirror editor for OpenScript (the Pine-like indicator language).
 *
 * Syntax highlighting and inline diagnostics come straight from the engine
 * package: `openScriptStreamParser` reuses the compiler's own lexer, and the
 * linter compiles the current document and maps diagnostics to CodeMirror lint
 * marks — so the editor can never disagree with how the code actually compiles.
 */

import { autocompletion, type CompletionSource } from '@codemirror/autocomplete'
import { StreamLanguage } from '@codemirror/language'
import { type Diagnostic, linter, lintGutter } from '@codemirror/lint'
import { type EditorState, type Extension, StateField } from '@codemirror/state'
import { EditorView, hoverTooltip, keymap, showTooltip, type Tooltip } from '@codemirror/view'
import { tags as t } from '@lezer/highlight'
import { compile } from '@openalgo/openscript/compiler'
import {
  completionsAt,
  formatSource,
  hoverAt,
  openScriptStreamParser,
  signatureAt,
  styleLint,
  toLintDiagnostics,
} from '@openalgo/openscript/codemirror'
import { createTheme } from '@uiw/codemirror-themes'
import CodeMirror from '@uiw/react-codemirror'
import { useMemo } from 'react'
import { useThemeStore } from '@/stores/themeStore'

/** Escape text before injecting into hover-tooltip innerHTML. */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * Adapts the engine's pure `completionsAt(source, pos)` to CodeMirror's
 * `CompletionSource` shape. `signatureAt` is engine-tested and exported but
 * not yet wired to a UI tooltip here — see TODO below.
 */
const openScriptCompletions: CompletionSource = (ctx) => {
  const word = ctx.matchBefore(/[\w.]*/)
  const items = completionsAt(ctx.state.doc.toString(), ctx.pos)
  if (items.length === 0) return null
  const from = word ? word.from + (word.text.includes('.') ? word.text.lastIndexOf('.') + 1 : 0) : ctx.pos
  return {
    from,
    options: items.map((c) => ({ label: c.label, type: c.kind, detail: c.detail, info: c.info })),
  }
}

/** Adapts the engine's pure `hoverAt(source, pos)` to CodeMirror's `hoverTooltip`. */
const openScriptHover = hoverTooltip((view, pos) => {
  const h = hoverAt(view.state.doc.toString(), pos)
  if (!h) return null
  return {
    pos,
    create: () => {
      const dom = document.createElement('div')
      dom.className = 'cm-os-hover'
      dom.innerHTML = `<div class="sig">${escapeHtml(h.signature)}</div><div class="doc">${escapeHtml(h.doc)}</div>`
      return { dom }
    },
  }
})

/**
 * Signature-help tooltip: a `showTooltip`-backed StateField that renders the
 * active call's signature (active parameter bolded) whenever the cursor sits
 * inside a builtin call. `signatureAt` is the engine's pure resolver.
 */
function signatureTooltip(state: EditorState): Tooltip | null {
  const pos = state.selection.main.head
  const info = signatureAt(state.doc.toString(), pos)
  if (!info) return null
  return {
    pos,
    above: true,
    create: () => {
      const dom = document.createElement('div')
      dom.className = 'cm-os-signature'
      const active = info.params[info.activeParam]
      const label = active
        ? escapeHtml(info.label).replace(
            new RegExp(`\\b${active.name}\\b`),
            `<strong>${escapeHtml(active.name)}</strong>`
          )
        : escapeHtml(info.label)
      const detail = active?.doc
        ? `<div class="param">${escapeHtml(active.name)}: ${escapeHtml(active.doc)}</div>`
        : ''
      dom.innerHTML = `<div class="sig">${label}</div>${detail}`
      return { dom }
    },
  }
}

const signatureField = StateField.define<Tooltip | null>({
  create: signatureTooltip,
  update(value, tr) {
    if (!tr.docChanged && !tr.selection) return value
    return signatureTooltip(tr.state)
  },
  provide: (f) => showTooltip.from(f),
})

interface OpenScriptEditorProps {
  value: string
  onChange?: (value: string) => void
  readOnly?: boolean
}

const openScriptLanguage = StreamLanguage.define(openScriptStreamParser)

const openScriptLinter = linter((view) => {
  const doc = view.state.doc.toString()
  return toLintDiagnostics([
    ...compile(doc).diagnostics,
    ...styleLint(doc),
  ]) as unknown as Diagnostic[]
})

/**
 * `Shift-Alt-F` replaces the whole document with `formatSource(doc)` — a
 * conservative, idempotent re-indent + operator/comma respacing. No-ops if
 * the document is already formatted.
 */
const formatKeymap = keymap.of([
  {
    key: 'Shift-Alt-f',
    run: (view) => {
      const doc = view.state.doc.toString()
      const out = formatSource(doc)
      if (out !== doc) {
        view.dispatch({ changes: { from: 0, to: doc.length, insert: out } })
      }
      return true
    },
  },
])

const createSyntaxTheme = (isDark: boolean): Extension =>
  createTheme({
    theme: isDark ? 'dark' : 'light',
    settings: {
      background: 'transparent',
      foreground: isDark ? '#e5e5e5' : '#171717',
      caret: isDark ? '#38bdf8' : '#0284c7',
      selection: isDark ? 'rgba(56, 189, 248, 0.2)' : 'rgba(2, 132, 199, 0.2)',
      selectionMatch: isDark ? 'rgba(56, 189, 248, 0.1)' : 'rgba(2, 132, 199, 0.1)',
      lineHighlight: isDark ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.02)',
      gutterBackground: 'transparent',
      gutterForeground: isDark ? 'rgba(255, 255, 255, 0.4)' : 'rgba(0, 0, 0, 0.4)',
      gutterBorder: 'transparent',
    },
    styles: [
      { tag: t.keyword, color: '#c084fc' }, // if / else / and / or / not / na
      { tag: t.string, color: '#34d399' },
      { tag: t.number, color: '#fb923c' },
      { tag: t.atom, color: '#f472b6' }, // color/shape/location literals
      { tag: t.comment, color: isDark ? '#6b7280' : '#9ca3af', fontStyle: 'italic' },
      { tag: t.operator, color: isDark ? '#a3a3a3' : '#525252' },
      { tag: t.variableName, color: isDark ? '#e5e5e5' : '#171717' },
      { tag: t.invalid, color: '#f87171' },
    ],
  })

const createBaseTheme = (isDark: boolean): Extension => {
  const borderColor = isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'
  const gutterBg = isDark ? 'rgba(255, 255, 255, 0.025)' : 'rgba(0, 0, 0, 0.02)'
  return EditorView.theme({
    '&': {
      fontSize: '13px',
      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      height: '100%',
      backgroundColor: 'transparent',
    },
    '&.cm-editor': { height: '100%', backgroundColor: 'transparent' },
    '.cm-scroller': { overflow: 'auto', height: '100%', backgroundColor: 'transparent' },
    '.cm-content': { padding: '12px 0', lineHeight: '22px', backgroundColor: 'transparent' },
    '.cm-line': { padding: '0 12px' },
    '.cm-gutters': { backgroundColor: gutterBg, borderRight: `1px solid ${borderColor}`, paddingRight: '2px' },
    '.cm-gutter': { minWidth: '32px' },
    '.cm-gutterElement': { padding: '0 6px 0 8px', lineHeight: '22px' },
    '&.cm-focused': { outline: 'none' },
    '.cm-activeLine': { backgroundColor: isDark ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.02)' },
    '.cm-activeLineGutter': { backgroundColor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.03)' },
    '.cm-cursor': { borderLeftColor: isDark ? '#38bdf8' : '#0284c7', borderLeftWidth: '2px' },
    '.cm-os-hover': {
      padding: '6px 8px',
      maxWidth: '320px',
      fontSize: '12px',
      lineHeight: '1.4',
    },
    '.cm-os-hover .sig': {
      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      fontWeight: 600,
      marginBottom: '2px',
    },
    '.cm-os-hover .doc': {
      color: isDark ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.65)',
    },
    '.cm-os-signature': {
      padding: '6px 8px',
      maxWidth: '360px',
      fontSize: '12px',
      lineHeight: '1.4',
      fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
    },
    '.cm-os-signature strong': { color: isDark ? '#38bdf8' : '#0284c7', fontWeight: 700 },
    '.cm-os-signature .param': {
      marginTop: '3px',
      color: isDark ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.65)',
    },
  })
}

export function OpenScriptEditor({ value, onChange, readOnly = false }: OpenScriptEditorProps) {
  const mode = useThemeStore((s) => s.mode)
  const isDark = mode === 'dark'

  const extensions = useMemo(
    () => [
      openScriptLanguage,
      openScriptLinter,
      lintGutter(),
      autocompletion({ override: [openScriptCompletions] }),
      openScriptHover,
      signatureField,
      formatKeymap,
      createSyntaxTheme(isDark),
      createBaseTheme(isDark),
      EditorView.lineWrapping,
    ],
    [isDark]
  )

  return (
    <div className="h-full w-full overflow-hidden [&>div]:h-full">
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={extensions}
        readOnly={readOnly}
        height="100%"
        theme={isDark ? 'dark' : 'light'}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: true,
          highlightActiveLine: true,
          foldGutter: false,
          dropCursor: true,
          indentOnInput: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: true,
          highlightSelectionMatches: true,
          searchKeymap: true,
          tabSize: 4,
        }}
      />
    </div>
  )
}
