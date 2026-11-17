#!/usr/bin/env python3
"""
Daily Options Strategy Runner
Run each morning to scan, score, and present top setups.
"""
import asyncio
import sys

# CRITICAL: Set event loop BEFORE importing ib_insync (eventkit imports at module level)
if sys.version_info >= (3, 10):
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

sys.path.insert(0, '/home/vermes/option-tools')

import pandas as pd
from datetime import datetime, date
from strategy_models import (
    OptionChainSnapshot, score_all_strategies, print_top_setups,
    find_strike_by_delta, get_mid_price
)
from trade_journal import get_open_trades, print_performance
import yaml


# ============================================================
# CONFIGURATION
# ============================================================
CONFIG = {
    'universe': ['SPY', 'QQQ', 'IWM', 'SPX', 'NDX', 'RUT', 'AAPL', 'NVDA', 'META', 'TSLA', 'AMD', 'MARA', 'PLTR', 'SMCI'],
    'dte_range': (7, 14),
    'min_iv_rank': 30,
    'max_positions': 2,
    'max_loss_per_trade_eur': 50,
    'account_size_eur': 1000,
}

# Load from YAML if exists
try:
    with open('/home/vermes/option-tools/config.yaml') as f:
        CONFIG.update(yaml.safe_load(f))
except FileNotFoundError:
    pass


# ============================================================
# MOCK CHAIN FETCHER (replace with IB/TastyTrade integration)
# ============================================================
def fetch_mock_chain(symbol: str, ref_date: date) -> OptionChainSnapshot:
    """
    MOCK: Replace with real IB/TastyTrade chain fetch.
    This creates synthetic data for testing the scoring models.
    """
    import numpy as np
    from math import log, sqrt, exp, erf
    
    # Simulated underlying prices and vols
    params = {
        'SPY': {'price': 450, 'inc': 1, 'vol': 0.15},
        'QQQ': {'price': 380, 'inc': 1, 'vol': 0.20},
        'IWM': {'price': 190, 'inc': 1, 'vol': 0.25},
        'SPX': {'price': 4500, 'inc': 5, 'vol': 0.14},
        'NDX': {'price': 15500, 'inc': 25, 'vol': 0.18},
        'RUT': {'price': 1900, 'inc': 10, 'vol': 0.22},
        # Defaults for stocks
        'AAPL': {'price': 180, 'inc': 2.5, 'vol': 0.28},
        'NVDA': {'price': 500, 'inc': 5, 'vol': 0.45},
        'META': {'price': 350, 'inc': 5, 'vol': 0.30},
        'TSLA': {'price': 250, 'inc': 5, 'vol': 0.50},
        'AMD': {'price': 160, 'inc': 2.5, 'vol': 0.35},
        'MARA': {'price': 20, 'inc': 1, 'vol': 0.80},
        'PLTR': {'price': 25, 'inc': 1, 'vol': 0.60},
        'SMCI': {'price': 300, 'inc': 5, 'vol': 0.55},
    }
    p = params.get(symbol, {'price': 100, 'inc': 1, 'vol': 0.20})
    price = p['price']
    inc = p['inc']
    vol = p['vol']
    
    # Simulated IVR and HV
    ivr = np.random.uniform(25, 80)
    hv = np.random.uniform(15, 35)
    
    # Create synthetic strikes
    strikes = np.arange(price * 0.85, price * 1.15, inc)
    
    # Time to expiry in years
    dte = np.random.randint(7, 15)
    t = dte / 365.0
    r = 0.05  # risk-free rate
    
    calls = []
    puts = []
    for k in strikes:
        moneyness = k / price
        # Black-Scholes delta approximation
        if vol * sqrt(t) > 0:
            d1 = (log(price / k) + (r + 0.5 * vol**2) * t) / (vol * sqrt(t))
            nd1 = 0.5 * (1 + erf(d1 / sqrt(2)))
        else:
            nd1 = 0.5
        call_delta = max(0.01, min(0.99, nd1))
        put_delta = call_delta - 1
        
        # Rough BS price
        intrinsic_call = max(0, price - k)
        intrinsic_put = max(0, k - price)
        # Time value approximation
        time_value = price * vol * sqrt(t) * 0.4 * exp(-(moneyness - 1)**2 / 0.02)
        
        call_mid = intrinsic_call + max(0, time_value)
        put_mid = intrinsic_put + max(0, time_value)
        
        # Bid/ask spread ~ 1-3% of mid
        spread_pct = 0.01 + 0.02 * abs(moneyness - 1)
        spread = max(inc * 0.05, call_mid * spread_pct)  # Min spread = half strike inc
        
        calls.append({
            'strike': round(k, 1),
            'bid': round(max(0.01, call_mid - spread/2), 2),
            'ask': round(call_mid + spread/2, 2),
            'mid': round(call_mid, 2),
            'delta': round(call_delta, 3),
            'gamma': round(0.01 * exp(-(moneyness-1)**2 * 50), 4),
            'theta': round(-0.02 * exp(-(moneyness-1)**2 * 10), 4),
            'vega': round(0.1 * exp(-(moneyness-1)**2 * 10), 4),
            'oi': int(np.random.uniform(100, 5000)),
            'volume': int(np.random.uniform(10, 1000)),
        })
        puts.append({
            'strike': round(k, 1),
            'bid': round(max(0.01, put_mid - spread/2), 2),
            'ask': round(put_mid + spread/2, 2),
            'mid': round(put_mid, 2),
            'delta': round(put_delta, 3),
            'gamma': round(0.01 * exp(-(moneyness-1)**2 * 50), 4),
            'theta': round(-0.02 * exp(-(moneyness-1)**2 * 10), 4),
            'vega': round(0.1 * exp(-(moneyness-1)**2 * 10), 4),
            'oi': int(np.random.uniform(100, 5000)),
            'volume': int(np.random.uniform(10, 1000)),
        })
    
    return OptionChainSnapshot(
        symbol=symbol,
        underlying_price=price,
        dte=dte,
        iv_rank=round(ivr, 1),
        hv_20=round(hv, 1),
        calls=pd.DataFrame(calls),
        puts=pd.DataFrame(puts),
        earnings_date=None,
        sma_50=price * 1.02 if symbol in ['SPY', 'QQQ', 'NVDA', 'META'] else price * 0.98,
        sma_200=price * 1.05 if symbol in ['SPY', 'QQQ'] else price * 0.95,
    )


# ============================================================
# MAIN PIPELINE
# ============================================================
def run_daily_scan(use_mock: bool = False):
    """Main daily routine"""
    ref_date = date.today()
    print(f"\n{'='*60}")
    print(f"DAILY OPTIONS SCAN - {ref_date}")
    print(f"{'='*60}")
    
    # 1. Check open positions
    open_trades = get_open_trades()
    print(f"\n📋 OPEN POSITIONS ({len(open_trades)}/{CONFIG['max_positions']}):")
    for t in open_trades:
        print(f"  #{t.id} {t.strategy} {t.symbol} DTE~{t.dte} | Entry: {t.entry_date} | Credit: ${t.credit_received:.2f} | MaxLoss: ${t.max_loss:.2f}")
    
    if len(open_trades) >= CONFIG['max_positions']:
        print("  ⚠️  Max positions reached. No new trades today.")
        return
    
    # 2. Fetch candidate chains
    print(f"\n🔍 Scanning {len(CONFIG['universe'])} symbols...")
    chains = []
    
    if not use_mock:
        # No real chain provider is implemented yet. Do NOT fall back to made-up data.
        raise SystemExit("Real option chains are not implemented yet (broker data pending). "
                         "Run with --mock to test the scoring models on RANDOM data.")
    print("*** MOCK DATA: random prices/IVR, for testing the models only ***")
    for sym in CONFIG['universe']:
        chain = fetch_mock_chain(sym, ref_date)
        if chain.iv_rank >= CONFIG['min_iv_rank']:
            chains.append(chain)

    print(f"  Found {len(chains)} candidates with IVR >= {CONFIG['min_iv_rank']}%")
    
    # 3. Score all strategies
    print(f"\n🧮 Scoring strategies...")
    setups = score_all_strategies(chains, pd.Timestamp(ref_date))
    
    # 4. Filter by position sizing (max loss <= €50)
    valid_setups = []
    for s in setups:
        max_loss_usd = s.details.get('max_loss', 0)
        max_loss_eur = max_loss_usd * 0.92  # rough USD/EUR
        if max_loss_eur <= CONFIG['max_loss_per_trade_eur']:
            valid_setups.append(s)
        else:
            print(f"  ⚠️  {s.strategy} {s.symbol} max loss ${max_loss_usd:.2f} > €{CONFIG['max_loss_per_trade_eur']}")
    
    # 5. Present top setups
    print_top_setups(valid_setups, top_n=5)
    
    # 6. Performance summary
    print_performance()
    
    print(f"\n{'='*60}")
    print("NEXT STEPS:")
    print("1. Review top setups above")
    print("2. Verify liquidity (bid/ask spread, OI) on IB/TastyTrade")
    print("3. Confirm no earnings before expiry")
    print("4. Place limit orders at mid-price")
    print("5. Record in trade journal after fill")
    print(f"{'='*60}\n")
    
    return valid_setups


if __name__ == '__main__':
    use_mock = '--mock' in sys.argv
    run_daily_scan(use_mock=use_mock)