"""
Per-strike signed dealer gamma exposure.

    GEX_k = gamma_k(call) * w_k(call) * F^2 * 0.01
          - gamma_k(put)  * w_k(put)  * F^2 * 0.01

Calls positive, puts negative. That is the standard convention across every
published GEX product and encodes the approximation that dealers are long
calls and short puts at the index level. It is deliberately a single constant
here rather than a setting - if Indian market structure is ever shown to
warrant inverting it, DEALER_CALL_SIGN is the one place to change.

Note there is no contract-multiplier (lot size) factor. The textbook formula
carries one because open interest is conventionally quoted in contracts
(lots); this broker's chain reports OI and volume already multiplied by the
lot size - verified across a live NIFTY chain where every OI and volume value
was exactly divisible by the lot size. Multiplying by `lot_size` again would
double-count it. See the comment at the multiplication site in
`price_exposures` before "fixing" this back to the textbook form.

Units are currency delta change per 1% move in the underlying. The F^2 * 0.01
factor is constant across strikes, so it moves neither the walls nor the
zero-gamma level relative to an unscaled profile - it converts units only.
"""

import math
from dataclasses import dataclass
from typing import Literal

from services.gex_levels.blackscholes import atm_iv_from, safe_gamma, safe_iv

WeightBy = Literal["oi", "volume"]

DEALER_CALL_SIGN = 1.0
DEALER_PUT_SIGN = -1.0

# Converts unit gamma into delta change per 1% move.
_ONE_PERCENT = 0.01


@dataclass(frozen=True)
class ChainRow:
    """One strike of the option chain, both legs, as fetched."""

    strike: float
    call_price: float
    put_price: float
    call_oi: float
    put_oi: float
    call_volume: float
    put_volume: float
    lot_size: int


@dataclass(frozen=True)
class StrikeExposure:
    """Signed gamma exposure at one strike, in currency per 1% move."""

    strike: float
    call_gex: float
    put_gex: float
    net_gex: float
    call_iv: float | None
    put_iv: float | None


@dataclass(frozen=True)
class ResolvedIVs:
    """
    Per-leg implied volatilities, inverted once at the real forward.

    Held separately from pricing because the zero-gamma scan re-prices the same
    chain at many hypothetical forwards, and the volatility must NOT move with
    them: the premiums these were inverted from were quoted at today's forward.

    Attributes:
        call: Strike to its call IV (decimal), or None where it did not invert.
        put: Strike to its put IV (decimal), or None where it did not invert.
        fallback: The chain-wide volatility to price a leg that did not invert.
            Never None - `atm_iv_from` always yields a number.
    """

    call: dict[float, float | None]
    put: dict[float, float | None]
    fallback: float


def resolve_ivs(
    black76,
    rows: list[ChainRow],
    forward: float,
    t_years: float,
    r: float,
    atm_strike: float | None,
) -> ResolvedIVs:
    """
    Invert both legs of every strike, and derive the chain-wide fallback.

    This is the expensive half of the calculation - two Black-76 solver calls
    per strike - and the half that is only ever valid at the real forward. It
    is a separate function so that `scan_zero_gamma` can run it exactly once
    and then re-price the resulting surface at sixty hypothetical forwards.

    A leg that will not invert stays None here rather than being filled in with
    the fallback, because the quality gate distinguishes an observed IV from a
    substituted one. Substitution happens at pricing time.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows, any order.
        forward: The REAL per-expiry forward price (F) the premiums were quoted
            at. Never spot, and never a hypothetical scan level.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        atm_strike: ATM strike, for the IV fallback.

    Returns:
        ResolvedIVs, keyed by strike.
    """
    per_strike_iv: dict[float, float | None] = {}
    call_ivs: dict[float, float | None] = {}
    put_ivs: dict[float, float | None] = {}

    for row in rows:
        call_iv = safe_iv(black76, row.call_price, forward, row.strike, r, t_years, "c")
        put_iv = safe_iv(black76, row.put_price, forward, row.strike, r, t_years, "p")
        call_ivs[row.strike] = call_iv
        put_ivs[row.strike] = put_iv
        sides = [v for v in (call_iv, put_iv) if v is not None]
        # At the same strike and forward, put-call parity puts the two legs
        # within noise of each other, so their mean is a more stable estimate
        # of that strike's volatility than either leg alone.
        per_strike_iv[row.strike] = sum(sides) / len(sides) if sides else None

    return ResolvedIVs(
        call=call_ivs,
        put=put_ivs,
        fallback=atm_iv_from(per_strike_iv, atm_strike),
    )


def price_exposures(
    black76,
    rows: list[ChainRow],
    ivs: ResolvedIVs,
    forward: float,
    t_years: float,
    r: float,
    weight_by: WeightBy,
) -> list[StrikeExposure]:
    """
    Signed GEX at `forward`, using PRE-RESOLVED volatilities.

    `forward` is what gamma is evaluated at and may be hypothetical; `ivs` must
    have been resolved at the real forward. That asymmetry is the whole point of
    the seam. Gamma genuinely depends on where the underlying sits, so the scan
    has to move F. Implied volatility does not: it was inverted out of premiums
    the market quoted at one particular forward, and re-deriving it against a
    price the market never traded at yields a number with no meaning - and, far
    enough away, no solution at all.

    A strike whose own premium would not invert is still priced, with the
    chain's fallback volatility, rather than dropped. Dropping it would move the
    walls by removing real open interest from the profile.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows, any order.
        ivs: Volatilities from `resolve_ivs`, inverted at the real forward.
            MUST have been resolved from this exact same `rows` list -
            `forward` here may be hypothetical (the zero-gamma scan moves it),
            but `ivs` may not: it is only ever valid at the real forward it was
            inverted at.
        forward: The price to evaluate gamma at. May be hypothetical.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        One StrikeExposure per input row, sorted by strike ascending. The sort
        is a precondition of `find_walls`, which reads the first and last
        entries to decide whether a wall sits at the window edge.

    Raises:
        ValueError: If `weight_by` is neither 'oi' nor 'volume'. An unrecognised
            weighting must never quietly read as open interest - it would change
            the meaning of the whole study with no signal to the caller.
        ValueError: If a row's strike is absent from `ivs.call` (or `ivs.put`).
            That is a genuine key absence - `rows` does not match what
            `resolve_ivs` was given - and is not the same thing as a strike
            that is present but `None`, which means the leg did not invert and
            is a legitimate, expected case that still takes the fallback.
    """
    if weight_by not in ("oi", "volume"):
        raise ValueError(f"weight_by must be 'oi' or 'volume', got {weight_by!r}")

    ordered = sorted(rows, key=lambda row: row.strike)
    use_volume = weight_by == "volume"

    # Converts unit gamma into currency delta change per 1% move. A non-finite
    # forward yields no exposure rather than a profile of NaN.
    scale_per_percent = forward * forward * _ONE_PERCENT if math.isfinite(forward) else 0.0

    out: list[StrikeExposure] = []
    for row in ordered:
        if row.strike not in ivs.call or row.strike not in ivs.put:
            raise ValueError(
                f"ivs was not resolved for strike {row.strike}; resolve_ivs and "
                "price_exposures must be given the same rows"
            )
        call_iv = ivs.call.get(row.strike)
        put_iv = ivs.put.get(row.strike)
        call_weight = _finite(row.call_volume if use_volume else row.call_oi)
        put_weight = _finite(row.put_volume if use_volume else row.put_oi)

        call_sigma = call_iv if call_iv is not None else ivs.fallback
        put_sigma = put_iv if put_iv is not None else ivs.fallback

        call_gamma = safe_gamma(black76, "c", forward, row.strike, t_years, r, call_sigma)
        put_gamma = safe_gamma(black76, "p", forward, row.strike, t_years, r, put_sigma)

        # No `row.lot_size` factor here, deliberately. The textbook formula
        # multiplies by the contract multiplier because OI is conventionally
        # quoted in contracts (lots); this broker's chain reports OI and
        # volume already multiplied by the lot size (verified: every OI and
        # volume value across a live chain divides evenly by it). Multiplying
        # by lot_size again would double-count it - e.g. 65x too large on
        # NIFTY. `lot_size` is still carried on `ChainRow` for display only.
        call_gex = DEALER_CALL_SIGN * call_gamma * call_weight * scale_per_percent
        put_gex = DEALER_PUT_SIGN * put_gamma * put_weight * scale_per_percent

        out.append(
            StrikeExposure(
                strike=row.strike,
                call_gex=call_gex,
                put_gex=put_gex,
                net_gex=call_gex + put_gex,
                call_iv=call_iv,
                put_iv=put_iv,
            )
        )
    return out


def compute_exposures(
    black76,
    rows: list[ChainRow],
    forward: float,
    t_years: float,
    r: float,
    atm_strike: float | None,
    weight_by: WeightBy,
) -> list[StrikeExposure]:
    """
    Resolve and price at the same forward - the single-shot path.

    This is what every caller wants except the zero-gamma scan, which needs to
    resolve once and price many times; see `price_exposures` for why the two
    halves must not share a forward there.

    Args:
        black76: The opengreeks.black76 module.
        rows: Chain rows, any order.
        forward: Per-expiry forward price (F). Never spot.
        t_years: Time to expiry in years.
        r: Risk-free rate as a decimal.
        atm_strike: ATM strike, for the IV fallback.
        weight_by: 'oi' for the standing book, 'volume' for today's flow.

    Returns:
        One StrikeExposure per input row, sorted by strike ascending.

    Raises:
        ValueError: If `weight_by` is neither 'oi' nor 'volume'.
    """
    ivs = resolve_ivs(
        black76,
        rows,
        forward=forward,
        t_years=t_years,
        r=r,
        atm_strike=atm_strike,
    )
    return price_exposures(
        black76,
        rows,
        ivs,
        forward=forward,
        t_years=t_years,
        r=r,
        weight_by=weight_by,
    )


def _finite(weight: float) -> float:
    """
    A weight of NaN or infinity is treated as no position at all.

    Broker adapters do emit NaN open interest, and a null field read as a float
    arrives the same way. One such strike would otherwise turn the entire net
    profile into NaN, which silently takes both walls and the zero-gamma level
    with it.
    """
    return weight if math.isfinite(weight) else 0.0
