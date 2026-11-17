# Options Trading System - Quick Start Guide

## 📁 Files Created

| File | Purpose |
|------|---------|
| `STRATEGY_PLAN.md` | Complete strategy document with 3 detailed strategies |
| `strategy_models.py` | Three scoring models (IC, PCS, IBF) with symbol-specific configs |
| `ib_chain.py` | Interactive Brokers option chain fetcher with greeks |
| `trade_journal.py` | SQLite trade journal with performance analytics |
| `daily_runner.py` | Morning scan pipeline (mock + real IB modes) |
| `config.yaml` | Central configuration (universe, thresholds, sizing) |
| `requirements.txt` | Updated with ib_insync, pyyaml |

---

## 🚀 Quick Start

### 1. Activate virtual environment
```bash
cd /home/vermes/option-tools
source venv/bin/activate
```

### 2. Run daily scan (mock mode)
```bash
python daily_runner.py
```
Output: Top 5 scored setups across 3 strategies + open positions + performance

### 3. Run with live IB data
```bash
# Start IB Gateway/TWS first (LIVE: port 4001, PAPER: port 4002 — 4001 is the real-money account)
python daily_runner.py --live
```

### 4. View performance
```python
from trade_journal import print_performance
print_performance()
```

---

## 📊 The Three Strategies

### 1. **Iron Condor (IC)** - Neutral, High IVR
- **Symbols**: SPY, QQQ, IWM, SPX, NDX, RUT
- **DTE**: 7-14 | **IVR**: >40% | **Δ**: 16Δ/84Δ | **Width**: Symbol-specific
- **Target**: Credit ≥ 33% width | **Manage**: 25-33% profit, roll at breach
- **Risk**: ~€45-50 per trade

### 2. **Put Credit Spread (PCS)** - Bullish, Trending
- **Symbols**: High-IVR stocks with 50MA > 200MA
- **DTE**: 10-14 | **IVR**: >35% | **Δ**: 30Δ put | **Width**: Symbol-specific
- **Target**: Credit ≥ 30% width | **Manage**: 50% profit, roll at breach
- **Risk**: ~€30-50 per trade

### 3. **Iron Butterfly (IBF)** - Range-Bound, Very High IVR
- **Symbols**: IVR >50%, HV/IV < 0.8
- **DTE**: 7-10 | **IVR**: >50% | **Center**: ATM | **Wings**: Symbol-specific
- **Target**: Credit ≥ 40% width | **Manage**: 25% profit, close at 5 DTE
- **Risk**: ~€40-50 per trade

---

## 🔧 Integration with Your Existing Tools

Your `option-tools` already has:
- ✅ `marketchameleon.py` → IVR rankings (universe filter)
- ✅ `barchart.py` → Volume/PCR (liquidity filter)
- ✅ `yahoo_tools.py` → HV, skew, kurtosis, earnings, SMA trend
- ✅ `strategy_helper.py` → Assembly pipeline

**Connect them:**
```python
# In daily_runner.py, replace fetch_mock_chain with:
import marketchameleon as mc
import barchart as bc
import yahoo_tools as y
import strategy_helper as s

# 1. Get high-IVR candidates from MarketChameleon
candidates = mc.get_option_list()  # Your existing function
candidates = candidates[candidates['ivr'] > 40]

# 2. Add volume/PCR from Barchart
candidates = s.add_volume_data(candidates)

# 3. Enrich with Yahoo stats (HV, skew, earnings, SMA)
# ... your existing pipeline ...

# 4. Fetch real chains from IB for top 20 candidates
from ib_chain import fetch_chains_for_candidates
chains = fetch_chains_for_candidates(top_symbols, 7, 14)

# 5. Score with strategy models
from strategy_models import score_all_strategies
setups = score_all_strategies(chains, pd.Timestamp.now())
```

---

## 📈 Trade Journal Usage

```python
from trade_journal import Trade, save_trade, get_open_trades, print_performance
import json
from datetime import date

# After filling a trade:
trade = Trade(
    entry_date=date.today().isoformat(),
    strategy="IRON_CONDOR",
    symbol="SPY",
    dte=10,
    iv_rank=55.0,
    legs_json=json.dumps([
        {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.20},
        {'type': 'put', 'strike': 438, 'action': 'buy', 'qty': 1, 'price': 0.55},
        {'type': 'call', 'strike': 452, 'action': 'sell', 'qty': 1, 'price': 1.15},
        {'type': 'call', 'strike': 454, 'action': 'buy', 'qty': 1, 'price': 0.50},
    ]),
    credit_received=1.30,
    max_loss=0.70,
    max_profit=1.30,
    buying_power_used=70.0,
    entry_notes="High IVR, 16Δ/84Δ, $2 wide",
    model_score=42.5,
)
save_trade(trade)

# After closing:
trade.exit_date = date.today().isoformat()
trade.exit_reason = "PROFIT_TARGET"
trade.debit_paid = 0.40
trade.realized_pnl = 0.90
trade.days_held = 5
save_trade(trade)

# Review performance
print_performance()
```

---

## ⚙️ Configuration (config.yaml)

Key settings to adjust:
```yaml
universe:           # Your watchlist
dte_range: [7, 14]  # DTE window
min_iv_rank: 30     # Global IVR floor
max_positions: 2    # Concurrent trades
max_loss_per_trade_eur: 50  # Risk per trade
account_size_eur: 1000

# Per-strategy thresholds (in config or strategy_models.py SYMBOL_CONFIG)
iron_condor:
  min_iv_rank: 40
  delta_short: 0.16
put_credit_spread:
  min_iv_rank: 35
  require_uptrend: true
iron_butterfly:
  min_iv_rank: 50
  max_hv_iv_ratio: 0.80
```

---

## 🎯 Next Steps for You

| Week | Task |
|------|------|
| 1 | Paper trade: Run daily scan, manually place 1-2 trades/week in IB paper |
| 2 | Connect MarketChameleon/Barchart/Yahoo pipeline to daily_runner |
| 3 | Add TastyTrade data fetch (use their SDK or Selenium) |
| 4 | Build simple backtest in `options.ipynb` using your historical data |
| 5 | Go live with 1 contract, track rule adherence in journal |
| 6 | Refine strike/DTE selection based on 20+ trade sample |

---

## 🛡️ Risk Rules (Hard Limits)

1. **Max loss/trade**: €50 (5% account)
2. **Max portfolio heat**: €150 (15%)
3. **No earnings through trade**
4. **No naked short options**
5. **Force close at 2 DTE**
6. **Stop if weekly loss > €100**
7. **All management rules pre-defined at entry**

---

## 📚 Your Research Assets (Already Integrated)

- **CherryTree Node 160** → IC management rules (adapted for shorter DTE)
- **CherryTree Node 88** → Technical filters (RSI, RVOL, VWAP, MA)
- **CherryTree Node 75/83** → Strategy ideas & images
- **option-tools/** → Scanners, stats, Yahoo integration

---

*Run `python daily_runner.py` each morning. Review top 3 setups. Place orders. Journal everything.*