import type { OptionTargetRequest, OptionTargetResponse } from '@/types/option-target'
import { apiClient } from './client'
import { optionChainApi } from './option-chain'

export const optionTargetApi = {
  project: async (apiKey: string, req: OptionTargetRequest): Promise<OptionTargetResponse> => {
    const response = await apiClient.post<OptionTargetResponse>('/optiontarget', {
      ...req,
      apikey: apiKey,
    })
    return response.data
  },

  /** Same `/expiry` endpoint the option chain uses; re-exported so callers don't import two API modules. */
  getExpiries: optionChainApi.getExpiries,
}

/** The expiry API returns DD-MMM-YY; the calculator needs DDMMMYY. */
export function toCompactExpiry(dashed: string): string {
  return dashed.replace(/-/g, '').toUpperCase()
}
