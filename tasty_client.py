"""
TastyTrade REST client (read-only).
Covers OAuth token caching, retries/backoff, error parsing, accounts, quotes, option chains, and OCC symbol helpers.
No network access at import time.

Transport exceptions (requests.Timeout, requests.ConnectionError, etc.) are NOT wrapped
or retried; they propagate to the caller.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, date, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import credentials


# ============================================================
# Exceptions
# ============================================================

class TastyError(Exception):
    """Base exception for tastytrade client errors."""
    pass


class TastyAuthError(TastyError):
    """Authentication/token exchange failure."""
    pass


class TastyApiError(TastyError):
    """API error response (non-2xx status)."""
    def __init__(self, status: int, code: Optional[str], message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"HTTP {status} {code}: {message}")


# ============================================================
# Transport abstraction
# ============================================================

@dataclass(frozen=True)
class Response:
    """Normalized HTTP response."""
    status: int
    headers: Dict[str, str]
    json: Optional[Dict[str, Any]]
    text: str


Transport = Callable[
    [str, str, Dict[str, str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]],
    Response
]


def requests_transport(
    method: str,
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Response:
    """Default transport using the `requests` library.
    
    Importing this module does no I/O. The function itself performs the request.
    """
    import requests  # local import to avoid mandatory dependency at import time
    
    r = requests.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=20,
    )
    
    # Try to parse JSON, but don't fail if body is not JSON
    try:
        json_data = r.json()
    except ValueError:
        json_data = None
    
    return Response(
        status=r.status_code,
        headers=dict(r.headers),
        json=json_data,
        text=r.text,
    )


# ============================================================
# Parsing helpers
# ============================================================

def _to_float(v: Any) -> Optional[float]:
    """Convert a value to float, returning None for unparsable/non-finite values."""
    if v is None or v == '' or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _to_dt(v: Any) -> Optional[datetime]:
    """Parse ISO timestamp, treating trailing 'Z' as UTC. Returns aware UTC datetime or None."""
    if v is None or v == '' or isinstance(v, bool):
        return None
    s = str(v).strip()
    # Replace trailing Z with +00:00 for fromisoformat
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # If naive, treat as UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


# ============================================================
# OCC symbol helpers
# ============================================================

# Allow uppercase alphanumeric, dot, and slash (e.g., BRK.B, BRK/B)
_OCC_ROOT_RE = re.compile(r'^[A-Z0-9./]{1,6}$')


def occ_symbol(root: str, expiry: date, right: str, strike: float) -> str:
    """Build an OCC option symbol.
    
    Format: root(6 chars, space-padded) + YYMMDD + C/P + strike*1000 as 8 digits.
    Example: SPY, 2026-11-24, C, 740 -> 'SPY   261124C00740000'
    
    Args:
        root: Underlying symbol (1-6 chars from A-Z, 0-9, ., /)
        expiry: Expiration date
        right: 'C' for call, 'P' for put
        strike: Strike price (e.g., 740.0 or 12.5)
    
    Returns:
        21-character OCC symbol string.
    
    Raises:
        ValueError: If root, right, or strike are invalid.
    """
    # Normalize and validate root
    if root is None:
        raise ValueError("Invalid OCC root: None")
    norm_root = root.strip().upper()
    if not norm_root or not _OCC_ROOT_RE.match(norm_root):
        raise ValueError(f"Invalid OCC root: {root!r} (must be 1-6 chars from A-Z, 0-9, ., /)")
    
    # Validate right
    if right not in ('C', 'P'):
        raise ValueError(f"Invalid right: {right!r} (must be 'C' or 'P')")
    
    # Reject non-finite strikes before rounding
    if not math.isfinite(strike):
        raise ValueError(f"Strike must be finite, got {strike!r}")
    
    # Compute strike integer (strike * 1000, rounded)
    n = int(round(strike * 1000))
    if n < 0 or n > 99_999_999:
        raise ValueError(f"Strike out of range: {strike} -> {n} (must be 0..99999.999)")
    
    # Build: root padded to 6 chars + YYMMDD + right + 8-digit strike
    root_padded = norm_root.ljust(6)
    exp_str = expiry.strftime('%y%m%d')
    strike_str = f'{n:08d}'
    return f'{root_padded}{exp_str}{right}{strike_str}'


def parse_occ_symbol(sym: str) -> Tuple[str, date, str, float]:
    """Parse an OCC option symbol into components.
    
    Args:
        sym: 21-character OCC symbol (e.g., 'SPY   261124C00740000')
    
    Returns:
        Tuple of (root, expiry_date, right, strike).
    
    Raises:
        ValueError: If the symbol is malformed.
    """
    if len(sym) != 21:
        raise ValueError(f"OCC symbol must be 21 characters, got {len(sym)}: {sym!r}")
    
    root = sym[:6].rstrip()
    if not root or not _OCC_ROOT_RE.match(root):
        raise ValueError(f"Invalid OCC root in symbol: {root!r} (must be 1-6 chars from A-Z, 0-9, ., /)")
    exp_str = sym[6:12]
    right = sym[12]
    strike_str = sym[13:21]
    
    if right not in ('C', 'P'):
        raise ValueError(f"Invalid right in OCC symbol: {right!r}")
    
    try:
        expiry = datetime.strptime(exp_str, '%y%m%d').date()
    except ValueError as e:
        raise ValueError(f"Invalid date in OCC symbol: {exp_str!r}") from e
    
    try:
        strike_int = int(strike_str)
    except ValueError as e:
        raise ValueError(f"Invalid strike in OCC symbol: {strike_str!r}") from e
    
    strike = strike_int / 1000.0
    return root, expiry, right, strike


# ============================================================
# Data classes for quotes and option chains
# ============================================================

@dataclass(frozen=True)
class Quote:
    """Market quote for an equity or option."""
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]
    mark: Optional[float]
    last: Optional[float]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class Strike:
    """Option strike with call/put symbols."""
    strike: float
    call: Optional[str]
    put: Optional[str]


@dataclass(frozen=True)
class Expiration:
    """Option expiration with strikes."""
    date: date
    dte: int
    strikes: List[Strike]


# ============================================================
# TastyClient
# ============================================================

class TastyClient:
    """Read-only REST client for tastytrade API."""
    
    def __init__(
        self,
        creds: credentials.TastyCredentials,
        transport: Transport = requests_transport,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str = 'option-tools/0.1',
    ):
        self.creds = creds
        self._transport = transport
        self._now = now
        self._sleep = sleep
        self._user_agent = user_agent
        self._token: Optional[str] = None
        self._expires_at: Optional[float] = None
    
    def __repr__(self) -> str:
        return f"TastyClient(profile={self.creds.profile!r}, env={self.creds.env!r})"
    
    # --------------------------------------------------------
    # Token management
    # --------------------------------------------------------
    
    def access_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        now = self._now()
        if self._token is not None and self._expires_at is not None and now < self._expires_at - 60:
            return self._token
        return self._refresh()
    
    def _refresh(self) -> str:
        """Exchange refresh token for access token."""
        url = f'{self.creds.base_url}/oauth/token'
        body = {
            'grant_type': 'refresh_token',
            'refresh_token': self.creds.refresh_token,
            'client_secret': self.creds.client_secret,
        }
        headers = {
            'User-Agent': self._user_agent,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        
        # Call transport directly (no Bearer header, no retry recursion)
        resp = self._transport('POST', url, headers, params=None, json_body=body)
        
        if resp.status != 200:
            # Scrub secrets from error message
            text = self._scrub_secrets(resp.text[:200])
            raise TastyAuthError(f'token exchange failed: HTTP {resp.status} {text}')
        
        if not isinstance(resp.json, dict):
            raise TastyAuthError('token response is not a JSON object')
        
        data = resp.json
        token = data.get('access_token')
        if not token:
            raise TastyAuthError('token response has no access_token')
        
        self._token = token
        
        # Parse expires_in; default 14 minutes if absent/invalid
        expires_in = _to_float(data.get('expires_in'))
        if expires_in is None or expires_in <= 0:
            expires_in = 14 * 60  # default 14 minutes
        self._expires_at = self._now() + expires_in
        
        return token
    
    def _scrub_secrets(self, text: Any) -> str:
        """Replace client_secret and refresh_token with *** in error messages."""
        # The server might echo these back in error responses
        if text is None:
            return ''
        # Ensure we're working with a string
        s = str(text)
        scrubbed = s
        if self.creds.client_secret:
            scrubbed = scrubbed.replace(self.creds.client_secret, '***')
        if self.creds.refresh_token:
            scrubbed = scrubbed.replace(self.creds.refresh_token, '***')
        return scrubbed
    
    # --------------------------------------------------------
    # Core request with retries
    # --------------------------------------------------------
    
    def _items(self, body: Any) -> List[Any]:
        """Extract items list from response envelope, raising on unexpected shape."""
        if not isinstance(body, dict):
            raise TastyError('unexpected response shape: missing data.items')
        data = body.get('data')
        if not isinstance(data, dict):
            raise TastyError('unexpected response shape: missing data.items')
        items = data.get('items')
        if not isinstance(items, list):
            raise TastyError('unexpected response shape: missing data.items')
        return items
    
    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an authenticated request with retry/backoff logic.
        
        Retries up to 3 times (4 attempts total) on 429 and 5xx.
        On 401, forces token refresh and retries once (separate from 429/5xx budget).
        Raises TastyError on 2xx response with non-JSON body.
        """
        url = f'{self.creds.base_url}{path}'
        headers_base = {
            'User-Agent': self._user_agent,
            'Accept': 'application/json',
        }
        
        # Retry state
        retry_429_5xx = 0
        max_retries_429_5xx = 3
        retry_401_done = False
        
        while True:
            token = self.access_token()
            headers = {**headers_base, 'Authorization': f'Bearer {token}'}
            
            resp = self._transport(method, url, headers, params, json_body)
            
            # 2xx success
            if 200 <= resp.status < 300:
                if not isinstance(resp.json, dict):
                    raise TastyError('unexpected response shape: non-JSON body on 2xx')
                return resp.json
            
            # 401 -> force refresh and retry once
            if resp.status == 401 and not retry_401_done:
                self._token = None  # force refresh on next access_token()
                self._expires_at = None
                retry_401_done = True
                continue
            
            # 429 or 5xx -> retry with backoff
            if (resp.status == 429 or 500 <= resp.status < 600) and retry_429_5xx < max_retries_429_5xx:
                delay = self._compute_backoff(resp, retry_429_5xx)
                self._sleep(delay)
                retry_429_5xx += 1
                continue
            
            # All other errors or retries exhausted
            error_code, error_message = self._parse_error(resp)
            raise TastyApiError(resp.status, error_code, error_message)
    
    def _compute_backoff(self, resp: Response, attempt: int) -> float:
        """Compute backoff delay for retry attempt."""
        # Check Retry-After header (case-insensitive)
        for key, value in resp.headers.items():
            if key.lower() == 'retry-after':
                try:
                    delay = float(value)
                    if math.isfinite(delay):
                        return min(max(delay, 0.0), 10.0)  # floor at 0, cap at 10 seconds
                except ValueError:
                    pass
        # Exponential backoff: 0.5 * 2^attempt
        return 0.5 * (2 ** attempt)
    
    def _parse_error(self, resp: Response) -> Tuple[Optional[str], str]:
        """Extract error code and message from response."""
        if isinstance(resp.json, dict):
            error = resp.json.get('error')
            if isinstance(error, dict):
                code = error.get('code')
                message = error.get('message') or resp.text[:200]
                return (self._scrub_secrets(code) if code is not None else None), self._scrub_secrets(message)
        # No envelope or non-dict JSON
        return None, self._scrub_secrets(resp.text[:200])
    
    # --------------------------------------------------------
    # High-level API methods
    # --------------------------------------------------------
    
    def accounts(self) -> List[str]:
        """Get list of account numbers."""
        data = self.request('GET', '/customers/me/accounts')
        items = self._items(data)
        return [item['account']['account-number'] for item in items if item.get('account')]
    
    def get_quotes(self, equities: Tuple[str, ...] = (), options: Tuple[str, ...] = ()) -> Dict[str, Quote]:
        """Get quotes for equities and/or options.
        
        Symbols are batched in groups of up to 100 total (equities + options).
        Equities are sent first in each batch, then options.
        Returns a dict keyed by symbol.
        
        Raises:
            TypeError: If equities or options is a bare string.
        """
        # Reject bare string arguments
        if isinstance(equities, str) or isinstance(options, str):
            raise TypeError("equities and options must be tuples, not bare strings")
        
        # Build tagged symbols, deduplicating while preserving order
        seen = set()
        tagged: List[Tuple[str, str]] = []
        for sym in equities:
            if sym not in seen:
                seen.add(sym)
                tagged.append(('equity', sym))
        for sym in options:
            if sym not in seen:
                seen.add(sym)
                tagged.append(('option', sym))
        
        if not tagged:
            return {}
        
        result: Dict[str, Quote] = {}
        batch_size = 100
        
        for i in range(0, len(tagged), batch_size):
            batch = tagged[i:i + batch_size]
            eq_batch = [sym for kind, sym in batch if kind == 'equity']
            opt_batch = [sym for kind, sym in batch if kind == 'option']
            
            params: Dict[str, str] = {}
            if eq_batch:
                params['equity'] = ','.join(eq_batch)
            if opt_batch:
                params['equity-option'] = ','.join(opt_batch)
            
            data = self.request('GET', '/market-data/by-type', params=params)
            items = self._items(data)
            
            for item in items:
                symbol = item.get('symbol')
                if not symbol:
                    continue
                quote = Quote(
                    symbol=symbol,
                    bid=_to_float(item.get('bid')),
                    ask=_to_float(item.get('ask')),
                    mid=_to_float(item.get('mid')),
                    mark=_to_float(item.get('mark')),
                    last=_to_float(item.get('last')),
                    updated_at=_to_dt(item.get('updated-at')),
                )
                result[symbol] = quote
        
        return result
    
    def option_chain(self, symbol: str) -> List[Expiration]:
        """Get nested option chain for a symbol.
        
        Returns list of Expiration objects with strikes.
        Raises TastyError if required keys are missing or invalid.
        """
        encoded_symbol = urllib.parse.quote(symbol, safe='')
        path = f'/option-chains/{encoded_symbol}/nested'
        data = self.request('GET', path)
        items = self._items(data)
        if not items:
            return []
        
        chain = items[0]
        expirations_data = chain.get('expirations')
        if expirations_data is None:
            raise TastyError('option chain response missing "expirations" key')
        if not isinstance(expirations_data, list):
            raise TastyError('option chain "expirations" is not a list')
        
        result: List[Expiration] = []
        for exp_data in expirations_data:
            if not isinstance(exp_data, dict):
                raise TastyError('expiration entry is not an object')
            
            exp_date_str = exp_data.get('expiration-date')
            if exp_date_str is None:
                raise TastyError('expiration missing "expiration-date"')
            dte = exp_data.get('days-to-expiration')
            if dte is None:
                raise TastyError('expiration missing "days-to-expiration"')
            try:
                exp_date = date.fromisoformat(exp_date_str)
                dte_int = int(dte)
            except (ValueError, TypeError) as e:
                raise TastyError(f'invalid expiration data: {e}') from e
            
            strikes_data = exp_data.get('strikes')
            if strikes_data is None:
                raise TastyError('expiration missing "strikes"')
            if not isinstance(strikes_data, list):
                raise TastyError('expiration "strikes" is not a list')
            
            strikes: List[Strike] = []
            for strike_data in strikes_data:
                if not isinstance(strike_data, dict):
                    raise TastyError('strike entry is not an object')
                strike_price = _to_float(strike_data.get('strike-price'))
                if strike_price is None:
                    raise TastyError('strike missing or invalid "strike-price"')
                call_sym = strike_data.get('call')
                put_sym = strike_data.get('put')
                strikes.append(Strike(strike=strike_price, call=call_sym, put=put_sym))
            
            result.append(Expiration(date=exp_date, dte=dte_int, strikes=strikes))
        
        return result