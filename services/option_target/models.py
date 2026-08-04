"""Shared value objects for the Option Target Calculator.

Every dataclass here is frozen. The projection engine is a pipeline of pure
functions and immutable inputs make it safe to cache, reorder and test.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StrikeQuote:
    """One side (CE or PE) of one strike, as quoted by the broker."""

    strike: float
    option_type: str  # "CE" or "PE"
    symbol: str
    ltp: float
    bid: float
    ask: float
    oi: int
    volume: int
    lot_size: int

    @property
    def mid(self) -> float:
        """Mid price, falling back to LTP.

        Mid is preferred over LTP for IV calibration because LTP goes stale and
        one-sided on thin strikes, which biases the whole smile. A crossed or
        one-sided book is treated as no book at all.
        """
        if self.bid > 0 and self.ask > 0 and self.ask >= self.bid:
            return (self.bid + self.ask) / 2
        return self.ltp

    @property
    def half_spread(self) -> float:
        """Half the quoted spread; 0 when the book is unusable."""
        if self.bid > 0 and self.ask > 0 and self.ask >= self.bid:
            return (self.ask - self.bid) / 2
        return 0.0

    @property
    def spread_pct(self) -> float:
        """Spread as a percentage of mid. 0 when not computable."""
        m = self.mid
        if m <= 0 or self.half_spread <= 0:
            return 0.0
        return (self.half_spread * 2) / m * 100


@dataclass(frozen=True)
class ForwardAnchor:
    """The forward the options of one expiry are actually priced off."""

    forward: float
    spot: float
    atm_strike: float
    source: str  # "parity" or "spot_fallback"

    @property
    def basis(self) -> float:
        return self.forward - self.spot


@dataclass(frozen=True)
class ForwardTarget:
    """Where the forward lands when the user's reference hits its target."""

    forward: float
    mode: str  # "exact" or "basis_modelled"
    reference: str  # "FUT" or "SPOT"
    reference_now: float
    reference_target: float

    @property
    def move_pct(self) -> float:
        if self.reference_now <= 0:
            return 0.0
        return (self.reference_target / self.reference_now - 1) * 100


@dataclass(frozen=True)
class CalibratedIv:
    """One strike's implied vol, backed out of its live mid."""

    strike: float
    option_type: str
    log_moneyness: float
    iv: float
    vega: float


@dataclass(frozen=True)
class SmileFit:
    """Vega-weighted quadratic fit of IV against log-moneyness."""

    a: float
    b: float
    c: float
    x_lo: float
    x_hi: float
    rms: float
    n_points: int
    degenerate: bool  # True when too few points; `a` is a flat ATM IV


@dataclass(frozen=True)
class Attribution:
    """Decomposition of the projected premium change."""

    delta: float
    gamma: float
    theta: float
    vega: float
    spread: float
    residual: float
    total: float
