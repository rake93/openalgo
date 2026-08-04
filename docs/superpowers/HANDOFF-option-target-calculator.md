# Option Target Calculator — Session Handoff

**Written:** 2026-08-04 end of session, updated same day after session 2
**Branch:** `feat/option-target-calculator` (base `d176bf068`)
**State:** Feature complete and running live. 196 passed, 1 skipped. Working tree clean.

---

## 1. Kickoff — run these first

```bash
cd C:/apps/foss/rock-edge/openalgo
git branch --show-current          # expect feat/option-target-calculator
git status --porcelain | grep -v frontend/dist   # expect empty

uv run python -m pytest \
  test/test_option_target_math.py test/test_option_target_ranking.py \
  test/test_option_target_sessions.py test/test_option_target_service.py \
  test/test_option_target_replay.py test/test_option_target_volbeta.py \
  test/test_pricing_underlying.py test/test_option_chain_underlying.py -q
# expect: 196 passed, 1 skipped
```

**`uv run pytest` is broken on this machine** (`uv trampoline failed to
canonicalize script path`). Always `uv run -m pytest`. Every doc in the repo
writes the broken form. The venv is Python 3.14.4.

Live smoke test (server must be running; it does NOT hot-reload Python):

```bash
uv run python -c "
from database.auth_db import get_first_available_api_key
from services.option_target_service import get_option_target
k = get_first_available_api_key()
ok, r, c = get_option_target(underlying='NIFTY', exchange='NFO', expiry_date=None,
                             reference='SPOT', target_price=24700.0, api_key=k)
print(ok, c, r['snapshot']['expiry_date'], len(r['candidates']))
" 2>&1 | tail -2
```

`get_first_available_api_key()` returns a working key — no need to ask for one.

---

## 2. What exists

Page `/optiontarget`, registered in `frontend/src/lib/tools.ts`. Given a futures
or spot target it projects every strike and ranks them.

**Pure math, zero IO** — `services/option_target/`:
`models.py`, `daycount.py`, `forward.py`, `smile.py`, `projection.py`,
`ranking.py`, `volbeta.py`.

**Service layer** — `services/option_target_service.py` (orchestrator, only
broker-touching module), `services/option_target_sessions.py` (validated session
provider), `services/pricing_underlying.py` (shared, see below).

**API** — `POST /api/v1/optiontarget`.

**Frontend** — `pages/OptionTargetCalculator.tsx`,
`pages/option-target/{ScenarioPanel,StrikeTable,StrikeDetail}.tsx`,
`hooks/useOptionTarget.ts`, `api/option-target.ts`, `types/option-target.ts`.

**Docs** — `docs/option-target-calculator.md`, spec and plan under
`docs/superpowers/`.

---

## 3. Open work, ranked

### 3.1 Wire `_vol_beta_samples` — DONE (2026-08-04, session 2)

`vol_beta: "auto"` now estimates from the ATM straddle's own 1-minute history.
Both legs of the ATM strike are fetched so put-call parity gives the forward
per bar; the OTM leg is solved for IV with `t` recomputed each minute, and the
fixed-strike IV is corrected back to at-the-money using the fitted smile.

Live on 2026-08-04: NIFTY n=121, R-squared 0.900; BANKNIFTY n=119, R-squared
0.912. Both measured above the Panic preset (raw 2.89 and 2.10) and were
clamped to 2.0 with a warning naming the raw value. That is still far closer to
the replay's optimum than the old 0.8 fallback (1.17% MAE at 1.5 vs 3.60% at
0.8).

Decisions taken, both previously open in spec Section 11:

- **Window: 120 minutes**, measured from the last bar, a maximum lookback and
  not a minimum wait. It is the shortest window where the first-differenced
  regression also clears the R-squared gate (1.99), so a shorter window's
  hotter levels estimate cannot be separated from a shared time-of-day trend.
- **No prior-session samples.** The existing gates already degrade correctly:
  no estimate before 09:35, weak fit to ~09:45, clean from 10:00.

Open caveat, documented in `docs/option-target-calculator.md`: beta is fitted
over the moves the session actually made (a few tenths of a percent) and then
applied to a target move that may be 1 percent or more. `r_squared` and
`samples` are reported so the extrapolation is visible, but it is real.

### 3.2 Cross-expiry compare — DEFERRED, not scheduled

Spec Step 13. Price the recommended strike across the next 2-3 expiries so the
weekly-versus-monthly choice is explicit. Self-contained, and still **zero
code** — no `compare_expiries` request field, no `expiry_compare[]` response
field, nothing in the UI.

**Decided on 2026-08-04 not to build it.** This is a deliberate deferral, not
an oversight or a blocked item: the feature works without it, and the design
spec's own envelope reserves the field whenever someone picks it up. Do not
treat its absence as a bug to be fixed on sight.

### 3.3 Buy button and Strategy Builder handoff — DONE (2026-08-04, session 2)

Both contracts turned out to be fully specified; nothing needed guessing.

**Buy button.** One per non-excluded row, opening a confirm dialog that shows
the exact payload before anything is sent. Orders go MARKET/MIS through
`tradingApi.placeOrder`. Two things worth keeping in mind:

- `place_order_service` already honours `get_analyze_mode()`, so analyzer mode
  routes to the sandbox with no special-casing here. The dialog reads
  `useThemeStore().appMode` to say which mode the click will actually hit.
- Quantity is `lots x lot_size`, not lots (`Scalping.tsx:842`). CNC is an
  equity product and is never used for options.

**Strategy Builder handoff.** `?exchange=NFO&underlying=NIFTY&expiry=11AUG26&legs=24000CE:BUY:1`,
parsed by `frontend/src/lib/strategyHandoff.ts` (26 tests). Only the legs'
identity travels: price, IV, lot size and the broker symbol are re-resolved by
the builder from its own live chain. That is deliberate - some brokers do not
follow the standard `BASE[DDMMMYY][STRIKE][CE|PE]` concatenation, so a symbol
built anywhere but the chain can be invalid (`StrategyBuilder.tsx:857`). It
also means the two pages will show slightly different premiums; that is two
quotes moments apart, not a bug.

Codec and receiver both fail closed - one malformed or unlisted leg rejects the
whole handoff, because a strategy short a leg is a different strategy with a
different payoff and would arrive looking valid.

The builder already had `ExecuteBasketDialog`, so the trade path continues from
there. No changes were needed to how it executes.

### 3.4 CDS — DEFERRED, blocked on data

Still excluded from `toolsFnoExchanges` in `useSupportedExchanges.ts`. This
broker's master has **no CDS option expiries at all**, so this is a data
question before it is a code question — enabling the exchange in the UI would
produce an empty chain, not a working surface.

Re-check the master contract before touching any code. If the expiries appear
there, the calculator itself needs no CDS-specific work: currency options price
off the same forward machinery as everything else.

### 3.5 Pre-existing, not caused by this work

- **8 test-collection errors** across the wider `test/` directory: missing
  `sandbox.order_manager`, missing `eventlet`, a renamed `telegram_bot_service`
  function. Already red before this branch.
- **`database/market_calendar_db.py` seeded MCX special sessions are corrupt** —
  895-minute windows spanning two calendar dates where the comments intend
  415-minute evening sessions. `services/option_target_sessions.py` validates
  and falls back rather than trusting them. Fixing it properly needs the NSE and
  MCX circulars.

---

## 4. Branch composition — read before merging

`feat/option-target-calculator` contains **31 commits, and one is not this
feature**:

- `0b4fb2320 fix(scheduler): survive a locked database instead of killing the thread`

That is SQLite-lock/APScheduler work from a concurrent session (7 files:
`database/__init__.py`, `services/flow_scheduler_service.py`,
`services/historify_scheduler_service.py`, `services/openscript/alert_service.py`,
`utils/scheduler.py`, and two tests). It landed on this branch because both
sessions shared one working directory. Whoever merges gets both. Split it out
first if that is not wanted.

Related hazard, hit twice this session: `git commit` commits **everything
staged**, not just what you named in `git add`. One commit here swallowed
another agent's staged files. Always `git status --porcelain | grep -v
frontend/dist` immediately before committing.

`frontend/dist/` has ~151 pre-existing modified entries. Never stage it.

---

## 5. Decisions with measurements — do not re-litigate without new data

| Decision | Evidence |
| --- | --- |
| Price off the **synthetic forward for the option's own expiry**, never spot | Basis: NIFTY 7d **+7.5** pts, BANKNIFTY 21d **+138.9** pts |
| **Full Black-76 reprice**, not delta | Replay across 37 strike series: delta-only **6.81%** MAE, full model **1.17%** |
| Vol-level **beta** is the biggest error term | Slide alone 6.77% is WORSE than sticky-strike 5.55%; sliding a fixed shape cannot represent a change in vol LEVEL |
| **Probability weighting rejected** | Over 45-90 min a 1% move is 3-9 sigma, so every strike scores ~0 and ranking degenerates. Replaced with 50/75/100% partial-move scenarios |
| **No 52-minute time floor** | `option_greeks_service` floors t at 0.0001 yr as a py_vollib legacy. The Rust core is stable to 30 s; that floor overstates an ATM call by **23%** in the last hour of expiry day |
| **Never emit non-finite floats** | `float("-inf")` serialises as `-Infinity`, which `JSON.parse` rejects, silently destroying a correct response. Guarded by `json.dumps(..., allow_nan=False)` |
| Smile **falls back to sticky-strike above RMS 3.0** vol pts | Measured RMS: 0.024 (7d), 0.446 (73 min), 3.89 (18 min), 14.36 (7 min). Sliding a curve with c~1120 produced 200%+ IVs |
| Beta window is **120 min**, not the spec's 90 | Shortest window where first differences also clear the R-squared gate (1.99). At 30/60/90 min differencing gives 0.22/0.22/0.29 and falls back, so a shorter window's hotter levels estimate cannot be told apart from a shared time-of-day trend |
| Beta samples come from the **ATM straddle**, not the index plus one leg | Parity gives the per-bar forward from the same two prices, so it costs 2 history calls not 3, needs no futures series, and works on commodities, which have no spot |
| **No prior-session samples** for a young session | The existing gates already degrade correctly: no estimate before 09:35, weak fit to ~09:45, clean from 10:00 (beta 1.60, R-squared 0.60). A 45-minute window is not worth new plumbing |
| Estimated beta is **clamped at 2.0** | 2026-08-04 NIFTY regressed to 2.89 off a sample range only 0.18% wide; on a 1% target that adds 3 vol pts to a 10.8% ATM IV. The clamp also lands near the trend-free differenced estimate (1.99) |

The replay harness is the regression gate. **Any change to the projection math
must not worsen its MAE.** Recapture the fixture with
`scripts/capture_option_target_fixture.py`.

---

## 6. Commodities

MCX has **no spot instrument**. Options are written on futures with a
**different expiry**: CRUDEOIL 17AUG26 options settle against
`CRUDEOIL19AUG26FUT`.

`services/pricing_underlying.py` resolves this platform-wide and is wired into
`services/option_chain_service.py`, so Option Chain, GEX, Max Pain, IV Smile,
Vol Surface, Gamma Density and OI Tracker all work for commodities now.

- Matching is on **`SymToken.name`** (the commodity root). Prefix matching
  confuses CRUDEOIL with CRUDEOILM and GOLD with GOLDM. `name` is **NULL on
  NFO**. There is no option-to-future key in the schema.
- The chain response keeps `underlying_ltp` and the string `underlying`
  unchanged for compatibility, and adds **`underlying_ref`**. Do not rename
  `underlying` — it is typed as a string in the public REST docs and exposed to
  MCP clients.
- For commodities the calculator reports `basis: null` plus
  `parity_vs_underlying`, rejects a SPOT reference with 400, and uses
  `forward_mode: "exact"` because the linked future is the settlement
  instrument even though its expiry differs.

---

## 7. Verification lessons that cost time

1. **curl cannot validate JSON.** It prints bytes. A response containing
   `-Infinity` looked perfectly healthy under curl while the browser discarded
   it entirely. If you verify an endpoint, parse the body.
2. **A restart is required for backend changes.** Python does not reload an
   imported module. Several "still broken" reports were stale processes. The
   server holds a live broker session, so a restart drops the feed; it resumes
   without re-login the same day.
3. **Tests that pass alone can fail in the suite.** `test/conftest.py` points
   `DATABASE_URL` at an empty DB; a module-level override only wins if nothing
   imported `database.symbol` first. Verify both orderings.
4. **Run it on live data before believing it.** The ranking looked correct on
   fixtures and recommended lottery-ticket far-OTM strikes on a real chain.
5. **`ruff check <dir> --fix` edits every file in the directory.** Running it
   on `services/` auto-fixed 21 lint errors across 11 modules that had nothing
   to do with the change, all silently staged into the working tree. Lint the
   files you touched by name, and re-check `git status` afterwards. This is the
   same hazard as Section 4's staging trap, from a different direction.
6. **A module-level cache breaks test isolation before it breaks production.**
   `_BETA_BARS_CACHE` is keyed on symbols, which every test in a file shares,
   so one test's bars answered the next test's fetch and three assertions went
   green against stale data. Both cache-holding test files now clear it in an
   autouse fixture — copy that pattern for any cache added here.
