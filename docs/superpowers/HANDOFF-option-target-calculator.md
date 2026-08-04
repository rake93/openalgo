# Option Target Calculator — Session Handoff

**Written:** 2026-08-04 end of session
**Branch:** `feat/option-target-calculator` (base `d176bf068`)
**State:** Feature complete and running live. 158 passed, 1 skipped. Working tree clean.

---

## 1. Kickoff — run these first

```bash
cd C:/apps/foss/rock-edge/openalgo
git branch --show-current          # expect feat/option-target-calculator
git status --porcelain | grep -v frontend/dist   # expect empty

uv run python -m pytest \
  test/test_option_target_math.py test/test_option_target_ranking.py \
  test/test_option_target_sessions.py test/test_option_target_service.py \
  test/test_option_target_replay.py test/test_pricing_underlying.py \
  test/test_option_chain_underlying.py -q
# expect: 158 passed, 1 skipped
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

### 3.1 Wire `_vol_beta_samples` (highest value)

`services/option_target_service.py::_vol_beta_samples` returns `[]`. So
`vol_beta: "auto"` always falls back to the Normal preset (0.8) and reports
`"Vol-beta estimate unavailable: Only 0 samples, need 20"` in `warnings`.

`services/option_target/volbeta.py::estimate_vol_beta` is complete and tested —
only the history plumbing is missing. It needs
`(percent_return, atm_iv_in_vol_points)` samples over a trailing window.

**Why it matters:** vol-level response is the single largest error term in the
model. Measured realised beta on the backtested day was **~1.4**, not 0.8. The
replay showed 3.60% MAE at beta 0.8 versus **1.17% at beta 1.5**.

Sketch: fetch 1-minute history for the underlying and the ATM strike over the
trailing window, back out ATM IV per bar, pair with the percent return. Decide
the window (the spec assumes 90 minutes) and how to behave before ~10:00 when
the session is too young for 20 samples.

### 3.2 Cross-expiry compare

Spec Step 13, never built. Price the recommended strike across the next 2-3
expiries so the weekly-versus-monthly choice is explicit. Self-contained.

### 3.3 Buy button and Strategy Builder handoff

The user asked for both. Left out because the order-placement and Strategy
Builder leg contracts were not investigated, and guessing would have produced
placeholder code. The page is analysis-only today.

### 3.4 CDS

Still excluded from `toolsFnoExchanges` in `useSupportedExchanges.ts`. This
broker's master has **no CDS option expiries at all**, so this is a data
question before it is a code question.

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
