# Slice C3: fill handling, journal update, wiring `run_execute` / `journal-sync`

Read `slices/_CONVENTIONS.md`, `tasty_executor.py` (C2), `tasty_orders.py` (C1), `manage.py` (`run_execute`, `execute_action_mock`, `run_journal_sync`), `trade_journal.py`, and `tasty_provider.py` (A3) for the quote handling.

## Goal
`manage.py execute` places real (sandbox, or later live) closing orders for the evaluated actions, walks the price toward the natural price within limits, and writes the journal from ACTUAL fills. `--mock` behaviour stays as is.

## Design

### Config (add to `config.py` allowed keys, `config.yaml`, tests)
```yaml
execution:
  fill_wait_seconds: 60        # wait per price step
  poll_seconds: 5
  steps: 3                     # number of price steps from mid toward natural
  max_slippage_pct_of_spread: 100   # never go beyond the natural price
  urgent_steps: 1              # for urgent actions (close_dte): jump straight to the natural price
```

### Price walk (pure function, `tasty_orders.py`)
`price_ladder(mid: float, natural: float, steps: int, tick: float, direction: "debit"|"credit") -> list[float]`: from mid to natural in `steps` equal increments, rounded to the tick toward "more fillable", duplicates removed, last element = natural. `natural` is computed from the legs' bid/ask (buy shorts at ask, sell longs at bid) - extend `tasty_provider` (or the order code) to expose `option_bids` and `option_asks` next to `option_mids` (keys via `legs.leg_key`); keep backward compatibility of the provider dict.

### `execute_action_real` (in `manage.py` or a new `execution.py`; prefer the new module)
For one action `{trade_id, action, params}`:
1. Load the trade; refuse if already closed.
2. Quotes via the provider -> mids, naturals; if bad data -> failed result (no order).
3. `ladder = price_ladder(...)` (urgent actions use `urgent_steps`).
4. For each price in the ladder: `submit_close` (first) or cancel + resubmit with a NEW payload (replace) - the external id suffix must differ per step (`...-s1`, `-s2`) but `find_order` must check all step ids so a crashed run never double-sends; wait `fill_wait_seconds` polling `get_status` every `poll_seconds` (injected `sleep`/`now`); stop at a terminal status.
5. Fully filled -> journal update: `exit_date`, `exit_reason` (map `ActionType` to the existing reason strings), `legs_exit_json` (fill price per leg when the broker provides it; otherwise omit), `debit_paid` (average fill, per share, signed like the journal expects: positive = paid), `realized_pnl = credit_received - debit_paid - fees_usd / (100 * quantity)` where `fees_usd` is the broker-reported fee total if available else `legs * commission_per_leg * quantity * 2` flagged in `exit_notes`, `days_held`, `return_on_capital = realized_pnl*100*quantity / buying_power_used` (guard against zero), `exit_notes` with order ids and ladder step reached.
6. Ladder exhausted without a fill -> cancel the last order, leave the journal untouched, result `failed` with reason `NOT_FILLED`.
7. Partially filled at any point -> cancel the remainder, leave the journal untouched, result `failed` with reason `PARTIAL_FILL` and the filled quantity; this needs a human. Never place further orders for that trade in the same run.

### `run_execute` / `run_journal_sync`
- `run_execute(run_id, mode, params_json, allow_mock=False, confirm_live=False)`: without `allow_mock`, build the executor (profile `profile_for("orders", mode)`) and call `execute_action_real` per action, sequentially. Keep the existing refusal when no backend can be constructed (credentials missing -> `success: False` with a clear error). Add CLI flag `--confirm-live` (only meaningful with `--mode live`). Max one action per trade per run; abort remaining actions if the kill switch appears.
- Result JSON per action: `{trade_id, action, status: filled|failed, reason, order_ids, avg_fill, fees_usd}`; `success` is true only if all actions filled.
- `run_journal_sync(run_id, mode)`: for open trades with stored order ids, query the broker (`get_status`); if an order is filled but the journal is not updated (crash recovery), apply the same journal update as step 5; return a summary. Without credentials -> `success: False` as today.

## Out of scope
Opening orders, rolls, notifications, changing rule evaluators, live trading enablement beyond the `--confirm-live` guard.

## Tests (`tests/test_execution.py`; fake executor/transport, fake clock, temp DB)
- `price_ladder` cases (debit and credit, 0.01 and 0.05 ticks, steps=1, mid==natural).
- Fill at step 1; fill at step 3; not filled -> cancelled, journal untouched; partial fill -> cancelled, journal untouched, reason `PARTIAL_FILL`.
- Urgent action jumps straight to natural.
- Journal values: realized_pnl arithmetic with broker fees and with estimated fees; quantity 2; zero buying power guard.
- Crash recovery: an order exists from an earlier step id -> no new order placed, journal sync closes the trade when it is filled.
- `run_execute` guards: no credentials -> failed; `--mode live` without `--confirm-live` -> refused; `--mock` still never touches the journal (existing tests).
- Kill switch set mid-run stops further orders.

## Acceptance
Tests pass; with `--mock` behaviour identical to before; no code path sends orders except via `OrderExecutor`.

## Manual check (for the human, do not run)
Sandbox: journal a position that exists in the sandbox account, run `evaluate`, then `execute` with `--mode paper` against actions that you provoke by temporarily lowering the profit target in the trade's `rules_json`; inspect journal row and sandbox order history. Remember the sandbox simulator fills from its own quotes, not production's.
