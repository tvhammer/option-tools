# Conventions for every slice

Read this fully before planning or coding. It overrides anything vague in a slice.

## Project facts
- Python 3.14 in `venv/` -> run everything with `venv/bin/python`. Tests: `venv/bin/python -m unittest discover -s tests` (stdlib `unittest` only, no pytest).
- HTTP uses `requests` (already installed). Do NOT add dependencies. Do not edit `requirements.txt` unless the slice says so.
- Modules: `manage.py` (rule engine, `run_evaluation`, `run_execute`, `run_journal_sync`, `MARKET_DATA_PROVIDERS`, `fetch_market_data`, `leg_key`, `credit_to_close`, `reconcile_positions`),
  `trade_journal.py` (`Trade` dataclass, sqlite, `save_trade/get_trade/get_open_trades`), `credentials.py` (`tasty_credentials(profile)`, `profile_for(role, mode)`),
  `config.py` + `config.yaml` (validated config; unknown keys are errors, so every new config key must be added to the allowed-key sets in `config.py` and covered by a test),
  `tests/`. Do not use a file named `secrets.py` (it shadows the stdlib).
- `manage.py evaluate` exit codes today: 0 nothing to do, 2 actions proposed, 1 error (no usable data and no actions).
- Existing tests must keep passing. Never weaken or delete an existing test; fix the code instead (tests that encode a changed contract may be updated, and you must say so in your final reply).

## Hard prohibitions
- NEVER read, print, log, copy or reference the contents of anything under `~/.config/option-tools/`, nor environment variables holding secrets. Tests must use a temp dir via `OPTION_TOOLS_SECRETS_DIR` and fake credentials.
- No real network calls in tests or in code you run. HTTP code takes an injectable transport; tests pass a fake. You may fetch tastytrade DOCUMENTATION with `curl -sL <url>.md` while planning (see links in the slice); you may not call the tastytrade API.
- No code path may send an order to a broker unless the slice is C2 or C3, and then only through the executor class, only to the sandbox in tests (fake transport) - never from tests against a real endpoint.
- Do not run `git commit`, do not edit files outside `~/option-tools`, do not touch `trade_journal.db` (tests use a temp DB by patching `trade_journal.DB_PATH`, see `tests/test_no_mock.py`).
- No fallback to mock, random or guessed data outside the explicit `--mock` flag. Missing or inconsistent data -> raise a clear error (`MarketDataError` or a new specific exception). No `except Exception: pass`, no silent defaults of 0.

## Units (important, already confusing in the code)
- Option PRICES are per-share as quoted (e.g. credit 1.30). Fields: `credit_received`, `debit_paid`, `max_loss`, `max_profit`, `realized_pnl`, leg `price`. Dollars = price x 100 x contracts.
- `buying_power_used` is in USD for the whole position.
- Never mix them; name variables with the unit when it is not obvious (`debit_per_share`, `fees_usd`).

## Style
- Type hints; small pure functions where possible; side effects (HTTP, files, clock) injected as parameters (`transport`, `now`) so tests are deterministic.
- Money/price rounding: use `round(x, 2)`; tick handling only where a slice says so.
- Comments only for non-obvious "why". No emojis in code or docs.

## tastytrade documentation (read the relevant page instead of guessing field names)
- Auth: https://developer.tastytrade.com/docs/authentication/oauth2.md
- Market data: https://developer.tastytrade.com/docs/concepts/market-data.md
- Positions: https://developer.tastytrade.com/docs/concepts/positions-and-balances.md
- Multi-leg orders: https://developer.tastytrade.com/docs/guides/multi-leg-spreads.md
- Order lifecycle / statuses: https://developer.tastytrade.com/docs/concepts/order-lifecycle.md
- Idempotency: https://developer.tastytrade.com/docs/guides/idempotency-and-retries.md
- Rate limits: https://developer.tastytrade.com/docs/guides/rate-limits-and-backoff.md
- Index of all pages: https://developer.tastytrade.com/llms.txt
Facts already verified: base URLs `https://api.tastyworks.com` (production) / `https://api.cert.tastyworks.com` (sandbox); every request needs header `User-Agent: product/version`;
`POST /oauth/token` with JSON `{grant_type: "refresh_token", refresh_token, client_secret}` returns `access_token` valid 15 minutes; JSON keys are dasherized; numbers arrive as strings;
`GET /market-data/by-type?equity=A,B&equity-option=SYM1,SYM2` (max 100 symbols) returns `data.items[]` with `symbol,bid,ask,mid,mark,last,updated-at`; the sandbox does NOT serve market data (HTTP 502);
`GET /option-chains/{sym}/nested` -> `data.items[0].expirations[]` with `expiration-date`, `days-to-expiration`, `strikes[]` each with `strike-price,call,put`;
`GET /customers/me/accounts` -> `data.items[].account.account-number`.
OCC option symbol format: root padded with spaces to 6 chars + `YYMMDD` + `C|P` + strike*1000 as 8 digits, e.g. `SPY   261124C00740000`.

## Final reply of the implement step must contain
1. List of files created/changed. 2. Test count before/after. 3. Anything you were unsure about or deviated from. 4. The "Manual check" commands for the human (do not run them).
Also append one line to `NEXT_STEPS.md` under a heading `## Slice log` (create it at the end of the file if missing): `- <slice id>: <one-line summary>`.
