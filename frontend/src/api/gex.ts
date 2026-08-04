import { webClient } from './client'

export interface GEXChainItem {
  strike: number
  ce_oi: number
  pe_oi: number
  ce_gamma: number
  pe_gamma: number
  ce_gex: number
  pe_gex: number
  net_gex: number
}

export interface GEXDataResponse {
  status: 'success' | 'error'
  message?: string
  underlying?: string
  spot_price?: number
  futures_price?: number | null
  lot_size?: number
  atm_strike?: number
  expiry_date?: string
  pcr_oi?: number
  total_ce_oi?: number
  total_pe_oi?: number
  total_ce_gex?: number
  total_pe_gex?: number
  total_net_gex?: number
  chain?: GEXChainItem[]
}

export interface UnderlyingsResponse {
  status: 'success' | 'error'
  underlyings: string[]
}

export interface ExpiriesResponse {
  status: 'success' | 'error'
  expiries: string[]
}

export type GEXWeightBy = 'oi' | 'volume'

export interface GEXStrikeLevel {
  strike: number
  call_gex: number
  put_gex: number
  net_gex: number
}

export interface GEXQuality {
  verdict: 'good' | 'degraded' | 'unusable'
  /** False only for `unusable` - a degraded snapshot still draws, with its notes shown. */
  may_draw: boolean
  strikes_used: number
  strikes_priced: number
  both_sides: boolean
  wall_at_edge: boolean
  /** Rendered verbatim in the studies panel. */
  notes: string[]
}

export interface GEXSentimentSignal {
  key: string
  label: string
  detail: string
  bias: 'bullish' | 'bearish' | 'neutral' | 'unavailable'
  /** Which threshold decided `bias`, and by how much - e.g. a PCR of 0.81 against
   * the 0.80 bearish threshold reads neutral, but only by 0.01. */
  why: string
  /** Its share of the composite - walls counts double the other two signals. */
  weight: number
}

export interface GEXSentiment {
  bias: 'bullish' | 'bearish' | 'neutral'
  score: number
  /** Signals matching the composite, and signals with any reading at all. */
  agreeing: number
  participating: number
  signals: GEXSentimentSignal[]
}

export interface GEXLevelsResponse {
  status: 'success' | 'error'
  message?: string
  underlying?: string
  exchange?: string
  expiry_date?: string
  weight_by?: GEXWeightBy
  spot_price?: number
  forward_price?: number
  atm_strike?: number
  lot_size?: number
  dte_days?: number
  strikes?: GEXStrikeLevel[]
  call_wall?: number | null
  put_wall?: number | null
  /**
   * The price where dealer gamma changes sign. `null` when the profile does not
   * cross zero near the forward, which is an ordinary market state the UI shows
   * as "No local cross" - not an error.
   */
  zero_gamma?: number | null
  total_call_gex?: number
  total_put_gex?: number
  net_gex?: number
  /**
   * Positive net gamma stabilises price; negative amplifies moves in BOTH
   * directions. Deliberately not framed as bullish or bearish.
   */
  regime?: 'suppressive' | 'amplifying'
  quality?: GEXQuality
  sentiment?: GEXSentiment
}

export const gexApi = {
  getGEXData: async (params: {
    underlying: string
    exchange: string
    expiry_date: string
  }): Promise<GEXDataResponse> => {
    const response = await webClient.post<GEXDataResponse>('/gex/api/gex-data', params)
    return response.data
  },

  getUnderlyings: async (exchange: string): Promise<UnderlyingsResponse> => {
    const response = await webClient.get<UnderlyingsResponse>(
      `/search/api/underlyings?exchange=${exchange}`
    )
    return response.data
  },

  getExpiries: async (exchange: string, underlying: string): Promise<ExpiriesResponse> => {
    const response = await webClient.get<ExpiriesResponse>(
      `/search/api/expiries?exchange=${exchange}&underlying=${underlying}`
    )
    return response.data
  },

  getGEXLevels: async (
    params: { underlying: string; exchange: string; expiry_date: string; weight_by: GEXWeightBy },
    signal?: AbortSignal
  ): Promise<GEXLevelsResponse> => {
    const response = await webClient.post<GEXLevelsResponse>('/gex/api/gex-levels', params, {
      signal,
    })
    return response.data
  },
}
