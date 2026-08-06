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
  forward_price?: number | null
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
  /**
   * `call_dex` is the call leg's own notional delta exposure, sign fixed by
   * side (always >= 0). `put_dex` is the put leg's own notional delta
   * exposure, sign fixed by side (always <= 0). `net_dex` is call_dex +
   * put_dex - positive where calls dominate the strike. Required rather than
   * optional - the server always sends both metrics in one payload, and an
   * optional field defaulted to 0 would draw a flat profile that looks like
   * real data.
   */
  call_dex: number
  put_dex: number
  net_dex: number
}

/**
 * Which Greek the strike-bar profile is drawn from. Client-only - a UI
 * rendering selector, not part of the server payload. Colocated here rather
 * than in a UI module so both consumers keep reusing this existing import.
 */
export type GexMetric = 'gamma' | 'delta'

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

/**
 * One recorded minute of the three levels, from the snapshot recorder.
 *
 * Every level is nullable and each null is a READING, not a hole in the
 * payload: `zero_gamma` is null whenever the gamma profile does not cross zero
 * near the forward, which the study already labels "No local cross". The band
 * renderer breaks its line rather than substituting a number.
 */
export interface GEXHistoryPoint {
  /** Epoch seconds, floored to the recorder's cadence. */
  ts: number
  call_wall: number | null
  put_wall: number | null
  zero_gamma: number | null
  net_gex: number | null
  regime: 'suppressive' | 'amplifying' | null
  /** What the snapshot's quality was WHEN RECORDED, so a reader can dim it. */
  quality_verdict: 'good' | 'degraded' | 'unusable' | null
}

export interface GEXHistoryResponse {
  status: 'success' | 'error'
  message?: string
  underlying?: string
  exchange?: string
  expiry_date?: string
  weight_by?: GEXWeightBy
  /**
   * The cadence actually returned. Always `1m` today; phase 5's heatmap
   * downsamples above a column budget, and a series that silently thinned
   * itself would look like a market that went quiet.
   */
  resolution?: string
  downsampled?: boolean
  /**
   * Whether this contract is on the recorder's watchlist, and which series it
   * belongs to.
   *
   * Answered by the server so the UI never re-derives the exchange mapping to
   * work it out. A chart sends its OWN exchange (`NSE_INDEX` for a NIFTY index
   * chart) while the watchlist stores the options exchange (`NFO`); matching
   * those client-side is exactly the duplication that made Bands draw nothing.
   *
   * `recorded: true` with an empty `points` is normal - it is the first minute
   * after switching recording on, and must not look like "not recorded".
   */
  recorded?: boolean
  series_id?: number | null
  /** Empty for a contract nobody chose to record - an ordinary state, not an error. */
  points?: GEXHistoryPoint[]
}

/** One recorded minute of the strike profile, aligned to the grid's strike axis. */
export interface GEXGridColumn {
  /** Epoch seconds, floored to the recorder's cadence (or to the bucket, if thinned). */
  ts: number
  /**
   * One value per entry in `strikes`, same order and length.
   *
   * `null` is a strike that minute's chain did not carry - the renderer leaves
   * that cell blank. It is never 0, which would assert gamma nobody measured.
   */
  values: (number | null)[]
  /** What the snapshot's quality was WHEN RECORDED, for the requested weighting. */
  quality: 'good' | 'degraded' | 'unusable' | null
}

export interface GEXGridResponse {
  status: 'success' | 'error'
  message?: string
  underlying?: string
  exchange?: string
  expiry_date?: string
  weight_by?: GEXWeightBy
  metric?: GexMetric
  /**
   * The cadence actually returned: `1m`, `5m` or `15m`.
   *
   * Always paired with `downsampled`, and both ride on the un-thinned response
   * too. A heatmap that quietly dropped four of every five columns would look
   * like a market that went quiet.
   */
  resolution?: string
  downsampled?: boolean
  recorded?: boolean
  series_id?: number | null
  /** The shared y axis, ascending. The union of every column's strikes. */
  strikes?: number[]
  /** Ordered by `ts` ascending. A minute the recorder missed is simply absent. */
  columns?: GEXGridColumn[]
  /**
   * The largest absolute value in the window, for normalising the colour scale.
   *
   * Computed server-side across the WHOLE window rather than per column:
   * normalising each column against its own maximum would paint every column at
   * full saturation and erase the change through time the heatmap exists to show.
   */
  max_abs_value?: number
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

  /**
   * Recorded levels for one contract over a window, backing Gamma Bands.
   *
   * `expiry_date` must be a RESOLVED DDMMMYY, never a rule: a "nearest" series
   * rolls weekly, and history spliced across a roll would show wall jumps that
   * are the book changing rather than the market moving. Callers take it from
   * the live snapshot they already hold.
   */
  getGEXHistory: async (
    params: {
      underlying: string
      exchange: string
      expiry_date: string
      weight_by: GEXWeightBy
      from_ts: number
      to_ts: number
    },
    signal?: AbortSignal
  ): Promise<GEXHistoryResponse> => {
    const response = await webClient.post<GEXHistoryResponse>('/gex/api/gex-history', params, {
      signal,
    })
    return response.data
  },

  /**
   * The recorded per-strike grid for one contract, backing the GEX Heatmap.
   *
   * Same window rules as `getGEXHistory` - a RESOLVED expiry, never a rule -
   * plus a `metric`, because gamma and delta are both recorded off one chain
   * fetch and switching between them costs no refetch on the server either.
   */
  getGEXGrid: async (
    params: {
      underlying: string
      exchange: string
      expiry_date: string
      weight_by: GEXWeightBy
      metric: GexMetric
      from_ts: number
      to_ts: number
    },
    signal?: AbortSignal
  ): Promise<GEXGridResponse> => {
    const response = await webClient.post<GEXGridResponse>(
      '/gex/api/gex-history',
      { ...params, fields: 'grid' },
      { signal }
    )
    return response.data
  },

  addGEXSeries: async (params: {
    underlying: string
    exchange: string
    expiry_rule: string
  }): Promise<{ status: string; message?: string }> => {
    const response = await webClient.post('/gex/api/gex-series', params)
    return response.data
  },

  removeGEXSeries: async (seriesId: number): Promise<{ status: string; message?: string }> => {
    const response = await webClient.delete(`/gex/api/gex-series/${seriesId}`)
    return response.data
  },
}
