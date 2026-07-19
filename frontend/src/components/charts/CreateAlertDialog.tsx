/**
 * Create-alert dialog for the OpenScript editor — pick one of the script's
 * `alertcondition()`s and register a headless server-side alert (fires via
 * Socket.IO + Telegram even with the browser closed). Modeled on TradingView's
 * "Create alert on …" dialog, scoped to what the engine supports.
 */

import { type ReactNode, useEffect, useState } from 'react'

export interface AlertCondition {
  conditionId: string
  title: string
  message: string
}

interface CreateAlertDialogProps {
  open: boolean
  symbol: string
  exchange: string
  timeframe: string
  conditions: AlertCondition[]
  /** Script must be saved (has a version) and not dirty. */
  canCreate: boolean
  onCreate: (payload: { conditionId: string; triggerMode: string }) => Promise<void>
  onClose: () => void
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[110px_1fr] items-center gap-3">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div>{children}</div>
    </div>
  )
}

const SELECT =
  'h-9 w-full rounded border border-border bg-background px-2 text-sm outline-none focus:border-primary'

export function CreateAlertDialog({
  open,
  symbol,
  exchange,
  timeframe,
  conditions,
  canCreate,
  onCreate,
  onClose,
}: CreateAlertDialogProps) {
  const [conditionId, setConditionId] = useState('')
  const [triggerMode, setTriggerMode] = useState('bar-close')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setConditionId(conditions[0]?.conditionId ?? '')
      setTriggerMode('bar-close')
      setStatus('')
    }
  }, [open, conditions])

  if (!open) {
    return null
  }

  const selected = conditions.find((c) => c.conditionId === conditionId)
  const disabled = !canCreate || !conditionId || busy

  const submit = async () => {
    if (!conditionId) {
      return
    }
    setBusy(true)
    try {
      await onCreate({ conditionId, triggerMode })
      setStatus(`Alert created — ${symbol} ${timeframe}`)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : 'Failed to create alert')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-[460px] max-w-full rounded-lg border border-border bg-card p-5 text-foreground shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">
            Create alert on <span className="text-primary">{symbol}</span>
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-lg leading-none text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {!canCreate ? (
          <p className="py-4 text-sm text-muted-foreground">Save the script first to create alerts on it.</p>
        ) : conditions.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            This script has no <code>alertcondition()</code>. Add one to create alerts.
          </p>
        ) : (
          <div className="space-y-3">
            <Row label="Condition">
              <select value={conditionId} onChange={(e) => setConditionId(e.target.value)} className={SELECT}>
                {conditions.map((c) => (
                  <option key={c.conditionId} value={c.conditionId}>
                    {c.title || c.conditionId}
                  </option>
                ))}
              </select>
            </Row>
            <Row label="Symbol">
              <div className="text-sm">
                {symbol} · {exchange} · {timeframe}
              </div>
            </Row>
            <Row label="Trigger">
              <select value={triggerMode} onChange={(e) => setTriggerMode(e.target.value)} className={SELECT}>
                <option value="bar-close">Once per bar close</option>
                <option value="intrabar">Once per bar (intrabar)</option>
              </select>
            </Row>
            <Row label="Message">
              <div className="rounded border border-border bg-background px-2 py-1.5 text-sm text-muted-foreground">
                {selected?.message || '—'}
              </div>
            </Row>
            <Row label="Notifications">
              <div className="text-sm text-muted-foreground">App toast · Telegram</div>
            </Row>
          </div>
        )}

        <div className="mt-5 flex items-center justify-between">
          <span className="text-xs text-muted-foreground">{status}</span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="h-9 rounded px-4 text-sm font-medium hover:bg-accent"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void submit()}
              disabled={disabled}
              className="h-9 rounded bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
            >
              {busy ? 'Creating…' : 'Create'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
