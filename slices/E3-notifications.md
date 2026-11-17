# Slice E3: push notifications (Telegram) and a heartbeat helper

Read `slices/_CONVENTIONS.md`, `credentials.py`, `config.py`, and `~/ai-workflows/trading/options-manage.yaml` (read only: it currently uses `logger -t options-manage` for alerts).

## Goal
A small `notify.py` CLI that sends a short message to Telegram (or falls back to syslog), so the workflow can later replace `logger` calls with `notify.py`. The workflow edit itself is a manual step.

## Design

### Credentials profile
Extend `credentials.py` with `notify_credentials() -> NotifyCredentials(bot_token, chat_id)` reading `~/.config/option-tools/notify.env` (keys `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) using the same permission checks (`load_profile`) and a redacted `repr`. Missing file or empty values -> `SecretsError` (the CLI treats this as "Telegram not configured" and falls back to syslog). Add the profile name to the documented profile list in the module docstring.

### `notify.py`
- `send_telegram(text: str, creds, transport) -> None`: POST `https://api.telegram.org/bot<token>/sendMessage` JSON `{chat_id, text}`; reuse the `Transport`/`Response` types from `tasty_client.py` (import them; do not duplicate); truncate to 3500 chars; the bot token must never appear in logs or exception messages (the URL contains it: build error messages without the URL).
- `notify(level: str, text: str, *, creds_loader=notify_credentials, transport=requests_transport, syslog=default_syslog) -> str`: level in `info|warning|error`; prefix `[options-manage][LEVEL]`; always writes to syslog via `logger -t options-manage -p user.<level>` (use `subprocess.run([...])`, no shell); tries Telegram; returns `"telegram"`, `"syslog-only"` (not configured) or `"syslog-fallback"` (Telegram failed). Never raises because of notification problems.
- CLI: `python notify.py --level error "message"` or message from stdin when no positional text; exit code 0 always except for usage errors (2). Deduplicate: a `--dedupe-key KEY` option suppresses repeats of the same key within `--dedupe-minutes` (default 60) using a small JSON file in `~/.local/state/options-manage/notify-dedupe.json` (path overridable by env `OPTION_TOOLS_STATE_DIR` for tests).

## Manual steps for the human (list in the final reply, DO NOT edit ai-workflows)
1. Create a Telegram bot, put token and chat id into `~/.config/option-tools/notify.env` (mode 600).
2. Replace the `logger -t options-manage ...` lines in `trading/options-manage.yaml` with `"{{TOOLS_DIR}}/venv/bin/python" "{{TOOLS_DIR}}/notify.py" --level error "..."` (provide the exact replacement snippets for each alert step in the reply).
3. Add a daily heartbeat: a second timer is NOT part of this slice; mention it as a follow-up.

## Out of scope
Email, other channels, editing workflows or deploy files, any trading logic.

## Tests (`tests/test_notify.py`; fake transport, fake syslog, temp dirs)
- Telegram success path (URL/body shape; token only in the URL); syslog always called with the right level.
- Not configured -> `syslog-only`; transport raising or HTTP 500 -> `syslog-fallback`; no exception escapes.
- Bot token never present in any returned/raised message or in the syslog text.
- Truncation; dedupe window (same key suppressed, different key sent, expiry after the window with an injected clock).
- Credentials: wrong file mode refused; empty values refused; `repr` redacted.

## Acceptance
Tests pass; no network.
