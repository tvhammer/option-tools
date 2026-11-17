# Slice D1: same-strike calendar spread management rules

Read `slices/_CONVENTIONS.md`, `STRATEGY_PLAN.md` ("Strategy 4 (candidate): Calendar Spread"), `legs.py` (A1: `front_and_back`, `leg_key`, `trade_dte`), `manage.py` (the three existing evaluators, `STRATEGY_EVALUATORS`, `credit_to_close`, `Position`), and `config.py`.

## Goal
`manage.py` can evaluate open `CALENDAR_SPREAD` trades: long back-month and short front-month option at the SAME strike, same right (both calls or both puts). Rules only; entry scoring is out of scope.

## Design

### Journal convention (document in `trade_journal.py` docstring and in the evaluator docstring)
- `strategy = "CALENDAR_SPREAD"`; exactly 2 legs: front `sell`, back `buy`, same type, same strike, different expiry (front earlier). Enforce in `legs.validate_calendar(legs)` (new, with tests) and call it from `save_trade` for this strategy.
- It is a DEBIT trade: store the entry debit per share as a NEGATIVE `credit_received` (e.g. `-0.85` for a $0.85 debit). `max_loss` = the debit (positive per share), `max_profit` may be None.
- Trade `quantity` scales as for other strategies.

### Value function (do not reuse `credit_to_close`, it rejects negatives)
`calendar_value(legs, mids) -> float` = back mid minus front mid (what we receive when we close), per share. Missing quote -> `MarketDataError`; a value `< 0` -> `MarketDataError("negative calendar value: stale or crossed quotes")`.
`evaluate_position` must dispatch calendar trades to a value computed with this function; keep `Position.current_credit` semantics for other strategies untouched (add a separate field `calendar_value: float | None` to `Position`).

### Rules (config section `calendar_spread`, per-trade overrides in `rules_json` with the same key names)
Let `debit = -trade.credit_received`, `value` = `calendar_value`.
1. Profit target: `value >= debit * (1 + profit_target_pct_of_debit)` -> `CLOSE_PROFIT`.
2. Stop loss: `value <= debit * (1 - stop_loss_pct_of_debit)` -> `CLOSE_STOP`.
3. Front expiry: front DTE (`legs.trade_dte`) `<= front_close_dte` -> `CLOSE_DTE` (urgent). This closes the WHOLE spread (rolling is a later phase).
4. Back expiry safety: back DTE `< min_back_dte` -> `CLOSE_DTE` with reason mentioning the back leg.
5. Tent break: `abs(underlying - strike) / strike > tent_break_pct` -> `CLOSE_BREACH`.
Priority = order above (first hit wins, as in the other evaluators). Action params: `{'type': 'close', 'limit_price': round(-value, 2)}` (negative = credit received, matching the signed convention used by `net_close_price` in C1: positive means we pay).

Defaults in `config.yaml` (and allowed keys in `config.py` with validation: ratios between 0 and 1 where applicable, DTE ints >= 0):
```yaml
calendar_spread:
  profit_target_pct_of_debit: 0.20
  stop_loss_pct_of_debit: 0.50
  front_close_dte: 2
  tent_break_pct: 0.03
  min_back_dte: 14
```
Register `CALENDAR_SPREAD` in `STRATEGY_EVALUATORS`. Update `STRATEGY_PLAN.md` strategy-4 rule block to match the final key names (small doc edit).

## Out of scope
Entry scoring, rolling the front leg, IV term structure, order code, B1/C changes beyond what is needed to keep tests passing.

## Tests (`tests/test_calendar.py`)
- One test per rule trigger plus "no action" in the middle; priority when two rules hold (profit and DTE).
- `calendar_value` errors: missing quote, negative value.
- `validate_calendar` rejects: same expiry, different strikes, mixed rights, wrong actions, 3 legs.
- Per-trade `rules_json` overrides beat config; config validation rejects unknown/invalid keys.
- Reconciliation (B1) matches a calendar (two expiries) - add one case if B1 already exists in the tree, otherwise skip.

## Acceptance
Tests pass; the three existing evaluators behave exactly as before (their tests untouched).
