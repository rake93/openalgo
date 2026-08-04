"""Capture a replay fixture for the Option Target Calculator.

Records 1-minute history for an underlying plus a range of option strikes on
one trading day, so a completed trade can be replayed offline against the
projection model without touching a live broker feed. See
`test/test_option_target_replay.py`, which reads the fixture this script
writes and asserts the projection's measured accuracy does not regress.

Usage:
    uv run python scripts/capture_option_target_fixture.py \\
        --underlying BANKNIFTY --expiry 25AUG26 --date 2026-08-04 \\
        --low 56800 --high 58800 --step 100 \\
        --out test/fixtures/option_target/banknifty_2026-08-04.json

Requires the OpenAlgo server to be running locally (POST /api/v1/history) and
at least one active broker session, so `get_first_available_api_key` has a
key to use.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Make sure we can import OpenAlgo's modules from repo root, matching
# scripts/extract_broker_token.py's pattern for standalone scripts.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

from database.auth_db import get_first_available_api_key  # noqa: E402
from services.option_symbol_service import construct_option_symbol  # noqa: E402
from utils.httpx_client import get_httpx_client  # noqa: E402

HISTORY_URL = "http://127.0.0.1:5000/api/v1/history"
HTTP_TIMEOUT = 30.0

# Maps the index exchange (where the underlying's spot/index history lives)
# to the exchange its options trade on.
OPTIONS_EXCHANGE_BY_INDEX = {
    "NSE_INDEX": "NFO",
    "BSE_INDEX": "BFO",
}
DEFAULT_OPTIONS_EXCHANGE = "NFO"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a 1-minute history fixture for Option Target Calculator replay tests."
    )
    parser.add_argument("--underlying", required=True, help="Underlying symbol, e.g. BANKNIFTY")
    parser.add_argument("--expiry", required=True, help="Option expiry, DDMMMYY, e.g. 25AUG26")
    parser.add_argument("--date", required=True, help="Trading date, YYYY-MM-DD")
    parser.add_argument("--low", required=True, type=float, help="Lowest strike to capture")
    parser.add_argument("--high", required=True, type=float, help="Highest strike to capture")
    parser.add_argument("--step", required=True, type=float, help="Strike spacing")
    parser.add_argument(
        "--index-exchange",
        default="NSE_INDEX",
        help="Exchange for the underlying's own history (default: NSE_INDEX)",
    )
    parser.add_argument("--out", required=True, help="Output fixture path (JSON)")
    return parser.parse_args()


def _options_exchange(index_exchange: str) -> str:
    return OPTIONS_EXCHANGE_BY_INDEX.get(index_exchange.upper(), DEFAULT_OPTIONS_EXCHANGE)


def _strike_label(strike: float) -> str:
    """Format a strike the same way `construct_option_symbol` does: no trailing .0."""
    if strike == int(strike):
        return str(int(strike))
    return str(strike)


def _fetch_series(
    client, api_key: str, symbol: str, exchange: str, date_str: str
) -> dict[str, float]:
    """Fetch one symbol's 1-minute closes for one day, keyed by epoch-second string.

    Returns an empty dict (never raises) on any failure - a single missing
    strike must not abort the whole capture.
    """
    payload = {
        "apikey": api_key,
        "symbol": symbol,
        "exchange": exchange,
        "interval": "1m",
        "start_date": date_str,
        "end_date": date_str,
    }
    try:
        response = client.post(HISTORY_URL, json=payload, timeout=HTTP_TIMEOUT)
    except Exception:
        logger.exception("History request failed for %s (%s)", symbol, exchange)
        return {}

    if response.status_code != 200:
        logger.warning(
            "History request for %s (%s) returned HTTP %s", symbol, exchange, response.status_code
        )
        return {}

    body = response.json()
    if body.get("status") != "success":
        logger.warning(
            "History request for %s (%s) failed: %s", symbol, exchange, body.get("message")
        )
        return {}

    bars = body.get("data") or []
    series = {
        str(int(bar["timestamp"])): float(bar["close"])
        for bar in bars
        if bar.get("timestamp") is not None and bar.get("close") is not None
    }
    if not series:
        logger.warning("History request for %s (%s) returned no bars", symbol, exchange)
    return series


def main() -> None:
    args = _parse_args()

    api_key = get_first_available_api_key()
    if not api_key:
        logger.error(
            "No API key available. Log in to at least one broker session before capturing a "
            "fixture (get_first_available_api_key requires an active, non-revoked auth session)."
        )
        sys.exit(1)

    client = get_httpx_client()

    spot_series = _fetch_series(
        client, api_key, args.underlying, args.index_exchange.upper(), args.date
    )
    if not spot_series:
        logger.error(
            "No spot history for %s on %s (%s) - market data unavailable for that date. "
            "Nothing to capture.",
            args.underlying,
            args.date,
            args.index_exchange,
        )
        sys.exit(1)

    strikes: list[float] = []
    strike = args.low
    while strike <= args.high + 1e-9:
        strikes.append(round(strike, 2))
        strike += args.step

    options_exchange = _options_exchange(args.index_exchange)
    options: dict[str, dict[str, float]] = {}
    for strike in strikes:
        for option_type in ("CE", "PE"):
            symbol = construct_option_symbol(args.underlying, args.expiry, strike, option_type)
            series = _fetch_series(client, api_key, symbol, options_exchange, args.date)
            if series:
                options[f"{_strike_label(strike)}{option_type}"] = series

    fixture = {
        "underlying": args.underlying,
        "expiry": args.expiry,
        "date": args.date,
        "spot": spot_series,
        "options": options,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, indent=2))

    # %s, not %d: the platform's SensitiveDataFilter (utils/logging.py) stringifies
    # every log arg before formatting, and "%d" % "2" raises TypeError - which the
    # console formatter's fallback path swallows, dropping every arg from the line.
    logger.info(
        "Captured %s spot bars and %s option series (%s strikes attempted) to %s",
        len(spot_series),
        len(options),
        len(strikes) * 2,
        out_path,
    )


if __name__ == "__main__":
    main()
