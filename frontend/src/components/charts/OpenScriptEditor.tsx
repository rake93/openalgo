/**
 * CodeMirror editor for OpenScript (the Pine-like indicator language).
 *
 * Syntax highlighting and inline diagnostics come straight from the engine
 * package: `openScriptStreamParser` reuses the compiler's own lexer, and the
 * linter compiles the current document and maps diagnostics to CodeMirror lint
 * marks — so the editor can never disagree with how the code actually compiles.
 */

import { StreamLanguage } from '@codemirror/language'
import { type Diagnostic, linter, lintGutter } from '@codemirror/lint'
import type { Extension } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { tags as t } from '@lezer/highlight'
import { compile } from '@openalgo/indicator-engine/compiler'
import { openScriptStreamParser, toLintDiagnostics } from '@openalgo/indicator-engine/codemirror'
import { createTheme } from '@uiw/codemirror-themes'
import CodeMirror from '@uiw/react-codemirror'
import { useMemo } from 'react'
import { useThemeStore } from '@/stores/themeStore'

interface OpenScriptEditorProps {
  value: string
  onChange?: (value: string) => void
  readOnly?: boolean
}

const openScriptLanguage = StreamLanguage.define(openScriptStreamParser)

const openScriptLinter = linter((view) =>
  toLintDiagnostics(compile(view.state.doc.toString()).diagnostics) as unknown as Diagnostic[]
)

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
      createSyntaxTheme(isDark),
      createBaseTheme(isDark),
      EditorView.lineWrapping,
    ],
    [isDark]
  )

  return (
    <div className="h-full w-full">
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
          autocompletion: false,
          highlightSelectionMatches: true,
          searchKeymap: true,
          tabSize: 4,
        }}
      />
    </div>
  )
}
