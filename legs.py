"""
Leg utilities for option trades.
Pure functions: validation, normalization, keys, DTE calculations.
No I/O, no network, no DB. Only imports datetime.date.
"""
from datetime import date
import json


ALLOWED_TYPES = {'put', 'call'}
ALLOWED_ACTIONS = {'buy', 'sell'}
MAX_LEGS = 4


def normalize_leg(leg: dict) -> dict:
    """Return a normalized copy of a leg dict.
    - type/action: stripped, lowercased
    - strike: float
    - qty: int (positive)
    - price: float if present
    - expiry: kept as string; empty string is dropped
    - unknown keys preserved
    - raises ValueError for non-convertible values, bool for numeric fields
    """
    if not isinstance(leg, dict):
        raise ValueError("leg must be a dict")
    
    out = {}
    for k, v in leg.items():
        if k == 'type':
            if not isinstance(v, str):
                raise ValueError("type must be a string")
            out[k] = v.strip().lower()
        elif k == 'action':
            if not isinstance(v, str):
                raise ValueError("action must be a string")
            out[k] = v.strip().lower()
        elif k == 'strike':
            if isinstance(v, bool):
                raise ValueError("strike must be a number, not bool")
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"strike must be a number, got {v!r}")
        elif k == 'qty':
            if isinstance(v, bool):
                raise ValueError("qty must be an integer, not bool")
            try:
                fv = float(v)
                if not fv.is_integer() or fv <= 0:
                    raise ValueError
                out[k] = int(fv)
            except (TypeError, ValueError):
                raise ValueError(f"qty must be a positive integer, got {v!r}")
        elif k == 'price':
            if v is None or v == '':
                continue
            if isinstance(v, bool):
                raise ValueError("price must be a number, not bool")
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"price must be a number, got {v!r}")
        elif k == 'expiry':
            if v is None or v == '':
                continue
            if not isinstance(v, str):
                raise ValueError("expiry must be a string")
            out[k] = v.strip()
        else:
            out[k] = v
    return out


def validate_legs(legs, *, require_expiry: bool = True) -> None:
    """Validate a list of legs for an open trade.
    - non-empty list, max 4 legs
    - per leg: type in allowed, action in allowed, strike > 0, qty > 0
    - if require_expiry: expiry present and parses as ISO date
    - no duplicate (type, strike, expiry) tuples (strike compared as float)
    - messages say leg[i] and list allowed values / offending value
    """
    if not isinstance(legs, list):
        raise ValueError("legs must be a list")
    if not legs:
        raise ValueError("legs must not be empty")
    
    seen = set()
    for i, leg in enumerate(legs):
        prefix = f"leg[{i}]"
        
        if not isinstance(leg, dict):
            raise ValueError(f"{prefix}: must be a dict")
        
        # type
        t = leg.get('type')
        if t not in ALLOWED_TYPES:
            raise ValueError(f"{prefix}: type must be one of {sorted(ALLOWED_TYPES)}, got {t!r}")
        
        # action
        a = leg.get('action')
        if a not in ALLOWED_ACTIONS:
            raise ValueError(f"{prefix}: action must be one of {sorted(ALLOWED_ACTIONS)}, got {a!r}")
        
        # strike
        strike = leg.get('strike')
        if not isinstance(strike, (int, float)):
            raise ValueError(f"{prefix}: strike must be a number, got {strike!r}")
        if strike <= 0:
            raise ValueError(f"{prefix}: strike must be > 0, got {strike}")
        
        # qty
        qty = leg.get('qty')
        if not isinstance(qty, int):
            raise ValueError(f"{prefix}: qty must be an int, got {qty!r}")
        if qty <= 0:
            raise ValueError(f"{prefix}: qty must be > 0, got {qty}")
        
        # expiry
        if require_expiry:
            expiry = leg.get('expiry')
            if not expiry:
                raise ValueError(f"{prefix}: expiry required")
            try:
                date.fromisoformat(expiry)
            except ValueError:
                raise ValueError(f"{prefix}: expiry must be YYYY-MM-DD, got {expiry!r}")
        
        # duplicate check
        key = (t, float(strike), leg.get('expiry', ''))
        if key in seen:
            raise ValueError(f"{prefix}: duplicate (type, strike, expiry) {key}")
        seen.add(key)
    
    if len(legs) > MAX_LEGS:
        raise ValueError(f"at most {MAX_LEGS} legs allowed, got {len(legs)}")


def leg_key(leg: dict) -> str:
    """Return a unique key for a leg.
    With expiry: "P440@2026-11-27" or "C440.5@2026-11-27"
    Without expiry: "P440" (legacy format, strike normalized via :g)
    """
    t = leg['type'][0].upper()
    strike = float(leg['strike'])
    expiry = leg.get('expiry', '')
    if expiry:
        return f"{t}{strike:g}@{expiry}"
    return f"{t}{strike:g}"


def leg_dte(leg: dict, today: date) -> int:
    """Days to expiry for a single leg. Raises ValueError if no expiry."""
    expiry = leg.get('expiry')
    if not expiry:
        raise ValueError("leg has no expiry")
    exp_date = date.fromisoformat(expiry)
    return (exp_date - today).days


def trade_dte(legs: list, today: date) -> int:
    """Minimum DTE across all legs. Raises ValueError on empty list."""
    if not legs:
        raise ValueError("no legs")
    return min(leg_dte(leg, today) for leg in legs)


def all_have_expiry(legs: list) -> bool:
    """Return True if every leg has a non-empty expiry."""
    return all(leg.get('expiry') for leg in legs)


def front_and_back(legs: list):
    """Split legs into front (nearest expiry) and back (later expiries).
    Returns (front_legs, back_legs).
    Raises ValueError if any leg has no expiry.
    """
    if not legs:
        return [], []
    if not all_have_expiry(legs):
        raise ValueError("all legs must have expiry")
    
    # Find the minimum expiry date
    expiries = [date.fromisoformat(leg['expiry']) for leg in legs]
    min_expiry = min(expiries)
    
    front = [leg for leg in legs if date.fromisoformat(leg['expiry']) == min_expiry]
    back = [leg for leg in legs if date.fromisoformat(leg['expiry']) > min_expiry]
    return front, back


if __name__ == '__main__':
    # Quick sanity checks
    from datetime import date, timedelta
    
    today = date.today()
    exp = (today + timedelta(days=10)).isoformat()
    
    leg1 = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.20, 'expiry': exp}
    leg2 = {'type': 'put', 'strike': 440.0, 'action': 'sell', 'qty': 1, 'price': 1.20, 'expiry': exp}
    leg3 = {'type': 'put', 'strike': 438, 'action': 'buy', 'qty': 1, 'price': 0.55, 'expiry': exp}
    leg4 = {'type': 'call', 'strike': 452, 'action': 'sell', 'qty': 1, 'price': 1.15, 'expiry': exp}
    leg5 = {'type': 'call', 'strike': 454, 'action': 'buy', 'qty': 1, 'price': 0.50, 'expiry': exp}
    
    # Same key for 440 and 440.0
    assert leg_key(leg1) == leg_key(leg2) == f"P440@{exp}"
    print(f"leg_key put 440: {leg_key(leg1)}")
    print(f"leg_key call 452.5: {leg_key({'type':'call','strike':452.5,'action':'sell','qty':1,'expiry':exp})}")
    print(f"leg_key legacy (no expiry): {leg_key({'type':'put','strike':440,'action':'sell','qty':1})}")
    
    # validate
    legs = [leg1, leg3, leg4, leg5]
    validate_legs(legs)
    print("validate_legs: OK")
    
    # calendar
    cal_front = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.20, 'expiry': exp}
    cal_back = {'type': 'put', 'strike': 440, 'action': 'buy', 'qty': 1, 'price': 1.80, 'expiry': (today + timedelta(days=30)).isoformat()}
    validate_legs([cal_front, cal_back])
    print("calendar validate_legs: OK")
    
    front, back = front_and_back([cal_front, cal_back])
    print(f"front_and_back: front={len(front)}, back={len(back)}")
    
    # DTE
    print(f"trade_dte condor: {trade_dte(legs, today)}")
    print(f"trade_dte calendar: {trade_dte([cal_front, cal_back], today)}")
    print(f"leg_dte: {leg_dte(leg1, today)}")
    
    print("All manual checks passed.")