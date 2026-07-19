/** Indicator engine session APIs (CSRF-protected web routes). */

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

/** Serializable workspace state stored in layout_json. */
export interface ChartLayoutState {
  indicators: { definitionId: string; inputs: Record<string, unknown> }[]
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
  payload: Partial<{ name: string; symbol: string; exchange: string; timeframe: string; layout: ChartLayoutState }>
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
  /** Present when a single script is fetched (get/create/update). */
  version_number?: number
  source?: string
  updated_at: string | null
  created_at: string | null
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
  const { data } = await webClient.post<ApiEnvelope<ScriptRecord>>('/indicators/api/scripts', payload)
  return data.data
}

export async function updateScript(
  id: number,
  payload: Partial<{ name: string; description: string; source: string }>
): Promise<ScriptRecord | undefined> {
  const { data } = await webClient.put<ApiEnvelope<ScriptRecord>>(`/indicators/api/scripts/${id}`, payload)
  return data.data
}

export async function deleteScript(id: number): Promise<void> {
  await webClient.delete(`/indicators/api/scripts/${id}`)
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
  const { data } = await webClient.put<ApiEnvelope<AlertRecord>>(`/indicators/api/alerts/${id}`, payload)
  return data.data
}

export async function deleteAlert(id: number): Promise<void> {
  await webClient.delete(`/indicators/api/alerts/${id}`)
}
