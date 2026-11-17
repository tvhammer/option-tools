# Slice B1: broker positions and journal reconciliation

Read `slices/_CONVENTIONS.md`, the positions doc linked there, `tasty_client.py` (A2), `legs.py` (A1), and `manage.py` (`reconcile_positions`, `run_evaluation`).

## Goal
Compare the journal's open trades with the real positions at the broker, leg by leg, and produce structured issues. No actions are ever derived from a trade that has an issue.

## Design

### Client additions (`tasty_client.py`)
- `positions(account_number: str) -> list[BrokerPosition]` from `GET /accounts/{n}/positions`.
- `@dataclass BrokerPosition`: `symbol: str` (OCC for options), `underlying: str`, `instrument_type: str`, `quantity: int` (always positive), `direction: "long"|"short"|"zero"` (from `quantity-direction`; do not trust the sign of `quantity`, see the doc note), `multiplier: int`.
- Keep only `Equity Option` positions with non-zero quantity in the helper `option_positions(...)`.
- Account selection helper `pick_account(client, configured: str | None) -> str`: configured value if given (and present), else the only account; more than one account and nothing configured -> `TastyApiError`-like `ValueError("set tastytrade.account in config.yaml")`. Add optional `tastytrade.account` to the config allowed keys + test. It is an account number, not a secret.

### Credentials
Add to `credentials.profile_for`: role `"positions"` -> paper: `tasty-sandbox`, live: `tasty-prod-read`. (Paper-mode positions live in the sandbox account.) Test it.

### New module `reconcile.py` (pure, no I/O)
```python
@dataclass
class Issue:
    code: str        # JOURNAL_ONLY | LEG_MISSING | QTY_MISMATCH | DIRECTION_MISMATCH | BROKER_ONLY | UNPARSABLE_LEG
    trade_id: int | None
    symbol: str
    detail: str

def reconcile(trades: list[Trade], positions: list[BrokerPosition], today: date) -> list[Issue]
```
Algorithm: for every journal trade expand legs to expected OCC symbols (`tasty_client.occ_symbol(trade.symbol, leg expiry, right, strike)`), expected quantity = `leg.qty * trade.quantity`, expected direction short for action "sell", long for "buy". Look each up in a dict of broker option positions; consume (subtract) matched quantities so two trades sharing a symbol are handled correctly. Report: no position at all for a trade -> `JOURNAL_ONLY` (once per trade); a missing leg -> `LEG_MISSING`; wrong size -> `QTY_MISMATCH`; wrong direction -> `DIRECTION_MISMATCH`; any broker option position not claimed by any open trade -> `BROKER_ONLY`. Journal legs without `expiry` -> `UNPARSABLE_LEG`. Equity (stock) positions are ignored.

### Wiring in `manage.py`
- New registry `POSITION_PROVIDERS: dict[str, Callable[[str, dict], list[BrokerPosition]]]` mirroring `MARKET_DATA_PROVIDERS`; register the tastytrade one lazily next to the market-data provider (module `tasty_positions.py`, tiny).
- `run_evaluation(mode, allow_mock=False, positions=None)`: obtain positions via the registry (strict: no provider -> add an entry to `errors`, do NOT continue with an empty list; with `allow_mock` use `[]` and skip reconciliation, marking `"reconciliation": "skipped (mock)"`).
- Issues are returned under a new key `issues` (list of dicts). Trades that appear in any issue are EXCLUDED from rule evaluation (no actions for them); put a line in `alerts` for each excluded trade.
- Remove the old stub `reconcile_positions` and the `ib_positions = []` line. Update text output (`--output text`) to print issues.
- Exit codes are handled in slice B2; in this slice keep the current exit logic unchanged.

## Out of scope
Exit code changes, workflow changes, order code, auto-import of broker positions into the journal.

## Tests (`tests/test_reconcile.py`, `tests/test_tasty_positions.py`)
- Clean match for an iron condor (4 legs) and for two trades sharing one underlying with different strikes.
- Each issue code is produced by one dedicated case; a trade with quantity 2 vs broker quantity 1 -> `QTY_MISMATCH`; direction swapped -> `DIRECTION_MISMATCH`; extra broker option -> `BROKER_ONLY`; calendar spread (same strike, two expiries) matches correctly.
- `positions()` parsing from an inline fixture shaped like the doc example, `Short` direction with positive quantity, zero positions dropped.
- `pick_account` cases.
- `run_evaluation` with injected providers: a trade with an issue yields no actions, appears in `issues`, and other trades are still evaluated.

## Acceptance
Tests pass; with no open trades `evaluate` still exits 0 without needing credentials.

## Manual check (for the human, do not run)
Open a small position in the matching environment, journal it with exact legs, run `evaluate --mode live` (read-only) and confirm no issues; then change one strike in the journal and confirm `LEG_MISSING`/`BROKER_ONLY`.
