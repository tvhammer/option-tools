# Slice B2: exit code scheme and alert plumbing for reconciliation problems

Read `slices/_CONVENTIONS.md`, `manage.py` (`main`, `run_evaluation`), and `~/ai-workflows/trading/options-manage.yaml` (READ ONLY: you may not edit it; see the manual step below).

## Goal
`manage.py evaluate` reports reconciliation problems with a distinct exit code so the stepflow workflow can alert on them instead of silently doing nothing.

## Design
Exit code of `evaluate` (documented in the module docstring and in `NEXT_STEPS.md`):

| Condition | Code |
|---|---|
| at least one action proposed | 2 (issues/errors for other trades are still printed to stderr and included in the JSON) |
| no actions, and `errors` non-empty | 1 |
| no actions, no errors, `issues` non-empty | 3 |
| none of the above | 0 |

- `--output json` always includes keys `actions, alerts, errors, issues, positions_checked, timestamp, mock`.
- On exit 3 print each issue as `ISSUE: <code> trade=<id> <symbol>: <detail>` to stderr.
- Add `exit_code_for(result: dict) -> int` (pure) and use it in `main`; test it exhaustively.
- Add a CLI subcommand `manage.py explain-exit` that prints the table above (documentation for humans).

## Manual step for the human (put these exact YAML steps into your final reply, DO NOT edit ai-workflows)
In `trading/options-manage.yaml`, after `evaluate_failed` add:
```yaml
  - id: evaluate_mismatch
    when: evaluate.exitCode == 3
    run: |
      logger -t options-manage -p user.err "run {{RUN_ID}}: position reconciliation problem, NO action taken. See {{STATE_DIR}}/{{RUN_ID}}.err and .json"
```
and mention in the header comment that exit 3 means a reconciliation problem.

## Out of scope
Any change to the rule evaluators, any workflow file edit, notifications.

## Tests
`tests/test_exit_codes.py`: table-driven test of `exit_code_for` (all 4 rows, plus actions+issues -> 2, errors+issues without actions -> 1); a subprocess test is NOT needed.

## Acceptance
Tests pass; existing tests unchanged in meaning.
