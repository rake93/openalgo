import re
from datetime import datetime

from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from utils.constants import VALID_EXCHANGES


# Custom validator for date or timestamp string
def validate_date_or_timestamp(data):
    """
    Validates that the input string is either in 'YYYY-MM-DD' format or a numeric timestamp.
    """
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    timestamp_pattern = re.compile(r"^\d{10,13}$")  # Allows for seconds or milliseconds
    if not (isinstance(data, str) and (date_pattern.match(data) or timestamp_pattern.match(data))):
        raise ValidationError(
            "Field must be a string in 'YYYY-MM-DD' format or a numeric timestamp."
        )


# Custom validator for option offset
def validate_option_offset(data):
    """
    Validates option offset: ATM, ITM1-ITM50, OTM1-OTM50
    """
    data_upper = data.upper()
    if data_upper == "ATM":
        return True

    # Check for ITM pattern: ITM followed by 1-50
    itm_pattern = re.compile(r"^ITM([1-9]|[1-4][0-9]|50)$")
    otm_pattern = re.compile(r"^OTM([1-9]|[1-4][0-9]|50)$")

    if not (itm_pattern.match(data_upper) or otm_pattern.match(data_upper)):
        raise ValidationError("Offset must be ATM, ITM1-ITM50, or OTM1-OTM50")

    return True


class QuotesSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    symbol = fields.Str(required=True)  # Single symbol
    exchange = fields.Str(
        required=True, validate=validate.OneOf(VALID_EXCHANGES)
    )  # Exchange (e.g., NSE, BSE)


class SymbolExchangePair(Schema):
    symbol = fields.Str(required=True)
    exchange = fields.Str(required=True, validate=validate.OneOf(VALID_EXCHANGES))


class MultiQuotesSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    symbols = fields.List(
        fields.Nested(SymbolExchangePair), required=True, validate=validate.Length(min=1)
    )


class HistorySchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    symbol = fields.Str(required=True)
    exchange = fields.Str(
        required=True, validate=validate.OneOf(VALID_EXCHANGES)
    )  # Exchange (e.g., NSE, BSE)
    interval = fields.Str(
        required=True,
        validate=validate.OneOf(
            [
                # Seconds intervals
                "1s",
                "5s",
                "10s",
                "15s",
                "30s",
                "45s",
                # Minutes intervals
                "1m",
                "2m",
                "3m",
                "5m",
                "10m",
                "15m",
                "20m",
                "30m",
                # Hours intervals
                "1h",
                "2h",
                "3h",
                "4h",
                # Daily, Weekly, Monthly, Quarterly, Yearly intervals
                "D",
                "W",
                "M",
                "Q",
                "Y",
            ]
        ),
    )
    start_date = fields.Date(required=True, format="%Y-%m-%d")  # YYYY-MM-DD
    end_date = fields.Date(required=True, format="%Y-%m-%d")  # YYYY-MM-DD
    # Optional: Data source - 'api' (broker, default) or 'db' (DuckDB/Historify)
    source = fields.Str(required=False, load_default="api", validate=validate.OneOf(["api", "db"]))
    # OI is now always included by default for F&O exchanges


class DepthSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    symbol = fields.Str(required=True)
    exchange = fields.Str(
        required=True, validate=validate.OneOf(VALID_EXCHANGES)
    )  # Exchange (e.g., NSE, BSE)


class IntervalsSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))


class SymbolSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    symbol = fields.Str(required=True)  # Symbol code (e.g., RELIANCE)
    exchange = fields.Str(
        required=True, validate=validate.OneOf(VALID_EXCHANGES)
    )  # Exchange (e.g., NSE, BSE)


class TickerSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    symbol = fields.Str(required=True)  # Combined exchange:symbol format
    interval = fields.Str(
        required=True,
        validate=validate.OneOf(["1m", "5m", "15m", "30m", "1h", "4h", "D", "W", "M"]),
    )  # Supported intervals: 1m, 5m, 15m, 30m, 1h, 4h, D, W, M etc.
    from_ = fields.Str(
        data_key="from", required=True, validate=validate_date_or_timestamp
    )  # YYYY-MM-DD or millisecond timestamp
    to = fields.Str(
        required=True, validate=validate_date_or_timestamp
    )  # YYYY-MM-DD or millisecond timestamp
    adjusted = fields.Bool(required=False, default=True)  # Adjust for splits
    sort = fields.Str(
        required=False, default="asc", validate=validate.OneOf(["asc", "desc"])
    )  # Sort direction


class SearchSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    query = fields.Str(required=True)  # Search query/symbol name
    exchange = fields.Str(
        required=False, validate=validate.OneOf(VALID_EXCHANGES)
    )  # Optional exchange filter (e.g., NSE, BSE)


#: Exchanges that list derivatives. Defined once: the same list was repeated in
#: three schemas, and NCO was added to the platform without reaching any of
#: them, so every expiry and option-chain request for NSE commodities was
#: rejected at the API boundary before the service ever ran. See #1748.
F_AND_O_EXCHANGES = ["NFO", "BFO", "MCX", "CDS", "NCO", "BCD", "NCDEX", "CRYPTO"]


class ExpirySchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    symbol = fields.Str(required=True)  # Underlying symbol (e.g., NIFTY, BANKNIFTY)
    exchange = fields.Str(
        required=True, validate=validate.OneOf(F_AND_O_EXCHANGES)
    )  # Exchange (e.g., NFO, BFO, MCX, CDS, CRYPTO)
    instrumenttype = fields.Str(
        required=True, validate=validate.OneOf(["futures", "options"])
    )  # futures or options


class OptionSymbolSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    strategy = fields.Str(
        required=False, allow_none=True
    )  # DEPRECATED: Strategy name (optional, will be removed in future versions)
    underlying = fields.Str(required=True)  # Underlying symbol (NIFTY, RELIANCE, NIFTY28OCT25FUT)
    exchange = fields.Str(
        required=True, validate=validate.OneOf(VALID_EXCHANGES)
    )  # Exchange (NSE_INDEX, NSE, NFO)
    expiry_date = fields.Str(
        required=False
    )  # Expiry date in DDMMMYY format (e.g., 28OCT25). Optional if underlying includes expiry
    strike_int = fields.Int(
        required=False, validate=validate.Range(min=1), allow_none=True
    )  # OPTIONAL: Strike interval. If not provided, actual strikes from database will be used (RECOMMENDED for accuracy)
    offset = fields.Str(
        required=True, validate=validate_option_offset
    )  # Strike offset from ATM (ATM, ITM1-ITM50, OTM1-OTM50)
    option_type = fields.Str(
        required=True, validate=validate.OneOf(["CE", "PE", "ce", "pe"])
    )  # Call or Put option


class OptionGreeksSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    symbol = fields.Str(required=True)  # Option symbol (e.g., NIFTY28NOV2424000CE)
    exchange = fields.Str(
        required=True, validate=validate.OneOf(F_AND_O_EXCHANGES)
    )  # Exchange (NFO, BFO, CDS, MCX, CRYPTO)
    interest_rate = fields.Float(
        required=False, validate=validate.Range(min=0, max=100)
    )  # Risk-free interest rate (annualized %). Optional, defaults per exchange
    forward_price = fields.Float(
        required=False, validate=validate.Range(min=0)
    )  # Optional: Custom forward/synthetic futures price. If provided, skips underlying price fetch
    underlying_symbol = fields.Str(
        required=False
    )  # Optional: Specify underlying symbol (e.g., NIFTY or NIFTY28NOV24FUT)
    underlying_exchange = fields.Str(
        required=False
    )  # Optional: Specify underlying exchange (NSE_INDEX, NFO, etc.)
    expiry_time = fields.Str(
        required=False
    )  # Optional: Custom expiry time in HH:MM format (e.g., "15:30", "19:00"). If not provided, uses exchange defaults


class InstrumentsSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    exchange = fields.Str(
        required=False,
        validate=validate.OneOf(VALID_EXCHANGES),
    )  # Optional exchange filter
    format = fields.Str(
        required=False, validate=validate.OneOf(["json", "csv"])
    )  # Output format (json or csv), defaults to json


class OptionChainSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    underlying = fields.Str(required=True)  # Underlying symbol (e.g., NIFTY, BANKNIFTY, RELIANCE)
    exchange = fields.Str(
        required=True, validate=validate.OneOf(VALID_EXCHANGES)
    )  # Exchange (NSE_INDEX, NSE, NFO, BSE_INDEX, BSE, BFO, MCX, CDS)
    expiry_date = fields.Str(
        required=True
    )  # Expiry date in DDMMMYY format (e.g., 28NOV25) - MANDATORY
    strike_count = fields.Int(
        required=False, validate=validate.Range(min=1, max=100), allow_none=True
    )  # Number of strikes above/below ATM. If not provided, returns entire chain
    with_greeks = fields.Bool(
        required=False, load_default=False
    )  # Attach IV + delta/gamma/theta/vega to every leg, from the quotes already fetched
    interest_rate = fields.Float(
        required=False, validate=validate.Range(min=0, max=100), allow_none=True
    )  # Annualized risk-free rate percentage, Greeks only. Defaults to the exchange default (0)


class OptionTargetSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    underlying = fields.Str(required=True)
    exchange = fields.Str(required=True, validate=validate.OneOf(VALID_EXCHANGES))
    expiry_date = fields.Str(
        required=False,
        allow_none=True,
        validate=validate.Regexp(
            r"^\d{2}[A-Z]{3}\d{2}$",
            error="expiry_date must be DDMMMYY, e.g. 11AUG26 (not the dashed form)",
        ),
    )  # DDMMMYY, e.g. 11AUG26. Optional - defaults to the nearest live expiry.
    reference = fields.Str(
        required=False, load_default="FUT", validate=validate.OneOf(["FUT", "SPOT"])
    )
    reference_price = fields.Float(required=False, allow_none=True)
    target_price = fields.Float(required=True, validate=validate.Range(min=0.01))
    hold_minutes = fields.Float(
        required=False, load_default=45.0, validate=validate.Range(min=0, max=525_600)
    )
    hold_days = fields.Float(
        required=False, allow_none=True, validate=validate.Range(min=0, max=365)
    )
    iv_model = fields.Str(
        required=False,
        load_default="smile_slide",
        validate=validate.OneOf(["smile_slide", "sticky_strike"]),
    )
    vol_beta = fields.Raw(required=False, load_default="auto")
    vol_shift = fields.Float(
        required=False, load_default=0.0, validate=validate.Range(min=-50, max=50)
    )
    day_count = fields.Str(
        required=False,
        load_default="calendar",
        validate=validate.OneOf(["calendar", "trading"]),
    )
    strike_count = fields.Int(
        required=False, load_default=12, validate=validate.Range(min=1, max=50)
    )
    side = fields.Str(
        required=False, load_default="AUTO", validate=validate.OneOf(["AUTO", "CE", "PE"])
    )
    lots = fields.Int(required=False, load_default=1, validate=validate.Range(min=1, max=10_000))
    interest_rate = fields.Float(
        required=False, load_default=0.0, validate=validate.Range(min=-10, max=50)
    )
    objective = fields.Str(
        required=False,
        load_default="balanced",
        validate=validate.OneOf(["balanced", "max_pnl", "max_return", "max_rr", "max_robust"]),
    )


class MarketHolidaysSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    year = fields.Int(
        required=False, validate=validate.Range(min=2020, max=2050)
    )  # Year to get holidays for (defaults to current year)


class MarketTimingsSchema(Schema):
    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    date = fields.Str(required=True)  # Date in YYYY-MM-DD format


class OptionSymbolRequest(Schema):
    """Schema for a single option symbol request in batch"""

    symbol = fields.Str(required=True)  # Option symbol (e.g., NIFTY28NOV2424000CE)
    exchange = fields.Str(required=True, validate=validate.OneOf(F_AND_O_EXCHANGES))
    underlying_symbol = fields.Str(required=False)  # Optional: Specify underlying symbol
    underlying_exchange = fields.Str(required=False)  # Optional: Specify underlying exchange


class MultiOptionGreeksSchema(Schema):
    """Schema for batch option greeks requests"""

    apikey = fields.Str(
        required=True, validate=validate.Length(min=1, max=256)
    )  # API Key for authentication
    symbols = fields.List(
        fields.Nested(OptionSymbolRequest),
        required=True,
        validate=validate.Length(min=1, max=50),  # Max 50 symbols per request
    )
    interest_rate = fields.Float(
        required=False, validate=validate.Range(min=0, max=100)
    )  # Common interest rate for all
    expiry_time = fields.Str(required=False)  # Optional: Common expiry time for all


class RollingOptionSchema(Schema):
    apikey = fields.Str(required=True, validate=validate.Length(min=1, max=256))
    underlying_security_id = fields.Int(required=True)
    exchange_segment = fields.Str(load_default="NSE_FNO")
    instrument = fields.Str(load_default="OPTIDX")
    expiry_flag = fields.Str(required=True, validate=validate.OneOf(["WEEK", "MONTH"]))
    # 1-based: Dhan rejects 0 with "DH-905: expiryCode is required". Verified
    # against the live API on 2026-08-09. WEEK 1/2/3 are the three nearest
    # weeklies; WEEK 3 == MONTH 1 when the third weekly is the monthly expiry.
    expiry_code = fields.Int(load_default=1, validate=validate.Range(min=1))
    # Dhan's own offset vocabulary (ATM, ATM+1..ATM+10, ATM-1..ATM-10 for index options;
    # +/-3 for stocks) -- NOT the ATM/ITM1-50/OTM1-50 scheme validate_option_offset checks
    # elsewhere in this file. Deliberately permissive on the numeric bound: stock options
    # use a different range than index options and we haven't confirmed exact limits
    # against the live API.
    strike = fields.Str(
        required=True,
        validate=validate.Regexp(
            r"^ATM([+-]\d{1,2})?\Z",
            error="strike must be ATM or ATM+n / ATM-n (Dhan's offset vocabulary, "
            "not OpenAlgo's ITM/OTM scheme)",
        ),
    )
    option_type = fields.Str(required=True, validate=validate.OneOf(["CALL", "PUT"]))
    # Dhan-native interval, NOT OpenAlgo's common format ("1m", "5m").
    interval = fields.Str(load_default="1", validate=validate.OneOf(["1", "5", "15", "25", "60"]))
    from_date = fields.Str(required=True)
    to_date = fields.Str(required=True)

    @validates_schema
    def validate_date_range(self, data, **kwargs):
        """Reject malformed and reversed ranges at the edge.

        The adapter's cap only tests `span > 30`, so a reversed range slips through
        it with a negative span and fails later as an opaque broker error. Catching
        it here turns a 500 into a 400 that names the problem.
        """
        try:
            start = datetime.strptime(data["from_date"], "%Y-%m-%d")
            end = datetime.strptime(data["to_date"], "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError(f"dates must be YYYY-MM-DD: {exc}") from exc
        if end < start:
            raise ValidationError(
                f"to_date {data['to_date']} precedes from_date {data['from_date']}"
            )
