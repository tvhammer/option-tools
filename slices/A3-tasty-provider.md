# Slice A3: real market-data provider for `manage.py evaluate`

Read `slices/_CONVENTIONS.md`, `tasty_client.py` (A2), `legs.py` (A1), and in `manage.py` the functions `fetch_market_data`, `MARKET_DATA_PROVIDERS`, `credit_to_close`, `run_evaluation`.

## Goal
`manage.py evaluate --mode paper|live` uses real tastytrade quotes (production, read-only profile) to evaluate open trades, and fails loudly on bad data.

## Design

### New module `tasty_provider.py`
- `class QuoteRules` (dataclass): `max_age_seconds: int = 900`, `max_spread_abs: float = 0.50`, `check_age: bool = True`. Loaded from a new config section:
```yaml
quotes:
  max_age_seconds: 900
  max_spread_abs: 0.50
  check_age: true
```
  Add `quotes` and its keys to the allowed sets in `config.py` (+ validation: positive numbers) and to `config.yaml`.
- `make_provider(client: TastyClient | None = None, now: Callable[[], datetime] = utc_now)` returns a function with the signature already used by `MARKET_DATA_PROVIDERS`: `provider(symbol, legs, mode, config) -> {"underlying_price": float, "option_mids": {leg_key: float}, "option_deltas": {}}`.
  - Build the client lazily from `credentials.tasty_credentials(credentials.profile_for("data", mode))` when none is injected (production read-only profile in ALL modes).
  - One `get_quotes` call: the underlying equity + each leg's OCC symbol built from `leg["expiry"]` (use `legs.leg_key(leg)` as the dict key; never re-derive keys yourself).
  - Underlying price = quote `mark`, else `mid`, else `last`; none -> `MarketDataError`.
  - Option mid = `(bid+ask)/2`; validate per leg, raising `manage.MarketDataError` with the symbol in the message when: quote missing; ask is None or <= 0; bid is None or < 0; ask < bid (crossed); `ask - bid > max_spread_abs`; with `check_age`, `updated-at` older than `max_age_seconds` (or missing).
  - Legs without `expiry` -> `MarketDataError("leg has no expiry; save the trade with expiry")`.
- At the bottom of `manage.py` (or in a function `register_default_providers()` called from `main()` and from `run_evaluation` when `config["broker"] == "tastytrade"`): register `MARKET_DATA_PROVIDERS["tastytrade"] = tasty_provider.make_provider()`. Keep it lazy so importing `manage` never touches credentials or the network. Tests that need an empty registry must still work (see `tests/test_no_mock.py`).
- `fetch_market_data` stays strict: unknown broker -> `MarketDataError` as today.

### Behavior notes
- Quotes may be stale outside market hours; with `check_age: true` this makes `evaluate` fail loudly then. That is intended until slice E1 adds market-hours handling. Do not weaken the check.
- Group by underlying: `run_evaluation` calls the provider once per trade; do not add caching in this slice.

## Out of scope
Positions/reconciliation, order code, market-hours logic, streaming, greeks (`option_deltas` stays `{}`).

## Tests (`tests/test_tasty_provider.py`, fake client; no network)
- Happy path: iron condor legs with expiries -> mids and underlying as expected; keys equal `legs.leg_key(leg)`.
- Each rejection case above raises `MarketDataError` (missing quote, ask 0, bid None, crossed, wide spread, stale, no expiry) and the message names the symbol.
- Uses `profile_for("data", "paper")` and `("data", "live")` -> both `tasty-prod-read` (assert via a fake credentials factory).
- Config validation: `quotes` keys accepted, unknown key rejected, negative value rejected.
- Integration with `run_evaluation` (temp DB, injected provider): an open PUT_CREDIT_SPREAD whose mids imply the profit target yields a `close_profit` action; a stale quote yields an entry in `errors` and no action.
- Importing `manage` does not import `requests` or call `credentials` (assert via `sys.modules`/mock).

## Acceptance
Tests pass; `manage.py evaluate` with no open trades still exits 0 without touching credentials.

## Manual check (for the human, do not run)
1. Enter a test trade (a real or imagined SPY put credit spread with legs including `expiry`) in the journal through a small script.
2. During US market hours: `venv/bin/python manage.py evaluate --mode paper --output text` -> positions checked 1, sensible numbers; compare the mids with the tastytrade app.
3. After hours it should exit 1 with a stale-quote error (expected).
