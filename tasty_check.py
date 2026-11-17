#!/usr/bin/env python3
"""
Read-only tastytrade data check (production, 'read' scope). Places no orders.
Verifies: OAuth refresh -> access token, equity quote, option chain, option quotes.
Never prints tokens or secrets.

    venv/bin/python tasty_check.py [SYMBOL]
"""
import sys
from datetime import date, datetime
import requests
import credentials

UA = 'option-tools-check/0.1'


def access_token(creds) -> str:
    body = {'grant_type': 'refresh_token', 'refresh_token': creds.refresh_token, 'client_secret': creds.client_secret}
    r = requests.post(f'{creds.base_url}/oauth/token', json=body, headers={'User-Agent': UA}, timeout=20)
    if r.status_code != 200:
        raise SystemExit(f'token exchange failed: HTTP {r.status_code} {r.text[:200]}')
    tok = r.json().get('access_token')
    if not tok:
        raise SystemExit(f'token response has no access_token; keys: {list(r.json())}')
    return tok


def get(creds, token, path, **params):
    r = requests.get(f'{creds.base_url}{path}', params=params, timeout=30,
                     headers={'Authorization': f'Bearer {token}', 'User-Agent': UA, 'Accept': 'application/json'})
    if r.status_code != 200:
        raise SystemExit(f'GET {path} failed: HTTP {r.status_code} {r.text[:300]}')
    return r.json()


def find_expirations(chain_json):
    """Return list of expiration dicts from the nested chain response, tolerant of wrapping."""
    data = chain_json.get('data', {})
    items = data.get('items', data if isinstance(data, list) else [])
    for item in items:
        if isinstance(item, dict) and 'expirations' in item:
            return item['expirations']
    return []


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else 'SPY'
    creds = credentials.tasty_credentials(credentials.profile_for('data', 'paper'))
    print(f'profile={creds.profile} env={creds.env}')
    token = access_token(creds)
    print('1. access token: OK (15 min)')

    q = get(creds, token, '/market-data/by-type', equity=sym)['data']['items']
    q = q[0] if q else {}
    print(f"2. {sym} quote: bid={q.get('bid')} ask={q.get('ask')} mark={q.get('mark')} last={q.get('last')} updated={q.get('updated-at')}")
    price = float(q.get('mark') or q.get('last') or 0)
    if price <= 0:
        raise SystemExit('no usable underlying price')

    chain = get(creds, token, f'/option-chains/{sym}/nested')
    exps = find_expirations(chain)
    if not exps:
        raise SystemExit(f'3. chain: unexpected shape, top-level data keys: {list(chain.get("data", {}))[:10]}')
    print(f'3. chain: {len(exps)} expirations; first has keys {sorted(exps[0])[:12]}')

    today = date.today()
    def dte(e):
        return (datetime.strptime(e['expiration-date'], '%Y-%m-%d').date() - today).days
    cands = [e for e in exps if 7 <= dte(e) <= 14] or sorted(exps, key=lambda e: abs(dte(e) - 10))[:1]
    exp = cands[0]
    strikes = exp['strikes']
    atm = min(strikes, key=lambda s: abs(float(s['strike-price']) - price))
    print(f"   using expiration {exp['expiration-date']} (DTE {dte(exp)}), ATM strike {atm['strike-price']}; strike keys {sorted(atm)}")

    syms = [atm['call'], atm['put']]
    oq = get(creds, token, '/market-data/by-type', **{'equity-option': ','.join(syms)})['data']['items']
    print(f'4. option quotes ({len(oq)} returned):')
    for o in oq:
        print(f"   {o.get('symbol')}: bid={o.get('bid')} ask={o.get('ask')} mid={o.get('mid')} mark={o.get('mark')} updated={o.get('updated-at')}")
    if len(oq) != len(syms):
        print('   WARNING: fewer quotes than requested')
    print('Data check finished (read-only, no orders placed).')


if __name__ == '__main__':
    try:
        main()
    except credentials.SecretsError as e:
        raise SystemExit(f'credentials problem: {e}')
