"""
Unit tests for tasty_client.py with fake transport and clock.
Tests: token caching/refresh, retries/backoff, error parsing, quotes, option chains, OCC symbols, accounts, secret scrubbing.
"""
import sys
import json
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tasty_client
from tasty_client import (
    TastyClient,
    TastyError,
    TastyAuthError,
    TastyApiError,
    Response,
    Quote,
    Strike,
    Expiration,
    occ_symbol,
    parse_occ_symbol,
    _to_float,
    _to_dt,
)
import credentials


# ============================================================
# Test fixtures and helpers
# ============================================================

class FakeTransport:
    """Fake transport that returns scripted responses."""
    
    def __init__(self):
        self.responses: List[Response] = []
        self.calls: List[Tuple[str, str, Dict[str, str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]] = []
        self._response_queue: Dict[Tuple[str, str], List[Response]] = {}
    
    def add_response(self, method: str, path: str, response: Response):
        """Add a response for a specific (method, path)."""
        key = (method.upper(), path)
        if key not in self._response_queue:
            self._response_queue[key] = []
        self._response_queue[key].append(response)
    
    def add_global_response(self, response: Response):
        """Add a response to the global fallback queue."""
        self.responses.append(response)
    
    def __call__(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Response:
        self.calls.append((method, url, headers, params, json_body))
        
        # Extract path from URL (after base_url)
        # We'll match on the full URL for simplicity in tests
        for (m, p), queue in self._response_queue.items():
            if method.upper() == m and url.endswith(p):
                if queue:
                    return queue.pop(0)
        
        # Fall back to global queue
        if self.responses:
            return self.responses.pop(0)
        
        raise AssertionError(f"No fake response for {method} {url}")


class FakeClock:
    """Fake clock with controllable time and sleep recording."""
    
    def __init__(self, start: float = 1000.0):
        self.t = start
        self.sleeps: List[float] = []
    
    def now(self) -> float:
        return self.t
    
    def sleep(self, duration: float):
        self.sleeps.append(duration)
        self.t += duration
    
    def advance(self, delta: float):
        self.t += delta


def make_fake_creds() -> credentials.TastyCredentials:
    """Create fake credentials for testing."""
    return credentials.TastyCredentials(
        profile='tasty-prod-read',
        env='production',
        base_url='https://api.tastyworks.com',
        client_secret='SECRET-XYZ',
        refresh_token='REFRESH-ABC',
    )


def token_response(access_token: str = 'TOKEN-123', expires_in: Optional[float] = 900) -> Response:
    """Create a token exchange success response."""
    body: Dict[str, Any] = {'access_token': access_token}
    if expires_in is not None:
        body['expires_in'] = expires_in
    return Response(200, {}, body, json.dumps(body))


def token_error_response(status: int, text: str) -> Response:
    """Create a token exchange error response."""
    return Response(status, {}, {'error': text}, text)


def api_response(json_data: Dict[str, Any], status: int = 200) -> Response:
    """Create a generic API success response."""
    return Response(status, {}, json_data, json.dumps(json_data))


def api_error_response(status: int, code: Optional[str], message: str) -> Response:
    """Create an API error response with envelope."""
    body = {'error': {'code': code, 'message': message}} if code else {'error': message}
    return Response(status, {}, body, json.dumps(body))


# ============================================================
# Test cases
# ============================================================

class TestParsingHelpers(unittest.TestCase):
    """Test _to_float and _to_dt helpers."""
    
    def test_to_float_valid(self):
        self.assertEqual(_to_float('1.25'), 1.25)
        self.assertEqual(_to_float(1.25), 1.25)
        self.assertEqual(_to_float('0'), 0.0)
        self.assertEqual(_to_float('-5.5'), -5.5)
    
    def test_to_float_none_empty_bool(self):
        self.assertIsNone(_to_float(None))
        self.assertIsNone(_to_float(''))
        self.assertIsNone(_to_float(True))
        self.assertIsNone(_to_float(False))
    
    def test_to_float_unparsable(self):
        self.assertIsNone(_to_float('abc'))
        self.assertIsNone(_to_float('1.2.3'))
    
    def test_to_float_non_finite(self):
        self.assertIsNone(_to_float(float('nan')))
        self.assertIsNone(_to_float(float('inf')))
        self.assertIsNone(_to_float(float('-inf')))
    
    def test_to_dt_with_z(self):
        dt = _to_dt('2025-11-17T15:30:00Z')
        self.assertEqual(dt, datetime(2025, 11, 17, 15, 30, 0, tzinfo=timezone.utc))
    
    def test_to_dt_with_ms_and_z(self):
        dt = _to_dt('2025-11-17T15:30:00.123Z')
        self.assertEqual(dt, datetime(2025, 11, 17, 15, 30, 0, 123000, tzinfo=timezone.utc))
    
    def test_to_dt_with_offset(self):
        dt = _to_dt('2025-11-17T15:30:00+00:00')
        self.assertEqual(dt, datetime(2025, 11, 17, 15, 30, 0, tzinfo=timezone.utc))
    
    def test_to_dt_naive_treated_as_utc(self):
        dt = _to_dt('2025-11-17T15:30:00')
        self.assertEqual(dt, datetime(2025, 11, 17, 15, 30, 0, tzinfo=timezone.utc))
    
    def test_to_dt_invalid(self):
        self.assertIsNone(_to_dt(None))
        self.assertIsNone(_to_dt(''))
        self.assertIsNone(_to_dt('not-a-date'))
        self.assertIsNone(_to_dt(True))


class TestOCCSymbol(unittest.TestCase):
    """Test occ_symbol and parse_occ_symbol round-trip."""
    
    def test_occ_symbol_basic(self):
        sym = occ_symbol('SPY', date(2026, 11, 24), 'C', 740.0)
        self.assertEqual(sym, 'SPY   261124C00740000')
    
    def test_occ_symbol_decimal_strike(self):
        sym = occ_symbol('SPY', date(2026, 11, 24), 'C', 12.5)
        self.assertEqual(sym, 'SPY   261124C00012500')
    
    def test_occ_symbol_put(self):
        sym = occ_symbol('AAPL', date(2025, 1, 17), 'P', 5.0)
        self.assertEqual(sym, 'AAPL  250117P00005000')
    
    def test_parse_occ_symbol_roundtrip(self):
        original = 'SPY   261124C00740000'
        root, expiry, right, strike = parse_occ_symbol(original)
        self.assertEqual((root, expiry, right, strike), ('SPY', date(2026, 11, 24), 'C', 740.0))
        rebuilt = occ_symbol(root, expiry, right, strike)
        self.assertEqual(rebuilt, original)
    
    def test_parse_occ_symbol_decimal(self):
        root, expiry, right, strike = parse_occ_symbol('SPY   261124C00012500')
        self.assertEqual((root, expiry, right, strike), ('SPY', date(2026, 11, 24), 'C', 12.5))
    
    def test_occ_symbol_invalid_root_too_long(self):
        with self.assertRaises(ValueError):
            occ_symbol('ABCDEFG', date(2025, 1, 17), 'C', 100.0)
    
    def test_occ_symbol_invalid_root_empty(self):
        with self.assertRaises(ValueError):
            occ_symbol('', date(2025, 1, 17), 'C', 100.0)
    
    def test_occ_symbol_lowercase_root_normalized(self):
        # Lowercase roots are normalized to uppercase
        sym = occ_symbol('spy', date(2025, 1, 17), 'C', 100.0)
        self.assertEqual(sym, 'SPY   250117C00100000')
    
    def test_occ_symbol_root_with_dot_and_slash(self):
        # Roots with . and / are allowed (e.g., BRK.B, BRK/B)
        sym1 = occ_symbol('BRK.B', date(2025, 1, 17), 'C', 100.0)
        self.assertEqual(sym1, 'BRK.B 250117C00100000')
        sym2 = occ_symbol('BRK/B', date(2025, 1, 17), 'C', 100.0)
        self.assertEqual(sym2, 'BRK/B 250117C00100000')
    
    def test_occ_symbol_invalid_root_too_long(self):
        with self.assertRaises(ValueError):
            occ_symbol('ABCDEFG', date(2025, 1, 17), 'C', 100.0)
    
    def test_occ_symbol_invalid_root_empty(self):
        with self.assertRaises(ValueError):
            occ_symbol('', date(2025, 1, 17), 'C', 100.0)
    
    def test_occ_symbol_invalid_root_special_chars(self):
        # Roots with other special chars are rejected
        with self.assertRaises(ValueError):
            occ_symbol('BRK#B', date(2025, 1, 17), 'C', 100.0)
        with self.assertRaises(ValueError):
            occ_symbol('BRK B', date(2025, 1, 17), 'C', 100.0)
    
    def test_occ_symbol_invalid_right(self):
        with self.assertRaises(ValueError):
            occ_symbol('SPY', date(2025, 1, 17), 'X', 100.0)
    
    def test_occ_symbol_strike_too_high(self):
        with self.assertRaises(ValueError):
            occ_symbol('SPY', date(2025, 1, 17), 'C', 100_000.0)
    
    def test_occ_symbol_strike_negative(self):
        with self.assertRaises(ValueError):
            occ_symbol('SPY', date(2025, 1, 17), 'C', -1.0)
    
    def test_occ_symbol_non_finite_strike_nan(self):
        with self.assertRaises(ValueError):
            occ_symbol('SPY', date(2025, 1, 17), 'C', float('nan'))
    
    def test_occ_symbol_non_finite_strike_inf(self):
        with self.assertRaises(ValueError):
            occ_symbol('SPY', date(2025, 1, 17), 'C', float('inf'))
    
    def test_occ_symbol_non_finite_strike_neg_inf(self):
        with self.assertRaises(ValueError):
            occ_symbol('SPY', date(2025, 1, 17), 'C', float('-inf'))
    
    def test_parse_occ_symbol_wrong_length(self):
        with self.assertRaises(ValueError):
            parse_occ_symbol('SHORT')
        with self.assertRaises(ValueError):
            parse_occ_symbol('SPY   261124C00740000EXTRA')
    
    def test_parse_occ_symbol_invalid_right(self):
        with self.assertRaises(ValueError):
            parse_occ_symbol('SPY   261124X00740000')
    
    def test_parse_occ_symbol_invalid_date(self):
        with self.assertRaises(ValueError):
            parse_occ_symbol('SPY   999999C00740000')
    
    def test_parse_occ_symbol_invalid_strike(self):
        with self.assertRaises(ValueError):
            parse_occ_symbol('SPY   261124CABCDEFGH')


class TestTokenManagement(unittest.TestCase):
    """Test token caching, refresh, and 401 handling."""
    
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = FakeClock(1000.0)
        self.creds = make_fake_creds()
        self.client = TastyClient(
            creds=self.creds,
            transport=self.transport,
            now=self.clock.now,
            sleep=self.clock.sleep,
        )
    
    def test_token_exchange_url_and_body(self):
        """Verify token exchange uses correct URL, JSON body, and User-Agent."""
        self.transport.add_global_response(token_response('TOKEN-123'))
        
        token = self.client.access_token()
        
        self.assertEqual(token, 'TOKEN-123')
        self.assertEqual(len(self.transport.calls), 1)
        method, url, headers, params, body = self.transport.calls[0]
        self.assertEqual(method, 'POST')
        self.assertEqual(url, 'https://api.tastyworks.com/oauth/token')
        self.assertEqual(body, {
            'grant_type': 'refresh_token',
            'refresh_token': 'REFRESH-ABC',
            'client_secret': 'SECRET-XYZ',
        })
        self.assertEqual(headers['User-Agent'], 'option-tools/0.1')
        self.assertEqual(headers['Accept'], 'application/json')
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Authorization', headers)
    
    def test_token_cached_across_requests(self):
        """Token should be cached and reused for subsequent requests."""
        self.transport.add_global_response(token_response('TOKEN-123'))
        self.transport.add_response('GET', '/customers/me/accounts', api_response({'data': {'items': []}}))
        self.transport.add_response('GET', '/market-data/by-type', api_response({'data': {'items': []}}))
        
        # First request
        self.client.request('GET', '/customers/me/accounts')
        # Second request
        self.client.request('GET', '/market-data/by-type', params={'equity': 'SPY'})
        
        # Only one token exchange should have happened
        token_calls = [c for c in self.transport.calls if '/oauth/token' in c[1]]
        self.assertEqual(len(token_calls), 1)
    
    def test_token_refresh_after_expiry_minus_60(self):
        """Token should refresh when clock passes expiry - 60 seconds."""
        self.transport.add_global_response(token_response('TOKEN-1', expires_in=900))  # 15 min
        self.transport.add_global_response(token_response('TOKEN-2', expires_in=900))
        
        # First token
        token1 = self.client.access_token()
        self.assertEqual(token1, 'TOKEN-1')
        
        # Advance to 14 minutes (840s) - still within 60s of expiry (900-60=840)
        # At exactly 840, now < expires_at - 60 -> 840 < 1000+900-60=1840, True, so cached
        # At 841, now >= 1840? Let's check: 1000+841=1841 >= 1840, so refresh
        self.clock.advance(841)
        token2 = self.client.access_token()
        self.assertEqual(token2, 'TOKEN-2')
        
        # Verify two token exchanges
        token_calls = [c for c in self.transport.calls if '/oauth/token' in c[1]]
        self.assertEqual(len(token_calls), 2)
    
    def test_token_ttl_default_14_min_when_expires_in_absent(self):
        """When expires_in is missing, default TTL is 14 minutes."""
        self.transport.add_global_response(token_response('TOKEN-1', expires_in=None))
        
        self.client.access_token()
        
        # expires_at should be now + 14*60
        self.assertAlmostEqual(self.client._expires_at, 1000 + 14 * 60, delta=1)
    
    def test_token_ttl_from_expires_in(self):
        """When expires_in=900, token reused at t=839, refreshed at t=841."""
        self.transport.add_global_response(token_response('TOKEN-1', expires_in=900))
        self.transport.add_global_response(token_response('TOKEN-2', expires_in=900))
        
        self.client.access_token()  # at t=1000
        
        # At t=1839 (1000+839), should reuse
        self.clock.t = 1839
        token1 = self.client.access_token()
        self.assertEqual(token1, 'TOKEN-1')
        
        # At t=1841, should refresh
        self.clock.t = 1841
        token2 = self.client.access_token()
        self.assertEqual(token2, 'TOKEN-2')
    
    def test_401_forces_refresh_and_retries_once(self):
        """On 401, client refreshes token and retries the request once."""
        # First request gets 401, then token refresh, then retry succeeds
        # Note: initial token exchange happens before first request
        self.transport.add_global_response(token_response('TOKEN-123'))
        self.transport.add_response('GET', '/test', api_error_response(401, 'unauthorized', 'Invalid token'))
        self.transport.add_global_response(token_response('NEW-TOKEN'))
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        # Calls: initial POST /oauth/token, 1st GET (401), POST /oauth/token (refresh), 2nd GET (success)
        self.assertEqual(len(self.transport.calls), 4)
        self.assertEqual(self.transport.calls[0][1], 'https://api.tastyworks.com/oauth/token')
        self.assertEqual(self.transport.calls[1][1], 'https://api.tastyworks.com/test')
        self.assertEqual(self.transport.calls[2][1], 'https://api.tastyworks.com/oauth/token')
        self.assertEqual(self.transport.calls[3][1], 'https://api.tastyworks.com/test')
        # Second GET should have new token
        auth_header = self.transport.calls[3][2].get('Authorization', '')
        self.assertIn('NEW-TOKEN', auth_header)
    
    def test_second_401_raises_error(self):
        """Second 401 after refresh raises TastyApiError."""
        self.transport.add_global_response(token_response('TOKEN-123'))
        self.transport.add_response('GET', '/test', api_error_response(401, 'unauthorized', 'Invalid token'))
        self.transport.add_global_response(token_response('NEW-TOKEN'))
        self.transport.add_response('GET', '/test', api_error_response(401, 'unauthorized', 'Still invalid'))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        self.assertEqual(cm.exception.status, 401)
        self.assertIsInstance(cm.exception, TastyError)
    
    def test_non_200_token_exchange_raises_TastyAuthError(self):
        """Non-200 token exchange raises TastyAuthError with status and no secrets."""
        self.transport.add_global_response(token_error_response(400, 'invalid_grant'))
        
        with self.assertRaises(TastyAuthError) as cm:
            self.client.access_token()
        
        error_msg = str(cm.exception)
        self.assertIn('HTTP 400', error_msg)
        self.assertNotIn('SECRET-XYZ', error_msg)
        self.assertNotIn('REFRESH-ABC', error_msg)
    
    def test_token_error_scrubs_secrets_from_server_echo(self):
        """If server echoes secrets in error body, they are scrubbed."""
        # Server might echo the request body in error response
        error_body = '{"error": "invalid client_secret SECRET-XYZ"}'
        self.transport.add_global_response(Response(400, {}, json.loads(error_body), error_body))
        
        with self.assertRaises(TastyAuthError) as cm:
            self.client.access_token()
        
        error_msg = str(cm.exception)
        self.assertNotIn('SECRET-XYZ', error_msg)
        self.assertIn('***', error_msg)
    
    def test_list_shaped_token_body_raises_tasty_auth_error(self):
        """List-shaped token body gives TastyAuthError."""
        # Server returns a list instead of object for token response
        resp = Response(200, {}, ['token1', 'token2'], '["token1", "token2"]')
        self.transport.add_global_response(resp)
        
        with self.assertRaises(TastyAuthError) as cm:
            self.client.access_token()
        
        self.assertIn('not a JSON object', str(cm.exception))


class TestRetriesAndBackoff(unittest.TestCase):
    """Test retry logic for 429, 5xx, and Retry-After header."""
    
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = FakeClock(1000.0)
        self.creds = make_fake_creds()
        self.client = TastyClient(
            creds=self.creds,
            transport=self.transport,
            now=self.clock.now,
            sleep=self.clock.sleep,
        )
        # Pre-add token response so we can focus on API retries
        self.transport.add_global_response(token_response('TOKEN-123'))
    
    def test_429_then_200_succeeds_with_sleep_0_5(self):
        """Single 429 then success -> sleep [0.5]."""
        self.transport.add_response('GET', '/test', api_error_response(429, 'rate_limit', 'Too many'))
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        self.assertEqual(self.clock.sleeps, [0.5])
    
    def test_three_429s_then_200_sleeps_0_5_1_2(self):
        """Three 429s then success -> sleeps [0.5, 1, 2]."""
        for _ in range(3):
            self.transport.add_response('GET', '/test', api_error_response(429, 'rate_limit', 'Too many'))
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        self.assertEqual(self.clock.sleeps, [0.5, 1.0, 2.0])
    
    def test_four_429s_raises_error(self):
        """Four 429s (exceeds 3 retries) raises TastyApiError."""
        for _ in range(4):
            self.transport.add_response('GET', '/test', api_error_response(429, 'rate_limit', 'Too many'))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        self.assertEqual(cm.exception.status, 429)
        # Should have slept 3 times
        self.assertEqual(len(self.clock.sleeps), 3)
    
    def test_retry_after_3_seconds(self):
        """Retry-After: 3 -> sleep [3]."""
        headers = {'Retry-After': '3'}
        resp = Response(429, headers, {'error': 'rate_limit'}, 'rate limit')
        self.transport.add_response('GET', '/test', resp)
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        self.assertEqual(self.clock.sleeps, [3.0])
    
    def test_retry_after_99_capped_at_10(self):
        """Retry-After: 99 -> sleep [10] (capped)."""
        headers = {'Retry-After': '99'}
        resp = Response(429, headers, {'error': 'rate_limit'}, 'rate limit')
        self.transport.add_response('GET', '/test', resp)
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        self.assertEqual(self.clock.sleeps, [10.0])
    
    def test_503_is_retried(self):
        """503 is retried with backoff."""
        self.transport.add_response('GET', '/test', api_error_response(503, 'unavailable', 'Service unavailable'))
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        self.assertEqual(self.clock.sleeps, [0.5])
    
    def test_400_not_retried(self):
        """400 is not retried, raises immediately."""
        self.transport.add_response('GET', '/test', api_error_response(400, 'bad_request', 'Bad request'))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(self.clock.sleeps, [])
    
    def test_retry_after_nan_falls_back_to_exponential(self):
        """Retry-After: nan falls back to exponential backoff."""
        headers = {'Retry-After': 'nan'}
        resp = Response(429, headers, {'error': 'rate_limit'}, 'rate limit')
        self.transport.add_response('GET', '/test', resp)
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        # Should fall back to exponential backoff (0.5 for attempt 0)
        self.assertEqual(self.clock.sleeps, [0.5])
    
    def test_retry_after_abc_falls_back_to_exponential(self):
        """Retry-After: abc falls back to exponential backoff."""
        headers = {'Retry-After': 'abc'}
        resp = Response(429, headers, {'error': 'rate_limit'}, 'rate limit')
        self.transport.add_response('GET', '/test', resp)
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        # Should fall back to exponential backoff (0.5 for attempt 0)
        self.assertEqual(self.clock.sleeps, [0.5])
    
    def test_retry_after_99_capped_at_10(self):
        """Retry-After: 99 -> sleep [10] (capped)."""
        headers = {'Retry-After': '99'}
        resp = Response(429, headers, {'error': 'rate_limit'}, 'rate limit')
        self.transport.add_response('GET', '/test', resp)
        self.transport.add_response('GET', '/test', api_response({'data': 'ok'}))
        
        result = self.client.request('GET', '/test')
        
        self.assertEqual(result, {'data': 'ok'})
        self.assertEqual(self.clock.sleeps, [10.0])


class TestErrorEnvelope(unittest.TestCase):
    """Test error parsing from response envelope."""
    
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = FakeClock(1000.0)
        self.creds = make_fake_creds()
        self.client = TastyClient(
            creds=self.creds,
            transport=self.transport,
            now=self.clock.now,
            sleep=self.clock.sleep,
        )
        self.transport.add_global_response(token_response('TOKEN-123'))
    
    def test_error_envelope_parsed(self):
        """Error envelope with code and message is parsed."""
        self.transport.add_response('GET', '/test', api_error_response(404, 'not_found', 'Resource not found'))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(cm.exception.code, 'not_found')
        self.assertEqual(cm.exception.message, 'Resource not found')
        self.assertEqual(str(cm.exception), 'HTTP 404 not_found: Resource not found')
    
    def test_error_no_envelope_uses_text(self):
        """Error without envelope uses status and text."""
        # Use 400 (not retried) to test error parsing without retry interference
        resp = Response(400, {}, None, 'Bad request')
        self.transport.add_response('GET', '/test', resp)
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        self.assertEqual(cm.exception.status, 400)
        self.assertIsNone(cm.exception.code)
        self.assertEqual(cm.exception.message, 'Bad request')

    def test_error_envelope_with_message_no_code(self):
        """Error envelope with message but no code key doesn't crash."""
        # Envelope has 'message' but no 'code' key
        resp = Response(400, {}, {'error': {'message': 'Something went wrong'}}, '{"error": {"message": "Something went wrong"}}')
        self.transport.add_response('GET', '/test', resp)
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        self.assertEqual(cm.exception.status, 400)
        self.assertIsNone(cm.exception.code)
        self.assertEqual(cm.exception.message, 'Something went wrong')
    
    def test_missing_data_key_raises_tasty_error(self):
        """Missing 'data' key in response raises TastyError."""
        # Response has no 'data' key at all
        resp = Response(200, {}, {'items': []}, '{"items": []}')
        self.transport.add_response('GET', '/customers/me/accounts', resp)
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.accounts()
        self.assertIn('missing data.items', str(cm.exception))
    
    def test_missing_items_key_raises_tasty_error(self):
        """Missing 'items' key in response raises TastyError."""
        # Response has 'data' but no 'items'
        resp = Response(200, {}, {'data': {}}, '{"data": {}}')
        self.transport.add_response('GET', '/customers/me/accounts', resp)
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.accounts()
        self.assertIn('missing data.items', str(cm.exception))
    
    def test_2xx_non_json_body_raises_tasty_error(self):
        """2xx response with non-JSON body raises TastyError."""
        resp = Response(200, {}, None, 'not json')
        self.transport.add_response('GET', '/test', resp)
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.request('GET', '/test')
        self.assertIn('non-JSON body', str(cm.exception))
    
    def test_accounts_missing_data_items_raises(self):
        """accounts() missing data.items raises TastyError."""
        resp = Response(200, {}, {'data': {}}, '{"data": {}}')
        self.transport.add_response('GET', '/customers/me/accounts', resp)
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.accounts()
        self.assertIn('missing data.items', str(cm.exception))
    
    def test_get_quotes_missing_data_items_raises(self):
        """get_quotes() missing data.items raises TastyError."""
        resp = Response(200, {}, {'data': {}}, '{"data": {}}')
        self.transport.add_response('GET', '/market-data/by-type', resp)
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.get_quotes(equities=('SPY',))
        self.assertIn('missing data.items', str(cm.exception))
    
    def test_option_chain_missing_data_items_raises(self):
        """option_chain() missing data.items raises TastyError."""
        resp = Response(200, {}, {'data': {}}, '{"data": {}}')
        self.transport.add_response('GET', '/option-chains/SPY/nested', resp)
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.option_chain('SPY')
        self.assertIn('missing data.items', str(cm.exception))
    
    def test_list_shaped_error_body_raises_tasty_api_error(self):
        """List-shaped error body gives TastyApiError with text as message."""
        # Server returns a list instead of object for error
        resp = Response(400, {}, ['error1', 'error2'], '["error1", "error2"]')
        self.transport.add_response('GET', '/test', resp)
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        self.assertEqual(cm.exception.status, 400)
        self.assertIn('error1', cm.exception.message)


class TestQuotes(unittest.TestCase):
    """Test get_quotes method."""
    
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = FakeClock(1000.0)
        self.creds = make_fake_creds()
        self.client = TastyClient(
            creds=self.creds,
            transport=self.transport,
            now=self.clock.now,
            sleep=self.clock.sleep,
        )
        self.transport.add_global_response(token_response('TOKEN-123'))
    
    def test_quotes_params_equity_and_option(self):
        """Params are equity=... and equity-option=... correctly."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {
                'items': [
                    {'symbol': 'SPY', 'bid': '450.0', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'},
                    {'symbol': 'SPY251121C00450000', 'bid': '1.20', 'ask': '1.25', 'updated-at': '2025-11-17T15:30:00Z'},
                ]
            }
        }))
        
        quotes = self.client.get_quotes(equities=('SPY',), options=('SPY251121C00450000',))
        
        # Check the request params
        call = self.transport.calls[1]  # First call is token
        self.assertEqual(call[3], {'equity': 'SPY', 'equity-option': 'SPY251121C00450000'})
        
        # Check parsed quotes
        self.assertEqual(len(quotes), 2)
        spy = quotes['SPY']
        self.assertEqual(spy.bid, 450.0)
        self.assertEqual(spy.ask, 450.1)
        self.assertEqual(spy.updated_at, datetime(2025, 11, 17, 15, 30, 0, tzinfo=timezone.utc))
        
        opt = quotes['SPY251121C00450000']
        self.assertEqual(opt.bid, 1.20)
        self.assertEqual(opt.ask, 1.25)
    
    def test_quotes_keyed_by_symbol(self):
        """Result is keyed by symbol."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {
                'items': [
                    {'symbol': 'SPY', 'bid': '450.0', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'},
                ]
            }
        }))
        
        quotes = self.client.get_quotes(equities=('SPY',))
        self.assertIn('SPY', quotes)
        self.assertIsInstance(quotes['SPY'], Quote)
    
    def test_quotes_string_numbers_parsed(self):
        """String numbers are parsed to float."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {'items': [{'symbol': 'SPY', 'bid': '450.0', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'}]}
        }))
        
        quotes = self.client.get_quotes(equities=('SPY',))
        self.assertIsInstance(quotes['SPY'].bid, float)
        self.assertEqual(quotes['SPY'].bid, 450.0)
    
    def test_quotes_missing_bid_gives_none(self):
        """Missing or null bid gives None, not 0."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {'items': [{'symbol': 'SPY', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'}]}
        }))
        
        quotes = self.client.get_quotes(equities=('SPY',))
        self.assertIsNone(quotes['SPY'].bid)
        self.assertEqual(quotes['SPY'].ask, 450.1)
    
    def test_quotes_updated_at_z_parsed(self):
        """updated-at ending in Z gives aware UTC datetime."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {'items': [{'symbol': 'SPY', 'bid': '450.0', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'}]}
        }))
        
        quotes = self.client.get_quotes(equities=('SPY',))
        self.assertIsNotNone(quotes['SPY'].updated_at)
        self.assertEqual(quotes['SPY'].updated_at.tzinfo, timezone.utc)
    
    def test_quotes_garbage_gives_none(self):
        """Garbage values give None, not 0."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {'items': [{'symbol': 'SPY', 'bid': 'not-a-number', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'}]}
        }))
        
        quotes = self.client.get_quotes(equities=('SPY',))
        self.assertIsNone(quotes['SPY'].bid)
    
    def test_quotes_150_symbols_two_requests(self):
        """150 symbols -> two requests (100 + 50)."""
        symbols = [f'SYM{i}' for i in range(150)]
        
        # First batch of 100
        self.transport.add_response('GET', '/market-data/by-type', api_response({'data': {'items': []}}))
        # Second batch of 50
        self.transport.add_response('GET', '/market-data/by-type', api_response({'data': {'items': []}}))
        
        self.client.get_quotes(equities=tuple(symbols))
        
        # Token + 2 quote requests
        quote_calls = [c for c in self.transport.calls if '/market-data/by-type' in c[1]]
        self.assertEqual(len(quote_calls), 2)
    
    def test_quotes_no_symbols_no_request(self):
        """Empty symbol lists -> no request made."""
        quotes = self.client.get_quotes(equities=(), options=())
        self.assertEqual(quotes, {})
        # Only token call should have been made (but access_token not even called since no request)
        quote_calls = [c for c in self.transport.calls if '/market-data/by-type' in c[1]]
        self.assertEqual(len(quote_calls), 0)
    
    def test_quotes_duplicate_symbols_deduped(self):
        """Duplicate symbols are deduplicated."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {'items': [{'symbol': 'SPY', 'bid': '450.0', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'}]}
        }))
        
        quotes = self.client.get_quotes(equities=('SPY', 'SPY', 'SPY'))
        
        self.assertEqual(len(quotes), 1)
        self.assertIn('SPY', quotes)
        # Only one request should be made for the single unique symbol
        quote_calls = [c for c in self.transport.calls if '/market-data/by-type' in c[1]]
        self.assertEqual(len(quote_calls), 1)
    
    def test_quotes_symbol_in_both_lists_handled(self):
        """Symbol appearing in both equities and options is handled without error."""
        self.transport.add_response('GET', '/market-data/by-type', api_response({
            'data': {
                'items': [
                    {'symbol': 'SPY', 'bid': '450.0', 'ask': '450.1', 'updated-at': '2025-11-17T15:30:00Z'},
                    {'symbol': 'SPY251121C00450000', 'bid': '1.20', 'ask': '1.25', 'updated-at': '2025-11-17T15:30:00Z'},
                ]
            }
        }))
        
        # SPY in both lists - should be treated as equity (first occurrence wins)
        quotes = self.client.get_quotes(equities=('SPY',), options=('SPY', 'SPY251121C00450000'))
        
        self.assertEqual(len(quotes), 2)
        self.assertIn('SPY', quotes)
        self.assertIn('SPY251121C00450000', quotes)
        # Check that SPY was sent as equity, not option
        call = self.transport.calls[1]
        self.assertIn('equity', call[3])
        self.assertEqual(call[3]['equity'], 'SPY')
        self.assertIn('equity-option', call[3])
        self.assertEqual(call[3]['equity-option'], 'SPY251121C00450000')
    
    def test_quotes_bare_string_raises_type_error(self):
        """Bare string argument raises TypeError."""
        with self.assertRaises(TypeError):
            self.client.get_quotes(equities='SPY')  # type: ignore
        with self.assertRaises(TypeError):
            self.client.get_quotes(options='SPY')  # type: ignore
    
    def test_quotes_150_mixed_symbols_two_requests(self):
        """150 mixed symbols -> two requests with at most 100 symbols each."""
        equities = [f'EQ{i}' for i in range(80)]
        options = [f'OP{i}' for i in range(70)]
        
        # First batch of 100 (80 equities + 20 options)
        self.transport.add_response('GET', '/market-data/by-type', api_response({'data': {'items': []}}))
        # Second batch of 50 (remaining 50 options)
        self.transport.add_response('GET', '/market-data/by-type', api_response({'data': {'items': []}}))
        
        self.client.get_quotes(equities=tuple(equities), options=tuple(options))
        
        quote_calls = [c for c in self.transport.calls if '/market-data/by-type' in c[1]]
        self.assertEqual(len(quote_calls), 2)
        
        # First call should have 80 equities + 20 options = 100 total
        params1 = quote_calls[0][3]
        eq_count1 = len(params1.get('equity', '').split(',')) if params1.get('equity') else 0
        opt_count1 = len(params1.get('equity-option', '').split(',')) if params1.get('equity-option') else 0
        self.assertEqual(eq_count1 + opt_count1, 100)
        
        # Second call should have remaining 50 options
        params2 = quote_calls[1][3]
        eq_count2 = len(params2.get('equity', '').split(',')) if params2.get('equity') else 0
        opt_count2 = len(params2.get('equity-option', '').split(',')) if params2.get('equity-option') else 0
        self.assertEqual(eq_count2 + opt_count2, 50)


class TestOptionChain(unittest.TestCase):
    """Test option_chain method."""
    
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = FakeClock(1000.0)
        self.creds = make_fake_creds()
        self.client = TastyClient(
            creds=self.creds,
            transport=self.transport,
            now=self.clock.now,
            sleep=self.clock.sleep,
        )
        self.transport.add_global_response(token_response('TOKEN-123'))
    
    def test_option_chain_parses_expirations_and_strikes(self):
        """Option chain fixture with 2 expirations parses correctly."""
        fixture = {
            'data': {
                'items': [{
                    'expirations': [
                        {
                            'expiration-date': '2025-11-21',
                            'days-to-expiration': 10,
                            'strikes': [
                                {'strike-price': '440', 'call': 'SPY251121C00440000', 'put': 'SPY251121P00440000'},
                                {'strike-price': '450', 'call': 'SPY251121C00450000', 'put': 'SPY251121P00450000'},
                            ]
                        },
                        {
                            'expiration-date': '2025-11-28',
                            'days-to-expiration': 17,
                            'strikes': [
                                {'strike-price': '445', 'call': 'SPY251128C00445000', 'put': 'SPY251128P00445000'},
                            ]
                        },
                    ]
                }]
            }
        }
        self.transport.add_response('GET', '/option-chains/SPY/nested', api_response(fixture))
        
        chain = self.client.option_chain('SPY')
        
        self.assertEqual(len(chain), 2)
        
        exp1 = chain[0]
        self.assertEqual(exp1.date, date(2025, 11, 21))
        self.assertEqual(exp1.dte, 10)
        self.assertEqual(len(exp1.strikes), 2)
        self.assertEqual(exp1.strikes[0].strike, 440.0)
        self.assertEqual(exp1.strikes[0].call, 'SPY251121C00440000')
        self.assertEqual(exp1.strikes[0].put, 'SPY251121P00440000')
        
        exp2 = chain[1]
        self.assertEqual(exp2.date, date(2025, 11, 28))
        self.assertEqual(exp2.dte, 17)
        self.assertEqual(len(exp2.strikes), 1)
        self.assertEqual(exp2.strikes[0].strike, 445.0)
    
    def test_option_chain_empty_items_returns_empty_list(self):
        """Empty items list returns empty list."""
        self.transport.add_response('GET', '/option-chains/SPY/nested', api_response({'data': {'items': []}}))
        
        chain = self.client.option_chain('SPY')
        self.assertEqual(chain, [])
    
    def test_option_chain_missing_expiration_date_raises(self):
        """Missing expiration-date raises TastyError."""
        fixture = {
            'data': {
                'items': [{
                    'expirations': [
                        {'days-to-expiration': 10, 'strikes': []},  # missing expiration-date
                    ]
                }]
            }
        }
        self.transport.add_response('GET', '/option-chains/SPY/nested', api_response(fixture))
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.option_chain('SPY')
        self.assertIn('expiration-date', str(cm.exception))
    
    def test_option_chain_missing_dte_raises(self):
        """Missing days-to-expiration raises TastyError."""
        fixture = {
            'data': {
                'items': [{
                    'expirations': [
                        {'expiration-date': '2025-11-21', 'strikes': []},  # missing dte
                    ]
                }]
            }
        }
        self.transport.add_response('GET', '/option-chains/SPY/nested', api_response(fixture))
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.option_chain('SPY')
        self.assertIn('days-to-expiration', str(cm.exception))
    
    def test_option_chain_missing_strikes_raises(self):
        """Missing strikes raises TastyError."""
        fixture = {
            'data': {
                'items': [{
                    'expirations': [
                        {'expiration-date': '2025-11-21', 'days-to-expiration': 10},  # missing strikes
                    ]
                }]
            }
        }
        self.transport.add_response('GET', '/option-chains/SPY/nested', api_response(fixture))
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.option_chain('SPY')
        self.assertIn('strikes', str(cm.exception))
    
    def test_option_chain_invalid_strike_price_raises(self):
        """Invalid strike-price raises TastyError."""
        fixture = {
            'data': {
                'items': [{
                    'expirations': [
                        {'expiration-date': '2025-11-21', 'days-to-expiration': 10,
                         'strikes': [{'strike-price': 'not-a-number', 'call': 'C', 'put': 'P'}]},
                    ]
                }]
            }
        }
        self.transport.add_response('GET', '/option-chains/SPY/nested', api_response(fixture))
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.option_chain('SPY')
        self.assertIn('strike-price', str(cm.exception))
    
    def test_option_chain_url_encoding(self):
        """Option chain path URL-encodes the symbol (e.g., BRK/B -> BRK%2FB)."""
        self.transport.add_response('GET', '/option-chains/BRK%2FB/nested', api_response({'data': {'items': []}}))
        
        chain = self.client.option_chain('BRK/B')
        
        self.assertEqual(chain, [])
        # Verify the URL was encoded
        call = self.transport.calls[1]
        self.assertIn('/option-chains/BRK%2FB/nested', call[1])
    
    def test_option_chain_missing_top_level_expirations_raises(self):
        """Missing top-level expirations key raises TastyError."""
        fixture = {
            'data': {
                'items': [{
                    # 'expirations' key is missing entirely
                }]
            }
        }
        self.transport.add_response('GET', '/option-chains/SPY/nested', api_response(fixture))
        
        with self.assertRaises(tasty_client.TastyError) as cm:
            self.client.option_chain('SPY')
        self.assertIn('expirations', str(cm.exception))


class TestAccounts(unittest.TestCase):
    """Test accounts method."""
    
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = FakeClock(1000.0)
        self.creds = make_fake_creds()
        self.client = TastyClient(
            creds=self.creds,
            transport=self.transport,
            now=self.clock.now,
            sleep=self.clock.sleep,
        )
        self.transport.add_global_response(token_response('TOKEN-123'))
    
    def test_accounts_parses_account_numbers(self):
        """Account numbers are extracted from response."""
        fixture = {
            'data': {
                'items': [
                    {'account': {'account-number': '12345'}},
                    {'account': {'account-number': '67890'}},
                ]
            }
        }
        self.transport.add_response('GET', '/customers/me/accounts', api_response(fixture))
        
        accounts = self.client.accounts()
        
        self.assertEqual(accounts, ['12345', '67890'])


class TestSecretsNotLeaked(unittest.TestCase):
    """Test that secrets never appear in error messages or repr."""
    
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = FakeClock(1000.0)
        self.creds = make_fake_creds()
        self.client = TastyClient(
            creds=self.creds,
            transport=self.transport,
            now=self.clock.now,
            sleep=self.clock.sleep,
        )
    
    def test_client_repr_no_secrets(self):
        """TastyClient repr does not contain secrets."""
        r = repr(self.client)
        self.assertNotIn('SECRET-XYZ', r)
        self.assertNotIn('REFRESH-ABC', r)
        self.assertIn('tasty-prod-read', r)
    
    def test_auth_error_no_secrets(self):
        """TastyAuthError message does not contain secrets."""
        self.transport.add_global_response(token_error_response(400, 'invalid_grant'))
        
        with self.assertRaises(TastyAuthError) as cm:
            self.client.access_token()
        
        msg = str(cm.exception)
        self.assertNotIn('SECRET-XYZ', msg)
        self.assertNotIn('REFRESH-ABC', msg)
        self.assertNotIn('SECRET-XYZ', repr(cm.exception))
        self.assertNotIn('REFRESH-ABC', repr(cm.exception))
    
    def test_auth_error_server_echo_scrubbed(self):
        """If server echoes secrets in error, they are scrubbed."""
        error_body = '{"error": "invalid client_secret SECRET-XYZ and refresh_token REFRESH-ABC"}'
        self.transport.add_global_response(Response(400, {}, json.loads(error_body), error_body))
        
        with self.assertRaises(TastyAuthError) as cm:
            self.client.access_token()
        
        msg = str(cm.exception)
        self.assertNotIn('SECRET-XYZ', msg)
        self.assertNotIn('REFRESH-ABC', msg)
        self.assertIn('***', msg)
    
    def test_api_error_no_secrets(self):
        """TastyApiError message does not contain secrets."""
        self.transport.add_global_response(token_response('TOKEN-123'))
        self.transport.add_response('GET', '/test', api_error_response(404, 'not_found', 'Not found'))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        msg = str(cm.exception)
        self.assertNotIn('SECRET-XYZ', msg)
        self.assertNotIn('REFRESH-ABC', msg)
        self.assertNotIn('SECRET-XYZ', repr(cm.exception))
        self.assertNotIn('REFRESH-ABC', repr(cm.exception))
    
    def test_api_error_server_echo_scrubbed(self):
        """If server echoes secrets in API error, they are scrubbed."""
        self.transport.add_global_response(token_response('TOKEN-123'))
        error_body = '{"error": {"code": "invalid", "message": "bad secret SECRET-XYZ and token REFRESH-ABC"}}'
        self.transport.add_response('GET', '/test', Response(400, {}, json.loads(error_body), error_body))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        msg = str(cm.exception)
        self.assertNotIn('SECRET-XYZ', msg)
        self.assertNotIn('REFRESH-ABC', msg)
        self.assertIn('***', msg)
        self.assertNotIn('SECRET-XYZ', repr(cm.exception))
        self.assertNotIn('REFRESH-ABC', repr(cm.exception))
    
    def test_retries_exhausted_server_echo_scrubbed(self):
        """Retries exhausted with server echoing secrets are scrubbed."""
        self.transport.add_global_response(token_response('TOKEN-123'))
        error_body = '{"error": {"code": "rate_limit", "message": "SECRET-XYZ REFRESH-ABC"}}'
        for _ in range(4):
            self.transport.add_response('GET', '/test', Response(429, {}, json.loads(error_body), error_body))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        msg = str(cm.exception)
        self.assertNotIn('SECRET-XYZ', msg)
        self.assertNotIn('REFRESH-ABC', msg)
        self.assertIn('***', msg)
        self.assertNotIn('SECRET-XYZ', repr(cm.exception))
        self.assertNotIn('REFRESH-ABC', repr(cm.exception))
    
    def test_retries_exhausted_no_secrets(self):
        """Retries exhausted error does not contain secrets."""
        self.transport.add_global_response(token_response('TOKEN-123'))
        for _ in range(4):
            self.transport.add_response('GET', '/test', api_error_response(429, 'rate_limit', 'Too many'))
        
        with self.assertRaises(TastyApiError) as cm:
            self.client.request('GET', '/test')
        
        msg = str(cm.exception)
        self.assertNotIn('SECRET-XYZ', msg)
        self.assertNotIn('REFRESH-ABC', msg)
        self.assertNotIn('SECRET-XYZ', repr(cm.exception))
        self.assertNotIn('REFRESH-ABC', repr(cm.exception))


if __name__ == '__main__':
    unittest.main()