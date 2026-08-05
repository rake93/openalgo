# GEX Snapshot Recorder (phase 3) Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. Execution is
> **inline, single reviewer** (user's choice) — no subagent fleet, no multi-agent review round.
> At execution start, copy this file to `docs/superpowers/plans/2026-08-05-gex-snapshot-recorder.md`
> so it lives with the other plans.

**Goal:** Record a GEX snapshot per watchlisted series per minute into a new isolated
`gex.db`, so Gamma Bands (phase 4) and the GEX Heatmap (phase 5) have history to draw,
and so N open chart tabs cost one broker poll instead of N.

**Architecture:** One compute pipeline, two callers. `gex_levels_service` grows an
IO/compute seam (`fetch_snapshot_inputs` → `build_snapshot`); the live endpoint calls it
once for the requested weighting, the recorder calls it twice (OI and volume) off a single
chain fetch and a single IV solve. Persistence is `database/gex_history_db.py` (seventh
isolated database, `engine_factory` → `NullPool`). Scheduling is a module-level
`ResilientBackgroundScheduler` singleton on a **memory** jobstore.

**Tech Stack:** Flask blueprint, SQLAlchemy ORM, APScheduler, pytest. Python 3.12 via `uv`.

---

## Context

The GEX Levels study draws from one live snapshot and throws away the previous one, so
"when did the Call Wall move?" is unanswerable and every open tab fetches its own chain.
Phases 1–2 shipped Gamma Profile and Delta Exposure with no recorder at all, as the phasing
intended. Phases 4 and 5 cannot start without one.

The design is settled — spec
[`docs/superpowers/specs/2026-08-05-gex-advanced-visualisations-design.md`](../../../apps/foss/rock-edge/openalgo/docs/superpowers/specs/2026-08-05-gex-advanced-visualisations-design.md)
§§4–9, six decisions with rejected alternatives recorded. **Nothing here re-decides them.**
Three points below are detail-level readings of the spec rather than changes, and each is
flagged where it appears:

| Point | Reading taken | Why |
| --- | --- | --- |
| `quality_*` columns | Suffixed per weighting, and stored as the **whole** quality dict (JSON) plus a queryable `quality_verdict_*` string | `assess_quality` takes the priced exposures, so quality genuinely differs by weighting. And `may_draw` is a `@property`: absent it reads as `undefined` → falsy in TS → "do not draw" for every good snapshot. That trap is already documented at `services/gex_levels_service.py:316`; storing verdict+notes only would walk straight back into it. |
| Watchlist cap | `MAX_SERIES = 10` | Not in the spec, but the design rejected auto-follow *for unbounded growth*, and §8 records a live `Rate limit hit (805)` from a single manual call. Ten series is 940 chain symbols a minute. |
| Session guard | Reuse `services/option_target_sessions.build_session_provider` | §8 says validate the calendar window before trusting it; that module already does exactly that validation (rejects the corrupt seeded MCX windows) and falls back to a static per-exchange table. Writing a second validator is the duplication the spec's own "one pipeline" rule argues against. |

**Free from phase 2, and the plan consumes it rather than rebuilding it:**
`weighted_legs(rows, ivs, weight_by)` already lives in `services/gex_levels/exposure.py`,
and `gex_levels_service` already resolves IVs once, builds legs once and prices GEX and DEX
from the same list object. Task 1 is therefore a lift-and-shift of an already-correct
pipeline into a named seam, not a rewrite.

**Deliberately untouched:** `exposure.py`, `delta_exposure.py`, `levels.py`, `quality.py`,
`sentiment.py`, `blackscholes.py`, `expiry.py`. Their tests staying green is the regression
guard for the whole change. In particular the known follow-up — `scan_zero_gamma`
re-resolving IVs the caller already has — **stays a follow-up**; folding it in changes
`levels.py`'s signature and forfeits the guard.

**Out of scope for phase 3** (spec §10 puts them in phases 4–5): `gex_history_service.py`,
`POST /gex/api/gex-history`, grid downsampling, and any frontend renderer. Also out: a
watchlist UI — the routes are the phase 3 surface (user's choice); a control lands with
Bands, when there is something visible to switch on.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `services/gex_levels_service.py` (modify) | Gains `SnapshotInputs`, `prepare_snapshot`, `build_snapshot`, `fetch_snapshot_inputs`, and two typed errors. `get_gex_levels` becomes a thin wrapper over them. |
| `database/gex_history_db.py` (create) | `gex.db`: three models, init, watchlist CRUD, snapshot write, latest/range read, prune. Only place that knows the schema. |
| `services/option_target_sessions.py` (modify) | Gains public `session_is_open(exchange, moment, default=...)`, lifted from `option_target_service._market_is_open`. |
| `services/option_target_service.py` (modify) | Calls the shared helper; loses its private copy. |
| `services/gex_recorder_service.py` (create) | Scheduler singleton, per-series record job, expiry-roll resolution, daily prune job. The only new thread owner. |
| `blueprints/gex.py` (modify) | `/gex/api/gex-series` GET/POST/DELETE/PATCH; recorded fast path on `/gex/api/gex-levels`. |
| `app.py` (modify) | Init the DB, start the recorder. |
| `utils/db_sessions.py` (modify) | Register `gex_history_db.db_session` so teardown and background threads release it. |
| `.sample.env` (modify) | `GEX_DATABASE_URL` and the two recorder knobs. |

Tests, all under `test/`: `test_gex_levels_service.py` (extended), `test_gex_history_db.py`,
`test_gex_recorder_service.py`, `test_gex_series_endpoint.py`, `test_gex_levels_endpoint.py`
(extended).

**Environment traps that will each cost an hour** (from the handoff §6):
- `uv run pytest` fails on this machine. Use **`uv run python -m pytest`**.
- Pre-existing collection errors in `test/sandbox/`, `test_bot_web.py`,
  `test_telegram_startup.py` are unrelated — always scope to the files you care about.
- Restart the Flask server after any backend change. Nothing hot-reloads.
- Ruff only: `uv run ruff check <files> --fix && uv run ruff format <files>`. No `npm` in
  this phase at all.

---

### Task 1: The `build_snapshot` seam

Split `get_gex_levels` into its IO half and its compute half so the recorder and the live
path run one pipeline. Behaviour must not change: the existing endpoint payload is
byte-identical afterwards.

**Files:**
- Modify: `services/gex_levels_service.py`
- Test: `test/test_gex_levels_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_gex_levels_service.py`:

```python
def test_the_recorder_seam_reproduces_the_live_payload_exactly():
    """The failure this whole design exists to prevent: a recorder that
    reimplements the maths and drifts. /gex drifted from the study exactly that
    way and shipped three defects. If these two ever differ, one of them is
    computing something the other is not."""
    from services.gex_levels_service import build_snapshot, fetch_snapshot_inputs

    chain, forward = _patched()
    with chain, forward:
        _, live_payload, _ = get_gex_levels("NIFTY", "NFO", EXPIRY, "key", weight_by="oi")

    chain, forward = _patched()
    with chain, forward:
        from opengreeks import black76

        inputs = fetch_snapshot_inputs("NIFTY", "NFO", EXPIRY, "key")
        seam_payload = build_snapshot(black76, inputs, "oi")

    # `source` and `as_of` are provenance, stamped by each wrapper rather than
    # by the compute core - the recorder's rows are not "live". Everything the
    # study actually draws from must match exactly.
    assert seam_payload == {k: v for k, v in live_payload.items() if k not in ("source", "as_of")}
    assert live_payload["source"] == "live"


def test_one_fetch_serves_both_weightings_with_one_iv_solve():
    """The recorder writes OI and volume columns from a single tick. resolve_ivs
    is weighting-independent and is the expensive half - two solver calls per
    strike - so it must be paid once, not twice."""
    from services.gex_levels import exposure
    from services.gex_levels_service import build_snapshot, fetch_snapshot_inputs

    chain, forward = _patched()
    with (
        chain,
        forward,
        patch(
            "services.gex_levels_service.resolve_ivs", wraps=exposure.resolve_ivs
        ) as solve,
    ):
        from opengreeks import black76

        inputs = fetch_snapshot_inputs("NIFTY", "NFO", EXPIRY, "key")
        oi = build_snapshot(black76, inputs, "oi")
        vol = build_snapshot(black76, inputs, "volume")

    assert solve.call_count == 1
    assert [s["strike"] for s in oi["strikes"]] == [s["strike"] for s in vol["strikes"]]
    assert oi["weight_by"] == "oi"
    assert vol["weight_by"] == "volume"


def test_an_unusable_chain_raises_a_typed_error_not_a_bare_valueerror():
    """The wrapper maps this to 404 and the recorder maps it to 'skip this tick'.
    A bare ValueError would be caught by the wrapper's broad except and reported
    as a 500 - an operator would go looking for a crash that never happened."""
    from services.gex_levels_service import UnusableChain, fetch_snapshot_inputs

    empty = {"status": "success", "chain": [], "atm_strike": None, "underlying_ltp": 0}
    with (
        patch(
            "services.gex_levels_service.get_option_chain", return_value=(True, empty, 200)
        ),
        patch("services.gex_levels_service._resolve_forward_price", return_value=24610.0),
    ):
        with pytest.raises(UnusableChain):
            fetch_snapshot_inputs("NIFTY", "NFO", EXPIRY, "key")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest test/test_gex_levels_service.py -v`
Expected: three FAILs with `ImportError: cannot import name 'build_snapshot'`.

- [ ] **Step 3: Implement the seam**

In `services/gex_levels_service.py`, add above `get_gex_levels`:

```python
class UnusableChain(ValueError):
    """The chain came back but carries no spot price or no usable strikes.

    Typed rather than bare so the two callers can tell it apart from a genuine
    crash: the endpoint maps it to 404, the recorder logs it and skips the tick.
    A bare ValueError would fall into the wrapper's broad except and be reported
    as a 500.
    """


class ChainFetchFailed(RuntimeError):
    """`get_option_chain` returned failure. Carries the broker's own response
    and status so the endpoint can pass them through unaltered."""

    def __init__(self, response: dict[str, Any], status_code: int):
        super().__init__(response.get("message", "Option chain fetch failed"))
        self.response = response
        self.status_code = status_code


@dataclass(frozen=True)
class SnapshotInputs:
    """Everything one chain fetch yields that does NOT depend on the weighting.

    This is the seam the recorder exists on top of. `resolve_ivs` does not take
    `weight_by` - it inverts at the real forward and is weighting-independent -
    so a caller that needs both weightings pays for the chain fetch, the forward
    resolution and the IV solve exactly once and then calls `build_snapshot`
    twice. See the design's "Both weightings cost one IV solve".
    """

    underlying: str
    exchange: str
    expiry_date: str
    rows: list[ChainRow]
    ivs: ResolvedIVs
    spot_price: float
    forward: float
    atm_strike: float | None
    t_years: float
    dte_days: float
    interest_rate: float
    lot_size: int
```

`prepare_snapshot(black76, chain_response, *, underlying, exchange, expiry_date, expiry_dt,
forward, interest_rate) -> SnapshotInputs` — lift lines 119–164 of the current
`get_gex_levels` verbatim: pull `chain`/`underlying_ltp`/`atm_strike`, raise `UnusableChain`
instead of returning the 404 tuple, `calculate_time_to_expiry(expiry_dt)`, `F = forward or
spot_price`, `_build_chain_rows`, `resolve_ivs`. No IO in this function.

`fetch_snapshot_inputs(underlying, exchange, expiry_date, api_key, interest_rate=None) ->
SnapshotInputs` — the IO half: `get_option_chain(strike_count=STRIKE_COUNT)` (raise
`ChainFetchFailed` on failure), `expiry_datetime`, the `DEFAULT_INTEREST_RATES` default,
`_resolve_forward_price`, then `prepare_snapshot`.

`build_snapshot(black76, inputs, weight_by) -> dict[str, Any]` — lift lines 166–265
verbatim: `weighted_legs` → `price_exposures` + `price_delta_exposures` → `find_walls` →
`scan_zero_gamma` → `assess_quality` → `read_sentiment` → the payload dict. Keep every
existing comment; they carry the reasoning (forward-not-spot, `strict=True` on the zip,
sentiment not derived from the net_gex sign).

`get_gex_levels` then becomes:

```python
    if weight_by not in ("oi", "volume"):
        return False, {"status": "error", "message": f"weight_by must be 'oi' or 'volume', got {weight_by!r}"}, 400
    try:
        try:
            from opengreeks import black76
        except ImportError:
            logger.error("opengreeks library not installed.")
            return False, {"status": "error", "message": "GEX Levels requires the opengreeks library. Install with: pip install opengreeks"}, 500

        inputs = fetch_snapshot_inputs(underlying, exchange, expiry_date, api_key, interest_rate)
        payload = build_snapshot(black76, inputs, weight_by)
        payload["source"] = "live"
        payload["as_of"] = int(time.time())
        return True, payload, 200
    except ChainFetchFailed as exc:
        return False, exc.response, exc.status_code
    except UnusableChain as exc:
        return False, {"status": "error", "message": str(exc)}, 404
    except Exception:
        logger.exception("Error in get_gex_levels")
        return False, {"status": "error", "message": "Error computing GEX levels"}, 500
```

Note `source`/`as_of` are set by the wrapper, not by `build_snapshot`: the recorder's rows
are not "live", so provenance belongs to whoever served the payload, not to the compute
core. The drift test in Step 1 already accounts for this.

Two imports to add at the top of the file: `time` (for `as_of`) and `dataclass` alongside
the existing `asdict`.

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest test/test_gex_levels_service.py test/test_gex_levels_endpoint.py -v`
Expected: all PASS.

Then the regression guard:
Run: `uv run python -m pytest test/test_gex_levels_math.py test/test_gex_levels_walls.py test/test_gex_levels_zero_gamma.py test/test_gex_levels_quality.py test/test_gex_levels_sentiment.py test/test_gex_levels_exposure.py test/test_gex_levels_delta.py -v`
Expected: all PASS, unchanged count.

- [ ] **Step 5: Commit**

```bash
git add services/gex_levels_service.py test/test_gex_levels_service.py
git commit -m "refactor(gex-levels): split the compute core out of the IO wrapper"
```

---

### Task 2: `gex.db` schema, init and watchlist CRUD

**Files:**
- Create: `database/gex_history_db.py`
- Test: `test/test_gex_history_db.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_gex_history_db.py`. Bind to a temp file exactly as
`test/test_indicator_script_endpoints.py:27-44,86-115` does — `tempfile.mkstemp`, set
`GEX_DATABASE_URL`, import, restore the env var, then a fixture that rebinds `db_session`,
runs `create_all`, and **disposes the engine and unlinks the file at teardown** (a leaked
engine holds its SQLite descriptor for the life of the process).

```python
def test_a_series_round_trips(gexdb):
    ok, _, row = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    assert ok is True
    assert row["underlying"] == "NIFTY" and row["enabled"] is True
    assert [s["id"] for s in gex_history_db.list_series()] == [row["id"]]


def test_the_same_series_cannot_be_added_twice(gexdb):
    gex_history_db.add_series("NIFTY", "NFO", "nearest")
    ok, msg, row = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    assert ok is False and row is None and "already" in msg.lower()


def test_the_same_underlying_may_be_watched_on_two_expiry_rules(gexdb):
    """A pinned contract and the rolling nearest are different series, not a
    duplicate - watching both is how you keep history across a roll."""
    assert gex_history_db.add_series("NIFTY", "NFO", "nearest")[0] is True
    assert gex_history_db.add_series("NIFTY", "NFO", "11AUG26")[0] is True


def test_disabling_a_series_keeps_it_and_its_history(gexdb):
    _, _, row = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    gex_history_db.set_series_enabled(row["id"], False)
    assert gex_history_db.list_series(enabled_only=True) == []
    assert len(gex_history_db.list_series()) == 1


def test_the_watchlist_ships_empty(gexdb):
    """An upgrade must not silently start making broker calls on a schedule
    nobody asked for."""
    assert gex_history_db.list_series() == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest test/test_gex_history_db.py -v`
Expected: FAIL, `ModuleNotFoundError: database.gex_history_db`.

- [ ] **Step 3: Implement the module header, models and CRUD**

Follow `database/scalping_db.py:1-38` for the engine/session preamble, with the URL
from the environment as `database/sandbox_db.py:37` does:

```python
GEX_DATABASE_URL = os.getenv("GEX_DATABASE_URL", "sqlite:///db/gex.db")
engine = create_db_engine(GEX_DATABASE_URL)
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

# One minute of ticks is one row per series. The unique constraint is what makes
# a coalesced double-fire a no-op rather than a duplicate column on the heatmap.
```

Models:

```python
class GexSeries(Base):
    """One watchlisted (underlying, exchange, expiry rule) the recorder polls."""

    __tablename__ = "gex_series"
    __table_args__ = (
        UniqueConstraint("underlying", "exchange", "expiry_rule", name="uq_gex_series"),
    )

    id = Column(Integer, primary_key=True)
    underlying = Column(String(20), nullable=False)
    exchange = Column(String(20), nullable=False)
    # "nearest" (resolved per tick, rolls weekly) or a pinned DDMMMYY.
    expiry_rule = Column(String(10), nullable=False, default="nearest")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GexSnapshot(Base):
    """One series, one minute. Both weightings, side by side."""

    __tablename__ = "gex_snapshot"
    # UniqueConstraint alone: SQLite backs it with a unique index on
    # (series_id, ts), which IS the range scan Bands and the Heatmap live on.
    # A second Index over the same columns, or index=True on series_id, would
    # be dead weight written on every insert.
    __table_args__ = (UniqueConstraint("series_id", "ts", name="uq_gex_snapshot_series_ts"),)

    id = Column(Integer, primary_key=True)
    series_id = Column(Integer, nullable=False)
    # Epoch SECONDS, floored to the cadence. Directly usable by the chart
    # library and immune to timezone drift; India has no DST.
    ts = Column(Integer, nullable=False)
    # The RESOLVED expiry, never the rule. On "nearest" the contract rolls
    # weekly, so 30 days of history is four or five different books: walls jump
    # at each roll because the book changed, not because the market moved. A
    # reader either filters to one contract or marks the boundary, and cannot
    # do either without this column.
    expiry_date = Column(String(10), nullable=False)

    spot_price = Column(Float, nullable=False)
    forward_price = Column(Float, nullable=False)
    atm_strike = Column(Float, nullable=True)
    dte_days = Column(Float, nullable=False)
    interest_rate = Column(Float, nullable=False)
    lot_size = Column(Integer, nullable=False, default=1)
    strikes_used = Column(Integer, nullable=False, default=0)

    # Per-weighting results as suffixed columns rather than a `weighting`
    # discriminator row: WeightBy is a closed set of exactly two values, so this
    # halves the strike table and lets the Bands query be a plain select with no
    # pivot. A third weighting later is a migration - accepted, and recorded.
    call_wall_oi = Column(Float, nullable=True)
    call_wall_vol = Column(Float, nullable=True)
    put_wall_oi = Column(Float, nullable=True)
    put_wall_vol = Column(Float, nullable=True)
    # None is a normal outcome, not missing data: a chain can be long or short
    # gamma across its whole plausible range.
    zero_gamma_oi = Column(Float, nullable=True)
    zero_gamma_vol = Column(Float, nullable=True)
    net_gex_oi = Column(Float, nullable=False, default=0.0)
    net_gex_vol = Column(Float, nullable=False, default=0.0)
    regime_oi = Column(String(12), nullable=True)
    regime_vol = Column(String(12), nullable=True)
    sentiment_oi = Column(JSON, nullable=True)
    sentiment_vol = Column(JSON, nullable=True)
    # Two columns per weighting on purpose. The string is what the Heatmap
    # filters on to dim or hatch a degraded column - it must not require
    # parsing JSON per column. The JSON is the WHOLE quality payload, including
    # `may_draw`: that is a @property, not a dataclass field, and an absent key
    # reads as undefined -> falsy in TypeScript, which would render every good
    # recorded snapshot as "do not draw". See _quality_payload in
    # services/gex_levels_service.py for the same trap on the live path.
    quality_verdict_oi = Column(String(12), nullable=True)
    quality_verdict_vol = Column(String(12), nullable=True)
    quality_oi = Column(JSON, nullable=True)
    quality_vol = Column(JSON, nullable=True)


class GexSnapshotStrike(Base):
    """The per-strike profile, both metrics, both weightings, plus raw inputs."""

    __tablename__ = "gex_snapshot_strike"

    snapshot_id = Column(Integer, primary_key=True)
    strike = Column(Float, primary_key=True)

    call_gex_oi = Column(Float, nullable=False, default=0.0)
    put_gex_oi = Column(Float, nullable=False, default=0.0)
    net_gex_oi = Column(Float, nullable=False, default=0.0)
    call_gex_vol = Column(Float, nullable=False, default=0.0)
    put_gex_vol = Column(Float, nullable=False, default=0.0)
    net_gex_vol = Column(Float, nullable=False, default=0.0)
    call_dex_oi = Column(Float, nullable=False, default=0.0)
    put_dex_oi = Column(Float, nullable=False, default=0.0)
    net_dex_oi = Column(Float, nullable=False, default=0.0)
    call_dex_vol = Column(Float, nullable=False, default=0.0)
    put_dex_vol = Column(Float, nullable=False, default=0.0)
    net_dex_vol = Column(Float, nullable=False, default=0.0)

    # The raw inputs, kept deliberately against normalisation. A lot-size units
    # bug in this exact pipeline survived 99 green tests and was caught only by
    # a live broker call. With these, a maths error means history can be
    # REPAIRED; without them it must be DISCARDED. Worth ~25% more disk.
    call_oi = Column(Float, nullable=False, default=0.0)
    put_oi = Column(Float, nullable=False, default=0.0)
    call_volume = Column(Float, nullable=False, default=0.0)
    put_volume = Column(Float, nullable=False, default=0.0)
```

Then `init_gex_history_db()` (via `database.db_init_helper.init_db_with_logging`, exactly as
`latency_db.init_latency_db` does — named for the database rather than a bare `init_db` so
the call site in `app.py` reads unambiguously), `_series_dict(row)`, `list_series(enabled_only=False)`,
`get_series(series_id)`, `add_series(underlying, exchange, expiry_rule="nearest")` →
`(ok, message, dict | None)`, `set_series_enabled(series_id, enabled)`, and
`remove_series(series_id)` which deletes the series **and** its snapshots and strike rows
(see Task 3's prune for why the children are deleted explicitly). Every function:
`try/except` with `logger.exception` + `db_session.rollback()`, matching
`latency_db.OrderLatency.log_latency`.

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest test/test_gex_history_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database/gex_history_db.py test/test_gex_history_db.py
git commit -m "feat(gex-history): add the gex.db schema and the recorder watchlist"
```

---

### Task 3: Snapshot write, latest/range read, retention prune

**Files:**
- Modify: `database/gex_history_db.py`
- Test: `test/test_gex_history_db.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_snapshot_and_its_strikes_round_trip(gexdb):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    sid = gex_history_db.write_snapshot(series["id"], _snapshot(ts=1_754_000_000), _strikes())
    assert sid is not None

    latest = gex_history_db.get_latest_snapshot("NIFTY", "NFO", "11AUG26")
    assert latest["ts"] == 1_754_000_000
    assert latest["call_wall_oi"] == 24800.0
    assert latest["quality_oi"]["may_draw"] is True
    assert len(latest["strikes"]) == 2
    assert latest["strikes"][0]["call_oi"] == 100000.0


def test_a_second_write_in_the_same_minute_is_dropped_not_duplicated(gexdb):
    """coalesce plus a retry can fire the same minute twice. The unique
    constraint is what makes that a no-op instead of two heatmap columns at the
    same timestamp."""
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    first = gex_history_db.write_snapshot(series["id"], _snapshot(ts=1_754_000_000), _strikes())
    second = gex_history_db.write_snapshot(series["id"], _snapshot(ts=1_754_000_000), _strikes())
    assert first is not None
    assert second is None
    assert len(gex_history_db.get_snapshots_in_range(series["id"], 0, 2_000_000_000)) == 1


def test_the_range_query_is_inclusive_at_both_ends(gexdb):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    for ts in (100, 160, 220):
        gex_history_db.write_snapshot(series["id"], _snapshot(ts=ts), _strikes())
    got = [s["ts"] for s in gex_history_db.get_snapshots_in_range(series["id"], 100, 220)]
    assert got == [100, 160, 220]


def test_a_gap_stays_a_gap(gexdb):
    """A failed tick has no row. The reader must see the hole, not an
    interpolated value - flat gamma where there was NO READING is the same error
    quality.py and direction.ts already forbid."""
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    gex_history_db.write_snapshot(series["id"], _snapshot(ts=100), _strikes())
    gex_history_db.write_snapshot(series["id"], _snapshot(ts=220), _strikes())
    assert [s["ts"] for s in gex_history_db.get_snapshots_in_range(series["id"], 0, 400)] == [100, 220]


def test_the_prune_deletes_strike_children_explicitly(gexdb):
    """SQLite does not enforce foreign keys unless PRAGMA foreign_keys=ON is set
    PER CONNECTION, and NullPool hands out a fresh connection every operation -
    so that pragma cannot be assumed armed and a cascade cannot be relied on.
    Orphaned strike rows are silent disk growth."""
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    old = int(time.time()) - 40 * 86400
    new = int(time.time())
    gex_history_db.write_snapshot(series["id"], _snapshot(ts=old), _strikes())
    gex_history_db.write_snapshot(series["id"], _snapshot(ts=new), _strikes())

    result = gex_history_db.prune_snapshots(retention_days=30)

    assert result["snapshots_deleted"] == 1
    assert result["strikes_deleted"] == 2
    assert result["snapshots_remaining"] == 1
    assert gex_history_db.db_session.query(gex_history_db.GexSnapshotStrike).count() == 2


def test_removing_a_series_takes_its_history_with_it(gexdb):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "nearest")
    gex_history_db.write_snapshot(series["id"], _snapshot(ts=100), _strikes())
    gex_history_db.remove_series(series["id"])
    assert gex_history_db.db_session.query(gex_history_db.GexSnapshot).count() == 0
    assert gex_history_db.db_session.query(gex_history_db.GexSnapshotStrike).count() == 0
```

`_snapshot(ts)` and `_strikes()` are module-level helpers in the test file returning the
plain dicts `write_snapshot` takes — two strikes, both weightings populated, a
`quality_oi` dict carrying `may_draw: True`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest test/test_gex_history_db.py -v`
Expected: the six new tests FAIL with `AttributeError: module has no attribute 'write_snapshot'`.

- [ ] **Step 3: Implement**

- `write_snapshot(series_id, snapshot: dict, strikes: list[dict]) -> int | None` — one
  transaction: insert `GexSnapshot`, `flush()` for the id, `bulk_insert_mappings` the
  strike rows, commit. On `IntegrityError` (the `(series_id, ts)` unique) rollback and
  return `None`, logging at **debug** — a coalesced double-fire is expected, not an error,
  and logging it at warning would fill `errors.jsonl` on every retry.
- `get_latest_snapshot(underlying, exchange, expiry_date) -> dict | None` — join
  `GexSeries` on `(underlying, exchange)` case-insensitively, filter
  `GexSnapshot.expiry_date == expiry_date`, order by `ts` desc, limit 1; then fetch its
  strike rows ordered by strike. Note this filters on the **resolved** expiry, so a
  `nearest` series and a pinned one both serve the same fast-path lookup.
- `get_snapshots_in_range(series_id, from_ts, to_ts) -> list[dict]` — inclusive both ends
  (`>=` / `<=`), ordered by `ts` ascending, snapshot rows only (no strikes: Bands does not
  need them and a month of strike rows is the whole point of the separate grid endpoint).
- `prune_snapshots(retention_days) -> dict` — compute the cutoff `ts`, select the doomed
  snapshot ids, delete their strike rows **first and explicitly**, then the snapshots,
  commit, and return `{"snapshots_deleted", "strikes_deleted", "snapshots_remaining"}`.
  Batch the id list into chunks of 500 for the `IN` clause; SQLite's default variable limit
  is 999 and 30 days of two series is ~22,500 ids.

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest test/test_gex_history_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add database/gex_history_db.py test/test_gex_history_db.py
git commit -m "feat(gex-history): store snapshots and prune them on a retention window"
```

---

### Task 4: One validated session guard, shared

`services/option_target_service._market_is_open` is already the guard spec §8 describes —
it goes through `build_session_provider`, which rejects the corrupt seeded MCX windows
(two calendar dates, 895 minutes) and falls back to the static per-exchange table. Lift it
so the recorder uses that one, rather than a second validator that will drift.

**Files:**
- Modify: `services/option_target_sessions.py`
- Modify: `services/option_target_service.py:257-274`
- Test: `test/test_option_target_sessions.py` (create if absent; otherwise extend)

- [ ] **Step 1: Write the failing test**

```python
def test_the_session_guard_default_differs_by_caller_on_a_hard_failure():
    """The two callers want opposite failure behaviour and the difference is
    deliberate. A price projection must never be blocked by a calendar lookup,
    so it fails OPEN. A recorder that fails open makes a broker call a minute
    around the clock, so it fails CLOSED. The realistic failure - a suspect
    window - is handled by the static-table fallback inside the provider and
    reaches neither default."""
    from services.option_target_sessions import session_is_open

    moment = datetime(2026, 8, 5, 11, 0, tzinfo=IST)
    with patch(
        "services.option_target_sessions.build_session_provider",
        side_effect=RuntimeError("calendar exploded"),
    ):
        assert session_is_open("NFO", moment) is True
        assert session_is_open("NFO", moment, default=False) is False


def test_the_session_guard_reports_closed_outside_the_window():
    from services.option_target_sessions import session_is_open

    with patch(
        "services.option_target_sessions.build_session_provider",
        return_value=lambda _day: ((9, 15), (15, 30)),
    ):
        assert session_is_open("NFO", datetime(2026, 8, 5, 11, 0, tzinfo=IST)) is True
        assert session_is_open("NFO", datetime(2026, 8, 5, 16, 0, tzinfo=IST)) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python -m pytest test/test_option_target_sessions.py -v`
Expected: FAIL, `ImportError: cannot import name 'session_is_open'`.

- [ ] **Step 3: Implement**

Move the body of `option_target_service._market_is_open` into
`services/option_target_sessions.py` as:

```python
def session_is_open(exchange: str, moment: datetime, *, default: bool = True) -> bool:
    """Whether `moment` falls inside `exchange`'s session for that date.

    Goes through `build_session_provider`, so a suspect calendar window - the
    seeded MCX special sessions decode to 895-minute windows spanning two dates -
    is rejected and the static per-exchange table is used instead. That is the
    normal failure path and it never reaches `default`.

    Args:
        exchange: Exchange code.
        moment: The instant to test. Must be IST-aware.
        default: What to return if the lookup RAISES outright. The Option Target
            Calculator passes True - a calendar error must never block a price
            projection. The GEX recorder passes False - failing open there means
            a broker call every minute around the clock.
    """
```

Then in `option_target_service`, delete `_market_is_open` and call
`session_is_open(exchange, moment)` at its call sites. **Check what
`test/test_option_target_service.py:498` patches** — if it patches the private name, repoint
it at `services.option_target_service.session_is_open`.

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest test/test_option_target_sessions.py test/test_option_target_service.py -v`
Expected: all PASS, existing count unchanged.

- [ ] **Step 5: Commit**

```bash
git add services/option_target_sessions.py services/option_target_service.py test/test_option_target_sessions.py test/test_option_target_service.py
git commit -m "refactor(sessions): share the validated market-session guard"
```

---

### Task 5: The recorder

**Files:**
- Create: `services/gex_recorder_service.py`
- Test: `test/test_gex_recorder_service.py`

- [ ] **Step 1: Write the failing tests**

Patch at the real IO boundaries so the whole pipeline runs: the chain fetch and forward
resolution inside `gex_levels_service` (exactly as `test_gex_levels_service._patched` does),
plus `get_first_available_api_key`, `session_is_open` and `get_expiry_dates` inside the
recorder module.

```python
def test_one_tick_writes_one_snapshot_with_both_weightings_populated(gexdb, recording):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "11AUG26")

    gex_recorder_service.record_series_once(series["id"])

    rows = gex_history_db.get_snapshots_in_range(series["id"], 0, 2_000_000_000)
    assert len(rows) == 1
    snap = rows[0]
    for column in ("call_wall_oi", "call_wall_vol", "net_gex_oi", "net_gex_vol",
                   "regime_oi", "regime_vol", "quality_verdict_oi", "quality_verdict_vol"):
        assert snap[column] is not None, column
    assert snap["quality_oi"]["may_draw"] in (True, False)


def test_every_strike_row_carries_both_metrics_both_weightings_and_the_raw_inputs(gexdb, recording):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "11AUG26")
    gex_recorder_service.record_series_once(series["id"])

    latest = gex_history_db.get_latest_snapshot("NIFTY", "NFO", "11AUG26")
    assert len(latest["strikes"]) == 5
    row = latest["strikes"][0]
    assert row["net_gex_oi"] != row["net_gex_vol"]      # weighting actually applied
    assert row["net_dex_oi"] != 0.0
    assert row["call_oi"] == 100000.0 and row["call_volume"] == 5000.0


def test_a_failed_fetch_writes_nothing_and_does_not_raise(gexdb, recording):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "11AUG26")
    with patch(
        "services.gex_levels_service.get_option_chain",
        return_value=(False, {"status": "error", "message": "broker down"}, 502),
    ):
        gex_recorder_service.record_series_once(series["id"])   # must not raise

    assert gex_history_db.get_snapshots_in_range(series["id"], 0, 2_000_000_000) == []


def test_a_closed_market_does_not_reach_the_broker(gexdb, recording):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "11AUG26")
    with (
        patch("services.gex_recorder_service.session_is_open", return_value=False),
        patch("services.gex_levels_service.get_option_chain") as fetch,
    ):
        gex_recorder_service.record_series_once(series["id"])

    fetch.assert_not_called()


def test_a_nearest_series_records_the_RESOLVED_expiry_and_follows_the_roll(gexdb, recording):
    """Walls jump at a roll because the book changed, not because the market
    moved. A reader cannot tell the two apart without the resolved contract on
    every row."""
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "nearest")

    with patch(
        "services.gex_recorder_service.get_expiry_dates",
        return_value=(True, {"data": ["11-AUG-26", "18-AUG-26"]}, 200),
    ):
        gex_recorder_service.record_series_once(series["id"])
    with patch(
        "services.gex_recorder_service.get_expiry_dates",
        return_value=(True, {"data": ["18-AUG-26"]}, 200),
    ):
        gex_recorder_service.record_series_once(series["id"], now=_next_minute())

    got = [s["expiry_date"] for s in gex_history_db.get_snapshots_in_range(series["id"], 0, 2_000_000_000)]
    assert got == ["11AUG26", "18AUG26"]


def test_a_disabled_series_is_skipped(gexdb, recording):
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "11AUG26")
    gex_history_db.set_series_enabled(series["id"], False)
    gex_recorder_service.record_series_once(series["id"])
    assert gex_history_db.get_snapshots_in_range(series["id"], 0, 2_000_000_000) == []


def test_the_timestamp_is_floored_to_the_cadence(gexdb, recording):
    """A ragged ts turns the heatmap's x-axis into jitter and makes the unique
    constraint useless as a double-fire guard."""
    _, _, series = gex_history_db.add_series("NIFTY", "NFO", "11AUG26")
    gex_recorder_service.record_series_once(series["id"], now=1_754_000_037)
    assert gex_history_db.get_snapshots_in_range(series["id"], 0, 2_000_000_000)[0]["ts"] == 1_754_000_000


def test_series_are_staggered_across_the_cadence(gexdb):
    """Rate limiting is live, not hypothetical - a single manual call during
    design hit 'Rate limit hit (805)'. Every series firing on the same second
    is the shape that triggers it."""
    offsets = {gex_recorder_service.stagger_seconds(i) for i in range(1, 6)}
    assert len(offsets) == 5
    assert all(0 <= o < gex_recorder_service.CADENCE_SECONDS for o in offsets)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest test/test_gex_recorder_service.py -v`
Expected: FAIL, `ModuleNotFoundError: services.gex_recorder_service`.

- [ ] **Step 3: Implement**

Module docstring must state: memory jobstore on purpose (fixed schedule, nothing to
persist — avoids both the write-lock hazard and the APScheduler jobstore import-path trap
where a persisted job stores its module path and renaming the module errors on startup);
and that this is the only new thread owner.

```python
CADENCE_SECONDS = int(os.getenv("GEX_RECORDER_CADENCE_SECONDS", "60"))
RETENTION_DAYS = int(os.getenv("GEX_RECORDER_RETENTION_DAYS", "30"))
# 03:30 IST: after the ~3:00 AM broker token rollover and well outside any session.
_PRUNE_HOUR, _PRUNE_MINUTE = 3, 30
IST = ZoneInfo("Asia/Kolkata")


def stagger_seconds(series_id: int) -> int:
    """A per-series offset into the cadence window.

    Three mitigations guard the live rate limit and this is the first: series
    must not all fire on the minute. 7 is coprime with 60, so consecutive ids
    land 7 seconds apart and wrap without colliding.
    """
    return (series_id * 7) % CADENCE_SECONDS
```

`GexRecorderScheduler` — copy the singleton shape of
`services/openscript/alert_service.IndicatorAlertScheduler:54-99` (`__new__` + `_init_lock`,
`init()`, `shutdown()`), with:

```python
self._scheduler = ResilientBackgroundScheduler(
    # coalesce: a backlog collapses to one run. max_instances: a slow tick can
    # never overlap itself. Both are rate-limit mitigations, not tidiness.
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30},
)
```

`init()` starts the scheduler, registers the daily prune via
`CronTrigger(hour=_PRUNE_HOUR, minute=_PRUNE_MINUTE, timezone="Asia/Kolkata")`, then calls
`sync_jobs()`. The scheduler thread starts unconditionally so a later `add_series` has
something to register against; "idle" means **no jobs and therefore no broker calls**, which
an empty watchlist guarantees.

`sync_jobs()` — reconcile: one `IntervalTrigger(seconds=CADENCE_SECONDS,
start_date=<next minute + stagger_seconds(id)>)` job per enabled series with id
`gex_record_{series_id}`, and remove any `gex_record_*` job whose series is gone or
disabled. Called from `init()` and from every watchlist mutation route.

`record_series_once(series_id, now: int | None = None)` — top-level, so APScheduler holds a
plain function reference:

1. `get_series`; return if missing or `not enabled`.
2. `session_is_open(series["exchange"], datetime.now(IST), default=False)`; return if shut.
3. `api_key = get_first_available_api_key()`; `logger.warning` and return if `None`.
4. Resolve the expiry: `"nearest"` → `get_expiry_dates(underlying, exchange, "options",
   api_key)` and take `data[0]` (already filtered to live expiries and sorted ascending),
   normalised `"11-AUG-26"` → `"11AUG26"` by stripping dashes and upper-casing — the same
   conversion `OITracker.tsx:formatExpiry` and `option_target_service._compact_expiry` do.
   Anything else is a pinned rule, used verbatim.
5. `inputs = fetch_snapshot_inputs(underlying, exchange, resolved_expiry, api_key)`.
6. `oi = build_snapshot(black76, inputs, "oi")`, `vol = build_snapshot(black76, inputs,
   "volume")` — one chain fetch, one IV solve, both weightings.
7. `ts = (now or int(time.time())) // CADENCE_SECONDS * CADENCE_SECONDS`.
8. Map to the row dicts and `write_snapshot`. Zip `oi["strikes"]` and `vol["strikes"]` with
   `strict=True` and assert the strikes match — both come from the same `legs` list, so a
   mismatch means the seam broke — and join the raw OI/volume from `inputs.rows` by strike.
9. Wrap the whole body in `try/except Exception: logger.exception(...)` so a bad tick never
   kills the schedule, and `finally: remove_all_scoped_sessions()` — the job touches
   `gex_history_db`, `database.symbol` (expiry lookup) and `database.auth_db`, and runs on
   a scheduler thread with no app context and therefore no `teardown_appcontext`.

`prune_history_once()` — same `try/except/finally` shape; calls
`gex_history_db.prune_snapshots(RETENTION_DAYS)` and logs **rows deleted and rows
remaining** at info (a silent prune failure is silent disk growth).

Module tail: `gex_recorder_scheduler = GexRecorderScheduler()`,
`get_gex_recorder()`, `init_gex_recorder()`.

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest test/test_gex_recorder_service.py test/test_gex_history_db.py test/test_gex_levels_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/gex_recorder_service.py test/test_gex_recorder_service.py
git commit -m "feat(gex-recorder): record a snapshot per watchlisted series per minute"
```

---

### Task 6: The watchlist routes

**Files:**
- Modify: `blueprints/gex.py`
- Test: `test/test_gex_series_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Reuse the fixture shape from `test/test_gex_levels_endpoint.py:26-46` — the real blueprint,
a real Flask test client, `session_transaction` setting `logged_in` / `user` / `login_time`
(`check_session_validity` is applied at import time, so patching the decorator after import
does not work; this was tried).

```python
def test_the_routes_require_a_session(client):
    assert client.get("/gex/api/gex-series").status_code == 401
    assert client.post("/gex/api/gex-series", json={"underlying": "NIFTY"}).status_code == 401


def test_adding_a_series_registers_its_recorder_job(authed_client):
    with (
        patch("blueprints.gex.gex_history_db.add_series", return_value=(True, "ok", {"id": 1})),
        patch("blueprints.gex.get_gex_recorder") as recorder,
    ):
        res = authed_client.post(
            "/gex/api/gex-series",
            json={"underlying": "NIFTY", "exchange": "NFO", "expiry_rule": "nearest"},
        )

    assert res.status_code == 201
    recorder.return_value.sync_jobs.assert_called_once()


def test_an_invalid_expiry_rule_is_rejected(authed_client):
    res = authed_client.post(
        "/gex/api/gex-series",
        json={"underlying": "NIFTY", "exchange": "NFO", "expiry_rule": "next-week"},
    )
    assert res.status_code == 400


def test_the_watchlist_is_capped(authed_client):
    """Ten series is 940 chain symbols a minute against a broker that rate-limited
    a single manual call during design."""
    existing = [{"id": i} for i in range(MAX_SERIES)]
    with patch("blueprints.gex.gex_history_db.list_series", return_value=existing):
        res = authed_client.post(
            "/gex/api/gex-series", json={"underlying": "NIFTY", "exchange": "NFO"}
        )
    assert res.status_code == 400
    assert "10" in res.get_json()["message"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest test/test_gex_series_endpoint.py -v`
Expected: FAIL, 404 from Flask (route not registered).

- [ ] **Step 3: Implement**

In `blueprints/gex.py`, add `MAX_SERIES = 10` and four routes on the existing `gex_bp`,
each `@cross_origin()` + `@check_session_validity` and each reusing the validation already
in `gex_levels()` (`^[A-Z0-9]+$` for underlying, `^[A-Z0-9_]+$` for exchange, `[:20]`
truncation) plus `expiry_rule in {"nearest"} or re.match(r"^\d{2}[A-Z]{3}\d{2}$", rule)`:

- `GET /gex/api/gex-series` → `{"status": "success", "data": [...]}`
- `POST /gex/api/gex-series` → validate, cap-check against `list_series()`, `add_series`,
  then `get_gex_recorder().sync_jobs()`. 201 on success, 400 on a duplicate or a bad field.
- `PATCH /gex/api/gex-series/<int:series_id>` → `{"enabled": bool}` → `set_series_enabled`
  then `sync_jobs()`.
- `DELETE /gex/api/gex-series/<int:series_id>` → `remove_series` then `sync_jobs()`. The
  response message must say the recorded history went with it — that is destructive and the
  caller should not have to read the source to find out.

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest test/test_gex_series_endpoint.py test/test_gex_levels_endpoint.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add blueprints/gex.py test/test_gex_series_endpoint.py
git commit -m "feat(gex-recorder): add the watchlist routes"
```

---

### Task 7: The recorded fast path

**Files:**
- Modify: `blueprints/gex.py` (the `gex_levels` view)
- Test: `test/test_gex_levels_endpoint.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_fresh_recorded_snapshot_is_served_without_a_broker_call(authed_client):
    """N open tabs cost one poll instead of N - that is the recorder paying for
    itself, not an optimisation."""
    recorded = _recorded_snapshot(age_seconds=30)
    with (
        patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=recorded),
        patch("blueprints.gex.get_gex_levels") as live,
    ):
        res = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_not_called()
    payload = res.get_json()
    assert payload["source"] == "recorded"
    assert payload["as_of"] == recorded["ts"]
    assert payload["quality"]["may_draw"] is True     # survived the round trip
    assert len(payload["strikes"]) == 2


def test_a_stale_recorded_snapshot_falls_back_to_a_live_fetch(authed_client):
    """Two cadence intervals, so a single missed tick does not force a fetch -
    but a recorder that is down must not freeze the study on old numbers."""
    with (
        patch(
            "blueprints.gex.gex_history_db.get_latest_snapshot",
            return_value=_recorded_snapshot(age_seconds=180),
        ),
        patch("blueprints.gex.get_gex_levels", return_value=(True, {"status": "success"}, 200)) as live,
    ):
        authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_called_once()


def test_a_series_nobody_recorded_still_renders(authed_client):
    """Unifying the fetch must not make the study fail closed on instruments
    nobody chose to record."""
    with (
        patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=None),
        patch("blueprints.gex.get_gex_levels", return_value=(True, {"status": "success"}, 200)) as live,
    ):
        res = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_called_once()
    assert res.status_code == 200


def test_a_history_lookup_failure_falls_back_rather_than_500s(authed_client):
    """The recorded path is an optimisation. A broken gex.db must degrade to the
    behaviour the study had before this phase, not take the study down."""
    with (
        patch("blueprints.gex.gex_history_db.get_latest_snapshot", side_effect=RuntimeError("db gone")),
        patch("blueprints.gex.get_gex_levels", return_value=(True, {"status": "success"}, 200)) as live,
    ):
        res = authed_client.post("/gex/api/gex-levels", json=body())

    live.assert_called_once()
    assert res.status_code == 200


def test_the_recorded_payload_serves_the_requested_weighting(authed_client):
    recorded = _recorded_snapshot(age_seconds=10)
    with patch("blueprints.gex.gex_history_db.get_latest_snapshot", return_value=recorded):
        oi = authed_client.post("/gex/api/gex-levels", json=body(weight_by="oi")).get_json()
        vol = authed_client.post("/gex/api/gex-levels", json=body(weight_by="volume")).get_json()

    assert oi["call_wall"] == recorded["call_wall_oi"]
    assert vol["call_wall"] == recorded["call_wall_vol"]
    assert oi["strikes"][0]["net_gex"] == recorded["strikes"][0]["net_gex_oi"]
    assert vol["strikes"][0]["net_gex"] == recorded["strikes"][0]["net_gex_vol"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run python -m pytest test/test_gex_levels_endpoint.py -v`
Expected: the five new tests FAIL — the live service is still called every time.

- [ ] **Step 3: Implement**

In `blueprints/gex.py`, after the existing validation and before calling `get_gex_levels`:

```python
# Two cadence intervals. One missed tick must not force a broker round trip
# (that would undo the point of the recorder); a recorder that is down must not
# freeze the study on stale numbers.
FAST_PATH_MAX_AGE_SECONDS = 120


def _recorded_payload(snapshot: dict, weight_by: str) -> dict:
    """Reshape one stored snapshot into the payload the study already renders.

    `strikes` is rebuilt with the SAME keys the live path emits (`net_gex`,
    `net_dex`, ...) so the frontend has one shape to handle, and the totals are
    summed from the strike rows rather than stored - they are derivable, and a
    stored total that disagreed with its own strikes would be unfixable.
    """
```

Suffix selection is `"oi" if weight_by == "oi" else "vol"`. The reshape must produce every
key the live payload has: `underlying`, `exchange`, `expiry_date`, `weight_by`,
`spot_price`, `forward_price`, `atm_strike`, `lot_size`, `dte_days`, `interest_rate`,
`strikes[]`, `total_call_gex`, `total_put_gex`, `call_wall`, `put_wall`, `zero_gamma`,
`net_gex`, `regime`, `quality` (the stored JSON verbatim, `may_draw` and all), `sentiment`,
plus `source: "recorded"` and `as_of: snapshot["ts"]`.

The lookup itself is wrapped so it can only ever *skip* the fast path:

```python
        try:
            snapshot = gex_history_db.get_latest_snapshot(underlying, exchange, expiry_date)
            if snapshot and (int(time.time()) - snapshot["ts"]) < FAST_PATH_MAX_AGE_SECONDS:
                return jsonify(_recorded_payload(snapshot, weight_by)), 200
        except Exception:
            # Never fatal. The recorded path is an optimisation; a broken gex.db
            # degrades the study to exactly the behaviour it had before phase 3.
            logger.exception("Recorded GEX fast path failed; falling back to a live fetch")
```

- [ ] **Step 4: Run the tests**

Run: `uv run python -m pytest test/test_gex_levels_endpoint.py test/test_gex_series_endpoint.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add blueprints/gex.py test/test_gex_levels_endpoint.py
git commit -m "feat(gex-levels): serve a fresh recorded snapshot instead of refetching"
```

---

### Task 8: Wiring

**Files:**
- Modify: `app.py` (near the `init_indicator_alert_scheduler` block, ~805)
- Modify: `utils/db_sessions.py:22-47`
- Modify: `.sample.env` (after `SANDBOX_DATABASE_URL`, ~line 60)

- [ ] **Step 1: Register the scoped session**

Add to `SCOPED_SESSION_MODULES`:

```python
    ("database.gex_history_db", "db_session"),
```

Without this the recorder's session is never released on a request path and
`remove_all_scoped_sessions()` silently skips it on the scheduler thread — the exact FD leak
shape `utils/db_sessions.py` exists to prevent.

- [ ] **Step 2: Init the database and start the recorder**

In `app.py`, alongside the other database imports near line 121:

```python
from database.gex_history_db import init_gex_history_db
```

and call it where the other `init_*_db()` calls run at startup. Then after the
indicator-alert scheduler block (~line 808), in the same `try/except` style:

```python
            try:
                # Snapshot recorder for the GEX Levels study. Ships with an EMPTY
                # watchlist and makes no broker call until a series is added, so
                # an upgrade never starts polling on a schedule nobody asked for.
                from services.gex_recorder_service import init_gex_recorder

                init_gex_recorder()
                logger.debug("GEX recorder initialized")
            except Exception as e:
                logger.error(f"Failed to initialize GEX recorder: {e}")
```

- [ ] **Step 3: Document the config**

In `.sample.env`:

```
GEX_DATABASE_URL = 'sqlite:///db/gex.db'   # GEX Levels snapshot history (recorder)
GEX_RECORDER_CADENCE_SECONDS = '60'        # One snapshot per series per minute
GEX_RECORDER_RETENTION_DAYS = '30'         # ~100 MB/month for two series
```

- [ ] **Step 4: Verify the app boots and the recorder is idle**

Run: `uv run python -c "import app"` — expect no traceback, and `db/gex.db` created.
Then start the server (`uv run app.py`) and confirm in `log/errors.jsonl` that nothing new
appeared and that no chain fetch happened — the watchlist is empty, so the recorder must
register zero jobs.

Then add a series live and watch it record:

```bash
curl -X POST http://127.0.0.1:5000/gex/api/gex-series \
  -H 'Content-Type: application/json' -b cookies.txt \
  -d '{"underlying":"NIFTY","exchange":"NFO","expiry_rule":"nearest"}'
```

(Log in through the browser first and export the session cookie, or drive it from the
browser console on an authenticated tab — these routes are session-gated, not API-key-gated.)
Wait two minutes during market hours, then `GET /gex/api/gex-series` and open the study:
`source` must read `"recorded"`, `as_of` must advance each minute, and a second tab must not
add a broker call.

- [ ] **Step 5: Commit**

```bash
git add app.py utils/db_sessions.py .sample.env
git commit -m "feat(gex-recorder): wire the recorder and gex.db into startup"
```

---

### Task 9: fd-audit (MANDATORY — CLAUDE.md)

This change touches a database, threads and a scheduler. It cannot be called done without
this.

- [ ] **Step 1: Run the skill**

Invoke the `fd-audit` skill over the phase 3 diff.

- [ ] **Step 2: Confirm each claim against the code, do not assert it**

The audit must actually check, not assume:
- `gex.db` engine comes from `create_db_engine()` → `NullPool`, never `StaticPool`.
- `gex_history_db.db_session` is in `SCOPED_SESSION_MODULES` (Task 8 Step 1).
- Both scheduler jobs end in `finally: remove_all_scoped_sessions()` — they run with no app
  context and never reach `teardown_appcontext`.
- Exactly one scheduler thread: `GexRecorderScheduler` is a module-level singleton with a
  double-checked `_init_lock`, and `init()` is idempotent.
- The prune is bounded and its `IN` clause is chunked (SQLite's 999-variable limit).
- No unbounded module-level dict or cache was introduced. `stagger_seconds` is pure; there
  is no per-series lock registry (unlike `alert_service._alert_locks`) because
  `max_instances: 1` already serialises a series against itself.
- `write_snapshot` commits or rolls back on every path, including `IntegrityError`.

- [ ] **Step 3: Fix anything it finds, then commit**

```bash
git commit -am "fix(gex-recorder): <what the audit found>"
```

---

### Task 10: Record what shipped

**Files:**
- Modify: `docs/superpowers/specs/2026-08-05-gex-advanced-visualisations-design.md` (§10 status table, header)
- Modify: `docs/superpowers/HANDOFF-gex-advanced-visualisations.md`

- [ ] **Step 1: Update the status table**

Mark step 3 **DONE 2026-08-05**. Update the header status line to "Phases 1–3 shipped".

- [ ] **Step 2: Record the three detail-level readings**

Add a short subsection to the spec noting, with reasoning: quality stored per weighting as
the full payload (the `may_draw` trap), `MAX_SERIES = 10`, and the reuse of
`option_target_sessions` for the §8 session guard. Future readers must not think these were
undocumented drift.

- [ ] **Step 3: Rewrite the handoff for phase 4**

Point it at Gamma Bands: the query shape is `get_snapshots_in_range(series_id, from_ts,
to_ts)`, which already exists and is already boundary-tested; what remains is
`services/gex_history_service.py`, `POST /gex/api/gex-history` with `fields: "levels"`, the
step-line renderers, and a watchlist control in the settings panel. Carry forward the still-open
follow-ups: `scan_zero_gamma` re-resolving IVs, `/gex` still on `compute_exposures`,
and Zero-Gamma living in forward space on a spot axis.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/
git commit -m "docs(gex): record the recorder and hand off Gamma Bands"
```

---

## Verification

**Backend suite** (the regression guard is the pure modules staying green — no pure module
changed, so they must):

```bash
uv run python -m pytest test/test_gex_levels_math.py test/test_gex_levels_walls.py \
  test/test_gex_levels_zero_gamma.py test/test_gex_levels_quality.py \
  test/test_gex_levels_sentiment.py test/test_gex_levels_exposure.py \
  test/test_gex_levels_delta.py test/test_gex_levels_service.py \
  test/test_gex_levels_endpoint.py test/test_gex_history_db.py \
  test/test_gex_recorder_service.py test/test_gex_series_endpoint.py \
  test/test_option_target_sessions.py test/test_option_target_service.py -v
```

Expected: all PASS. Baseline is 160 backend tests before this work; the new files should
take it past 200.

**Lint**, scoped (never tree-wide):

```bash
uv run ruff check database/gex_history_db.py services/gex_recorder_service.py \
  services/gex_levels_service.py services/option_target_sessions.py \
  services/option_target_service.py blueprints/gex.py app.py utils/db_sessions.py \
  test/test_gex_history_db.py test/test_gex_recorder_service.py \
  test/test_gex_series_endpoint.py --fix
uv run ruff format <same files>
```

**Live verification — this is not optional.** Three defects reached the live chart last
session with a full green suite. Restart the Flask server (nothing hot-reloads), then during
market hours:

1. `GET /gex/api/gex-series` returns `[]` on a fresh install and the log shows zero broker
   calls from the recorder. **The empty-watchlist idle case is the one an upgrade hits.**
2. Add NIFTY/NFO/nearest. Within two minutes `db/gex.db` has a `gex_snapshot` row with both
   `call_wall_oi` and `call_wall_vol` populated and 47 `gex_snapshot_strike` children.
3. Open the GEX Levels study on `/charts` (`NIFTY28JUL26FUT` per the workspace notes) and
   confirm the response carries `source: "recorded"`, that the walls and the bar column look
   exactly as they did before phase 3, and that `as_of` advances each minute.
4. Open a second tab. The broker call count must not double.
5. Compare one recorded snapshot against a forced live fetch of the same chain — disable the
   series, reload, and check the walls and net GEX agree. This is the drift test done for
   real, against the broker, which is the only place the last three defects were visible.
6. Check `log/errors.jsonl` is clean.

Then, after the close, confirm the recorder stops: no new rows after the session window, and
no rate-limit warnings in the log.

## What comes next

Phase 4, Gamma Bands — the smallest query shape and the first consumer of this history.
Not in this plan.
