"""Depth-mode payloads must carry the last-traded quantity.

A tradeable symbol subscribes to Depth alone — the depth payload embeds the LTP,
so there is no second Quote subscription to fall back on. Anything downstream
that needs per-trade size (the charts workspace's live order flow / footprint,
the live candle's volume) therefore only ever sees what the depth payload
carries. Dhan's `full` packet parses `ltq` off the wire and then dropped it
during normalisation, so the footprint accumulated nothing.

Zerodha, Angel and the Noren family already emit `last_quantity` in depth mode;
these tests pin that contract for Dhan so the omission cannot come back.
"""

import websocket_proxy  # noqa: F401  (imported first to break a circular import)
from broker.dhan.streaming.dhan_adapter import DhanWebSocketAdapter


def _adapter() -> DhanWebSocketAdapter:
    """An adapter instance without __init__'s sockets, threads and DB handles."""
    return object.__new__(DhanWebSocketAdapter)


def _full_packet(**over):
    """A Dhan `full` (mode 3) packet as _parse_full_packet produces it."""
    packet = {
        "type": "full",
        "exchange_segment": 2,
        "security_id": "35004",
        "ltp": 23966.4,
        "ltq": 75,
        "ltt": 1753600000,
        "atp": 23950.0,
        "volume": 338000,
        "total_sell_quantity": 1200,
        "total_buy_quantity": 1400,
        "oi": 12_000_000,
        "oi_high": 12_500_000,
        "oi_low": 11_000_000,
        "open": 23900.0,
        "close": 23890.0,
        "high": 23990.0,
        "low": 23880.0,
        "depth": {
            "buy": [{"price": 23966.35, "quantity": 75, "orders": 3}],
            "sell": [{"price": 23966.45, "quantity": 150, "orders": 5}],
        },
    }
    packet.update(over)
    return packet


def test_depth_payload_carries_last_quantity():
    out = _adapter()._normalize_5depth_data(_full_packet(), "NIFTY28JUL26FUT", "NFO")

    assert out["mode"] == 3
    assert out["last_quantity"] == 75


def test_depth_payload_keeps_cumulative_volume_for_the_trade_delta():
    # `last_quantity` is sticky — brokers repeat the previous trade's size on
    # every book update. Cumulative volume is what lets a consumer difference
    # out the quantity actually traded since the last message, so it has to
    # survive normalisation too.
    out = _adapter()._normalize_5depth_data(_full_packet(), "NIFTY28JUL26FUT", "NFO")

    assert out["volume"] == 338000


def test_depth_payload_defaults_last_quantity_when_the_packet_omits_it():
    packet = _full_packet()
    del packet["ltq"]

    out = _adapter()._normalize_5depth_data(packet, "NIFTY28JUL26FUT", "NFO")

    assert out["last_quantity"] == 0


def test_depth_payload_carries_the_exchange_pressure_fields():
    # Total buy/sell quantity are the *pending* order book totals and the
    # average traded price is the day's VWAP: all three come straight from the
    # exchange, so unlike a bid/ask-classified delta they need no inference.
    # They are what a market-direction readout can be built on honestly.
    # _parse_full_packet already reads all three off the wire.
    out = _adapter()._normalize_5depth_data(_full_packet(), "NIFTY28JUL26FUT", "NFO")

    assert out["total_buy_quantity"] == 1400
    assert out["total_sell_quantity"] == 1200
    assert out["average_price"] == 23950.0


def test_quote_payload_still_carries_last_quantity():
    # Regression guard: the quote branch already emitted it.
    quote = {"type": "quote", "ltp": 23966.4, "ltq": 50, "volume": 338000}

    out = _adapter()._normalize_5depth_data(quote, "NIFTY28JUL26FUT", "NFO")

    assert out["mode"] == 2
    assert out["last_quantity"] == 50
