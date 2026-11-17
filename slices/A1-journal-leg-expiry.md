# Slice A1: journal leg expiry, unit rules, leg keys, trade DTE

Read `slices/_CONVENTIONS.md`, `NEXT_STEPS.md` (Phase A step 1) and `STRATEGY_PLAN.md` (calendar section) first.

## Goal
Every leg of an open trade carries its own expiration date, so option symbols can be built, DTE is computed from real expirations, and same-strike calendar spreads (two expiries, same strike) are representable. Make the units convention explicit.

## Design

### Leg format (inside `Trade.legs_json`)
```
{"type": "put"|"call", "strike": 440.0, "action": "sell"|"buy", "qty": 1, "price": 1.20, "expiry": "2026-11-27"}
```
- `expiry` is an ISO date string. Required for every leg of an OPEN trade (exit_date is None) when saving.
- Closed legacy trades without `expiry` must still load and save unchanged (validation applies only when `exit_date` is None).

### New module `legs.py` (pure functions, no I/O)
- `validate_legs(legs: list[dict]) -> None` raising `ValueError` with a precise message: allowed type/action values, strike > 0 number, qty positive int, `expiry` parses with `date.fromisoformat`, no two legs identical in (type, strike, expiry), at most 4 legs.
- `normalize_leg(leg: dict) -> dict`: strike -> float, qty -> int, keys lowercased values.
- `leg_key(leg: dict) -> str`: `"{T}{strike:g}@{expiry}"` with T = `P`/`C`, e.g. `P440@2026-11-27`; if `expiry` is missing (legacy) return `"{T}{strike:g}"`. `strike:g` makes `440` and `440.0` produce the same key (this was an existing bug source).
- `leg_dte(leg, today: date) -> int` and `trade_dte(legs, today: date) -> int` = days to the NEAREST expiry among legs (for a calendar this is the front leg).
- `front_and_back(legs) -> tuple[list, list]` splitting legs by nearest expiry vs later expiry (used later by the calendar slice; just implement and test).

### Wiring
- `trade_journal.save_trade`: when `trade.exit_date is None`, parse `legs_json`, run `normalize_leg` on each, `validate_legs`, and store the normalized JSON. Invalid -> raise `ValueError` (nothing written).
- `manage.py`: remove the local `leg_key` and import it from `legs.py` (keep the name `manage.leg_key` available so existing tests keep working). `credit_to_close` keeps its signature.
- `manage.py`: where the remaining DTE is computed (`evaluate_position` and `run_evaluation`), use `legs.trade_dte(legs, date.today())` when all legs have `expiry`, otherwise fall back to the existing `trade.dte - days_since_entry` logic.
- `trade_journal.py` `__main__` example and docstrings: add `expiry` to the sample legs; state the units rule from `_CONVENTIONS.md` in the module docstring (prices per share; `buying_power_used` in USD; dollars = price x 100 x qty). Fix the misleading comment "1.30 = $130/contract" on `credit_received` accordingly (do not change stored values).
- Add a tiny helper `trade_journal.legs_of(trade) -> list[dict]` returning parsed legs ([] if none).

## Out of scope
Market data, tastytrade code, rule changes, schema changes (legs are inside the existing JSON column), migrating old rows.

## Tests (new file `tests/test_legs.py`, plus adjustments)
- `leg_key` equality for 440 vs 440.0; includes expiry; calendar front/back with same strike give DIFFERENT keys.
- `validate_legs` rejects: missing expiry, bad date, strike <= 0, qty 0, duplicate legs, 5 legs, unknown type/action.
- `trade_dte` for a condor (all same expiry) and for a calendar (returns the front DTE) with a fixed `today`.
- `save_trade` (temp DB) rejects an open trade without leg expiry, accepts a closed legacy trade without expiry, and stores normalized floats.
- `run_evaluation` uses expiry-based DTE (inject a provider via `manage.MARKET_DATA_PROVIDERS` like `tests/test_no_mock.py` does and assert a `CLOSE_DTE` action appears when the nearest expiry is within `close_dte`).
- Update `tests/test_no_mock.py` helper trades to include `expiry`.

## Acceptance
- All tests pass; `venv/bin/python manage.py evaluate --mode paper --output text` still exits 0 with no open trades.
- No behavior change for closed legacy rows.
- Grep shows a single definition of `leg_key`.
