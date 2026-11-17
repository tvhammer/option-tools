# Slice E2: systemd timer for the stepflow workflow; retire the watchdog daemon

Read `slices/_CONVENTIONS.md`, `watchdog.py`, `option-tools-watchdog.service`, `~/ai-workflows/trading/options-manage.yaml` (read only), and `NEXT_STEPS.md` (Phase E).

## Goal
One scheduler: a user-level systemd timer runs the stepflow workflow every 15 minutes. The old self-scheduling daemon is moved out of the way. Nothing is enabled or started by this slice; it only produces files and docs.

## Design

### New directory `deploy/`
- `options-manage.service` (user unit, `Type=oneshot`):
  - `WorkingDirectory=%h/option-tools`
  - `Environment=STEPFLOW_TEMPLATES=%h/ai-workflows`
  - `ExecStart=/bin/bash -lc 'exec stepflow run trading/options-manage --cwd %h/option-tools -p RUN_ID=$(date +%%Y%%m%%d-%%H%%M%%S)'` (`%%` escaping is required inside unit files; verify with `systemd-analyze verify`).
  - `TimeoutStartSec=600`, `Nice=10`, no restart.
  - Must NOT contain credentials and must NOT pass `MODE=live` or `LIVE_OK` (paper by default).
  - Do not add any STOP-file check to the unit (the workflow already handles STOP and logs it).
- `options-manage.timer`: `OnCalendar=Mon..Fri *:0/15`, `Persistent=false`, `AccuracySec=30s`, `RandomizedDelaySec=0`, `[Install] WantedBy=timers.target`. Comment that market hours/holidays/DST are handled by `manage.py` (slice E1), so the timer may fire around the clock on weekdays.
- `deploy/README.md`: exact install commands (`mkdir -p ~/.config/systemd/user`, copy/symlink, `systemctl --user daemon-reload`, `systemctl --user enable --now options-manage.timer`), how to see logs (`journalctl --user -u options-manage` and `journalctl -t options-manage`), how to stop (`systemctl --user disable --now options-manage.timer`, `touch ~/option-tools/STOP`), how to run once by hand, and the note that user timers run only while the user session/lingering is active (`loginctl enable-linger $USER` to run without login).

### Retire the daemon
- Move `watchdog.py`, `option-tools-watchdog.service`, `watchdog.log`-related code into `legacy/` with `legacy/README.md` explaining why (duplicate scheduler, DST handling, STOP file reset, decommissioned login flow) and that it is unmaintained. Fix imports so `venv/bin/python -c "import manage"` and all tests still pass (no test may import the legacy module). Also move `tasty_chain.py` (decommissioned username/password flow) to `legacy/`.
- Remove references to `watchdog.py` from `QUICKSTART.md`/`README` docs (replace with a pointer to `deploy/README.md`).

## Out of scope
Enabling/starting any unit, notifications, editing the workflow.

## Tests / checks
- `tests/test_deploy_files.py`: parse the unit files with `configparser` (strict=False) and assert: timer has the expected `OnCalendar`, service is `oneshot`, `ExecStart` mentions `trading/options-manage` and does not mention `MODE=live`, `LIVE_OK`, `TASTY`, `token`, `secret`; no file in `deploy/` contains the string `password`.
- If `systemd-analyze` exists on the machine, a test `skipUnless` runs `systemd-analyze verify` on the service (skip otherwise) and `systemd-analyze calendar "Mon..Fri *:0/15"` returns 0.
- All existing tests pass.

## Acceptance
`deploy/` complete, legacy files moved (git mv semantics: just move them), docs updated.

## Manual check (for the human, do not run)
Follow `deploy/README.md` on your machine; confirm `systemctl --user list-timers` shows the next run and that a run on a weekend or at night logs "market closed" (after E1).
