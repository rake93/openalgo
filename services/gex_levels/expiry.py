"""Expiry-string parsing shared by the options analytics services."""

from datetime import datetime

_MONTH_MAP = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def expiry_datetime(expiry_date: str, exchange: str) -> datetime:
    """
    Build an expiry datetime from a DDMMMYY string and its exchange.

    Uses the same default expiry times as
    `option_greeks_service.parse_option_symbol`: NFO/BFO 15:30, CDS 12:30,
    MCX 23:30.

    Args:
        expiry_date: Expiry in DDMMMYY format (e.g. 11AUG26).
        exchange: Options exchange (NFO, BFO, CDS, MCX, ...).

    Returns:
        Naive datetime at the exchange close, interpreted as IST downstream.
    """
    day = int(expiry_date[:2])
    month = _MONTH_MAP[expiry_date[2:5].upper()]
    year = 2000 + int(expiry_date[5:7])

    ex = exchange.upper()
    if ex == "MCX":
        hour, minute = 23, 30
    elif ex == "CDS":
        hour, minute = 12, 30
    else:  # NFO, BFO, crypto, equity
        hour, minute = 15, 30

    return datetime(year, month, day, hour, minute)
