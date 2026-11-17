"""
Interactive Brokers Option Chain Fetcher
Requires: pip install ib_insync
IB Gateway/TWS must be running on port 4001 (LIVE) or 4002 (paper)

NOTE: With delayed market data (free tier), option greeks and bid/ask are not available.
For production use, you need real-time market data subscriptions for:
- US Stocks (for underlying price)
- US Options (for option chains, greeks, bid/ask)

This module provides the framework. For now, use mock data in daily_runner.py
"""

# CRITICAL: Set event loop BEFORE importing ib_insync (eventkit imports at module level)
import asyncio
import sys

if sys.version_info >= (3, 10):
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

from ib_insync import IB, Stock, Option, Index
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Symbol-specific contract configurations
SYMBOL_CONTRACTS = {
    'SPY': {'type': 'stock', 'symbol': 'SPY', 'exchange': 'SMART', 'currency': 'USD'},
    'QQQ': {'type': 'stock', 'symbol': 'QQQ', 'exchange': 'SMART', 'currency': 'USD'},
    'IWM': {'type': 'stock', 'symbol': 'IWM', 'exchange': 'SMART', 'currency': 'USD'},
    'AAPL': {'type': 'stock', 'symbol': 'AAPL', 'exchange': 'SMART', 'currency': 'USD'},
    'NVDA': {'type': 'stock', 'symbol': 'NVDA', 'exchange': 'SMART', 'currency': 'USD'},
    'META': {'type': 'stock', 'symbol': 'META', 'exchange': 'SMART', 'currency': 'USD'},
    'TSLA': {'type': 'stock', 'symbol': 'TSLA', 'exchange': 'SMART', 'currency': 'USD'},
    'AMD': {'type': 'stock', 'symbol': 'AMD', 'exchange': 'SMART', 'currency': 'USD'},
    'MARA': {'type': 'stock', 'symbol': 'MARA', 'exchange': 'SMART', 'currency': 'USD'},
    'PLTR': {'type': 'stock', 'symbol': 'PLTR', 'exchange': 'SMART', 'currency': 'USD'},
    'SMCI': {'type': 'stock', 'symbol': 'SMCI', 'exchange': 'SMART', 'currency': 'USD'},
    # Indices - use Index contract on CBOE
    'SPX': {'type': 'index', 'symbol': 'SPX', 'exchange': 'CBOE', 'currency': 'USD'},
    'NDX': {'type': 'index', 'symbol': 'NDX', 'exchange': 'CBOE', 'currency': 'USD'},
    'RUT': {'type': 'index', 'symbol': 'RUT', 'exchange': 'CBOE', 'currency': 'USD'},
}


class IBOptionChain:
    def __init__(self, host: str = '127.0.0.1', port: int = 4002, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = None
        self.connected = False
    
    def connect(self):
        """Connect to IB Gateway/TWS using synchronous connect"""
        if not self.connected:
            self.ib = IB()
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            # Request delayed market data type (3 = delayed, 1 = live)
            self.ib.reqMarketDataType(3)
            self.connected = True
            logger.info(f"Connected to IB on {self.host}:{self.port}")
    
    def disconnect(self):
        if self.connected and self.ib:
            self.ib.disconnect()
            self.connected = False
    
    def _create_underlying(self, symbol: str):
        """Create the appropriate underlying contract"""
        cfg = SYMBOL_CONTRACTS.get(symbol, {'type': 'stock', 'symbol': symbol, 'exchange': 'SMART', 'currency': 'USD'})
        if cfg['type'] == 'index':
            return Index(cfg['symbol'], cfg['exchange'], cfg['currency'])
        else:
            return Stock(cfg['symbol'], cfg['exchange'], cfg['currency'])
    
    def _get_price_from_ticker(self, ticker):
        """Extract price from ticker"""
        if ticker.marketPrice() and ticker.marketPrice() > 0:
            return ticker.marketPrice()
        if ticker.last and ticker.last > 0:
            return ticker.last
        if ticker.close and ticker.close > 0:
            return ticker.close
        return None
    
    def get_underlying_price(self, symbol: str) -> Optional[float]:
        """Get current underlying price (works with delayed data)"""
        self.connect()
        try:
            underlying = self._create_underlying(symbol)
            self.ib.qualifyContracts(underlying)
            ticker = self.ib.reqMktData(underlying, '', False, False)
            self.ib.sleep(2)
            return self._get_price_from_ticker(ticker)
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            return None
    
    def get_option_expirations(self, symbol: str, dte_min: int = 7, dte_max: int = 14) -> list:
        """Get available option expirations in DTE range"""
        self.connect()
        try:
            underlying = self._create_underlying(symbol)
            self.ib.qualifyContracts(underlying)
            chains = self.ib.reqSecDefOptParams(underlying.symbol, '', underlying.secType, underlying.conId)
            
            today = datetime.now().date()
            target_expirations = []
            for chain in chains:
                for exp in chain.expirations:
                    exp_date = datetime.strptime(exp, '%Y%m%d').date()
                    dte = (exp_date - today).days
                    if dte_min <= dte <= dte_max:
                        target_expirations.append((exp, dte, chain.exchange))
            return target_expirations
        except Exception as e:
            logger.error(f"Error getting expirations for {symbol}: {e}")
            return []
    
    def get_option_chain(self, symbol: str, dte_min: int = 7, dte_max: int = 14) -> dict:
        """
        Fetch option chain for given symbol and DTE range.
        
        NOTE: With delayed market data, option greeks and bid/ask are NOT available.
        This returns the chain structure (strikes, expirations) but not pricing.
        For full option data, you need real-time market data subscriptions.
        
        Returns dict with expirations, each containing calls/puts DataFrames.
        """
        logger.warning("Option chain pricing requires real-time market data subscription. "
                      "Returning chain structure only. Use mock data for testing.")
        return {}
    
    def get_iv_rank(self, symbol: str, lookback_days: int = 252) -> Optional[float]:
        """Calculate IV Rank from historical IV (requires historical data subscription)"""
        # This would need historical IV data - placeholder
        # You can compute from your stored data or use MarketChameleon/Barchart
        return None


def fetch_chains_for_candidates(symbols: list[str], dte_min: int = 7, dte_max: int = 14) -> dict:
    """Convenience function to fetch chains for multiple symbols"""
    ib = IBOptionChain()
    all_chains = {}
    try:
        for sym in symbols:
            chains = ib.get_option_chain(sym, dte_min, dte_max)
            if chains:
                all_chains[sym] = chains
    finally:
        ib.disconnect()
    return all_chains


# Example usage - get underlying prices only
if __name__ == "__main__":
    ib = IBOptionChain(port=4002)
    try:
        for sym in ['SPY', 'QQQ', 'IWM', 'AAPL', 'NVDA']:
            price = ib.get_underlying_price(sym)
            print(f"{sym}: {price}")
    finally:
        ib.disconnect()