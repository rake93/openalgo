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

from services.option_chain_service import get_option_chain
from services.option_target.daycount import year_fraction
from services.option_target.forward import compute_forward, project_forward
from services.option_target.models import StrikeQuote
from services.option_target.projection import project_strike
from services.option_target.ranking import build_candidate, rank_candidates
from services.option_target.smile import calibrate_ivs, fit_smile, smile_iv
from services.option_target.volbeta import PRESETS, estimate_vol_beta
from services.option_target_sessions import build_session_provider
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Bounded on purpose - see module docstring.
_SNAPSHOT_CACHE: TTLCache = TTLCache(maxsize=64, ttl=3)

LADDER_STEPS = 15
DEFAULT_STRIKE_COUNT = 12

# Mirrors option_greeks_service.calculate_time_to_expiry: below this, seconds
# to expiry become numerically unstable inside black76.implied_volatility/vega.
MIN_TIME_YEARS = 0.0001

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


def _vol_beta_samples(underlying: str, exchange: str, api_key: str) -> list[tuple[float, float]]:
    """(percent_return, atm_iv_vol_points) samples for beta estimation.

    History plumbing is deliberately deferred - this always returns [].
    `estimate_vol_beta` then falls back to the Normal preset and reports, via
    its `source`/`reason` fields, that it did. A history-fetch failure must
    never block a projection, so this stays a stub rather than a fallible call.
    """
    return []


def get_option_target(
    underlying: str,
    exchange: str,
    expiry_date: str,
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

        quotes = parse_chain_quotes(rows)
        strikes = sorted({s for s, _ in quotes})
        step = strike_step_of(strikes)

        anchor = compute_forward(quotes, atm_strike=atm_strike, spot=spot)
        if anchor.source == "spot_fallback":
            warnings.append(
                "ATM call/put quotes unavailable - forward fell back to spot, "
                "so projections carry the full basis as error."
            )

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
            warnings.append("Hold runs past expiry - projected values are intrinsic only.")

        rate = interest_rate / 100.0
        points, rejects = calibrate_ivs(quotes, anchor.forward, t_now, rate)
        atm_fallback = next((p.iv for p in points if abs(p.strike - atm_strike) < 1e-6), 0.12)
        fit = fit_smile(points, atm_iv_fallback=atm_fallback)
        if fit.degenerate:
            warnings.append(
                f"Only {fit.n_points} strikes calibrated - using a flat ATM vol, no smile."
            )

        if vol_beta == "auto":
            beta_info = estimate_vol_beta(_vol_beta_samples(underlying, exchange, api_key))
        elif isinstance(vol_beta, str):
            beta_info = {
                "beta": PRESETS.get(vol_beta, PRESETS["normal"]),
                "r_squared": 0.0,
                "samples": 0,
                "source": "preset",
                "reason": "",
            }
        else:
            beta_info = {
                "beta": float(vol_beta),
                "r_squared": 0.0,
                "samples": 0,
                "source": "manual",
                "reason": "",
            }
        if beta_info["source"] == "fallback":
            warnings.append(f"Vol-beta estimate unavailable: {beta_info['reason']}")

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
                iv_model=iv_model,
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
                    iv_model=iv_model,
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
                    "basis": anchor.basis,
                    "forward_source": anchor.source,
                    "atm_strike": atm_strike,
                    "strike_step": step,
                    "atm_iv_pct": smile_iv(fit, 0.0) * 100,
                    "days_to_expiry": t_now * 365,
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
                    "iv_model": iv_model,
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
