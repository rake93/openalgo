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
            "open": [10.0], "high": [12.0], "low": [9.0], "close": [11.0],
            "volume": [100], "oi": [500], "iv": [12.5],
            "strike": [25000.0], "spot": [24990.0],
        },
        "pe": {
            "timestamp": [1754282340],
            "open": [8.0], "high": [9.0], "low": [7.0], "close": [7.5],
            "volume": [80], "oi": [400], "iv": [13.1],
            "strike": [25000.0], "spot": [24990.0],
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
            "open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot",
        ],
        "fromDate": "2026-07-01",
        "toDate": "2026-07-31",
    }
    assert set(result) == {"ce", "pe"}
    assert result["ce"]["close"] == [11.0]


def test_rolling_option_history_rejects_range_over_30_days(monkeypatch):
    monkeypatch.setattr(data, "get_api_response", lambda *a, **k: _fake_body())
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
