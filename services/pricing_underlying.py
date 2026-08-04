"""Shared, expiry-aware pricing-underlying resolver.

A "pricing underlying" is the instrument whose live price should be used to
value a set of options — the thing a Black-Scholes/Black-76 pricer, a Greeks
calculator, or the Option Target Calculator plugs in as spot/forward. For NSE
and BSE index and equity options (`NFO`, `BFO`) that instrument is simply the
underlying's own spot quote: `NIFTY` options price off `NIFTY` spot.

MCX (and the other commodity exchanges, `NCDEX`/`NCO`) has no such spot
instrument. Commodity options are written on a futures contract, not a cash
market, so there is no tradeable "CRUDEOIL" quote to fetch — only dated
contracts like `CRUDEOIL19AUG26FUT`. Worse, the option's own expiry and the
future's expiry are usually different dates:

    CRUDEOIL17AUG269150CE   name=CRUDEOIL   CE   expiry 17-AUG-26
    CRUDEOIL19AUG26FUT      name=CRUDEOIL   FUT  expiry 19-AUG-26

Pricing a CRUDEOIL option therefore means finding the *right* future — the
one that will still be live when the option expires — not just any future
with a matching name.

There is no option-to-future foreign key anywhere in the `SymToken` schema
(columns: symbol, brsymbol, name, exchange, brexchange, token, expiry,
strike, lotsize, instrumenttype, tick_size, contract_value). `name` is the
strongest linkage available: it holds the commodity root and separates
contract families exactly (`CRUDEOIL` vs `CRUDEOILM`, `GOLD` vs `GOLDM`) where
prefix-matching the `symbol` column would not — `CRUDEOIL%` also matches
`CRUDEOILM...`, silently pricing the mini contract's options off the
full-size future. This module matches on `name` (exact, case-normalized)
plus `exchange` plus `instrumenttype == "FUT"`, and says so rather than
implying a stronger relationship exists.

`SymToken.name` is only populated for the exchanges that need this
disambiguation — it is `NULL` on `NFO` — so the futures-lookup branch below is
scoped to the commodity exchanges by construction; NFO/BFO callers never touch
the database here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from cachetools import TTLCache

from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Commodity exchanges: options are written on a future, not a spot. Every
# other exchange keeps today's spot-quoting behaviour untouched.
FUTURES_UNDERLYING_EXCHANGES = frozenset({"MCX", "NCDEX", "NCO"})

_MONTHS = {
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

# Symbol masters refresh roughly once a day (broker download / ~3 AM IST
# rollover), so a resolved link is valid for a long time. Bounded and
# time-limited so a Gunicorn worker that never restarts (-w 1, no scheduled
# recycle) cannot accumulate one entry per (symbol, exchange, expiry) forever.
_RESOLUTION_CACHE: TTLCache = TTLCache(maxsize=256, ttl=3600)


@dataclass(frozen=True)
class UnderlyingRef:
    """The instrument whose price should be used to price a set of options."""

    symbol: str
    exchange: str
    kind: str  # "SPOT" or "FUTURE"
    option_expiry: str | None  # DDMMMYY as supplied by the caller
    underlying_expiry: str | None  # DD-MMM-YY from the master; None for spot
    method: str  # how it was resolved, for display and debugging


def requires_futures_underlying(options_exchange: str) -> bool:
    """Whether `options_exchange` prices its options off a future, not spot."""
    return (options_exchange or "").upper() in FUTURES_UNDERLYING_EXCHANGES


def _parse_dashed_expiry(expiry: str) -> date:
    """Parse a `DD-MMM-YY` expiry string, as stored in `SymToken.expiry`.

    Raises:
        ValueError: `expiry` does not match the `DD-MMM-YY` shape.
    """
    try:
        day_s, mon_s, year_s = expiry.strip().split("-")
        month = _MONTHS[mon_s.strip().upper()]
        day = int(day_s)
        year = 2000 + int(year_s)
        return date(year, month, day)
    except (AttributeError, KeyError, ValueError) as exc:
        raise ValueError(f"Unparsable expiry {expiry!r}; expected DD-MMM-YY") from exc


def _parse_compact_expiry(expiry: str) -> date:
    """Parse a `DDMMMYY` expiry string, as callers supply `option_expiry`.

    Raises:
        ValueError: `expiry` does not match the `DDMMMYY` shape.
    """
    try:
        cleaned = expiry.strip().upper()
        day = int(cleaned[:2])
        month = _MONTHS[cleaned[2:5]]
        year = 2000 + int(cleaned[5:7])
        return date(year, month, day)
    except (KeyError, ValueError, IndexError) as exc:
        raise ValueError(f"Unparsable option_expiry {expiry!r}; expected DDMMMYY") from exc


def _find_linked_futures(base_symbol: str, options_exchange: str) -> list[tuple[str, str]]:
    """Query `SymToken` for the futures linked to `base_symbol` by `name`.

    Returns a list of (symbol, expiry) pairs, unsorted, with rows that have no
    expiry dropped. Matching is exact (case-normalized) on `name` — never a
    prefix match, which would collide `CRUDEOIL` with `CRUDEOILM`.

    Raises whatever the database layer raises; callers are responsible for
    catching, logging, and degrading. Imports `database.symbol` locally so the
    spot path (the common case for NFO/BFO) never touches the DB module at
    all, and to avoid a startup import cycle.
    """
    from database.symbol import SymToken, db_session

    upper_symbol = base_symbol.upper()
    upper_exchange = options_exchange.upper()
    with db_session() as session:
        rows = (
            session.query(SymToken.symbol, SymToken.expiry)
            .filter(
                SymToken.name == upper_symbol,
                SymToken.exchange == upper_exchange,
                SymToken.instrumenttype == "FUT",
            )
            .all()
        )
    return [(row[0], row[1]) for row in rows if row[1]]


def _spot_ref(
    base_symbol: str,
    options_exchange: str,
    option_expiry: str | None,
    spot_symbol: str | None,
    spot_exchange: str | None,
    method: str,
) -> UnderlyingRef:
    return UnderlyingRef(
        symbol=spot_symbol or base_symbol,
        exchange=spot_exchange or options_exchange,
        kind="SPOT",
        option_expiry=option_expiry,
        underlying_expiry=None,
        method=method,
    )


def _resolve_futures_underlying(
    base_symbol: str,
    options_exchange: str,
    option_expiry: str | None,
) -> UnderlyingRef:
    """Resolve the linked future for a commodity root, degrading to spot on failure."""
    try:
        candidates = _find_linked_futures(base_symbol, options_exchange)
    except Exception:
        logger.exception(
            "Pricing-underlying lookup failed for %s/%s; degrading to spot",
            base_symbol,
            options_exchange,
        )
        return _spot_ref(base_symbol, options_exchange, option_expiry, None, None, "lookup_failed")

    parsed: list[tuple[str, str, date]] = []
    for symbol, expiry_str in candidates:
        try:
            parsed.append((symbol, expiry_str, _parse_dashed_expiry(expiry_str)))
        except ValueError:
            logger.warning("Skipping future %s with unparsable expiry %r", symbol, expiry_str)

    if not parsed:
        logger.warning(
            "No linked future found for %s on %s; degrading to spot",
            base_symbol,
            options_exchange,
        )
        return _spot_ref(
            base_symbol,
            options_exchange,
            option_expiry,
            None,
            None,
            "no_linked_future_found",
        )

    if option_expiry:
        try:
            reference_date = _parse_compact_expiry(option_expiry)
            method = "linked_future_nearest_on_or_after_option_expiry"
        except ValueError:
            logger.warning(
                "Unparsable option_expiry %r for %s/%s; using today instead",
                option_expiry,
                base_symbol,
                options_exchange,
            )
            reference_date = datetime.now(IST).date()
            method = "linked_future_nearest_live"
    else:
        reference_date = datetime.now(IST).date()
        method = "linked_future_nearest_live"

    on_or_after = [p for p in parsed if p[2] >= reference_date]
    if on_or_after:
        chosen = min(on_or_after, key=lambda p: p[2])
    else:
        chosen = max(parsed, key=lambda p: p[2])
        method = "linked_future_latest_available_fallback"
        logger.warning(
            "All linked futures for %s on %s expire before %s; falling back to latest "
            "available future %s",
            base_symbol,
            options_exchange,
            reference_date,
            chosen[0],
        )

    return UnderlyingRef(
        symbol=chosen[0],
        exchange=options_exchange.upper(),
        kind="FUTURE",
        option_expiry=option_expiry,
        underlying_expiry=chosen[1],
        method=method,
    )


def resolve_pricing_underlying(
    base_symbol: str,
    options_exchange: str,
    option_expiry: str | None = None,
    spot_symbol: str | None = None,
    spot_exchange: str | None = None,
) -> UnderlyingRef:
    """Resolve the instrument that should be used to price `base_symbol` options.

    For every exchange except the commodity ones in
    `FUTURES_UNDERLYING_EXCHANGES`, this returns a `SPOT` ref immediately with
    no database access — today's behaviour, untouched. For MCX/NCDEX/NCO it
    looks up the linked future by `SymToken.name`, choosing the nearest future
    that is still live at (on or after) the option's own expiry, and degrades
    to a `SPOT` ref — never an exception — if that lookup fails or comes up
    empty. See the module docstring for why a future is needed at all.

    Args:
        base_symbol: The commodity/underlying root, e.g. "CRUDEOIL", "NIFTY".
        options_exchange: The exchange the options trade on, e.g. "MCX", "NFO".
        option_expiry: The option's own expiry as DDMMMYY (e.g. "17AUG26"), if
            known. Used to pick the nearest future that outlives the option.
        spot_symbol: Override for the spot path's resolved symbol. Defaults to
            `base_symbol`.
        spot_exchange: Override for the spot path's resolved exchange.
            Defaults to `options_exchange`.

    Returns:
        An `UnderlyingRef` describing what to price and how it was resolved.
    """
    exchange_upper = (options_exchange or "").upper()

    if exchange_upper not in FUTURES_UNDERLYING_EXCHANGES:
        return _spot_ref(
            base_symbol, options_exchange, option_expiry, spot_symbol, spot_exchange, "spot_default"
        )

    cache_key = ((base_symbol or "").upper(), exchange_upper, option_expiry)
    cached = _RESOLUTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    ref = _resolve_futures_underlying(base_symbol, options_exchange, option_expiry)
    _RESOLUTION_CACHE[cache_key] = ref
    return ref
