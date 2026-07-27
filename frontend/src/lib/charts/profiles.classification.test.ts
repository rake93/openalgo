/**
 * Trade classification for the live footprint.
 *
 * The quote rule compares a print against the quote that was *prevailing when it
 * traded*. OpenAlgo's depth feed reports a book update and the last traded price
 * in the same packet, but the trade happened before that book update — it is
 * what caused it. Classifying against the book that arrived with the print
 * inverts the common case: a buy that lifts the ask consumes that level, so the
 * post-trade best bid sits at the traded price and the print reads as a sell.
 */

import { describe, expect, it } from 'vitest'
import { ProfileManager } from './profiles'

function manager() {
  const m = new ProfileManager({
    onChange: () => {},
    refPrice: () => 100,
    tickSize: () => 0.1,
    visibleRange: () => null,
  })
  // Row size pinned to one tick so each price keeps its own cell.
  m.setFootprintConfig({ enabled: true, rowSize: 0.1 })
  return m
}

const book = (bid: number, ask: number) => ({
  bids: [{ price: bid, qty: 100 }],
  asks: [{ price: ask, qty: 100 }],
  ltp: bid,
})

describe('footprint trade classification', () => {
  it('classifies a print against the quote prevailing before it traded', () => {
    const m = manager()

    // Prevailing quote: 100.00 bid / 100.10 ask.
    m.onDepth(book(100.0, 100.1))

    // A buy lifts the 100.10 ask. That consumes the level, so the packet
    // carrying the print reports a book already moved up a tick.
    m.onDepth(book(100.1, 100.2))
    m.onTrade({ time: 60, price: 100.1, qty: 50 })

    const bar = m.footprintTape[0]
    expect(bar).toBeDefined()
    // Buy-initiated: it hit the ask, so delta is positive.
    expect(bar.delta).toBe(50)
    expect(bar.cells[0].askVol).toBe(50)
    expect(bar.cells[0].bidVol).toBe(0)
  })

  it('classifies a print that hits the bid as sell-initiated', () => {
    const m = manager()

    m.onDepth(book(100.0, 100.1))

    // A sell hits the 100.00 bid, dropping the book a tick.
    m.onDepth(book(99.9, 100.0))
    m.onTrade({ time: 60, price: 100.0, qty: 40 })

    const bar = m.footprintTape[0]
    expect(bar.delta).toBe(-40)
    expect(bar.cells[0].bidVol).toBe(40)
  })

  it('drops the tape and the quote when the feed reconnects', () => {
    const m = manager()
    m.onDepth(book(100.0, 100.1))
    m.onDepth(book(100.1, 100.2))
    m.onTrade({ time: 60, price: 100.1, qty: 50 })
    expect(m.footprintTape.length).toBe(1)

    // A symbol or timeframe switch: the old instrument's columns and its
    // cumulative delta must not survive into the new one.
    m.resetTape()
    expect(m.footprintTape.length).toBe(0)

    // The stale quote is gone too, so the first print of the new instrument is
    // not classified against the old book.
    m.onTrade({ time: 60, price: 100.1, qty: 10 })
    expect(m.footprintTape[0].cells[0].askVol).toBe(10) // tick rule, not stale bid
  })

  it('ignores zero-quantity messages so no-trade book updates build nothing', () => {
    const m = manager()
    m.onDepth(book(100.0, 100.1))
    m.onTrade({ time: 60, price: 100.05, qty: 0 })

    expect(m.footprintTape.length).toBe(0)
  })
})
