import { useState } from 'react'

import { tradingApi } from '@/api/trading'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { useThemeStore } from '@/stores/themeStore'
import type { Candidate, Snapshot } from '@/types/option-target'
import { showToast } from '@/utils/toast'

/** Options are intraday or carry-forward only; CNC is an equity product. */
const PRODUCT = 'MIS'
const PRICE_TYPE = 'MARKET'

/** Above this, a market order's slippage is worth calling out by name. */
const WIDE_SPREAD_PCT = 1.0

interface Props {
  candidate: Candidate | null
  snapshot: Snapshot | null
  lots: number
  apiKey: string | null
  onOpenChange: (open: boolean) => void
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums font-medium text-right">{value}</span>
    </div>
  )
}

export default function PlaceOrderDialog({
  candidate,
  snapshot,
  lots,
  apiKey,
  onOpenChange,
}: Props) {
  const [isPlacing, setIsPlacing] = useState(false)
  const appMode = useThemeStore((s) => s.appMode)

  if (!candidate || !snapshot) return null

  const quantity = lots * candidate.lot_size
  const estimatedCost = candidate.mid_now * quantity
  const isAnalyzer = appMode === 'analyzer'

  const placeOrder = async () => {
    if (!apiKey) {
      showToast.error('No API key available')
      return
    }
    setIsPlacing(true)
    try {
      const response = await tradingApi.placeOrder({
        apikey: apiKey,
        strategy: 'Option Target Calculator',
        exchange: snapshot.exchange,
        symbol: candidate.symbol,
        action: 'BUY',
        quantity,
        pricetype: PRICE_TYPE,
        product: PRODUCT,
      })
      if (response.status === 'success') {
        showToast.success(
          `${isAnalyzer ? 'Analyzer' : 'Live'} order placed: ${candidate.symbol}` +
            (response.data?.orderid ? ` (${response.data.orderid})` : '')
        )
        onOpenChange(false)
      } else {
        showToast.error(response.message || 'Order rejected')
      }
    } catch (err) {
      showToast.error(err instanceof Error ? err.message : 'Order failed')
    } finally {
      setIsPlacing(false)
    }
  }

  return (
    <AlertDialog open onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            Buy {candidate.strike} {candidate.option_type}
            <Badge variant={isAnalyzer ? 'secondary' : 'destructive'}>
              {isAnalyzer ? 'Analyzer mode' : 'Live order'}
            </Badge>
          </AlertDialogTitle>
          <AlertDialogDescription>
            {isAnalyzer
              ? 'Analyzer mode is on, so this is simulated against the sandbox and places nothing with your broker.'
              : 'This places a real order with your broker using real money.'}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="text-sm border rounded-md px-3 py-2">
          <Row label="Symbol" value={candidate.symbol} />
          <Row label="Exchange" value={snapshot.exchange} />
          <Row label="Action" value="BUY" />
          <Row
            label="Quantity"
            value={`${quantity} (${lots} lot${lots === 1 ? '' : 's'} x ${candidate.lot_size})`}
          />
          <Row label="Order type" value={`${PRICE_TYPE} / ${PRODUCT}`} />
          <Row
            label="Estimated cost"
            value={estimatedCost.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
          />
          <Row label="Spread" value={`${candidate.spread_pct.toFixed(2)}%`} />
        </div>

        {candidate.spread_pct > WIDE_SPREAD_PCT && (
          <p className="text-xs text-destructive">
            The book is {candidate.spread_pct.toFixed(2)}% wide. A market order can fill well
            outside the {candidate.mid_now.toFixed(2)} mid this projection assumed, so the realised
            P&amp;L may differ from the table.
          </p>
        )}

        <p className="text-xs text-muted-foreground">
          The projection is an estimate, not a forecast. Nothing here guarantees the target is
          reached.
        </p>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPlacing}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault()
              void placeOrder()
            }}
            disabled={isPlacing}
          >
            {isPlacing ? 'Placing...' : isAnalyzer ? 'Place simulated order' : 'Place live order'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
