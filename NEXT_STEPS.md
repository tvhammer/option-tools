# Next steps: automated position management

Companion to `STRATEGY_PLAN.md` (strategies and rules). This file tracks **what to build next and in which order**.
Principle: entries stay manual; once a trade is open, deterministic code manages and closes it. No LLM places, approves, vetoes or modifies orders.

_Last updated: 2026-11-17_

---

## 1. Where we are

**Works / verified**
- Rule engine in `manage.py` (IC, PCS, IBF): pure functions, per-trade `rules_json`, config in `config.yaml` (validated by `config.py`; unknown keys and secret-like keys are rejected).
- No made-up data in the real path: no provider → `evaluate` exits 1; `execute` and `journal-sync` refuse; `--mock` is test-only, never with `--mode live`, never writes the journal. (`tests/test_no_mock.py`)
- Credentials outside the repo: `~/.config/option-tools/<profile>.env` (dir 700, files 600), loaded by `credentials.py` (refuses loose permissions, checks `TASTY_ENV` matches the profile, redacts repr). (`tests/test_config_credentials.py`)
- tastytrade OAuth2 works: `tasty-prod-read` (production, `read` scope) returns real SPY quotes, the option chain and option bid/ask/mid; `tasty-sandbox` token exchange and account list work. Check script: `venv/bin/python tasty_check.py [SYMBOL]`.
- stepflow workflow `ai-workflows/trading/options-manage.yaml`: evaluate → limit check → execute → journal sync → notify → LLM report; guards for live mode (`LIVE_OK=yes`), `STOP` file and max actions per run. Tested against a stub.

**Not working yet**
- No market-data provider registered (`MARKET_DATA_PROVIDERS` is empty): `evaluate` fails whenever a trade is open.
- No execution backend; `ib_executor.py` (IB) exists but is unwired; no tastytrade order code.
- Position reconciliation compares against an empty list (always a false `JOURNAL-ONLY` alert; alerts don't change the exit code).
- Journal legs have no expiry date, so OCC option symbols cannot be built.
- No calendar-spread evaluator (decided strategy: same-strike calendars only).
- `tasty_chain.py` and `watchdog.py`'s tastytrade login use the decommissioned username/password flow.
- `trend_break` rule is a stub; IVR-drop uses the entry-time IV rank.

**Facts about tastytrade that shape the design** (from developer.tastytrade.com)
- OAuth2 only (client secret + non-expiring refresh token → 15-minute access tokens). Legacy `POST /sessions` is gone. Every request needs `User-Agent: product/version`.
- Production REST `GET /market-data/by-type` gives real-time bid/ask/mid/mark for equities and OCC options, max 100 symbols per request. Greeks only via the DXLink stream.
- Sandbox: separate credentials; **no REST market data** (HTTP 502); positions/trades reset every 24 h; fills come from a simulator. Pattern: **read data from production (read scope), send paper orders to the sandbox.**
- Order placement supports dry-run first; use an external identifier for idempotency.
- Sandbox positions vanish daily, so multi-day paper positions must be created by the test itself.

---

## 2. Roadmap

Each phase has an exit criterion. Do not start the next phase before it is met.

### Phase A: Real data, read-only (next)
1. **Journal leg format:** add `expiry` (YYYY-MM-DD) to each leg in `legs_json`; one-off migration for existing rows (none open today); `rules_json` unchanged. Per-contract USD for credit/max loss (already the plan's unit convention).
2. **`tasty_provider.py`** registered in `MARKET_DATA_PROVIDERS['tastytrade']`:
   - lazy access-token refresh (re-use until ~1 min before expiry);
   - build OCC symbols from legs; one request for underlying + all leg quotes (≤ 100 symbols);
   - reject (raise `MarketDataError`) one-sided, crossed, zero-bid-and-ask, or stale quotes (age threshold from config; stricter outside market hours rules: see 6);
   - mids and underlying price in the shape `manage.py` already expects.
3. **Unit tests** with a fake HTTP layer (success, 401 → refresh once, 429 → backoff, missing symbol, crossed quote, stale quote).
4. Run `manage.py evaluate` against a hand-entered test trade during market hours.

_Exit:_ `evaluate` on a real open position produces sensible rule decisions that match a manual calculation, and fails loudly on bad data.

### Phase B: Reconciliation and alerts
1. Fetch real positions through the tastytrade API (read scope) and group legs into strategies by underlying, expiry and strike.
2. Mismatch cases: journal-only, broker-only, quantity mismatch, wrong strikes → `errors`/`alerts` with a distinct exit code (proposal: exit 3 = reconciliation problem, no action taken).
3. `watchdog`/workflow: alert on exit 3 through the same log/notify path as other errors.
4. Option: a manual "import position" helper that proposes a journal entry from broker positions, so manually opened trades get `rules_json` without retyping.

_Exit:_ a manually opened paper/real position is detected, journaled with rules, and reconciles cleanly; deliberately breaking one leg raises the alert.

### Phase C: Execution on the sandbox (paper)
1. `tasty_executor.py`: build multi-leg close orders (limit, net price from mids, bounded price walk toward natural), use **dry-run first**, idempotent external identifier per (trade, action), never market orders.
2. Wire `run_execute` to it (profile `tasty-sandbox`; `--mode paper` only); journal written from **actual fills**, not simulated ones; partial-fill handling (cancel/replace rules; never leave a half-closed spread: alert).
3. Safety: only closing orders allowed; qty must equal journal qty; refuse symbols not in the journal; max actions per run (already in workflow); kill switch checked immediately before each order.
4. Tests: unit tests with fake API; scripted sandbox scenario that creates its own position, opens/closes a condor, and reconciles (sandbox resets every 24 h, so tests create their own state).
5. Journal sync from broker fills; commission accounting from the fill/fee data.

_Exit:_ ≥ 2 weeks of paper runs with no false actions, every action explained by a logged rule hit, no orphaned legs.

### Phase D: Calendar spread (same strike)
1. `evaluate_calendar_spread` in `manage.py` per the plan: per-leg DTE, `front_close_dte`, profit/stop as % of debit, tent break, `min_back_dte`; config section `calendar_spread` (keys already allowed in `config.py`).
2. Scoring model for entries (term structure needs per-expiry IV: DXLink greeks or computed from quotes; decide at that point).
3. Native roll: sell new front leg against the same long (no added risk); first candidate for an automated roll.
4. Tests for each trigger, including both expirations.

### Phase E: Scheduling and operations
1. **One scheduler:** systemd timer (or cron) every 10–30 min running the stepflow workflow. Retire `watchdog.py` as a daemon (it duplicates the workflow, has DST bugs and a self-resetting STOP file).
2. Market-hours handling in `manage.py evaluate` (use `zoneinfo("America/New_York")`, US holidays/half days, skip + exit 0 outside hours).
3. Push notifications (Telegram or email) for errors, executed actions, mismatches and "watchdog not running" (heartbeat absence).
4. Log retention for `~/.local/state/options-manage/` and stepflow run artifacts.
5. Backups of `trade_journal.db`.

### Phase F: Live, close-only
1. Create `tasty-prod-trade` profile (separate OAuth app with `trade` scope; create it only now).
2. Live run requires **both** `MODE=live` and `LIVE_OK=yes`; start with one contract, closing orders only, kill switch tested, manual approval via a stepflow gate for stop-loss actions for the first weeks.
3. Review the journal weekly (rule adherence, slippage, fees).

### Phase G (optional): Rolls and extras
- Roll proposals for IC/PCS with approval; automation only if the journal shows rolls help.
- Trend-break rule (SMA data) and fresh IV-rank for IVR-drop rule.
- Use the tastytrade MCP server (`TASTYTRADE_READ_ONLY=1`, read-scope credentials) interactively from a pi session for exploration. Not part of the order path.

---

## 3. Cross-cutting rules

- Real data or nothing: no fallback to mock/simulated data outside `--mock`.
- Units: per-contract USD for credit, debit, max loss, buying power; per-share prices only for quotes.
- Secrets only in `~/.config/option-tools/`; never in config, journal, logs, stepflow params, or the repos. Keep `tasty-prod-read` at `read` scope.
- Every automated decision is logged with inputs, rule hit and order id (journal + stepflow run artifacts + syslog).
- The LLM report step has no tools and runs after execution; it never gates anything.
- New rule or strategy = pure function + unit tests before it can reach `execute`.

---

## 4. Open decisions

1. **Risk cap:** €100 per trade is configured; confirm it, and whether $1-wide spreads (~€64 max loss) are acceptable.
2. **Stop-loss multiple** (config: 1.5× credit) and calendar stop (50% of debit): placeholders, validate against paper results.
3. **Exit code scheme** for reconciliation problems (3?) and whether any alert should block actions on the same underlying.
4. **Quote freshness limits** (seconds) per instrument type and market state.
5. **Fill policy:** walk limit price from mid toward natural, how many steps, how long; what to do when not filled before `close_dte`.
6. **Notification channel** (Telegram vs email) and where its token lives (another `~/.config/option-tools/*.env`).
7. **Journal import:** manual entry only, or broker-position import helper (Phase B.4)?
8. Whether to keep `ib_executor.py`/`ib_chain.py` as a backup broker or remove them to reduce surface area.

## 5. Housekeeping

- Delete or replace `tasty_chain.py` (decommissioned login flow, wrong endpoints) once the provider exists.
- Remove the stray `.requirements.txt.swp`, add `.gitignore` (venv, `__pycache__`, `*.db`, `*.log`, `STOP`, `.stepflow/`), and commit option-tools work (still untracked).
- Rotate the Bitbucket token that was exposed in a debugging session; check that the old GitHub token embedded in the former `~/.gitconfig` is revoked.
- `requirements.txt`: replace `ib_insync` (unmaintained) with `ib_async` if IB stays; add nothing for tastytrade (plain `requests` is enough).

---

## Slice log

A1 NOT in the tree — the A1 run (dev_feature-flow-2026-11-17T16-34-15-634Z) finished and was approved, but its changes (legs.py, tests/test_legs.py, edits to trade_journal.py and manage.py) were never committed and are gone from the working tree. Re-run slice A1 before A3 (A3 needs `legs.leg_key`). The approved plan is kept in that run's directory under `.stepflow/runs/`.

A2 done — tasty_client.py REST client with OAuth token caching, retries/backoff, error parsing, accounts, quotes, option chains, OCC symbol helpers; 108 tests with fake transport/clock (token, retries, envelopes, quotes batching, OCC, chains, secrets).
