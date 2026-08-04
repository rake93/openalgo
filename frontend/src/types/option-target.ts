export type IvModel = 'smile_slide' | 'sticky_strike'
export type Objective = 'balanced' | 'max_pnl' | 'max_return' | 'max_rr' | 'max_robust'
export type Reference = 'FUT' | 'SPOT'
export type DayCount = 'calendar' | 'trading'
export type OptionSide = 'AUTO' | 'CE' | 'PE'
export type VolBetaSource = 'estimated' | 'fallback' | 'preset' | 'manual'
export type ForwardMode = 'exact' | 'basis_modelled'

export interface OptionTargetRequest {
  apikey: string
  underlying: string
  exchange: string
  /** DDMMMYY, e.g. "04AUG26". Omit to default to the nearest live expiry. */
  expiry_date?: string
  target_price: number
  reference?: Reference
  reference_price?: number
  hold_minutes?: number
  hold_days?: number
  iv_model?: IvModel
  vol_beta?: number | string
  vol_shift?: number
  day_count?: DayCount
  strike_count?: number
  side?: OptionSide
  lots?: number
  interest_rate?: number
  objective?: Objective
}

export interface Snapshot {
  underlying: string
  exchange: string
  expiry_date: string
  spot: number
  forward: number
  /** Forward minus spot. Null when the underlying has no spot instrument
   *  (e.g. MCX) — see `parity_vs_underlying` for the equivalent number there. */
  basis: number | null
  /** Present when the pricing underlying is a future rather than spot (MCX
   *  and other commodity exchanges) — see services/pricing_underlying.py. */
  underlying_ref?: {
    symbol: string
    exchange: string
    kind: 'SPOT' | 'FUTURE'
    option_expiry: string | null
    underlying_expiry: string | null
    method: string
  }
  /** Discrepancy between the put-call-parity forward and the linked future.
   *  Only meaningful (non-null) when underlying_ref.kind is 'FUTURE'. */
  parity_vs_underlying?: number | null
  forward_source: string
  atm_strike: number
  strike_step: number
  atm_iv_pct: number
  days_to_expiry: number
  is_zero_dte: boolean
  /** Null when `basis` is null — the carry-bound check that produces this
   *  has no spot to check carry against. */
  basis_plausible: boolean | null
  market_open: boolean
  t_years: number
  matched_future: string | null
  lot_size: number
}

export interface SmileInfo {
  a: number
  b: number
  c: number
  x_lo: number
  x_hi: number
  rms_vol_pts: number
  n_points: number
  degenerate: boolean
  rejected: unknown[]
}

export interface VolBetaInfo {
  beta: number
  r_squared: number
  samples: number
  source: VolBetaSource
  reason: string
  /**
   * Raw measured beta when it exceeded the Panic preset and was clamped back,
   * otherwise null. Only an estimate is ever clamped; a preset or a manual
   * value is used as given.
   */
  clamped_from: number | null
}

export interface Scenario {
  reference: Reference
  reference_now: number
  reference_target: number
  forward_target: number
  forward_mode: ForwardMode
  move_pct: number
  hold_minutes: number
  day_count: DayCount
  t_target_years: number
  iv_model: IvModel
  iv_model_requested: IvModel
  iv_model_overridden: boolean
  vol_beta: VolBetaInfo
  vol_shift: number
  side: OptionSide
  objective: Objective
  lots: number
}

export interface Attribution {
  delta: number
  gamma: number
  theta: number
  vega: number
  spread: number
  residual: number
  total: number
}

export interface GreeksNow {
  delta: number
  gamma: number
  theta: number
  vega: number
}

export interface ScenarioPnl {
  '50': number
  '75': number
  '100': number
}

export interface Candidate {
  strike: number
  option_type: 'CE' | 'PE'
  symbol: string
  label: string
  lot_size: number
  bid: number
  ask: number
  mid_now: number
  spread_pct: number
  entry_cost: number
  iv_now_pct: number
  iv_target_pct: number
  greeks_now: GreeksNow
  projected_premium: number
  exit_value: number
  pnl_per_lot: number
  pnl_total: number
  return_pct: number
  effective_delta: number
  theta_cost_per_lot: number
  adverse_premium: number
  adverse_pnl_per_lot: number
  reward_risk: number
  scenario_pnl: ScenarioPnl
  robust_pnl_per_lot: number
  attribution: Attribution
  oi: number
  volume: number
  excluded: boolean
  exclude_reason: string
  recommended: boolean
  recommend_reason: string
  score: number
}

export interface LadderRow {
  reference_level: number
  premium: number
  pnl_per_lot: number
}

export interface OptionTargetResponse {
  status: 'success' | 'error'
  snapshot: Snapshot
  smile: SmileInfo
  scenario: Scenario
  candidates: Candidate[]
  recommended_strike: number
  ladder: LadderRow[]
  warnings: string[]
  message?: string
}
