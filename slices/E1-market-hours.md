# Slice E1: market calendar and "market closed" handling

Read `slices/_CONVENTIONS.md`, `manage.py` (`main`, `run_evaluation`), `tasty_provider.py` (A3, quote age check) and `watchdog.py` (`_is_market_hours`, to be replaced later).

## Goal
`evaluate` knows when the US options market is open (with DST, holidays and early closes) and exits 0 with a clear message when it is closed, so a scheduler can run every 15 minutes around the clock without errors or stale-quote failures.

## Design

### New module `market_hours.py` + data file `market_calendar.json`
- `market_calendar.json`: `{"timezone": "America/New_York", "regular": {"open": "09:30", "close": "16:00"}, "holidays": ["2026-01-01", ...], "early_close": {"2026-11-27": "13:00", ...}}` for 2026 and 2027. Fill the dates from your knowledge of the NYSE calendar AND clearly mark in your final reply that a human must verify every date against the official NYSE holiday schedule (do not claim certainty). NYSE 2026 holidays to include if correct: Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19, Jul 3 (Independence Day observed), Sep 7, Nov 26, Dec 25.
- `is_market_open(now: datetime) -> bool`, `market_session(now) -> Session(open: datetime, close: datetime) | None`, `next_open(now) -> datetime`; use `zoneinfo.ZoneInfo("America/New_York")`; naive datetimes raise `ValueError`; weekends closed; dates beyond the calendar's last year raise `CalendarExpired` (never silently treat as open or closed).
- A "grace" of `quotes_open_grace_minutes` (config, default 2) after the open before evaluating, so the first quotes exist; and evaluation also runs up to the close.

### Config
```yaml
market_hours:
  enforce: true
  open_grace_minutes: 2
```
(add allowed keys + validation + tests).

### Wiring
- `manage.py evaluate`: if `market_hours.enforce` and the market is closed (or inside the grace window) and `--force-market-hours` was not given -> print JSON `{"actions": [], "alerts": [], "errors": [], "issues": [], "positions_checked": 0, "message": "market closed"}` and exit 0 BEFORE any credentials/network are touched. `CalendarExpired` -> exit 1 with a clear error (so the calendar gets extended).
- `manage.py execute` and `journal-sync`: `execute` refuses outside market hours (`success: False`, error "market closed") unless `--force-market-hours`; `journal-sync` is allowed any time.
- Do not change `watchdog.py` in this slice.

## Out of scope
systemd units (E2), notifications (E3), per-instrument hours (assume SPY/QQQ/IWM-style equity options close at 16:00 ET for simplicity; note this limitation in the module docstring).

## Tests (`tests/test_market_hours.py`, no real clock: pass `now` explicitly)
- Open/closed at 09:29, 09:30, 15:59, 16:00 New York time on a normal Tuesday; the same instants expressed in UTC in winter (EST) and summer (EDT), including the weeks of the DST changes in March and November 2026.
- Weekend, a holiday, an early-close day at 12:59 and 13:00.
- `next_open` over a weekend and over a holiday.
- Date beyond calendar -> `CalendarExpired`.
- `evaluate` closed-market path exits 0 without importing/calling credentials (patch `credentials.tasty_credentials` to raise and assert it is never called); `--force-market-hours` bypasses.
- `execute` refuses when closed.

## Acceptance
Tests pass; with the market open the behavior of earlier slices is unchanged.
