"""
GEX Levels Service

The single IO boundary for the "GEX Levels" chart study: fetch one option
chain, resolve the per-expiry forward, run the pure math in
`services/gex_levels/`, and assemble a JSON-safe payload for the `/charts`
workspace. Every other module under `services/gex_levels/` is pure - no
network, no broker calls - so this is the only place that can fail on IO, and
the only place that needs a try/except around the whole pipeline.

That IO is split from the compute in three named steps, and the split is
load-bearing rather than cosmetic:

    fetch_snapshot_inputs   IO      chain fetch + forward resolution
      -> prepare_snapshot   pure    rows + IVs, everything weighting-independent
      -> build_snapshot     pure    one weighting's exposures, levels, payload

`get_gex_levels` runs all three for the requested weighting.
`services/gex_recorder_service.py` runs the first two ONCE and then
`build_snapshot` twice, for 'oi' and 'volume', off a single chain fetch and a
single IV solve. Both callers therefore run ONE pipeline: a recorder that
reimplemented the maths is the failure this seam exists to prevent, and it is
not hypothetical - the `/gex` Tools page drifted from this study exactly that
way and shipped three defects (see §11 of the 2026-08-04 design).

Why this is built on the Gamma Density pipeline, not `gex_service.py`:
`services/gex_service.py` (the `/gex` Tools page) calls `calculate_greeks`
once per strike - up to 90 service calls for a 45-strike chain - and prices
Black-76 with SPOT as the forward. Neither is acceptable for a study that
refreshes on a timer: the per-strike service-call loop does not scale to a
polling refresh, and pricing at spot displaces both the call wall and the
put wall away from where dealer gamma actually concentrates (gamma peaks at
the ATM-forward strike, not the ATM-spot strike). `services/gamma_density_service.py`
already solved both problems - one `get_option_chain` call, then `black76`
directly - so this module follows that same shape.
"""

import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from services.gex_levels.delta_exposure import price_delta_exposures
from services.gex_levels.expiry import expiry_datetime
from services.gex_levels.exposure import (
    ChainRow,
    ResolvedIVs,
    WeightBy,
    price_exposures,
    resolve_ivs,
    weighted_legs,
)
from services.gex_levels.levels import find_walls, scan_zero_gamma
from services.gex_levels.quality import assess_quality
from services.gex_levels.sentiment import read_sentiment
from services.option_chain_service import get_option_chain
from services.option_greeks_service import (
    DEFAULT_INTEREST_RATES,
    _resolve_forward_price,
    calculate_time_to_expiry,
    get_underlying_exchange,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# 23 strikes each side of ATM = 47 strikes = 94 option symbols. This is a hard
# broker limit, not a preference: oi_tracker_service.py documents that it is
# sized to fit the fyers multiquote OI bucket (<=100 symbols) so OI comes back
# populated. Exceeding it does not raise - it silently returns EMPTY open
# interest, which would zero every exposure in this feature without an error
# anywhere. Never raise this number.
STRIKE_COUNT = 23


class UnusableChain(ValueError):
    """The chain came back, but carries no spot price or no usable strikes.

    Typed rather than bare so the two callers can tell it apart from a genuine
    crash: the endpoint maps it to 404, the recorder logs it and skips the tick.
    A bare ValueError would be swallowed by the wrapper's broad `except` and
    reported as a 500, sending an operator to look for a crash that never
    happened.
    """


class PricingLibraryMissing(RuntimeError):
    """`opengreeks` is not installed.

    Its own type rather than a bare RuntimeError so the wrapper can surface its
    message verbatim - it tells the operator exactly what to install - without
    also surfacing the message of every other RuntimeError that could come out
    of the pipeline.
    """


class ChainFetchFailed(RuntimeError):
    """`get_option_chain` returned failure.

    Carries the broker's own response body and status code so the endpoint can
    pass them through unaltered rather than flattening every upstream failure
    into one generic message.
    """

    def __init__(self, response: dict[str, Any], status_code: int):
        super().__init__(response.get("message", "Option chain fetch failed"))
        self.response = response
        self.status_code = status_code


@dataclass(frozen=True)
class SnapshotInputs:
    """Everything one chain fetch yields that does NOT depend on the weighting.

    This is the seam the recorder is built on. `resolve_ivs` takes no
    `weight_by` - it inverts at the real forward and is weighting-independent -
    and it is the expensive half of a pricing pass, two Black-76 solver calls
    per strike. So a caller that needs both weightings pays for the chain fetch,
    the forward resolution and the IV solve exactly once, then calls
    `build_snapshot` twice.

    Attributes:
        underlying: The chain's own base symbol, not necessarily what was asked
            for - `option_chain_service` may normalise it.
        exchange: Options exchange the chain was fetched from.
        expiry_date: Expiry in DDMMMYY. For a recorder series on the `nearest`
            rule this is the RESOLVED contract, never the rule.
        rows: One ChainRow per usable strike, unsorted.
        ivs: Per-leg IVs inverted at `forward`. Resolved from `rows` exactly.
        spot_price: Underlying LTP.
        forward: The per-expiry forward (F) actually used for pricing. Equals
            the resolved synthetic forward, or `spot_price` if it would not
            resolve.
        atm_strike: ATM strike from the chain, for the IV fallback.
        t_years: Time to expiry in years.
        dte_days: Time to expiry in days, for display.
        interest_rate: Annualized rate as a PERCENT, as the payload reports it.
        lot_size: Contract multiplier, carried for display only - never applied
            to OI or volume, which this broker already reports in units.
    """

    underlying: str
    exchange: str
    expiry_date: str
    rows: list[ChainRow]
    ivs: ResolvedIVs
    spot_price: float
    forward: float
    atm_strike: float | None
    t_years: float
    dte_days: float
    interest_rate: float
    lot_size: int


def fetch_snapshot_inputs(
    underlying: str,
    exchange: str,
    expiry_date: str,
    api_key: str,
    interest_rate: float | None = None,
) -> SnapshotInputs:
    """The IO half: fetch the chain, resolve the forward, prepare the inputs.

    Everything in this function talks to the network. Everything downstream of
    it is pure, which is why the recorder's tests can drive the whole pipeline
    with two patches.

    Args:
        underlying: Underlying symbol (e.g. NIFTY, BANKNIFTY).
        exchange: Options exchange (NFO, BFO, CDS, MCX, ...).
        expiry_date: Expiry in DDMMMYY format (e.g. 11AUG26).
        api_key: OpenAlgo API key.
        interest_rate: Optional risk-free rate (annualized %); default per exchange.

    Returns:
        SnapshotInputs ready for one or more `build_snapshot` calls.

    Raises:
        ChainFetchFailed: The broker refused or could not serve the chain.
        UnusableChain: The chain came back empty or without a spot price.
    """
    success, chain_response, status_code = get_option_chain(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        strike_count=STRIKE_COUNT,
        api_key=api_key,
    )
    if not success:
        raise ChainFetchFailed(chain_response, status_code)

    expiry_dt = expiry_datetime(expiry_date, exchange)

    if interest_rate is None:
        interest_rate = DEFAULT_INTEREST_RATES.get(exchange.upper(), 0)

    # Forward, never spot: gamma peaks at the ATM-FORWARD strike, so pricing off
    # spot displaces both walls by the cash-future basis. The measured BANKNIFTY
    # 21-day basis is +138.9 points - large enough to land a wall on the wrong
    # strike.
    base_symbol = chain_response.get("underlying", underlying)
    forward = _resolve_forward_price(
        base_symbol,
        exchange,
        get_underlying_exchange(base_symbol, exchange),
        expiry_dt,
        api_key,
    )

    return prepare_snapshot(
        chain_response,
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        expiry_dt=expiry_dt,
        forward=forward,
        interest_rate=interest_rate,
    )


def prepare_snapshot(
    chain_response: dict[str, Any],
    *,
    underlying: str,
    exchange: str,
    expiry_date: str,
    expiry_dt: datetime,
    forward: float | None,
    interest_rate: float,
) -> SnapshotInputs:
    """Flatten a fetched chain and invert its IVs. No IO.

    Args:
        chain_response: The response body from `option_chain_service.get_option_chain`.
        underlying: Underlying symbol, used only when the chain omits its own.
        exchange: Options exchange.
        expiry_date: Expiry in DDMMMYY.
        expiry_dt: The same expiry as a tz-aware datetime, from `expiry_datetime`.
        forward: Resolved synthetic forward, or None to fall back to spot.
        interest_rate: Annualized rate as a percent.

    Returns:
        SnapshotInputs.

    Raises:
        UnusableChain: No spot price, a non-positive spot, or an empty chain.
    """
    full_chain = chain_response.get("chain", [])
    spot_price = chain_response.get("underlying_ltp")
    atm_strike = chain_response.get("atm_strike")

    if not spot_price or spot_price <= 0 or not full_chain:
        raise UnusableChain("Spot price or option chain unavailable")

    t_years, dte_days = calculate_time_to_expiry(expiry_dt)
    r = interest_rate / 100.0
    F = forward or spot_price
    rows = _build_chain_rows(full_chain)

    # Resolved once and priced as many times as the caller needs. resolve_ivs
    # does not depend on which Greek is being priced or on the weighting, and it
    # is the expensive half of a pricing pass - two solver calls per strike - so
    # delta exposure and the second weighting both cost no extra inversion and
    # no extra broker call.
    ivs = resolve_ivs(
        load_black76(),
        rows,
        forward=F,
        t_years=t_years,
        r=r,
        atm_strike=atm_strike,
    )

    return SnapshotInputs(
        underlying=chain_response.get("underlying", underlying),
        exchange=exchange,
        expiry_date=expiry_date,
        rows=rows,
        ivs=ivs,
        spot_price=spot_price,
        forward=F,
        atm_strike=atm_strike,
        t_years=t_years,
        dte_days=dte_days,
        interest_rate=interest_rate,
        lot_size=rows[0].lot_size if rows else 1,
    )


def build_snapshot(black76, inputs: SnapshotInputs, weight_by: WeightBy) -> dict[str, Any]:
    """Price one weighting off prepared inputs and assemble the study payload.

    Pure: no network, no database, no clock. Called once by the live endpoint
    and twice by the recorder (once per weighting), which is what guarantees the
    two surfaces can never drift.

    Provenance (`source`, `as_of`) is deliberately NOT set here. It belongs to
    whoever served the payload - the recorder's rows are not "live" - so each
    wrapper stamps its own.

    Args:
        black76: The opengreeks.black76 module.
        inputs: From `prepare_snapshot` / `fetch_snapshot_inputs`.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        The JSON-safe payload the `/charts` study renders.

    Raises:
        ValueError: If `weight_by` is neither 'oi' nor 'volume'. Propagated from
            `weighted_legs`.
    """
    rows = inputs.rows
    F = inputs.forward
    t_years = inputs.t_years
    r = inputs.interest_rate / 100.0

    # Built ONCE and handed to both pricers. The zip below then walks two lists
    # derived from the same object rather than two merely equal ones, so a
    # strike's gamma and its delta cannot drift apart.
    legs = weighted_legs(rows, inputs.ivs, weight_by)
    exposures = price_exposures(black76, legs, forward=F, t_years=t_years, r=r)
    delta_exposures = price_delta_exposures(black76, legs, forward=F, t_years=t_years, r=r)
    walls = find_walls(exposures)
    zero_gamma = scan_zero_gamma(
        black76,
        rows,
        forward=F,
        t_years=t_years,
        r=r,
        atm_strike=inputs.atm_strike,
        weight_by=weight_by,
    )

    use_volume = weight_by == "volume"
    total_weight = sum(
        (row.call_volume + row.put_volume) if use_volume else (row.call_oi + row.put_oi)
        for row in rows
    )
    quality = assess_quality(exposures, walls, forward=F, total_weight=total_weight)

    total_call_gex = sum(e.call_gex for e in exposures)
    total_put_gex = sum(e.put_gex for e in exposures)
    net_gex = total_call_gex + total_put_gex
    regime = "suppressive" if net_gex >= 0 else "amplifying"

    # Sentiment is a SEPARATE directional read from Regime, never derived from
    # net_gex's sign - see services/gex_levels/sentiment.py's module docstring
    # for why that sign is deliberately unused here.
    sentiment = read_sentiment(
        exposures,
        walls,
        rows,
        spot=inputs.spot_price,
        forward=F,
        weight_by=weight_by,
    )

    return {
        "status": "success",
        "underlying": inputs.underlying,
        "exchange": inputs.exchange,
        "expiry_date": inputs.expiry_date,
        "weight_by": weight_by,
        "spot_price": round(inputs.spot_price, 2),
        "forward_price": round(F, 2),
        "atm_strike": inputs.atm_strike,
        "lot_size": inputs.lot_size,
        "dte_days": round(inputs.dte_days, 2),
        "interest_rate": round(inputs.interest_rate, 2),
        # The per-strike profile the chart's bar column is drawn from. Without
        # it the study renders levels but no distribution, so a trader cannot
        # see how concentrated a wall actually is.
        # strict=True: a mismatch would pair one strike's gamma with another's
        # delta. Surface it, do not truncate.
        "strikes": [
            {
                "strike": e.strike,
                "call_gex": round(e.call_gex, 2),
                "put_gex": round(e.put_gex, 2),
                "net_gex": round(e.net_gex, 2),
                "call_dex": round(d.call_dex, 2),
                "put_dex": round(d.put_dex, 2),
                "net_dex": round(d.net_dex, 2),
            }
            for e, d in zip(exposures, delta_exposures, strict=True)
        ],
        "total_call_gex": round(total_call_gex, 2),
        "total_put_gex": round(total_put_gex, 2),
        "call_wall": walls.call_wall,
        "put_wall": walls.put_wall,
        # None is a normal outcome - a chain can be long or short gamma across
        # its whole plausible range - and the UI shows "No local cross" rather
        # than treating it as missing data.
        "zero_gamma": round(zero_gamma, 2) if zero_gamma is not None else None,
        "net_gex": round(net_gex, 2),
        "regime": regime,
        "quality": _quality_payload(quality),
        "sentiment": {
            "bias": sentiment.bias,
            "score": round(sentiment.score, 3),
            "agreeing": sentiment.agreeing,
            "participating": sentiment.participating,
            "signals": [
                {
                    "key": x.key,
                    "label": x.label,
                    "detail": x.detail,
                    "bias": x.bias,
                    "why": x.why,
                    "weight": x.weight,
                }
                for x in sentiment.signals
            ],
        },
    }


def load_black76():
    """Import the pricing library, or raise a message the caller can surface.

    Kept behind a function because `opengreeks` is an optional dependency: the
    rest of the platform runs without it, and only this feature needs it.

    Raises:
        PricingLibraryMissing: If the library is not installed.
    """
    try:
        from opengreeks import black76
    except ImportError as exc:
        raise PricingLibraryMissing(
            "GEX Levels requires the opengreeks library. Install with: pip install opengreeks"
        ) from exc
    return black76


def get_gex_levels(
    underlying: str,
    exchange: str,
    expiry_date: str,
    api_key: str,
    weight_by: str = "oi",
    interest_rate: float | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """
    Fetch one option chain and assemble the GEX Levels payload for it.

    A thin wrapper over the seam: validate the weighting before spending a
    broker round trip, run `fetch_snapshot_inputs` then `build_snapshot`, stamp
    provenance, and translate the seam's typed errors into HTTP statuses. The
    recorder runs the same two functions - see this module's docstring.

    Args:
        underlying: Underlying symbol (e.g. NIFTY, BANKNIFTY).
        exchange: Options exchange (NFO, BFO, CDS, MCX, ...).
        expiry_date: Expiry in DDMMMYY format (e.g. 11AUG26).
        api_key: OpenAlgo API key.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.
        interest_rate: Optional risk-free rate (annualized %); default per exchange.

    Returns:
        Tuple of (success, response_data, status_code).
    """
    # Validated first, before the chain fetch: price_exposures raises on a bad
    # weighting, and discovering that after a broker round trip has already
    # been spent would waste it for no reason - the value never depends on
    # the chain.
    if weight_by not in ("oi", "volume"):
        return (
            False,
            {
                "status": "error",
                "message": f"weight_by must be 'oi' or 'volume', got {weight_by!r}",
            },
            400,
        )

    try:
        black76 = load_black76()
        inputs = fetch_snapshot_inputs(
            underlying,
            exchange,
            expiry_date,
            api_key,
            interest_rate=interest_rate,
        )
        payload = build_snapshot(black76, inputs, weight_by)
        # Provenance, stamped by the wrapper rather than the compute core. The
        # study renders it so a reader can tell a fresh live fetch from a
        # recorded row served by the fast path in blueprints/gex.py.
        payload["source"] = "live"
        payload["as_of"] = int(time.time())
        return True, payload, 200

    except ChainFetchFailed as exc:
        # The broker's own message and status, passed through unaltered.
        return False, exc.response, exc.status_code

    except UnusableChain as exc:
        return False, {"status": "error", "message": str(exc)}, 404

    except PricingLibraryMissing as exc:
        logger.error(str(exc))
        return False, {"status": "error", "message": str(exc)}, 500

    except Exception:
        logger.exception("Error in get_gex_levels")
        return (
            False,
            {"status": "error", "message": "Error computing GEX levels"},
            500,
        )


def _build_chain_rows(full_chain: list[dict[str, Any]]) -> list[ChainRow]:
    """
    Flatten the option-chain response into `ChainRow`s for the pure math.

    Rows whose strike is missing or non-positive are skipped - they carry no
    usable premium or position data and would only inject a bad strike into
    the profile.

    Args:
        full_chain: The `chain` list from `option_chain_service.get_option_chain`.

    Returns:
        One ChainRow per usable strike.
    """
    rows: list[ChainRow] = []
    for item in full_chain:
        strike = item.get("strike")
        if not isinstance(strike, (int, float)) or strike <= 0:
            continue
        ce = item.get("ce") or {}
        pe = item.get("pe") or {}
        lot_size = ce.get("lotsize") or pe.get("lotsize") or 1

        rows.append(
            ChainRow(
                strike=strike,
                call_price=ce.get("ltp", 0) or 0,
                put_price=pe.get("ltp", 0) or 0,
                call_oi=ce.get("oi", 0) or 0,
                put_oi=pe.get("oi", 0) or 0,
                call_volume=ce.get("volume", 0) or 0,
                put_volume=pe.get("volume", 0) or 0,
                lot_size=lot_size,
            )
        )
    return rows


def _quality_payload(quality) -> dict[str, Any]:
    """
    Build the quality dict explicitly, including `may_draw`.

    `Quality.may_draw` is a `@property`, not a dataclass field, so
    `dataclasses.asdict(quality)` silently drops it. An absent key reads as
    `undefined` in TypeScript - falsy - which would render every good
    snapshot as "do not draw": a silent failure in the safe-looking
    direction. Building the dict by hand keeps `may_draw` in the payload.

    Args:
        quality: The `Quality` verdict from `assess_quality`.

    Returns:
        A JSON-safe dict carrying every field, including `may_draw`.
    """
    payload = asdict(quality)
    payload["may_draw"] = quality.may_draw
    return payload
