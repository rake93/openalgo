"""Rolling (spot-relative) option history, including expired contracts.

Unlike history_service, this endpoint is addressed by underlying security id plus
expiry and strike offset rather than by symbol, so it performs no symbol-to-token
resolution and works for contracts absent from the master contract.
"""

from typing import Any

from database.auth_db import get_auth_token_broker
from services.history_service import import_broker_module
from utils.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_BROKERS = {"dhan"}


def get_rolling_option_history(
    api_key: str,
    underlying_security_id: int,
    exchange_segment: str,
    instrument: str,
    expiry_flag: str,
    expiry_code: int,
    strike: str,
    option_type: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> tuple[bool, dict[str, Any], int]:
    """Return (ok, response_body, http_status)."""
    auth_token, _feed_token, broker_name = get_auth_token_broker(api_key, include_feed_token=True)
    if auth_token is None:
        return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403

    if broker_name not in SUPPORTED_BROKERS:
        return (
            False,
            {
                "status": "error",
                "message": f"Rolling option history is not supported for broker '{broker_name}'",
            },
            400,
        )

    broker_module = import_broker_module(broker_name)
    if broker_module is None:
        return False, {"status": "error", "message": "Broker module not found"}, 404

    try:
        data_handler = broker_module.BrokerData(auth_token)
        payload = data_handler.get_rolling_option_history(
            underlying_security_id=underlying_security_id,
            exchange_segment=exchange_segment,
            instrument=instrument,
            expiry_flag=expiry_flag,
            expiry_code=expiry_code,
            strike=strike,
            option_type=option_type,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )
    except ValueError as exc:
        return False, {"status": "error", "message": str(exc)}, 400
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a 500
        logger.exception("rolling option history failed")
        return False, {"status": "error", "message": str(exc)}, 500

    if not isinstance(payload, dict) or not ({"ce", "pe"} & payload.keys()):
        return (
            False,
            {"status": "error", "message": f"Unexpected broker response: {payload}"},
            502,
        )

    return True, {"status": "success", "data": payload}, 200
