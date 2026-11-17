"""
TastyTrade Integration - Free market data & order execution
Uses tastytrade-sdk (pip install tastytrade) or REST API directly
"""
import os
import json
import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path

try:
    from tastytrade import Session, DXLinkStreamer
    from tastytrade.account import Account
    from tastytrade.instruments import get_option_chain, get_future_option_chain
    from tastytrade.order import Order, OrderAction, OrderType, TimeInForce, Leg
    TASTYTRADE_SDK = True
except ImportError:
    TASTYTRADE_SDK = False
    Session = None

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TastyTrade API endpoints
TASTY_BASE = "https://api.tastyworks.com"
TASTY_CERT = "https://api.cert.tastyworks.com"  # Paper trading

# Symbol mapping for TastyTrade format
SYMBOL_MAP = {
    'SPY': 'SPY',
    'QQQ': 'QQQ',
    'IWM': 'IWM',
    'SPX': 'SPX',
    'SPXW': 'SPXW',  # Weekly SPX options
    'NDX': 'NDX',
    'RUT': 'RUT',
    'AAPL': 'AAPL',
    'NVDA': 'NVDA',
    'META': 'META',
    'TSLA': 'TSLA',
    'AMD': 'AMD',
    'MARA': 'MARA',
    'PLTR': 'PLTR',
    'SMCI': 'SMCI',
}


@dataclass
class TastyCredentials:
    username: str
    password: str
    is_paper: bool = True  # True for cert.tastyworks.com


@dataclass
class OptionQuote:
    symbol: str
    strike: float
    expiry: str
    right: str  # 'C' or 'P'
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]
    last: Optional[float]
    volume: int
    open_interest: int
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    iv: Optional[float]


@dataclass
class UnderlyingQuote:
    symbol: str
    price: float
    change: float
    change_pct: float


class TastyClient:
    """TastyTrade API client for market data and order execution"""
    
    def __init__(self, creds: TastyCredentials):
        self.creds = creds
        self.base_url = TASTY_CERT if creds.is_paper else TASTY_BASE
        self.session_token: Optional[str] = None
        self.account_number: Optional[str] = None
        self._session: Optional[Session] = None
    
    def login(self) -> bool:
        """Authenticate and get session token"""
        if TASTYTRADE_SDK:
            try:
                self._session = Session(self.creds.username, self.creds.password)
                self.session_token = self._session.session_token
                # Get account number
                accounts = Account.get_accounts(self._session)
                if accounts:
                    self.account_number = accounts[0].account_number
                logger.info(f"TastyTrade login successful (paper={self.creds.is_paper})")
                return True
            except Exception as e:
                logger.error(f"TastyTrade SDK login failed: {e}")
                return False
        else:
            # Fallback to REST API
            return self._login_rest()
    
    def _login_rest(self) -> bool:
        """REST API login fallback"""
        url = f"{self.base_url}/sessions"
        resp = requests.post(url, json={
            "login": self.creds.username,
            "password": self.creds.password,
            "remember_me": True
        })
        if resp.status_code == 201:
            data = resp.json()
            self.session_token = data['data']['session-token']
            # Get accounts
            acc_resp = requests.get(f"{self.base_url}/customers/me/accounts",
                                   headers={"Authorization": self.session_token})
            if acc_resp.status_code == 200:
                accounts = acc_resp.json()['data']
                if accounts:
                    self.account_number = accounts[0]['account']['account-number']
            logger.info(f"TastyTrade REST login successful")
            return True
        logger.error(f"Login failed: {resp.text}")
        return False
    
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": self.session_token, "Content-Type": "application/json"}
    
    # ============================================================
    # MARKET DATA
    # ============================================================
    
    def get_underlying_price(self, symbol: str) -> Optional[float]:
        """Get current underlying price"""
        if TASTYTRADE_SDK and self._session:
            try:
                from tastytrade.instruments import get_equity
                equity = get_equity(self._session, symbol)
                return equity.last_price if equity.last_price else equity.close_price
            except Exception as e:
                logger.warning(f"SDK get_underlying_price failed: {e}")
        
        # REST fallback
        url = f"{self.base_url}/quote?symbols={symbol}"
        resp = requests.get(url, headers=self._headers())
        if resp.status_code == 200:
            data = resp.json()
            items = data.get('data', {}).get('items', [])
            if items:
                return items[0].get('last') or items[0].get('close')
        return None
    
    def get_option_chain(self, symbol: str, dte_min: int = 7, dte_max: int = 14) -> Dict:
        """
        Fetch option chain for symbol within DTE range.
        Returns dict: {expiry: {'dte': int, 'calls': [...], 'puts': [...], 'underlying_price': float}}
        """
        if TASTYTRADE_SDK and self._session:
            return self._get_chain_sdk(symbol, dte_min, dte_max)
        return self._get_chain_rest(symbol, dte_min, dte_max)
    
    def _get_chain_sdk(self, symbol: str, dte_min: int, dte_max: int) -> Dict:
        """Get option chain using tastytrade-sdk"""
        try:
            from tastytrade.instruments import get_option_chain
            chain = get_option_chain(self._session, symbol)
            
            today = date.today()
            result = {}
            
            for exp in chain.expirations:
                exp_date = datetime.strptime(exp.expiration_date, '%Y-%m-%d').date()
                dte = (exp_date - today).days
                if dte_min <= dte <= dte_max:
                    calls = []
                    puts = []
                    for opt in exp.options:
                        if opt.option_type == 'Call':
                            calls.append(self._sdk_opt_to_dict(opt))
                        else:
                            puts.append(self._sdk_opt_to_dict(opt))
                    
                    result[exp.expiration_date.replace('-', '')] = {
                        'dte': dte,
                        'calls': calls,
                        'puts': puts,
                        'underlying_price': chain.underlying_price,
                    }
            
            return result
        except Exception as e:
            logger.error(f"SDK get_option_chain failed: {e}")
            return {}
    
    def _sdk_opt_to_dict(self, opt) -> Dict:
        return {
            'strike': opt.strike_price,
            'bid': opt.bid,
            'ask': opt.ask,
            'mid': (opt.bid + opt.ask) / 2 if opt.bid and opt.ask else None,
            'last': opt.last_price,
            'volume': opt.volume,
            'open_interest': opt.open_interest,
            'delta': opt.delta,
            'gamma': opt.gamma,
            'theta': opt.theta,
            'vega': opt.vega,
            'iv': opt.implied_volatility,
        }
    
    def _get_chain_rest(self, symbol: str, dte_min: int, dte_max: int) -> Dict:
        """Get option chain using REST API"""
        url = f"{self.base_url}/option-chains/{symbol}"
        params = {"dte-min": dte_min, "dte-max": dte_max}
        resp = requests.get(url, headers=self._headers(), params=params)
        if resp.status_code != 200:
            logger.error(f"Option chain failed: {resp.text}")
            return {}
        
        data = resp.json()
        result = {}
        for chain in data.get('data', {}).get('items', []):
            exp = chain['expiration-date']
            dte = chain['days-to-expiration']
            underlying = chain.get('underlying-price', 0)
            
            calls = []
            puts = []
            for opt in chain.get('options', []):
                d = {
                    'strike': opt['strike-price'],
                    'bid': opt.get('bid'),
                    'ask': opt.get('ask'),
                    'mid': (opt.get('bid', 0) + opt.get('ask', 0)) / 2 if opt.get('bid') and opt.get('ask') else None,
                    'last': opt.get('last'),
                    'volume': opt.get('volume', 0),
                    'open_interest': opt.get('open-interest', 0),
                    'delta': opt.get('delta'),
                    'gamma': opt.get('gamma'),
                    'theta': opt.get('theta'),
                    'vega': opt.get('vega'),
                    'iv': opt.get('implied-volatility'),
                }
                if opt['option-type'] == 'Call':
                    calls.append(d)
                else:
                    puts.append(d)
            
            result[exp.replace('-', '')] = {
                'dte': dte,
                'calls': calls,
                'puts': puts,
                'underlying_price': underlying,
            }
        return result
    
    # ============================================================
    # ORDER EXECUTION
    # ============================================================
    
    def place_combo_order(self, legs: List[Dict], net_price: float, 
                          action: str = 'Buy', tif: str = 'GTC') -> Dict:
        """
        Place a combo (multi-leg) order.
        legs: [{'symbol', 'expiry', 'strike', 'right', 'action', 'quantity', 'ratio'}]
        net_price: positive = debit paid, negative = credit received
        action: 'Buy' (pay debit) or 'Sell' (receive credit)
        """
        if TASTYTRADE_SDK and self._session:
            return self._place_combo_sdk(legs, net_price, action, tif)
        return self._place_combo_rest(legs, net_price, action, tif)
    
    def _place_combo_sdk(self, legs: List[Dict], net_price: float, 
                         action: str, tif: str) -> Dict:
        """Place combo order using SDK"""
        try:
            from tastytrade.order import Order, OrderAction, OrderType, TimeInForce, Leg
            
            order_legs = []
            for leg in legs:
                order_legs.append(Leg(
                    symbol=leg['symbol'],
                    quantity=leg['quantity'],
                    action=OrderAction[leg['action'].upper()],
                    instrument_type='Equity Option',
                    expiration_date=leg['expiry'],
                    strike_price=leg['strike'],
                    option_type=leg['right'].upper(),
                ))
            
            order = Order(
                account_number=self.account_number,
                legs=order_legs,
                action=OrderAction[action.upper()],
                order_type=OrderType.LIMIT,
                price=abs(net_price),
                time_in_force=TimeInForce[tif],
            )
            
            result = order.place(self._session)
            return {
                'success': True,
                'order_id': result.id,
                'status': result.status,
            }
        except Exception as e:
            logger.error(f"SDK place_combo_order failed: {e}")
            return {'success': False, 'error': str(e)}
    
    def _place_combo_rest(self, legs: List[Dict], net_price: float,
                          action: str, tif: str) -> Dict:
        """Place combo order using REST API"""
        url = f"{self.base_url}/accounts/{self.account_number}/orders"
        
        order_legs = []
        for leg in legs:
            order_legs.append({
                "symbol": leg['symbol'],
                "quantity": leg['quantity'],
                "action": leg['action'].capitalize(),
                "instrument-type": "Equity Option",
                "expiration-date": leg['expiry'],
                "strike-price": leg['strike'],
                "option-type": leg['right'].upper(),
            })
        
        payload = {
            "order": {
                "legs": order_legs,
                "action": action.capitalize(),
                "order-type": "Limit",
                "price": abs(net_price),
                "time-in-force": tif,
            }
        }
        
        resp = requests.post(url, headers=self._headers(), json=payload)
        if resp.status_code in (200, 201):
            return {'success': True, 'order_id': resp.json()['data']['id']}
        return {'success': False, 'error': resp.text}
    
    def get_positions(self) -> List[Dict]:
        """Get current positions grouped by underlying"""
        if TASTYTRADE_SDK and self._session:
            try:
                from tastytrade.account import Account
                acct = Account.get_account(self._session, self.account_number)
                positions = []
                for pos in acct.positions:
                    if pos.quantity != 0:
                        positions.append({
                            'symbol': pos.symbol,
                            'quantity': pos.quantity,
                            'avg_price': pos.average_price,
                            'mark_price': pos.mark_price,
                            'instrument_type': pos.instrument_type,
                        })
                return positions
            except Exception as e:
                logger.error(f"SDK get_positions failed: {e}")
        
        # REST fallback
        url = f"{self.base_url}/accounts/{self.account_number}/positions"
        resp = requests.get(url, headers=self._headers())
        if resp.status_code == 200:
            return resp.json().get('data', {}).get('items', [])
        return []


def create_tasty_client_from_env() -> TastyClient:
    """Create client from environment variables"""
    return TastyClient(TastyCredentials(
        username=os.getenv('TASTY_USERNAME', ''),
        password=os.getenv('TASTY_PASSWORD', ''),
        is_paper=os.getenv('TASTY_PAPER', 'true').lower() == 'true',
    ))


# Example usage
if __name__ == "__main__":
    client = create_tasty_client_from_env()
    if client.login():
        print("Logged in successfully")
        print(f"Account: {client.account_number}")
        
        # Test underlying price
        price = client.get_underlying_price('SPY')
        print(f"SPY price: {price}")
        
        # Test option chain
        chains = client.get_option_chain('SPY', 7, 14)
        print(f"Chains found: {len(chains)}")
        for exp, data in list(chains.items())[:2]:
            print(f"  {exp} DTE={data['dte']}: {len(data['calls'])} calls, {len(data['puts'])} puts")
    else:
        print("Login failed")