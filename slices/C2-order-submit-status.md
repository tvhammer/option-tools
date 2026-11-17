# Slice C2: submit, status, cancel (idempotent), sandbox-guarded

Read `slices/_CONVENTIONS.md`, `tasty_orders.py` (C1), the order lifecycle and idempotency docs linked there (fetch them; use the exact status names and endpoints from the docs, do not guess), and `tasty_client.py`.

## Goal
An `OrderExecutor` that can safely submit a closing order built in C1, look up its status, and cancel it. It is the ONLY place in the codebase that sends orders.

## Design

### `class OrderExecutor` (in new module `tasty_executor.py`)
`OrderExecutor(client: TastyClient, account_number: str, mode: "paper"|"live", *, confirm_live: bool = False, kill_switch: Callable[[], bool])`
Constructor guards (each raises `ExecutionRefused`, tested):
- `mode == "paper"` requires `client` credentials env == `sandbox`.
- `mode == "live"` requires env == `production` AND `confirm_live is True`.
- `kill_switch()` must be False at construction AND is re-checked immediately before every submit/cancel (STOP file at `Path(__file__).parent / "STOP"` is the default implementation `stop_file_present()`).

Methods:
- `find_order(external_id: str) -> dict | None`: look in live orders (`GET /accounts/{n}/orders/live`) and, if not found, in today's/yesterday's order history (see idempotency guide) for an order with that `external-identifier`.
- `submit_close(payload: dict, *, trade: Trade) -> SubmittedOrder`:
  1. Re-validate: payload legs are all closing actions (`Buy to Close` / `Sell to Close`); quantity does not exceed the journal quantity; symbols match the trade's legs. Violation -> `ExecutionRefused`.
  2. `find_order(external-identifier)`; if an active or filled order already exists return it with `already_existed=True` (never send a second one).
  3. Run the dry-run (C1 `dry_run`); if `ok` is False -> `ExecutionRefused` with the broker errors.
  4. `POST /accounts/{n}/orders` with the same payload. On timeout/5xx do NOT resend blindly: call `find_order` again and only resend if it is definitely absent, at most once.
  5. Return `SubmittedOrder(order_id, external_id, status, raw, already_existed)`.
- `get_status(order_id) -> OrderStatus` (dataclass with `status`, `filled_quantity`, `remaining_quantity`, `avg_fill_price: float|None` per share signed like `net_close_price`, `fees: float|None`, `is_terminal: bool`, `raw`). Map the broker's status strings per the lifecycle doc; unknown status -> `is_terminal=False` and keep raw.
- `cancel(order_id) -> None`: `DELETE /accounts/{n}/orders/{id}`; treat "already terminal" responses as success.

### Recording
After a successful submit, append the broker order id to `trade.ib_order_ids` (rename not needed; document that the column now holds broker order ids as a JSON list of strings) via `save_trade`; do this inside `submit_close` only when `trade.id` is set. A failure to save must raise (the order exists at the broker: include its id in the message).

## Out of scope
Price walking, polling loops, realized P&L, wiring into `run_execute` (slice C3). Do not call any of this from `manage.py` yet.

## Tests (`tests/test_tasty_executor.py`, fake transport; use a temp journal DB)
- Constructor guards: paper with production creds, live without confirm, live with sandbox creds, kill switch on -> all refused; kill switch turning on between construct and submit -> refused.
- `submit_close` happy path: exactly one POST to `/orders` after one dry-run POST; journal row got the order id.
- Idempotency: existing order with the same external id (live list) -> no POST; found only in history -> no POST.
- Timeout on POST then `find_order` finds it -> no second POST; timeout and not found -> exactly one retry.
- Dry-run rejection -> `ExecutionRefused`, no POST to `/orders`.
- Non-closing action or oversized quantity in the payload -> refused before any HTTP call.
- `get_status` mapping for each status fixture (working, partially filled, filled, rejected, cancelled, unknown); `cancel` on an already filled order is not an error.
- No secrets in messages (reuse the helper approach from A2 tests).

## Acceptance
Tests pass; grep shows `/orders"` POSTs only in `tasty_executor.py`.

## Manual check (for the human, do not run)
Sandbox only: write a small throwaway script that opens a position through the sandbox UI/API yourself, then submits a closing order with `OrderExecutor(mode="paper")`, prints status, cancels it. The sandbox resets every 24 h.
