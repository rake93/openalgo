# Symbol search relevance ranking

**Status:** approved 2026-07-31 · **Register entry:** M12 (engine `docs/openscript-pending-deliverables.md`)

---

## 1. The defect

`NIFTY` — the NSE_INDEX instrument — cannot be found from the chart search box. It sits at position
**11,224 of 11,334** matches while the service caps at 500. Live-reproduced 2026-07-31.

## 2. The actual cause is the `break`, not the missing sort

`BrokerSymbolCache.search_symbols` (`database/token_db_enhanced.py`) ends its match loop with:

```python
if all_match:
    matches.append(symbol_data)
    if len(matches) >= limit:
        break
```

Matches accumulate in **master-contract load order** and the loop **stops at the cap**. The 500 limit
is therefore applied to an arbitrarily-ordered stream: the first 500 encountered win, and everything
after is never *examined*.

**Sorting the results afterwards could not fix this** — the candidate is never reached. Any fix must
score before truncating. This is the whole bug; "no relevance ranking" is the symptom.

There is no ranking anywhere to build on: `database/tv_search.py` is exact-match only, and there are
no search tests.

## 3. Fix

Score every match during the **existing single pass** and keep a bounded heap of the best `limit`,
then sort the survivors exactly. The scan is the same `O(n)` walk the code already performs for any
query matching fewer than `limit` symbols; only broad queries do more work than before.

### 3.1 Scoring — purely structural

| Tier | Match against the symbol |
|---|---|
| **4** | exactly equals the query |
| **3** | starts with the query |
| **2** | contains the query |
| **1** | matched only via `name` / `brsymbol` / `token` / strike |

Within a tier: **shorter symbol first** (`NIFTY` before `NIFTY25DEC24000CE`), then **exchange
ascending**, then **symbol ascending**, giving a total and stable order.

No hand-maintained list of favoured instruments — such a list drifts out of date, and structural
tiers put exact matches first for *every* query rather than only the reported one.

**The exchange tie-break is alphabetical because it is DETERMINISTIC, and is explicitly NOT a
preference ranking.** Ranking NSE_INDEX above NFO would encode a product judgement in a data-layer
sort that consumers of the public API may not share — an options trader searching `NIFTY` may
legitimately want contracts first.

### 3.2 The heap key, and why it is not the sort key

A min-heap evicts the smallest, so every component of its key must mean "smaller = worse". `tier` and
`-len(symbol)` satisfy that; **alphabetical exchange does not**, because a string cannot be negated.
So the two concerns are separated:

- **eviction key** — `(tier, -len(symbol), insertion_counter)`. Fully deterministic; among exact ties
  at the cap boundary, first-encountered wins.
- **final order** — the ≤ `limit` survivors are sorted with the complete key including exchange and
  symbol.

The only imprecision is *which* of several equally-ranked items survives at the last position. It is
deterministic, and invisible in a UI showing 30 of 500.

## 4. Preserved exactly

Multi-term AND logic, the `by_exchange` index fast path, numeric strike matching, and the
`brsymbol` / `token` match sources. Multi-term queries mostly land in tier 1 and are ordered by the
tie-breaks — acceptable, and still better than today's arbitrary order.

## 5. Blast radius

`/api/v1/search` is a public namespace (`restx_api/__init__.py`). Three consumers:

| Consumer | Limit |
|---|---|
| `services/search_service.py` → public REST API + chart search box | 500 |
| `database/token_db_enhanced.py` module wrapper | 10 000 |
| `test/test_cache_performance.py` | 10 |

Result **order** changes. This is documented API behaviour, but the current order is arbitrary load
order, so the change is strictly an improvement rather than a contract break. Worth a changelog note,
not a version bump.

## 6. Testing

No search tests exist; this starts the file.

**The load-bearing requirement is non-vacuity.** The fixture must place the exact match **beyond the
cap in load order** — otherwise the test passes against the *unfixed* code and proves nothing. That
is exactly the NIFTY-at-11,224 shape.

Also covered: tier ordering, the shorter-symbol tie-break, determinism across repeated calls, and
that multi-term AND logic, the exchange filter and strike matching still behave.

## 7. Risks

| Risk | Handling |
|---|---|
| Broad queries now scan the full universe | bounded heap, no full sort; measure before adding an index |
| A test that passes without the fix | fixture places the target beyond the cap (§6) |
| Ordering read as a preference ranking | §3.1 states the exchange tie-break is deterministic, not preferential |
