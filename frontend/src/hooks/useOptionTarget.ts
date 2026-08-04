import axios from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import { optionTargetApi } from '@/api/option-target'
import type { OptionTargetRequest, OptionTargetResponse } from '@/types/option-target'

interface UseOptionTargetOptions {
  apiKey: string | null
  request: OptionTargetRequest | null
  frozen: boolean
  intervalMs?: number
}

interface UseOptionTargetResult {
  data: OptionTargetResponse | null
  error: string | null
  isLoading: boolean
  updatedAt: Date | null
  refetch: () => void
}

/**
 * Turn a thrown request error into something a trader can act on.
 *
 * Deliberately never reuses the backend's own error text. The two must stay
 * distinguishable: a message that could have come from either side makes it
 * impossible to tell a rejected projection from an unreachable endpoint.
 */
function extractErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as { message?: string } | undefined
    if (data?.message) return data.message
    const status = err.response?.status
    return status
      ? `Projection request failed with HTTP ${status}.`
      : `Could not reach the projection endpoint: ${err.message}`
  }
  if (err instanceof Error) return err.message
  return 'Projection request failed for an unknown reason.'
}

/**
 * True when the body is not the JSON envelope this endpoint returns.
 *
 * The usual cause is the SPA index.html served by the app's 404 fallthrough,
 * which arrives as HTTP 200 and so does not throw. That happens when the API
 * route is missing from the running process, typically because the server has
 * not been restarted since the namespace was added.
 */
function isNotOurPayload(response: unknown): boolean {
  return typeof response !== 'object' || response === null || !('status' in response)
}

/**
 * Polls POST /api/v1/optiontarget while `frozen` is false. Freezing stops all
 * fetching (no request goes out at all) so the user can inspect a snapshot
 * without it changing under them; the last data stays visible.
 *
 * Callers should memoize `request` (e.g. useMemo) — a new object identity on
 * every render restarts the poll cycle since it is an effect dependency.
 */
export function useOptionTarget({
  apiKey,
  request,
  frozen,
  intervalMs = 5000,
}: UseOptionTargetOptions): UseOptionTargetResult {
  const [data, setData] = useState<OptionTargetResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  // Monotonically increasing request id. Polling plus rapid input edits can
  // fire overlapping requests; only the response matching the latest id is
  // allowed to update state, so a stale response never clobbers a fresh one.
  const requestIdRef = useRef(0)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchData = useCallback(async () => {
    if (!apiKey || !request) return

    const requestId = ++requestIdRef.current
    setIsLoading(true)

    try {
      const response = await optionTargetApi.project(apiKey, request)
      if (requestIdRef.current !== requestId) return // superseded by a newer request

      if (isNotOurPayload(response)) {
        setError(
          'The projection endpoint returned a non-JSON response. The API route is ' +
            'probably not registered in the running server - restart it to pick up ' +
            '/api/v1/optiontarget.'
        )
        return
      }

      if (response.status === 'success') {
        setData(response)
        setError(null)
        setUpdatedAt(new Date())
      } else {
        // A transient failure must not blank a table the user is reading:
        // surface the error but keep the previous data in place.
        setError(response.message || 'The server rejected the projection request.')
      }
    } catch (err) {
      if (requestIdRef.current !== requestId) return
      setError(extractErrorMessage(err))
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false)
    }
  }, [apiKey, request])

  useEffect(() => {
    if (!apiKey || !request || frozen) return undefined

    fetchData()
    intervalRef.current = setInterval(fetchData, intervalMs)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [apiKey, request, frozen, intervalMs, fetchData])

  const refetch = useCallback(() => {
    fetchData()
  }, [fetchData])

  return { data, error, isLoading, updatedAt, refetch }
}
