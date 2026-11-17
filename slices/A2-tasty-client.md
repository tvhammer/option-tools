# Slice A2: tastytrade HTTP client (auth, retries, symbols, quotes)

Read `slices/_CONVENTIONS.md`, the tastytrade docs linked there (auth, market data, rate limits) and `credentials.py`, `tasty_check.py` first.

## Goal
A small, well-tested client module `tasty_client.py` that every later slice uses for tastytrade REST access. No order code here.

## Design

### Transport (injectable)
```python
@dataclass
class Response:
    status: int
    json: dict | None
    text: str
    headers: dict
Transport = Callable[[str, str, dict, dict | None, dict | None], Response]
# (method, url, headers, params, json_body) -> Response
def requests_transport(method, url, headers, params, json_body) -> Response   # timeout 20 s, uses requests
```
Tests pass a fake transport. No test may use `requests_transport`.

### Class `TastyClient`
`TastyClient(creds: credentials.TastyCredentials, transport: Transport = requests_transport, now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep, user_agent: str = "option-tools/0.1")`
- `access_token() -> str`: POST `{base_url}/oauth/token` JSON `{grant_type, refresh_token, client_secret}`; cache the token; reuse until 60 s before expiry (use `expires_in` from the response if present, else 14 minutes); never log it. Non-200 -> `TastyAuthError` (message with status and a truncated body, NEVER the secrets).
- `request(method, path, params=None, json_body=None) -> dict`: adds `Authorization: Bearer ...`, `User-Agent`, `Accept`. On 401 refresh the token once and retry once. On 429 and 5xx: retry up to 3 times with exponential backoff 0.5, 1, 2 s (honor a numeric `Retry-After` header, capped at 10 s) using the injected `sleep`. Other non-2xx -> `TastyApiError(status, code, message)` parsed from the error envelope `{"error": {"code", "message"}}` when present. Return the parsed JSON.
- `accounts() -> list[str]`: account numbers from `GET /customers/me/accounts`.
- `get_quotes(equities: list[str] = (), options: list[str] = ()) -> dict[str, Quote]`: `GET /market-data/by-type` with `equity` / `equity-option` comma-joined params; refuse more than 100 symbols in total by splitting into several requests; key the result by the symbol exactly as returned.
- `option_chain(symbol: str) -> list[Expiration]` from `/option-chains/{symbol}/nested` (dataclasses `Expiration(date, dte, strikes: list[Strike])`, `Strike(strike: float, call: str, put: str)`).

### Dataclass `Quote`
`symbol, bid: float|None, ask: float|None, mid: float|None, mark: float|None, last: float|None, updated_at: datetime|None` (parse strings to float, ISO timestamps with `Z` to aware UTC datetimes; unparsable -> None, never 0).

### Module functions
- `occ_symbol(root: str, expiry: date, right: str, strike: float) -> str` per the format in `_CONVENTIONS.md` (right is "C"/"P"; strike*1000 rounded to int; raise `ValueError` for roots longer than 6 chars or strikes that do not fit 8 digits).
- `parse_occ_symbol(sym: str) -> tuple[str, date, str, float]` (inverse; used by later slices).

### Exceptions
`TastyError` base; `TastyAuthError`, `TastyApiError(status, code, message)`.

## Out of scope
Order endpoints, positions, any use of the client inside `manage.py` (next slices), streaming/DXLink.

## Tests (`tests/test_tasty_client.py`, fake transport and fake clock)
- Token: exchange request has the right URL/body/User-Agent; token cached; refreshed after expiry; refreshed once on 401 then request retried; second 401 -> error.
- 429 then 200 succeeds after the sleeps `[0.5]`; three 429s -> `TastyApiError`; `Retry-After: 3` is honored.
- Error envelope parsed into `TastyApiError.code`/`.message`.
- `get_quotes`: params correct, response keyed by symbol, string numbers parsed, missing/`null` bid -> `None`; 150 symbols -> two requests.
- `occ_symbol` for `SPY`, 2026-11-24, C, 740 -> `SPY   261124C00740000`; fractional strike 12.5 -> `...00012500`; round trip with `parse_occ_symbol`; 7-char root raises.
- `option_chain` parsing from a fixture shaped like the real response (put the JSON fixture inline in the test).
- No secret value (client secret / refresh token strings used in the test) appears in any exception message or `repr`.

## Acceptance
All tests pass; the module imports without network access; `tasty_check.py` is left untouched.

## Manual check (for the human, do not run)
`venv/bin/python -c "import tasty_client as t, credentials as c; cl=t.TastyClient(c.tasty_credentials('tasty-prod-read')); print(cl.get_quotes(equities=['SPY'])['SPY'])"`
