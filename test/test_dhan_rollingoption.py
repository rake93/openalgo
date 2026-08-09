import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("API_KEY_PEPPER", "test-pepper-value-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import broker.dhan.api.data as data  # noqa: E402


def _fake_body():
    return {
        "ce": {
            "timestamp": [1754282340],
            "open": [10.0],
            "high": [12.0],
            "low": [9.0],
            "close": [11.0],
            "volume": [100],
            "oi": [500],
            "iv": [12.5],
            "strike": [25000.0],
            "spot": [24990.0],
        },
        "pe": {
            "timestamp": [1754282340],
            "open": [8.0],
            "high": [9.0],
            "low": [7.0],
            "close": [7.5],
            "volume": [80],
            "oi": [400],
            "iv": [13.1],
            "strike": [25000.0],
            "spot": [24990.0],
        },
    }


def test_rolling_option_history_builds_documented_payload(monkeypatch):
    captured = {}

    def fake_get_api_response(endpoint, auth, method="POST", payload="", retry_count=0):
        captured["endpoint"] = endpoint
        captured["method"] = method
        captured["auth"] = auth
        captured["payload"] = json.loads(payload)
        return _fake_body()

    monkeypatch.setattr(data, "get_api_response", fake_get_api_response)

    broker = data.BrokerData("test-token")
    result = broker.get_rolling_option_history(
        underlying_security_id=13,
        exchange_segment="NSE_FNO",
        instrument="OPTIDX",
        expiry_flag="WEEK",
        expiry_code=0,
        strike="ATM",
        option_type="CALL",
        interval="1",
        from_date="2026-07-01",
        to_date="2026-07-31",
    )

    assert captured["endpoint"] == "/v2/charts/rollingoption"
    assert captured["method"] == "POST"
    assert captured["auth"] == "test-token"
    assert captured["payload"] == {
        "exchangeSegment": "NSE_FNO",
        "interval": "1",
        "securityId": "13",
        "instrument": "OPTIDX",
        "expiryFlag": "WEEK",
        "expiryCode": 0,
        "strike": "ATM",
        "drvOptionType": "CALL",
        "requiredData": [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "oi",
            "iv",
            "strike",
            "spot",
        ],
        "fromDate": "2026-07-01",
        "toDate": "2026-07-31",
    }
    assert set(result) == {"ce", "pe"}
    assert result["ce"]["close"] == [11.0]


def _fail_if_called(*args, **kwargs):
    pytest.fail("guard must reject before calling the API")


def test_rolling_option_history_rejects_range_over_30_days(monkeypatch):
    monkeypatch.setattr(data, "get_api_response", _fail_if_called)
    broker = data.BrokerData("test-token")

    with pytest.raises(ValueError, match="30 days"):
        broker.get_rolling_option_history(
            underlying_security_id=13,
            exchange_segment="NSE_FNO",
            instrument="OPTIDX",
            expiry_flag="WEEK",
            expiry_code=0,
            strike="ATM",
            option_type="CALL",
            interval="1",
            from_date="2026-06-01",
            to_date="2026-07-31",
        )


def test_rolling_option_history_rejects_range_of_31_days(monkeypatch):
    """Pins the exact boundary the guard exists to reject.

    30 days is accepted (see the payload test above, which spans
    2026-07-01..2026-07-31); 31 days must be rejected. Without this test,
    either widening ROLLING_OPTION_MAX_DAYS to 31 or loosening `>` to `>=`
    would still leave both other tests green.
    """
    monkeypatch.setattr(data, "get_api_response", _fail_if_called)
    broker = data.BrokerData("test-token")

    with pytest.raises(ValueError, match="30 days"):
        broker.get_rolling_option_history(
            underlying_security_id=13,
            exchange_segment="NSE_FNO",
            instrument="OPTIDX",
            expiry_flag="WEEK",
            expiry_code=0,
            strike="ATM",
            option_type="CALL",
            interval="1",
            from_date="2026-07-01",
            to_date="2026-08-01",
        )


def test_service_rejects_invalid_api_key(monkeypatch):
    import services.rollingoption_service as svc

    monkeypatch.setattr(
        svc, "get_auth_token_broker", lambda api_key, include_feed_token=True: (None, None, None)
    )

    ok, response, status = svc.get_rolling_option_history(
        api_key="bad-key",
        underlying_security_id=13,
        exchange_segment="NSE_FNO",
        instrument="OPTIDX",
        expiry_flag="WEEK",
        expiry_code=0,
        strike="ATM",
        option_type="CALL",
        interval="1",
        from_date="2026-07-01",
        to_date="2026-07-31",
    )

    assert ok is False
    assert status == 403
    assert response["status"] == "error"


def test_service_returns_broker_payload(monkeypatch):
    import services.rollingoption_service as svc

    monkeypatch.setattr(
        svc,
        "get_auth_token_broker",
        lambda api_key, include_feed_token=True: ("tok", "feed", "dhan"),
    )

    class FakeBrokerData:
        def __init__(self, auth_token):
            self.auth_token = auth_token

        def get_rolling_option_history(self, **kwargs):
            return _fake_body()

    fake_module = type("M", (), {"BrokerData": FakeBrokerData})
    monkeypatch.setattr(svc, "import_broker_module", lambda name: fake_module)

    ok, response, status = svc.get_rolling_option_history(
        api_key="good-key",
        underlying_security_id=13,
        exchange_segment="NSE_FNO",
        instrument="OPTIDX",
        expiry_flag="WEEK",
        expiry_code=0,
        strike="ATM",
        option_type="CALL",
        interval="1",
        from_date="2026-07-01",
        to_date="2026-07-31",
    )

    assert ok is True
    assert status == 200
    assert response["status"] == "success"
    assert response["data"]["ce"]["close"] == [11.0]


def test_schema_rejects_a_reversed_date_range():
    from marshmallow import ValidationError as MarshmallowValidationError

    from restx_api.data_schemas import RollingOptionSchema

    payload = {
        "apikey": "k",
        "underlying_security_id": 13,
        "expiry_flag": "WEEK",
        "strike": "ATM",
        "option_type": "CALL",
        "from_date": "2026-07-31",
        "to_date": "2026-07-01",
    }
    with pytest.raises(MarshmallowValidationError, match="precedes"):
        RollingOptionSchema().load(payload)


def test_schema_rejects_a_malformed_date():
    from marshmallow import ValidationError as MarshmallowValidationError

    from restx_api.data_schemas import RollingOptionSchema

    payload = {
        "apikey": "k",
        "underlying_security_id": 13,
        "expiry_flag": "WEEK",
        "strike": "ATM",
        "option_type": "CALL",
        "from_date": "01-07-2026",
        "to_date": "2026-07-31",
    }
    with pytest.raises(MarshmallowValidationError, match="YYYY-MM-DD"):
        RollingOptionSchema().load(payload)
