"""Symbol search relevance ranking (M12).

The reported defect: ``NIFTY`` (NSE_INDEX) sat at position 11,224 of 11,334
matches while the service capped at 500, so it could never be found from the
chart search box.

The cause was NOT a missing sort. ``search_symbols`` accumulated matches in
master-contract load order and broke at the cap, so the limit was applied to an
arbitrarily-ordered stream and later candidates were never EXAMINED. Sorting
afterwards could not have fixed it.

That is why the fixtures below deliberately place the target symbol AFTER the
cap in load order. A fixture that put it early would pass against the unfixed
code and prove nothing.

See docs/symbol-search-relevance-ranking.md.
"""

import pytest

from database.token_db_enhanced import BrokerSymbolCache, SymbolData


def _sym(symbol: str, exchange: str = "NFO", name: str = "", token: str = "") -> SymbolData:
    return SymbolData(
        symbol=symbol,
        brsymbol=symbol,
        name=name or symbol,
        exchange=exchange,
        brexchange=exchange,
        token=token or symbol,
    )


def _cache(symbols: list[SymbolData]) -> BrokerSymbolCache:
    """A cache populated in the given ORDER — load order is the whole point."""
    cache = BrokerSymbolCache()
    for i, s in enumerate(symbols):
        cache.symbols[f"{s.exchange}:{s.symbol}:{i}"] = s
        cache.by_exchange.setdefault(s.exchange, []).append(s)
    return cache


def _nifty_universe(option_count: int) -> list[SymbolData]:
    """`option_count` NIFTY option contracts, THEN the NIFTY index last.

    Mirrors the real master contract, where the index loads after the F&O chain.
    """
    options = [
        _sym(f"NIFTY25DEC{24000 + i * 50}CE", exchange="NFO") for i in range(option_count)
    ]
    return [*options, _sym("NIFTY", exchange="NSE_INDEX", name="Nifty 50")]


class TestExactMatchReachability:
    """The reported defect itself."""

    def test_exact_match_beyond_the_cap_is_still_returned_first(self):
        # 1200 decoys > the 500 cap: under the old early-break loop the index
        # was never even examined. This is the assertion the fix exists for.
        cache = _cache(_nifty_universe(1200))

        results = cache.search_symbols("NIFTY", limit=500)

        assert results, "non-vacuity: the query must match something"
        assert results[0].symbol == "NIFTY"
        assert results[0].exchange == "NSE_INDEX"

    def test_the_fixture_really_does_bury_the_target(self):
        """Guards the guard: if the decoys stopped preceding the index, the
        test above would pass without the fix and silently stop protecting."""
        universe = _nifty_universe(1200)

        index_position = next(i for i, s in enumerate(universe) if s.symbol == "NIFTY")

        assert index_position > 500, (
            f"the exact match must sit beyond the cap to exercise the bug, "
            f"but it is at position {index_position}"
        )

    def test_result_count_still_respects_the_limit(self):
        cache = _cache(_nifty_universe(1200))

        assert len(cache.search_symbols("NIFTY", limit=500)) == 500
        assert len(cache.search_symbols("NIFTY", limit=10)) == 10


class TestRankingTiers:
    def test_exact_beats_prefix_beats_substring(self):
        cache = _cache(
            [
                _sym("XNIFTYX", exchange="NFO"),  # substring
                _sym("NIFTYBEES", exchange="NSE"),  # prefix
                _sym("NIFTY", exchange="NSE_INDEX"),  # exact
            ]
        )

        got = [s.symbol for s in cache.search_symbols("NIFTY", limit=10)]

        assert got == ["NIFTY", "NIFTYBEES", "XNIFTYX"]

    def test_a_name_only_match_ranks_below_any_symbol_match(self):
        cache = _cache(
            [
                _sym("RELIND", exchange="NSE", name="RELIANCE INDUSTRIES"),  # name only
                _sym("RELIANCE", exchange="NSE", name="Reliance"),  # exact
            ]
        )

        got = [s.symbol for s in cache.search_symbols("RELIANCE", limit=10)]

        assert got[0] == "RELIANCE"
        assert got[1] == "RELIND"

    def test_shorter_symbol_wins_within_a_tier(self):
        cache = _cache(
            [
                _sym("NIFTYNEXT50", exchange="NSE"),
                _sym("NIFTYBEES", exchange="NSE"),
            ]
        )

        got = [s.symbol for s in cache.search_symbols("NIFTY", limit=10)]

        assert got == ["NIFTYBEES", "NIFTYNEXT50"]

    def test_exchange_breaks_ties_deterministically_not_preferentially(self):
        # Same symbol, same length, same tier: ordered by exchange ascending.
        # Alphabetical is chosen for DETERMINISM — it is deliberately not a
        # statement that one exchange is more relevant than another.
        cache = _cache(
            [
                _sym("INFY", exchange="NSE"),
                _sym("INFY", exchange="BSE"),
            ]
        )

        got = [s.exchange for s in cache.search_symbols("INFY", limit=10)]

        assert got == ["BSE", "NSE"]


class TestPreservedBehaviour:
    def test_multi_term_queries_still_require_every_term(self):
        cache = _cache(
            [
                _sym("NIFTY25DEC24000CE", exchange="NFO"),
                _sym("BANKNIFTY25DEC24000CE", exchange="NFO"),
            ]
        )

        got = [s.symbol for s in cache.search_symbols("BANKNIFTY 24000", limit=10)]

        assert got == ["BANKNIFTY25DEC24000CE"]

    def test_exchange_filter_still_restricts_results(self):
        cache = _cache(
            [
                _sym("NIFTY", exchange="NSE_INDEX"),
                _sym("NIFTY25DEC24000CE", exchange="NFO"),
            ]
        )

        got = cache.search_symbols("NIFTY", exchange="NFO", limit=10)

        assert [s.symbol for s in got] == ["NIFTY25DEC24000CE"]

    def test_empty_query_returns_nothing(self):
        cache = _cache(_nifty_universe(10))

        assert cache.search_symbols("   ", limit=10) == []

    def test_repeated_calls_return_an_identical_order(self):
        cache = _cache(_nifty_universe(700))

        first = [(s.symbol, s.exchange) for s in cache.search_symbols("NIFTY", limit=50)]
        second = [(s.symbol, s.exchange) for s in cache.search_symbols("NIFTY", limit=50)]

        assert first == second
