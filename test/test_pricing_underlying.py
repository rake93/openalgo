"""Tests for the shared, expiry-aware pricing-underlying resolver.

Runs without a broker session and without any production data.

Layer 1 (the bulk of this file) tests the selection logic in isolation by
patching the private DB-fetch helper, `_find_linked_futures`, with a fixed
in-memory candidate list. This is where the interesting behaviour lives:
which candidate wins, how expiries are compared, and how failures degrade.

Layer 2 is a single integration test against the real symbol master. It is
skipped cleanly (not failed) when that master is not the active database,
since symbol masters change daily and a unit test must not depend on the
developer's live `db/openalgo.db`.
"""

from datetime import date
from unittest.mock import patch

import pytest

from services.pricing_underlying import (
    _RESOLUTION_CACHE,
    _parse_dashed_expiry,
    requires_futures_underlying,
    resolve_pricing_underlying,
)


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    """Bounded TTLCache is module-level state; isolate tests from each other."""
    _RESOLUTION_CACHE.clear()
    yield
    _RESOLUTION_CACHE.clear()


def test_nfo_resolves_to_spot_without_touching_the_database():
    with patch(
        "services.pricing_underlying._find_linked_futures",
        side_effect=AssertionError("NFO must never query the database"),
    ) as mocked:
        ref = resolve_pricing_underlying("NIFTY", "NFO", "11AUG26")

    mocked.assert_not_called()
    assert ref.kind == "SPOT"
    assert ref.symbol == "NIFTY"
    assert ref.exchange == "NFO"
    assert ref.method == "spot_default"
    assert ref.underlying_expiry is None


def test_bfo_resolves_to_spot():
    with patch(
        "services.pricing_underlying._find_linked_futures",
        side_effect=AssertionError("BFO must never query the database"),
    ) as mocked:
        ref = resolve_pricing_underlying("SENSEX", "BFO", "06AUG26")

    mocked.assert_not_called()
    assert ref.kind == "SPOT"
    assert ref.symbol == "SENSEX"
    assert ref.exchange == "BFO"
    assert ref.method == "spot_default"


def test_requires_futures_underlying_flags_only_commodity_exchanges():
    assert requires_futures_underlying("MCX") is True
    assert requires_futures_underlying("NCDEX") is True
    assert requires_futures_underlying("NCO") is True
    assert requires_futures_underlying("mcx") is True  # case-insensitive
    assert requires_futures_underlying("NFO") is False
    assert requires_futures_underlying("BFO") is False
    assert requires_futures_underlying("NSE") is False


def test_nearest_future_on_or_after_expiry_wins_not_first_or_absolute_nearest():
    candidates = [
        ("CRUDEOIL15AUG26FUT", "15-AUG-26"),  # closer in absolute distance, but
        # before the option's own expiry - must be rejected
        ("CRUDEOIL19SEP26FUT", "19-SEP-26"),  # also valid, but not the nearest
        ("CRUDEOIL22AUG26FUT", "22-AUG-26"),  # correct answer, listed last
    ]
    with patch("services.pricing_underlying._find_linked_futures", return_value=candidates):
        ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "17AUG26")

    assert ref.kind == "FUTURE"
    assert ref.symbol == "CRUDEOIL22AUG26FUT"
    assert ref.underlying_expiry == "22-AUG-26"
    assert ref.method == "linked_future_nearest_on_or_after_option_expiry"


def test_expiry_comparison_is_chronological_not_lexicographic():
    early = _parse_dashed_expiry("17-AUG-26")
    late = _parse_dashed_expiry("08-OCT-26")

    # Naive string ordering gets this backwards: "08-OCT-26" < "17-AUG-26"
    # lexicographically, even though October is chronologically later.
    assert "08-OCT-26" < "17-AUG-26"
    assert late > early


def test_selection_uses_chronological_not_lexicographic_comparison():
    # "08-OCT-26" sorts before "17-AUG-26" as a string (leading "0" < "1"),
    # but October is the chronologically later expiry. A resolver that sorted
    # candidates as strings instead of parsed dates would pick October here,
    # since it looks "smaller"/"earlier" lexicographically; the correct,
    # nearest-on-or-after answer is August.
    candidates = [
        ("CRUDEOIL08OCT26FUT", "08-OCT-26"),
        ("CRUDEOIL17AUG26FUT", "17-AUG-26"),
    ]
    with patch("services.pricing_underlying._find_linked_futures", return_value=candidates):
        ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "01AUG26")

    assert ref.kind == "FUTURE"
    assert ref.symbol == "CRUDEOIL17AUG26FUT"
    assert ref.underlying_expiry == "17-AUG-26"
    assert ref.method == "linked_future_nearest_on_or_after_option_expiry"


def test_all_candidates_before_expiry_falls_back_to_latest_available():
    candidates = [
        ("CRUDEOIL17AUG26FUT", "17-AUG-26"),
        ("CRUDEOIL08OCT26FUT", "08-OCT-26"),  # chronologically the latest,
        # even though its string form sorts before "17-AUG-26"
    ]
    with patch("services.pricing_underlying._find_linked_futures", return_value=candidates):
        ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "15NOV26")

    assert ref.kind == "FUTURE"
    assert ref.symbol == "CRUDEOIL08OCT26FUT"
    assert ref.underlying_expiry == "08-OCT-26"
    assert ref.method == "linked_future_latest_available_fallback"


def test_empty_candidate_list_degrades_to_spot():
    with patch("services.pricing_underlying._find_linked_futures", return_value=[]):
        ref = resolve_pricing_underlying("NOTAREALCOMMODITYROOT", "MCX", "17AUG26")

    assert ref.kind == "SPOT"
    assert ref.method == "no_linked_future_found"
    assert ref.symbol == "NOTAREALCOMMODITYROOT"
    assert ref.underlying_expiry is None


def test_fetch_failure_degrades_to_spot_without_raising():
    with patch(
        "services.pricing_underlying._find_linked_futures",
        side_effect=RuntimeError("database exploded"),
    ):
        ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "17AUG26")

    assert ref.kind == "SPOT"
    assert ref.method == "lookup_failed"
    assert ref.symbol == "CRUDEOIL"
    assert ref.underlying_expiry is None


def test_resolved_ref_reports_both_expiries_and_the_method():
    candidates = [("CRUDEOIL19AUG26FUT", "19-AUG-26")]
    with patch("services.pricing_underlying._find_linked_futures", return_value=candidates):
        ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "17AUG26")

    assert ref.kind == "FUTURE"
    assert ref.symbol == "CRUDEOIL19AUG26FUT"
    assert ref.option_expiry == "17AUG26"
    assert ref.underlying_expiry == "19-AUG-26"
    assert _parse_dashed_expiry(ref.underlying_expiry) >= date(2026, 8, 17)
    assert ref.method == "linked_future_nearest_on_or_after_option_expiry"


def _production_master_available() -> bool:
    """True when the live symbol master is the active database and populated."""
    try:
        from database.symbol import SymToken, db_session

        with db_session() as session:
            row = (
                session.query(SymToken.id)
                .filter(SymToken.exchange == "MCX", SymToken.instrumenttype == "FUT")
                .first()
            )
        return row is not None
    except Exception:  # noqa: BLE001 - must never blow up test collection
        return False


@pytest.mark.skipif(
    not _production_master_available(),
    reason="live symbol master not attached to this test database",
)
def test_real_master_resolves_crudeoil_to_its_own_future():
    ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "17AUG26")

    assert ref.kind == "FUTURE"
    # Explicit symbol assertion: prefix-matching `CRUDEOIL%` would also match
    # a `CRUDEOILM...FUT` row here, which is the wrong (mini) contract. No
    # assertion on the specific expiry, since the real master rolls forward.
    assert ref.symbol.startswith("CRUDEOIL")
    assert not ref.symbol.startswith("CRUDEOILM")
