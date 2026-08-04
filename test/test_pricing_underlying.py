"""Tests for the shared, expiry-aware pricing-underlying resolver.

Runs without a broker session. The futures-lookup tests use the real, live
`db/openalgo.db` symbol master rather than a fixture, so they assert on
structure (root separation, expiry-comparison direction, method labels)
rather than on specific live prices or contract counts that would change as
the master refreshes.

`test/conftest.py` defaults `DATABASE_URL` to the (empty) `db/openalgo-test.db`
before this module is imported, so the override below must happen first, at
import time, before anything here imports `database.symbol` (directly or via
`services.pricing_underlying`'s lazily-imported DB helper).
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///db/openalgo.db"

from datetime import date  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from services.pricing_underlying import (  # noqa: E402
    _RESOLUTION_CACHE,
    _parse_dashed_expiry,
    requires_futures_underlying,
    resolve_pricing_underlying,
)

try:
    from database.symbol import SymToken, db_session  # noqa: E402

    with db_session() as _session:
        _MCX_ROOTS = {
            row[0]
            for row in _session.query(SymToken.name)
            .filter(SymToken.exchange == "MCX", SymToken.instrumenttype == "FUT")
            .distinct()
            .all()
        }
except Exception:  # noqa: BLE001 - collection must not blow up if the DB is unavailable
    _MCX_ROOTS = set()

_HAS_CRUDEOILM = "CRUDEOILM" in _MCX_ROOTS
_HAS_GOLDM = "GOLDM" in _MCX_ROOTS


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


def test_mcx_option_resolves_to_the_linked_future():
    ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "17AUG26")

    assert ref.kind == "FUTURE"
    assert ref.symbol == "CRUDEOIL19AUG26FUT"
    assert ref.underlying_expiry == "19-AUG-26"
    assert ref.method == "linked_future_nearest_on_or_after_option_expiry"


@pytest.mark.skipif(not _HAS_CRUDEOILM, reason="CRUDEOILM has no futures in this symbol master")
def test_mini_contract_does_not_collide_with_the_full_size_root():
    ref = resolve_pricing_underlying("CRUDEOILM", "MCX", "17AUG26")

    assert ref.kind == "FUTURE"
    # Explicit symbol assertion: prefix-matching `CRUDEOIL%` would also match
    # a `CRUDEOIL...FUT` row here, which is exactly the wrong contract.
    assert ref.symbol == "CRUDEOILM19AUG26FUT"
    assert ref.symbol.startswith("CRUDEOILM")


@pytest.mark.skipif(
    not (_HAS_GOLDM and "GOLD" in _MCX_ROOTS),
    reason="GOLD/GOLDM futures not both present in this symbol master",
)
def test_gold_and_goldm_resolve_to_their_own_families():
    gold_ref = resolve_pricing_underlying("GOLD", "MCX", "31AUG26")
    goldm_ref = resolve_pricing_underlying("GOLDM", "MCX", "28AUG26")

    assert gold_ref.kind == "FUTURE"
    assert gold_ref.symbol == "GOLD05OCT26FUT"
    assert not gold_ref.symbol.startswith("GOLDM")

    assert goldm_ref.kind == "FUTURE"
    assert goldm_ref.symbol == "GOLDM04SEP26FUT"
    assert goldm_ref.symbol.startswith("GOLDM")


def test_resolved_ref_reports_both_expiries_and_the_method():
    ref = resolve_pricing_underlying("CRUDEOIL", "MCX", "17AUG26")

    assert ref.kind == "FUTURE"
    assert ref.option_expiry == "17AUG26"
    assert ref.underlying_expiry is not None
    assert _parse_dashed_expiry(ref.underlying_expiry) >= date(2026, 8, 17)
    assert ref.method
    assert isinstance(ref.method, str)


def test_unknown_root_degrades_to_spot():
    ref = resolve_pricing_underlying("NOTAREALCOMMODITYROOT", "MCX", "17AUG26")

    assert ref.kind == "SPOT"
    assert ref.method == "no_linked_future_found"
    assert ref.symbol == "NOTAREALCOMMODITYROOT"
    assert ref.underlying_expiry is None


def test_expiry_comparison_is_chronological_not_lexicographic():
    early = _parse_dashed_expiry("17-AUG-26")
    late = _parse_dashed_expiry("08-OCT-26")

    # Naive string ordering gets this backwards: "08-OCT-26" < "17-AUG-26"
    # lexicographically, even though October is chronologically later.
    assert "08-OCT-26" < "17-AUG-26"
    assert late > early
