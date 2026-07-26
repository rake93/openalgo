/** Indicator engine session APIs (CSRF-protected web routes). */

import type { IRProgram } from '@openalgo/openscript'
import type { StyleOverrides, TimeframeVisibility } from '@/lib/charts/indicator-host'
import type { WorkspaceSnapshot } from '@/lib/charts/workspace'
import { webClient } from './client'

export interface ChartLayoutRecord {
  id: number
  name: string
  symbol: string | null
  exchange: string | null
  timeframe: string | null
  layout: ChartLayoutState
  updated_at: string | null
}

/**
 * Serializable workspace state stored in `layout_json` (a free-form JSON
 * column, so adding to this shape needs no migration).
 *
 * `indicators` is the original flat list and stays for layouts written by
 * earlier builds; `workspace` carries the full snapshot — chart type, transform
 * settings, both indicator runtimes, drawings, studies, and trading
 * preferences. A reader prefers `workspace` and falls back to `indicators`.
 */
export interface ChartLayoutState {
  indicators: {
    definitionId: string
    inputs: Record<string, unknown>
    styleOverrides?: StyleOverrides
    visibility?: TimeframeVisibility
    hidden?: boolean
  }[]
  workspace?: WorkspaceSnapshot
}

interface ApiEnvelope<T> {
  status: 'success' | 'error'
  data?: T
  message?: string
}

export async function listLayouts(): Promise<ChartLayoutRecord[]> {
  const { data } = await webClient.get<ApiEnvelope<ChartLayoutRecord[]>>('/indicators/api/layouts')
  return data.data ?? []
}

export async function createLayout(payload: {
  name: string
  symbol?: string
  exchange?: string
  timeframe?: string
  layout: ChartLayoutState
}): Promise<ChartLayoutRecord | undefined> {
  const { data } = await webClient.post<ApiEnvelope<ChartLayoutRecord>>(
    '/indicators/api/layouts',
    payload
  )
  return data.data
}

export async function updateLayout(
  id: number,
  payload: Partial<{
    name: string
    symbol: string
    exchange: string
    timeframe: string
    layout: ChartLayoutState
  }>
): Promise<void> {
  await webClient.put(`/indicators/api/layouts/${id}`, payload)
}

export async function deleteLayout(id: number): Promise<void> {
  await webClient.delete(`/indicators/api/layouts/${id}`)
}

/* ── OpenScript scripts ─────────────────────────────────────────────────── */

export interface ScriptRecord {
  id: number
  name: string
  description: string | null
  language: string
  visibility: string
  current_version_id: number | null
  /* Identity of the resolved version. Present whenever a version is resolved —
   * i.e. on get/create/update, not on the list. `id` + `version_id` is what a
   * durable indicator persists so it can re-fetch its own authoritative IR;
   * `definitionId` is a UI sentinel and is not identity. */
  version_id?: number
  version_number?: number
  /** Canonical sha-256 of the exact source this version was compiled from. */
  source_hash?: string
  compiler_version?: string
  /* The heavy half, present only when a single script is fetched. */
  source?: string
  /**
   * The server's own compiled IR, read from storage and never recompiled.
   * `null` when the server could not compile the source — a failed compile, or
   * a construct the Python port does not implement (`request.security`
   * compiles in the browser and does not here). Callers must handle null
   * rather than assume an IR is present.
   */
  compiled_ir?: IRProgram | null
  diagnostics?: ScriptDiagnostic[]
  updated_at: string | null
  created_at: string | null
}

/** One compiler diagnostic as stored alongside a version. */
export interface ScriptDiagnostic {
  code: string
  message: string
  severity: string
  [key: string]: unknown
}

export async function listScripts(): Promise<ScriptRecord[]> {
  const { data } = await webClient.get<ApiEnvelope<ScriptRecord[]>>('/indicators/api/scripts')
  return data.data ?? []
}

export async function getScript(id: number): Promise<ScriptRecord | undefined> {
  const { data } = await webClient.get<ApiEnvelope<ScriptRecord>>(`/indicators/api/scripts/${id}`)
  return data.data
}

export async function createScript(payload: {
  name: string
  description?: string
  source: string
}): Promise<ScriptRecord | undefined> {
  const { data } = await webClient.post<ApiEnvelope<ScriptRecord>>(
    '/indicators/api/scripts',
    payload
  )
  return data.data
}

export async function updateScript(
  id: number,
  payload: Partial<{ name: string; description: string; source: string }>
): Promise<ScriptRecord | undefined> {
  const { data } = await webClient.put<ApiEnvelope<ScriptRecord>>(
    `/indicators/api/scripts/${id}`,
    payload
  )
  return data.data
}

export async function deleteScript(id: number): Promise<void> {
  await webClient.delete(`/indicators/api/scripts/${id}`)
}

/** One immutable version in a script's history (source omitted for the list). */
export interface ScriptVersion {
  id: number
  version_number: number
  source_hash: string
  compiler_version: string
  created_at: string | null
  is_current: boolean
}

/** A single version fetched with its full source (for preview / restore). */
export interface ScriptVersionDetail extends ScriptVersion {
  source_code: string
  diagnostics: ScriptDiagnostic[]
  /** Authoritative IR for THIS version — how a layout pinned to an older
   *  version restores without recompiling. Null when the server could not
   *  compile it. */
  compiled_ir: IRProgram | null
}

export async function listVersions(scriptId: number): Promise<ScriptVersion[]> {
  const { data } = await webClient.get<ApiEnvelope<ScriptVersion[]>>(
    `/indicators/api/scripts/${scriptId}/versions`
  )
  return data.data ?? []
}

export async function getVersion(
  scriptId: number,
  versionId: number
): Promise<ScriptVersionDetail | undefined> {
  const { data } = await webClient.get<ApiEnvelope<ScriptVersionDetail>>(
    `/indicators/api/scripts/${scriptId}/versions/${versionId}`
  )
  return data.data
}

/* ── Indicator alerts ───────────────────────────────────────────────────── */

export interface AlertRecord {
  id: number
  script_version_id: number | null
  builtin_id: string | null
  symbol: string
  exchange: string
  timeframe: string
  condition_id: string
  inputs: Record<string, unknown>
  trigger_mode: string
  is_active: boolean
  last_evaluated_bar: number | null
  last_triggered_at: string | null
  created_at: string | null
}

export async function listAlerts(): Promise<AlertRecord[]> {
  const { data } = await webClient.get<ApiEnvelope<AlertRecord[]>>('/indicators/api/alerts')
  return data.data ?? []
}

export async function createAlert(payload: {
  script_version_id?: number
  builtin_id?: string
  symbol: string
  exchange: string
  timeframe: string
  condition_id: string
  inputs?: Record<string, unknown>
  trigger_mode?: string
}): Promise<AlertRecord | undefined> {
  const { data } = await webClient.post<ApiEnvelope<AlertRecord>>('/indicators/api/alerts', payload)
  return data.data
}

export async function updateAlert(
  id: number,
  payload: Partial<{ is_active: boolean; inputs: Record<string, unknown>; timeframe: string }>
): Promise<AlertRecord | undefined> {
  const { data } = await webClient.put<ApiEnvelope<AlertRecord>>(
    `/indicators/api/alerts/${id}`,
    payload
  )
  return data.data
}

export async function deleteAlert(id: number): Promise<void> {
  await webClient.delete(`/indicators/api/alerts/${id}`)
}
