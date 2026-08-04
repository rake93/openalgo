"""Option Target Calculator — pure projection math.

The projection pipeline is built from pure functions over immutable inputs, so
the whole algorithm is testable from recorded fixtures without a live broker
session. That matters because a sign error here is invisible in the UI and
costs real money.

One deliberate exception: `daycount.trading` mode consults the exchange holiday
calendar via `utils.trading_calendar`, which transitively reaches
`services.market_calendar_service`. That lookup degrades to weekends-only
rather than raising, so tests and offline use stay unaffected. Every other
module here is free of database, broker and service dependencies.
"""

from services.option_target.models import (
    Attribution,
    CalibratedIv,
    ForwardAnchor,
    ForwardTarget,
    SmileFit,
    StrikeQuote,
)

__all__ = [
    "Attribution",
    "CalibratedIv",
    "ForwardAnchor",
    "ForwardTarget",
    "SmileFit",
    "StrikeQuote",
]
