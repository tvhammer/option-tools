# Slices

Each file is one input for `dev/feature-flow` (`stepflow run dev/feature-flow --input-file slices/<file>.md ...`).
Always read `_CONVENTIONS.md` together with a slice (every slice tells the agent to do so).
Slices build on each other: run them in order, one at a time, and **commit after each approved slice**
(the flow's diff step uses `git diff HEAD`).

## Slice 0 (manual, before anything else)
```
cd ~/option-tools
printf 'venv/\n__pycache__/\n*.pyc\n*.db\n*.log\n*.swp\nSTOP\n.stepflow/\nwatchdog.pid\n' > .gitignore
git add -A && git commit -m "Baseline: management engine, tests, plans"
```
Check `git status` is clean and `venv/bin/python -m unittest discover -s tests` passes.

## Run command
```
cd ~/option-tools
export STEPFLOW_TEMPLATES=~/ai-workflows
stepflow run dev/feature-flow --input-file slices/<file>.md \
  -p TEST_CMD="venv/bin/python -m unittest discover -s tests"
```
At the plan gate you may edit `.stepflow/runs/<id>/plan-*.md` before `--approve`.
If the plan has more than ~8 implementation steps, `--revise "split this slice"`.

## Order and dependencies

| # | File | Needs | Notes |
|---|---|---|---|
| A1 | `A1-journal-leg-expiry.md` | 0 | leg format, units, leg_key, trade DTE |
| A2 | `A2-tasty-client.md` | A1 | auth, HTTP, OCC symbols, quotes (fake transport tests) |
| A3 | `A3-tasty-provider.md` | A1, A2 | real market data for `evaluate`; manual live check afterwards |
| B1 | `B1-positions-reconcile.md` | A2 | positions fetch + reconciliation logic |
| B2 | `B2-exit-codes-alerts.md` | B1 | exit code scheme; workflow change is a MANUAL step in ai-workflows |
| C1 | `C1-order-builder-dryrun.md` | A1, A2 | order payloads + dry-run only, nothing is submitted |
| C2 | `C2-order-submit-status.md` | C1 | submit/status/cancel with idempotency, sandbox only |
| C3 | `C3-fills-journal-execute.md` | C2, B1 | price walk, fills to journal, wires `run_execute` |
| D1 | `D1-calendar-evaluator.md` | A1 | same-strike calendar management rules |
| E1 | `E1-market-hours.md` | A3 | market calendar; evaluate skips when closed |
| E2 | `E2-scheduler-deploy.md` | E1 | systemd timer files; retire watchdog daemon |
| E3 | `E3-notifications.md` | - | Telegram notifier + credentials profile |

Phase F (live, close-only) is not a slice: it is a checklist in `NEXT_STEPS.md`, done by hand.

## After each slice (human checklist)
1. Read the diff yourself (for B/C/D slices, read every line touching orders or rules).
2. `venv/bin/python -m unittest discover -s tests` is green.
3. Do the slice's "Manual check" if it has one (it needs your credentials, so the agent must not run it).
4. `git commit`.
