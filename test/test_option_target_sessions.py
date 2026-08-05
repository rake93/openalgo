"""Validation tests for the live market-session provider."""

from datetime import date
from unittest.mock import patch

import pytest

from services.option_target_sessions import _SESSION_CACHE, build_session_provider


@pytest.fixture(autouse=True)
def _clear_session_cache():
    """Isolate tests from each other.

    Several tests below deliberately reuse the same (exchange, date) — they
    are exercising different mocked responses for that same key — so the
    production module-level cache must be cleared between tests or an earlier
    test's cached result leaks into a later one.
    """
    _SESSION_CACHE.clear()
    yield
    _SESSION_CACHE.clear()


def _timings(rows):
    """Shape a fake get_timings success response."""
    return True, {"status": "success", "data": rows}, 200


def _epoch_ms(y, m, d, hh, mm):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return int(datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp() * 1000)


def test_provider_parses_a_valid_session():
    rows = [
        {
            "exchange": "MCX",
            "start_time": _epoch_ms(2026, 8, 4, 9, 0),
            "end_time": _epoch_ms(2026, 8, 4, 23, 55),
        }
    ]
    with patch("services.option_target_sessions.get_timings", return_value=_timings(rows)):
        provider = build_session_provider("MCX")
        assert provider(date(2026, 8, 4)) == ((9, 0), (23, 55))


def test_provider_reports_closed_when_exchange_absent_for_the_date():
    rows = [
        {
            "exchange": "NFO",
            "start_time": _epoch_ms(2026, 8, 4, 9, 15),
            "end_time": _epoch_ms(2026, 8, 4, 15, 30),
        }
    ]
    with patch("services.option_target_sessions.get_timings", return_value=_timings(rows)):
        provider = build_session_provider("MCX")
        assert provider(date(2026, 8, 4)) is None


def test_provider_rejects_a_session_spanning_two_dates_and_falls_back():
    # This is the exact signature of the corrupt seeded special-session data.
    rows = [
        {
            "exchange": "MCX",
            "start_time": _epoch_ms(2026, 8, 4, 11, 20),
            "end_time": _epoch_ms(2026, 8, 5, 2, 15),
        }
    ]
    with patch("services.option_target_sessions.get_timings", return_value=_timings(rows)):
        provider = build_session_provider("MCX")
        # Falls back to the static MCX table entry rather than trusting bad data.
        assert provider(date(2026, 8, 4)) == ((9, 0), (23, 55))


def test_provider_rejects_a_non_positive_duration_and_falls_back():
    rows = [
        {
            "exchange": "MCX",
            "start_time": _epoch_ms(2026, 8, 4, 15, 0),
            "end_time": _epoch_ms(2026, 8, 4, 15, 0),
        }
    ]
    with patch("services.option_target_sessions.get_timings", return_value=_timings(rows)):
        provider = build_session_provider("MCX")
        assert provider(date(2026, 8, 4)) == ((9, 0), (23, 55))


def test_provider_falls_back_when_the_service_fails():
    with patch(
        "services.option_target_sessions.get_timings",
        return_value=(False, {"status": "error", "message": "boom"}, 500),
    ):
        provider = build_session_provider("NFO")
        assert provider(date(2026, 8, 4)) == ((9, 15), (15, 30))


def test_provider_falls_back_when_the_service_raises():
    with patch("services.option_target_sessions.get_timings", side_effect=RuntimeError("down")):
        provider = build_session_provider("NFO")
        assert provider(date(2026, 8, 4)) == ((9, 15), (15, 30))


def test_provider_caches_per_date():
    rows = [
        {
            "exchange": "NFO",
            "start_time": _epoch_ms(2026, 8, 4, 9, 15),
            "end_time": _epoch_ms(2026, 8, 4, 15, 30),
        }
    ]
    with patch(
        "services.option_target_sessions.get_timings", return_value=_timings(rows)
    ) as mock_timings:
        provider = build_session_provider("NFO")
        provider(date(2026, 8, 4))
        provider(date(2026, 8, 4))
        provider(date(2026, 8, 4))
        assert mock_timings.call_count == 1


def test_provider_is_case_insensitive_on_exchange():
    rows = [
        {
            "exchange": "MCX",
            "start_time": _epoch_ms(2026, 8, 4, 9, 0),
            "end_time": _epoch_ms(2026, 8, 4, 23, 55),
        }
    ]
    with patch("services.option_target_sessions.get_timings", return_value=_timings(rows)):
        provider = build_session_provider("mcx")
        assert provider(date(2026, 8, 4)) == ((9, 0), (23, 55))


# ---------------------------------------------------------- session_is_open


def _ist(y, m, d, hh, mm):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Kolkata"))


def test_session_is_open_reports_the_window_boundaries_inclusively():
    from services.option_target_sessions import session_is_open

    with patch(
        "services.option_target_sessions.build_session_provider",
        return_value=lambda _day: ((9, 15), (15, 30)),
    ):
        assert session_is_open("NFO", _ist(2026, 8, 5, 9, 15)) is True
        assert session_is_open("NFO", _ist(2026, 8, 5, 11, 0)) is True
        assert session_is_open("NFO", _ist(2026, 8, 5, 15, 30)) is True
        assert session_is_open("NFO", _ist(2026, 8, 5, 9, 14)) is False
        assert session_is_open("NFO", _ist(2026, 8, 5, 16, 0)) is False


def test_session_is_open_reports_closed_when_the_exchange_has_no_session():
    """A full holiday. The provider returns None and that must not be read as
    "no data, assume open"."""
    from services.option_target_sessions import session_is_open

    with patch(
        "services.option_target_sessions.build_session_provider",
        return_value=lambda _day: None,
    ):
        assert session_is_open("NFO", _ist(2026, 8, 5, 11, 0)) is False
        assert session_is_open("NFO", _ist(2026, 8, 5, 11, 0), default=False) is False


def test_the_session_guard_default_differs_by_caller_on_a_hard_failure():
    """The two callers want opposite behaviour when the lookup RAISES, and the
    difference is deliberate. A price projection must never be blocked by a
    calendar error, so it fails OPEN. A recorder that fails open makes a broker
    call a minute around the clock, so it fails CLOSED.

    The realistic failure - a suspect window, like the seeded MCX sessions that
    decode to 895 minutes across two dates - is handled by the static-table
    fallback inside the provider and reaches neither default.
    """
    from services.option_target_sessions import session_is_open

    with patch(
        "services.option_target_sessions.build_session_provider",
        side_effect=RuntimeError("calendar exploded"),
    ):
        assert session_is_open("NFO", _ist(2026, 8, 5, 11, 0)) is True
        assert session_is_open("NFO", _ist(2026, 8, 5, 11, 0), default=False) is False
