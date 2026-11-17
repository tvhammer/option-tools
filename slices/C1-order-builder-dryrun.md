# Slice C1: closing-order builder and dry-run (nothing is submitted)

Read `slices/_CONVENTIONS.md`, the multi-leg spreads guide and the idempotency guide linked there, `tasty_client.py`, `legs.py`, and `manage.py` (action types and `params`).

## Goal
Turn a management action on a journal trade into a validated tastytrade closing order payload and run it through the broker's DRY-RUN endpoint. This slice must not contain any code that submits a real order.

## Design

### New module `tasty_orders.py`
- `build_close_order(trade: Trade, net_price: float, *, action_tag: str, tick: float = 0.01) -> dict` (pure). Returns the JSON body for `POST /accounts/{n}/orders/dry-run` per the guide:
  - one leg per journal leg; for a leg with action `sell` (we are short) -> `"Buy to Close"`, for `buy` (we are long) -> `"Sell to Close"`; `instrument-type: "Equity Option"`; `symbol` = OCC via `tasty_client.occ_symbol`; `quantity = leg.qty * trade.quantity`.
  - `"order-type": "Limit"`, `"time-in-force": "Day"`.
  - `net_price` is the PER-SHARE net amount, positive; `price-effect` is `"Debit"` when the closing trade costs money, `"Credit"` when it brings money. The caller passes `net_price` as a signed value: positive = we pay (debit), negative = we receive (credit); the payload carries `abs()` rounded to the tick, rounded in the direction that makes filling MORE likely (debit: up, credit: down).
  - `"external-identifier": f"ot-{trade.id}-{action_tag}"` (stable per trade and action so a retry can find an existing order; max 36 chars, assert it).
  - Refuse (ValueError) when the trade has no id, no expiry on a leg, more than 4 legs, or is already closed.
- `net_close_price(trade, mids: dict[str, float]) -> float` (pure): signed per-share net: sum of mids of short legs minus sum of mids of long legs (positive = debit to close, negative = credit). Uses `legs.leg_key`.
- `round_to_tick(price: float, tick: float, direction: "up"|"down") -> float` using `decimal` to avoid float artefacts.
- `tick_for(symbol: str, price: float, config: dict) -> float`: `0.01` for symbols in new config key `penny_symbols` (default `[SPY, QQQ, IWM]`), otherwise `0.05` if price >= 3.00 else `0.01`. Add `penny_symbols` to config + validation.
- `class OrderDryRun` result dataclass: `ok: bool`, `buying_power_effect: str|None`, `fees: float|None`, `warnings: list[str]`, `errors: list[str]`, `raw: dict`.
- `dry_run(client: TastyClient, account_number: str, payload: dict) -> OrderDryRun`: POST to `/accounts/{n}/orders/dry-run`; a 422 returns `ok=False` with the error list (do not raise); other failures raise. Parse defensively (look under `data` and `data["order"]`); keep the full raw response.

### Environment guard
`dry_run` must refuse when `client`'s credentials env does not match an explicit `expected_env` parameter ("sandbox" for paper, "production" for live) - raise `ValueError`.

## Out of scope
Submitting, cancelling, polling orders, journal updates, price walking, wiring into `run_execute`.

## Tests (`tests/test_tasty_orders.py`, fake transport only)
- Iron condor close payload: 4 legs with the correct close actions, symbols, quantity scaling (trade.quantity 2), price effect Debit, rounding up to tick; payload has no extra/unexpected keys.
- Calendar (front short, back long) closes for a Credit when back > front.
- `external-identifier` stable and <= 36 chars; refuses trades without id / expiry / already closed.
- `net_close_price` signs; `round_to_tick` cases including 0.05 ticks and directions.
- `dry_run`: success fixture parsed; 422 -> `ok=False` with errors; wrong `expected_env` raises; the URL ends with `/orders/dry-run` and NOT `/orders` (assert explicitly).
- A test that greps the source of `tasty_orders.py` and asserts there is no function that POSTs to a URL ending in `/orders` (guards the "no submission in this slice" rule).

## Acceptance
Tests pass; no network.

## Manual check (for the human, do not run)
A small script that builds the payload for a sandbox position and prints the dry-run result; verify `buying-power-effect` and warnings are plausible.
