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
