"""Option Target Calculator orchestration.

This is the ONLY module in the Option Target Calculator feature that touches
the broker. It fetches a chain snapshot via `services.option_chain_service`,
resolves whether a same-expiry future exists, runs the pure-math pipeline in
`services.option_target` (day-count, forward anchoring, smile calibration,
per-strike projection and ranking), and returns a fully self-describing
response: every assumption the engine made along the way (basis modelling,
degenerate smile, vol-beta fallback, hold running past expiry, ...) is echoed
back in `warnings` and the response body itself, so nothing is applied
invisibly.

The chain snapshot fetched from the broker is cached in `_SNAPSHOT_CACHE` for a
3-second TTL, keyed on (underlying, exchange, expiry_date, strike_count). A
single scenario tweak in the UI - moving the target price, changing the
objective - re-issues the request, and the frontend also polls; without the
cache each of those re-hits the broker chain endpoint, which is what caused
observed rate-limit retries in production. Only successful chain fetches are
cached. Any caching added here MUST stay a BOUNDED `TTLCache`: an unbounded
dict keyed by (symbol, expiry) would grow for the life of a Gunicorn worker
that never restarts (`-w 1`, no scheduled recycle) - see CLAUDE.md's
FD-hygiene invariant.
"""

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from cachetools import TTLCache

from services.expiry_service import get_expiry_dates
from services.history_service import get_history
from services.option_chain_service import get_option_chain
from services.option_target.daycount import year_fraction
from services.option_target.forward import compute_forward, project_forward
from services.option_target.models import SmileFit, StrikeQuote
from services.option_target.projection import project_strike
from services.option_target.ranking import build_candidate, rank_candidates
from services.option_target.smile import calibrate_ivs, fit_smile, smile_iv
from services.option_target.volbeta import (
    DEFAULT_WINDOW_MINUTES,
    MAX_ESTIMATED_BETA,
    PRESETS,
    build_beta_samples,
    estimate_vol_beta,
)
from services.option_target_sessions import build_session_provider, session_is_open
from services.pricing_underlying import requires_futures_underlying
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Bounded on purpose - see module docstring.
_SNAPSHOT_CACHE: TTLCache = TTLCache(maxsize=64, ttl=3)

# The vol-beta regression spans two hours of 1-minute bars, so a longer TTL
# than the chain's costs almost nothing in accuracy and saves two broker
# history calls per scenario tweak. Bounded for the same reason as above.
_BETA_BARS_CACHE: TTLCache = TTLCache(maxsize=32, ttl=60)

LADDER_STEPS = 15
DEFAULT_STRIKE_COUNT = 12

# Pure divide-by-zero guard, not a modelling clamp. Measured directly against
# the Rust opengreeks Black-76 core at F=24470, K=24450, sigma=0.386: price
# and implied vol round-trip exactly (error ~0.0000 vp) all the way down to
# 30 seconds to expiry. The old value, 0.0001 (52.6 minutes) - copied from
# option_greeks_service, itself a legacy of the original py_vollib Python
# implementation - is unnecessary for this library and actively harmful: it
# clamps most of expiry-day trading, and at 15:00 with 30 minutes left it
# overstates an at-the-money call by 23% (48.52 instead of the correct
# 39.57). 1e-6 years is ~31 seconds, comfortably below the measured-stable
# range while still guarding the divide.
MIN_TIME_YEARS = 1e-6

# Below this many days to expiry, theta dominates and a short hold on a
# far-OTM strike can lose value even on a favourable underlying move.
ZERO_DTE_DAYS = 1.0

# Generous annualised carry bound. Indian index carry runs well under this;
# the margin is deliberate so ordinary quotes never trip the check.
MAX_PLAUSIBLE_CARRY_RATE = 0.15
# Absolute allowance for bid-ask noise in the two at-the-money legs that
# put-call parity is derived from, as a percent of spot.
BASIS_QUOTE_TOLERANCE_PCT = 0.10

# Hold duration, as a fraction of the remaining time to expiry, above which
# the projection is consuming most of the option's remaining life.
HOLD_FRACTION_WARN = 0.5

# Measured smile RMS: 0.024 vol points at 7 DTE, 0.625 at 0DTE. Above this,
# the fitted smile - and every IV/premium projected from it - is unreliable.
MAX_SMILE_RMS_VOL_PTS = 1.0

# Measured smile RMS by horizon: 0.024 vol pts at 7 days, 0.446 at 73 minutes,
# 3.89 at 18 minutes, 14.36 at 7 minutes. Above this, sliding the fitted smile
# with the forward (smile_slide) produces tens-of-vol-point jumps and strikes
# priced at implausible IVs - the fit itself has broken down, not just gotten
# noisier. Sits between the last usable 0DTE case (0.446) and the first
# degenerate one (3.89).
SMILE_UNRELIABLE_VOL_PTS = 3.0

EXPIRY_TIMES = {"MCX": (23, 30), "CDS": (12, 30)}
DEFAULT_EXPIRY_TIME = (15, 30)
MONTHS = {
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


def parse_chain_quotes(chain_rows: list[dict[str, Any]]) -> dict[tuple[float, str], StrikeQuote]:
    """Convert the option-chain service payload into StrikeQuote objects.

    Rows with a non-positive strike and legs with no `symbol` (the option-chain
    service marks a non-existent leg with `None`, or callers may pass an empty
    dict in tests) are skipped rather than raising - a missing leg on one side
    of the chain must not take down the whole projection.
    """
    quotes: dict[tuple[float, str], StrikeQuote] = {}
    for row in chain_rows:
        strike = float(row.get("strike", 0) or 0)
        if strike <= 0:
            continue
        for key, opt_type in (("ce", "CE"), ("pe", "PE")):
            leg = row.get(key) or {}
            symbol = leg.get("symbol")
            if not symbol:
                continue
            quotes[(strike, opt_type)] = StrikeQuote(
                strike=strike,
                option_type=opt_type,
                symbol=symbol,
                ltp=float(leg.get("ltp") or 0),
                bid=float(leg.get("bid") or 0),
                ask=float(leg.get("ask") or 0),
                oi=int(leg.get("oi") or 0),
                volume=int(leg.get("volume") or 0),
                lot_size=int(leg.get("lotsize") or 0),
            )
    return quotes


def strike_step_of(strikes: list[float]) -> float:
    """Modal gap between consecutive sorted unique strikes. 0 when undeterminable.

    Ties break toward the smallest gap: real chains widen spacing away from
    the ATM, so the tightest spacing is the one that matters for moneyness
    labelling, and `statistics.mode` would otherwise pick whichever gap
    happens to appear first in the input.
    """
    ordered = sorted(set(strikes))
    if len(ordered) < 2:
        return 0.0
    gaps = [round(b - a, 4) for a, b in zip(ordered, ordered[1:], strict=False)]
    counts = Counter(gaps)
    best = max(counts.values())
    return float(min(gap for gap, n in counts.items() if n == best))


def resolve_hold(hold_minutes: float | None, hold_days: float | None) -> float:
    """Hold duration in minutes. `hold_days` wins when both are supplied."""
    if hold_days is not None:
        minutes = float(hold_days) * 24 * 60
    else:
        minutes = float(hold_minutes if hold_minutes is not None else 0)
    if minutes < 0:
        raise ValueError("Hold duration must not be negative")
    return minutes


def build_ladder(
    reference_now: float,
    reference_target: float,
    steps: int,
    project: Callable[[float], float],
) -> list[dict[str, float]]:
    """Premium across a span of reference levels bracketing the target.

    The span runs 1.5x the target distance either side of `reference_now`, so
    the chart shows what happens if the move overshoots or reverses, not only
    if it lands exactly on target.
    """
    distance = reference_target - reference_now
    lo = reference_now - abs(distance) * 1.5
    hi = reference_now + abs(distance) * 1.5
    if steps < 2 or hi <= lo:
        return [{"reference_level": reference_target, "premium": project(reference_target)}]

    width = (hi - lo) / (steps - 1)
    return [
        {
            "reference_level": lo + i * width,
            "premium": project(lo + i * width),
        }
        for i in range(steps)
    ]


def _matched_future_symbol(base_symbol: str, expiry_date: str, exchange: str) -> str | None:
    """Return the same-expiry futures symbol when one exists, else None.

    A matched future removes the basis assumption entirely (an exact 1:1
    forward shift instead of proportional basis modelling), so detecting it is
    worth a DB lookup. Imports `database.symbol` locally to avoid a startup
    import cycle. Any failure - including a genuinely missing future - must
    never kill the request, so every exception is logged and swallowed here.
    """
    from database.symbol import SymToken, db_session

    candidate = f"{base_symbol}{expiry_date}FUT"
    try:
        with db_session() as session:
            row = (
                session.query(SymToken.symbol)
                .filter(SymToken.symbol == candidate, SymToken.exchange == exchange)
                .first()
            )
        return candidate if row else None
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not kill the request
        logger.warning("Matched-future lookup failed for %s: %s", candidate, exc)
        return None


def _expiry_datetime(expiry_date: str, exchange: str) -> datetime:
    """Parse DDMMMYY into an expiry datetime in IST.

    Expiry times mirror `option_greeks_service` so the two pages agree. The
    schema validates the DDMMMYY shape before this is ever called, but this
    stays defensive so a direct call (tests, other callers) still fails with a
    400-worthy `ValueError` instead of an `IndexError`/`KeyError` that would
    otherwise fall through to a generic 500.
    """
    try:
        day = int(expiry_date[:2])
        month = MONTHS[expiry_date[2:5].upper()]
        year = 2000 + int(expiry_date[5:7])
        hour, minute = EXPIRY_TIMES.get(exchange.upper(), DEFAULT_EXPIRY_TIME)
        return datetime(year, month, day, hour, minute, tzinfo=IST)
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"Invalid expiry_date {expiry_date!r}; expected DDMMMYY") from exc


def _compact_expiry(dashed: str) -> str:
    """Convert the expiry API's DD-MMM-YY into the DDMMMYY the chain needs."""
    return dashed.replace("-", "").upper()


def _default_expiry(underlying: str, exchange: str, api_key: str) -> str | None:
    """Nearest expiry whose session has not already ended.

    `get_expiry_dates` filters by calendar date, so on expiry day after the
    close it still lists today's dead contract. Skipping it here means a caller
    who omits expiry_date gets a tradeable contract rather than a 400.
    """
    ok, resp, _ = get_expiry_dates(underlying, exchange, "options", api_key)
    if not ok:
        return None
    now = datetime.now(IST)
    for dashed in resp.get("data") or []:
        compact = _compact_expiry(dashed)
        expiry_dt = _expiry_datetime(compact, exchange)
        if expiry_dt > now:
            return compact
    return None


def _one_minute_closes(symbol: str, exchange: str, day: str, api_key: str) -> dict[float, float]:
    """Timestamp -> close for each TRADED minute of `day`.

    Zero-volume bars are dropped. They are either post-close padding (the
    broker repeats the last close out to the current time) or an untraded
    minute; either way the close is stale, and a stale option price pinned
    against a live one fabricates a forward that never existed.
    """
    ok, resp, _ = get_history(symbol, exchange, "1m", day, day, api_key=api_key)
    if not ok:
        logger.info("Vol-beta history unavailable for %s: %s", symbol, resp.get("message"))
        return {}

    closes: dict[float, float] = {}
    for row in resp.get("data") or []:
        try:
            if float(row.get("volume") or 0) <= 0:
                continue
            closes[float(row["timestamp"])] = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
    return closes


def _atm_straddle_bars(
    call_symbol: str, put_symbol: str, exchange: str, api_key: str
) -> list[tuple[datetime, float, float]]:
    """Today's 1-minute (timestamp, call close, put close) bars for one strike.

    Only minutes where BOTH legs traded survive: put-call parity needs the two
    prices to be contemporaneous, and pairing a fresh quote with a stale one
    moves the implied forward by the whole staleness.

    Cached because the page refetches on every scenario tweak while the
    regression itself spans two hours - a minute of extra staleness moves the
    estimate far less than two broker calls per keystroke costs.
    """
    day = datetime.now(IST).strftime("%Y-%m-%d")
    cache_key = (call_symbol, put_symbol, exchange, day)
    cached = _BETA_BARS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    calls = _one_minute_closes(call_symbol, exchange, day, api_key)
    # Skip the second call when the first found nothing; a failure is cached
    # too, so a delisted or illiquid strike does not cost a broker round trip
    # on every request.
    puts = _one_minute_closes(put_symbol, exchange, day, api_key) if calls else {}

    bars = [
        (datetime.fromtimestamp(ts, IST), calls[ts], puts[ts])
        for ts in sorted(calls.keys() & puts.keys())
    ]
    _BETA_BARS_CACHE[cache_key] = bars
    return bars


def _vol_beta_samples(
    call_symbol: str,
    put_symbol: str,
    exchange: str,
    *,
    strike: float,
    expiry: datetime,
    rate: float,
    fit: SmileFit | None,
    api_key: str,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
) -> list[tuple[float, float]]:
    """(percent_return, atm_iv_vol_points) samples for beta estimation.

    Sampled from the ATM straddle's own 1-minute history, so the forward comes
    from put-call parity exactly as the live snapshot's does, and commodities -
    which have no spot instrument - work unchanged.

    A history failure must never block a projection: this returns [] on any
    error, `estimate_vol_beta` then falls back to the Normal preset, and the
    `source`/`reason` fields say so in the response.
    """
    try:
        bars = _atm_straddle_bars(call_symbol, put_symbol, exchange, api_key)
        return build_beta_samples(
            bars,
            strike=strike,
            expiry=expiry,
            rate=rate,
            fit=fit,
            window_minutes=window_minutes,
        )
    except Exception as exc:  # noqa: BLE001 - must never block a projection
        logger.warning("Vol-beta sampling failed for %s: %s", call_symbol, exc)
        return []


def get_option_target(
    underlying: str,
    exchange: str,
    *,
    expiry_date: str | None = None,
    reference: str,
    target_price: float,
    api_key: str,
    reference_price: float | None = None,
    hold_minutes: float | None = 45,
    hold_days: float | None = None,
    iv_model: str = "smile_slide",
    vol_beta: float | str = "auto",
    vol_shift: float = 0.0,
    day_count: str = "calendar",
    strike_count: int = DEFAULT_STRIKE_COUNT,
    side: str = "AUTO",
    lots: int = 1,
    interest_rate: float = 0.0,
    objective: str = "balanced",
) -> tuple[bool, dict[str, Any], int]:
    """Project every strike to the user's target and rank them.

    Fetches a live chain snapshot, anchors the forward, calibrates the smile,
    reprices every strike on the chosen side at the target, and ranks the
    result by `objective`. Every assumption made along the way is surfaced in
    the `warnings` list and in the response body itself.
    """
    warnings: list[str] = []

    try:
        if target_price is None or target_price <= 0:
            return False, {"status": "error", "message": "target_price must be positive"}, 400

        if requires_futures_underlying(exchange) and reference.upper() == "SPOT":
            return (
                False,
                {
                    "status": "error",
                    "message": (
                        f"{exchange.upper()} has no spot instrument - its options are written on "
                        f"futures. Use reference FUT and target the linked futures contract."
                    ),
                },
                400,
            )

        if not expiry_date:
            expiry_date = _default_expiry(underlying, exchange, api_key)
            if not expiry_date:
                return (
                    False,
                    {"status": "error", "message": "No live expiry available for this underlying"},
                    404,
                )
            warnings.append(
                f"No expiry supplied; defaulted to the nearest live expiry {expiry_date}."
            )

        hold_min = resolve_hold(hold_minutes, hold_days)

        cache_key = (underlying.upper(), exchange.upper(), expiry_date.upper(), strike_count)
        cached = _SNAPSHOT_CACHE.get(cache_key)
        if cached is not None:
            ok, chain_resp, status = True, cached, 200
        else:
            ok, chain_resp, status = get_option_chain(
                underlying=underlying,
                exchange=exchange,
                expiry_date=expiry_date,
                strike_count=strike_count,
                api_key=api_key,
            )
            if ok:
                _SNAPSHOT_CACHE[cache_key] = chain_resp
        if not ok:
            return False, chain_resp, status

        rows = chain_resp.get("chain") or []
        spot = float(chain_resp.get("underlying_ltp") or 0)
        atm_strike = float(chain_resp.get("atm_strike") or 0)
        if not rows or spot <= 0 or atm_strike <= 0:
            return False, {"status": "error", "message": "Chain snapshot incomplete"}, 502

        # A missing/None block (older response shape, or a resolver failure inside
        # get_option_chain) degrades to SPOT so downstream logic never has to
        # special-case "no block at all" separately from "resolved to SPOT".
        underlying_ref = chain_resp.get("underlying_ref")
        ref_kind = (underlying_ref or {}).get("kind", "SPOT")

        quotes = parse_chain_quotes(rows)
        strikes = sorted({s for s, _ in quotes})
        step = strike_step_of(strikes)

        anchor = compute_forward(quotes, atm_strike=atm_strike, spot=spot)
        if anchor.source == "spot_fallback":
            if requires_futures_underlying(exchange):
                warnings.append(
                    "ATM call/put quotes unavailable - forward fell back to the linked "
                    "future, so projections carry the full basis as error."
                )
            else:
                warnings.append(
                    "ATM call/put quotes unavailable - forward fell back to spot, "
                    "so projections carry the full basis as error."
                )

        # On MCX/NCDEX/NCO the resolver has already identified the exact contract
        # the option settles against (the linked future - see underlying_ref), so
        # that IS the matched future; re-querying via _matched_future_symbol would
        # look for a future sharing the option's own expiry, which never exists on
        # those exchanges. NFO/BFO keep the original same-expiry DB lookup.
        if requires_futures_underlying(exchange) and ref_kind == "FUTURE":
            matched = (underlying_ref or {}).get("symbol")
        else:
            matched = _matched_future_symbol(underlying, expiry_date, exchange)
        ref = reference.upper()
        ref_now = (
            reference_price
            if reference_price
            else (anchor.forward if (matched and ref == "FUT") else spot)
        )

        fwd_target = project_forward(
            anchor,
            reference=ref,
            reference_now=ref_now,
            reference_target=target_price,
            matched_future=bool(matched),
        )
        if fwd_target.mode == "basis_modelled":
            warnings.append(
                f"No same-expiry future for {expiry_date}; forward is basis-modelled "
                f"(current basis {anchor.basis:+.1f} pts)."
            )

        now = datetime.now(IST)
        # Default True on a hard lookup failure: a calendar error must never
        # block a price projection. See session_is_open's `default` argument.
        market_open = session_is_open(exchange, now)
        if not market_open:
            warnings.append(
                "Market is closed for this exchange. Every price below is the last "
                "traded value rather than a live quote, so the forward, implied vols "
                "and projections are indicative only."
            )
        expiry = _expiry_datetime(expiry_date, exchange)
        session_provider = build_session_provider(exchange) if day_count == "trading" else None
        t_now = year_fraction(
            now, expiry, day_count, exchange=exchange, session_provider=session_provider
        )
        t_target = year_fraction(
            now + timedelta(minutes=hold_min),
            expiry,
            day_count,
            exchange=exchange,
            session_provider=session_provider,
        )
        if t_now <= 0:
            return False, {"status": "error", "message": "Option has already expired"}, 400
        if 0 < t_now < MIN_TIME_YEARS:
            logger.info(
                "Time to expiry below the numerical floor; clamping to %s years", MIN_TIME_YEARS
            )
            t_now = MIN_TIME_YEARS
            warnings.append(
                "Very close to expiry - time to expiry clamped to the numerical floor, "
                "so Greeks and projections are indicative only."
            )
        t_target = min(t_target, t_now)
        if t_target <= 0:
            warnings.insert(
                0,
                f"The {hold_min:.0f} minute hold exceeds the {t_now * 365 * 24 * 60:.1f} minutes "
                f"remaining to expiry. Every projection below is the value AT expiry, so all "
                f"out-of-the-money strikes show a total loss.",
            )

        days_to_expiry = t_now * 365
        carry_bound = spot * MAX_PLAUSIBLE_CARRY_RATE * t_now
        quote_tolerance = spot * BASIS_QUOTE_TOLERANCE_PCT / 100
        max_plausible_basis = carry_bound + quote_tolerance
        basis_plausible = abs(anchor.basis) <= max_plausible_basis
        # A carry bound is measured against spot; MCX/NCDEX/NCO have no spot, so
        # the check - and the warning it would produce - is meaningless there.
        if ref_kind != "FUTURE" and not basis_plausible:
            warnings.append(
                f"Forward sits {anchor.basis:+.1f} points from spot, beyond the "
                f"{max_plausible_basis:.1f} points that carry over "
                f"{t_now * 365:.2f} days plus quote noise can explain. The "
                f"at-the-money quotes driving put-call parity are probably stale or "
                f"wide, and every projection inherits that error."
            )
        if days_to_expiry < ZERO_DTE_DAYS:
            warnings.append(
                f"Expiry is {days_to_expiry * 24:.1f} hours away. Theta dominates at this range - "
                f"the projection is highly sensitive to the hold time, and far out-of-the-money "
                f"strikes can lose value even when the move goes your way."
            )

        if t_target > 0 and t_now > 0 and (t_now - t_target) / t_now > HOLD_FRACTION_WARN:
            warnings.append(
                f"The {hold_min:.0f} minute hold consumes "
                f"{((t_now - t_target) / t_now) * 100:.0f} percent of the remaining time to expiry."
            )

        rate = interest_rate / 100.0
        points, rejects = calibrate_ivs(quotes, anchor.forward, t_now, rate)
        atm_fallback = next((p.iv for p in points if abs(p.strike - atm_strike) < 1e-6), 0.12)
        fit = fit_smile(points, atm_iv_fallback=atm_fallback)
        if fit.degenerate:
            warnings.append(
                f"Only {fit.n_points} strikes calibrated - using a flat ATM vol, no smile."
            )
        if not fit.degenerate and fit.rms * 100 > MAX_SMILE_RMS_VOL_PTS:
            warnings.append(
                f"Volatility smile fits poorly (RMS {fit.rms * 100:.2f} vol points). "
                f"Projected implied vols, and therefore projected premiums, are less reliable."
            )

        effective_iv_model = iv_model
        iv_model_overridden = False
        if (
            iv_model == "smile_slide"
            and not fit.degenerate
            and fit.rms * 100 > SMILE_UNRELIABLE_VOL_PTS
        ):
            effective_iv_model = "sticky_strike"
            iv_model_overridden = True
            warnings.append(
                f"Smile fit is too poor to slide (RMS {fit.rms * 100:.2f} vol points, above "
                f"{SMILE_UNRELIABLE_VOL_PTS}). Falling back to sticky-strike implied vols; "
                f"projected premiums no longer assume the smile shape travels with the forward."
            )

        if vol_beta == "auto":
            atm_call = quotes.get((atm_strike, "CE"))
            atm_put = quotes.get((atm_strike, "PE"))
            beta_info = estimate_vol_beta(
                _vol_beta_samples(
                    atm_call.symbol,
                    atm_put.symbol,
                    exchange,
                    strike=atm_strike,
                    expiry=expiry,
                    rate=rate,
                    fit=fit,
                    api_key=api_key,
                )
                if atm_call and atm_put
                else []
            )
        elif isinstance(vol_beta, str):
            beta_info = {
                "beta": PRESETS.get(vol_beta, PRESETS["normal"]),
                "r_squared": 0.0,
                "samples": 0,
                "source": "preset",
                "reason": "",
                "clamped_from": None,
            }
        else:
            # A value the user typed is theirs to own - only an ESTIMATE is
            # clamped, because only an estimate can be wrong about itself.
            beta_info = {
                "beta": float(vol_beta),
                "r_squared": 0.0,
                "samples": 0,
                "source": "manual",
                "reason": "",
                "clamped_from": None,
            }
        if beta_info["source"] == "fallback":
            warnings.append(f"Vol-beta estimate unavailable: {beta_info['reason']}")
        elif beta_info["clamped_from"] is not None:
            warnings.append(
                f"Measured vol response was {beta_info['clamped_from']:+.2f} vol points per "
                f"1 percent move, beyond the {MAX_ESTIMATED_BETA:.1f} the Panic preset allows. "
                f"Clamped to {beta_info['beta']:+.2f} - a fit that extreme is more likely a "
                f"narrow sample range than a real regime. Override it if you disagree."
            )

        move_pct = fwd_target.move_pct
        forward_adverse = anchor.forward - (fwd_target.forward - anchor.forward)

        chosen = side.upper()
        if chosen == "AUTO":
            chosen = "CE" if target_price >= ref_now else "PE"

        candidates = [
            build_candidate(
                quote=quotes[(strike, chosen)],
                forward_now=anchor.forward,
                forward_target=fwd_target.forward,
                forward_adverse=forward_adverse,
                t_now=t_now,
                t_target=t_target,
                rate=rate,
                fit=fit,
                iv_model=effective_iv_model,
                vol_beta=beta_info["beta"],
                move_pct=move_pct,
                vol_shift=vol_shift,
                lots=lots,
                atm_strike=atm_strike,
                strike_step=step,
            )
            for strike in strikes
            if (strike, chosen) in quotes and quotes[(strike, chosen)].mid > 0
        ]
        ranked = rank_candidates(candidates, objective=objective)

        best = next((c for c in ranked if c["recommended"]), None)
        ladder: list[dict[str, float]] = []
        if best is not None:

            def _project_at(ref_level: float) -> float:
                ft = project_forward(
                    anchor,
                    reference=ref,
                    reference_now=ref_now,
                    reference_target=ref_level,
                    matched_future=bool(matched),
                )
                return project_strike(
                    strike=best["strike"],
                    option_type=best["option_type"],
                    forward_target=ft.forward,
                    t_target=t_target,
                    rate=rate,
                    iv_now=best["iv_now_pct"] / 100,
                    fit=fit,
                    iv_model=effective_iv_model,
                    vol_beta=beta_info["beta"],
                    move_pct=ft.move_pct,
                    vol_shift=vol_shift,
                )

            ladder = build_ladder(ref_now, target_price, LADDER_STEPS, _project_at)
            for row in ladder:
                row["pnl_per_lot"] = (row["premium"] - best["entry_cost"]) * best["lot_size"]

        return (
            True,
            {
                "status": "success",
                "snapshot": {
                    "underlying": underlying,
                    "exchange": exchange,
                    "expiry_date": expiry_date,
                    "spot": spot,
                    "forward": anchor.forward,
                    # No spot instrument exists on MCX/NCDEX/NCO (see
                    # services.pricing_underlying), so `basis = forward - spot`
                    # would silently mean "forward minus linked future" there.
                    # Report None instead and surface the real, useful number -
                    # parity forward vs. the linked future - separately.
                    "basis": None if ref_kind == "FUTURE" else anchor.basis,
                    "parity_vs_underlying": anchor.basis if ref_kind == "FUTURE" else None,
                    "underlying_ref": underlying_ref,
                    "forward_source": anchor.source,
                    "atm_strike": atm_strike,
                    "strike_step": step,
                    "atm_iv_pct": smile_iv(fit, 0.0) * 100,
                    "days_to_expiry": days_to_expiry,
                    "is_zero_dte": days_to_expiry < ZERO_DTE_DAYS,
                    # The carry-bound check is meaningless with no spot to check
                    # carry against; see the ref_kind guard on the warning above.
                    "basis_plausible": None if ref_kind == "FUTURE" else basis_plausible,
                    "market_open": market_open,
                    "t_years": t_now,
                    "matched_future": matched,
                    "lot_size": next(iter(quotes.values())).lot_size if quotes else 0,
                },
                "smile": {
                    "a": fit.a,
                    "b": fit.b,
                    "c": fit.c,
                    "x_lo": fit.x_lo,
                    "x_hi": fit.x_hi,
                    "rms_vol_pts": fit.rms * 100,
                    "n_points": fit.n_points,
                    "degenerate": fit.degenerate,
                    "rejected": rejects,
                },
                "scenario": {
                    "reference": ref,
                    "reference_now": ref_now,
                    "reference_target": target_price,
                    "forward_target": fwd_target.forward,
                    "forward_mode": fwd_target.mode,
                    "move_pct": move_pct,
                    "hold_minutes": hold_min,
                    "day_count": day_count,
                    "t_target_years": t_target,
                    "iv_model": effective_iv_model,
                    "iv_model_requested": iv_model,
                    "iv_model_overridden": iv_model_overridden,
                    "vol_beta": beta_info,
                    "vol_shift": vol_shift,
                    "side": chosen,
                    "objective": objective,
                    "lots": lots,
                },
                "candidates": ranked,
                "recommended_strike": best["strike"] if best else None,
                "ladder": ladder,
                "warnings": warnings,
            },
            200,
        )

    except (ValueError, TypeError) as exc:
        logger.warning("Validation error in option target: %s", exc)
        return False, {"status": "error", "message": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001 - final backstop, must always return a response
        logger.exception("Unexpected error in option target: %s", exc)
        return False, {"status": "error", "message": "Failed to compute option target"}, 500
