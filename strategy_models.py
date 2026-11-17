"""
Strategy Models for Semi-Automated Options Trading
Extends your existing option-tools pipeline.
"""
from dataclasses import dataclass
from typing import Optional, Dict
import pandas as pd


# Symbol-specific configuration
SYMBOL_CONFIG = {
    'SPY': {'width': 1.0, 'ic_width': 2.0, 'ibf_width': 2.0, 'min_credit': 0.30},
    'QQQ': {'width': 1.0, 'ic_width': 2.0, 'ibf_width': 2.0, 'min_credit': 0.30},
    'IWM': {'width': 1.0, 'ic_width': 2.0, 'ibf_width': 2.0, 'min_credit': 0.30},
    'SPX': {'width': 5.0, 'ic_width': 10.0, 'ibf_width': 10.0, 'min_credit': 0.30},
    'NDX': {'width': 25.0, 'ic_width': 50.0, 'ibf_width': 50.0, 'min_credit': 0.30},
    'RUT': {'width': 10.0, 'ic_width': 20.0, 'ibf_width': 20.0, 'min_credit': 0.30},
    # Default for stocks
    'DEFAULT': {'width': 1.0, 'ic_width': 2.0, 'ibf_width': 2.0, 'min_credit': 0.30},
}


def get_symbol_config(symbol: str) -> Dict:
    return SYMBOL_CONFIG.get(symbol, SYMBOL_CONFIG['DEFAULT'])


@dataclass
class OptionChainSnapshot:
    """Normalized option chain from IB/TastyTrade/Yahoo"""
    symbol: str
    underlying_price: float
    dte: int
    iv_rank: float
    hv_20: float
    calls: pd.DataFrame  # columns: strike, bid, ask, mid, delta, gamma, theta, vega, oi, volume
    puts: pd.DataFrame
    earnings_date: Optional[pd.Timestamp] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None


@dataclass
class ScoredSetup:
    strategy: str
    symbol: str
    score: float
    details: dict
    management_rules: list[str]


def find_strike_by_delta(df: pd.DataFrame, target_delta: float, option_type: str) -> float:
    """Find strike closest to target delta. option_type: 'call' or 'put'"""
    if df.empty:
        return None
    # Delta for puts is negative
    if option_type == 'put':
        target_delta = -abs(target_delta)
    else:
        target_delta = abs(target_delta)
    
    df = df.copy()
    df['delta_diff'] = (df['delta'] - target_delta).abs()
    return df.loc[df['delta_diff'].idxmin(), 'strike']


def get_mid_price(df: pd.DataFrame, strike: float) -> float:
    """Get mid price for a strike"""
    row = df[df['strike'] == strike]
    if row.empty:
        return None
    return (row.iloc[0]['bid'] + row.iloc[0]['ask']) / 2


def has_earnings_before_expiry(earnings_date: Optional[pd.Timestamp], dte: int, ref_date: pd.Timestamp) -> bool:
    if earnings_date is None or pd.isna(earnings_date):
        return False
    expiry_date = ref_date + pd.Timedelta(days=dte)
    return earnings_date <= expiry_date


def trend_direction(sma_50: Optional[float], sma_200: Optional[float]) -> str:
    if sma_50 and sma_200 and sma_50 > 0 and sma_200 > 0:
        return "up" if sma_50 > sma_200 else "down"
    return "unknown"


# ============================================================
# STRATEGY 1: High-IVR Iron Condor
# ============================================================
def score_iron_condor(chain: OptionChainSnapshot, ref_date: pd.Timestamp) -> Optional[ScoredSetup]:
    """
    7-14 DTE, IVR > 40%, 16Δ/84Δ short strikes, symbol-appropriate width, credit >= 33% width
    """
    if chain.iv_rank < 40:
        return None
    if chain.dte < 7 or chain.dte > 14:
        return None
    if has_earnings_before_expiry(chain.earnings_date, chain.dte, ref_date):
        return None
    
    cfg = get_symbol_config(chain.symbol)
    target_width = cfg['ic_width']
    
    # Find 16Δ put and 84Δ call
    put_short = find_strike_by_delta(chain.puts, 0.16, 'put')
    call_short = find_strike_by_delta(chain.calls, 0.16, 'call')
    
    if put_short is None or call_short is None:
        return None
    
    actual_width = call_short - put_short
    # Allow some flexibility around target width
    if actual_width < target_width * 0.7 or actual_width > target_width * 1.5:
        return None
    
    # Long strikes at target width
    put_long = put_short - target_width
    call_long = call_short + target_width
    
    # Calculate credit
    credit = (get_mid_price(chain.puts, put_short) or 0) + \
             (get_mid_price(chain.calls, call_short) or 0) - \
             (get_mid_price(chain.puts, put_long) or 0) - \
             (get_mid_price(chain.calls, call_long) or 0)
    
    if credit <= 0:
        return None
    
    credit_ratio = credit / target_width
    if credit_ratio < cfg['min_credit']:
        return None
    
    # Scoring
    score = (credit_ratio * 40) + (chain.iv_rank * 0.3) + (10 / chain.dte)
    
    return ScoredSetup(
        strategy="IRON_CONDOR",
        symbol=chain.symbol,
        score=score,
        details={
            'put_short': put_short, 'put_long': put_long,
            'call_short': call_short, 'call_long': call_long,
            'width': target_width, 'credit': round(credit, 2),
            'credit_ratio': round(credit_ratio, 3),
            'pop_est': round(1 - 2 * 0.16, 2),  # ~0.68
            'max_loss': round(target_width - credit, 2),
            'max_profit': round(credit, 2),
        },
        management_rules=[
            "Close at 25-33% profit (BTC for 25-33% of credit)",
            "If short strike breached + >7 DTE: roll untested side to new 16Δ, collect >= $0.10",
            "If short strike breached + <=7 DTE: close breached side, roll untested to 30Δ",
            "Force close at 2 DTE",
        ]
    )


# ============================================================
# STRATEGY 2: Put Credit Spread (Bullish)
# ============================================================
def score_put_credit_spread(chain: OptionChainSnapshot, ref_date: pd.Timestamp) -> Optional[ScoredSetup]:
    """
    10-14 DTE, IVR > 35%, trend UP (50>200 SMA), 30Δ short put, symbol-appropriate width, credit >= 30% width
    """
    if chain.iv_rank < 35:
        return None
    if chain.dte < 10 or chain.dte > 14:
        return None
    if has_earnings_before_expiry(chain.earnings_date, chain.dte, ref_date):
        return None
    
    trend = trend_direction(chain.sma_50, chain.sma_200)
    if trend != "up":
        return None
    
    cfg = get_symbol_config(chain.symbol)
    width = cfg['width']
    
    # 30Δ put
    short_strike = find_strike_by_delta(chain.puts, 0.30, 'put')
    if short_strike is None:
        return None
    
    long_strike = short_strike - width
    
    credit = (get_mid_price(chain.puts, short_strike) or 0) - (get_mid_price(chain.puts, long_strike) or 0)
    
    if credit <= 0:
        return None
    
    credit_ratio = credit / width
    if credit_ratio < cfg['min_credit']:
        return None
    
    score = (credit_ratio * 35) + (chain.iv_rank * 0.4) + (15 / chain.dte)
    
    return ScoredSetup(
        strategy="PUT_CREDIT_SPREAD",
        symbol=chain.symbol,
        score=score,
        details={
            'short_strike': short_strike, 'long_strike': long_strike,
            'width': width, 'credit': round(credit, 2),
            'credit_ratio': round(credit_ratio, 3),
            'pop_est': 0.70,
            'max_loss': round(width - credit, 2),
            'max_profit': round(credit, 2),
        },
        management_rules=[
            "Close at 50% profit",
            "If underlying touches short strike + >7 DTE: roll down 1 strike for credit",
            "If underlying touches short strike + <=7 DTE: close",
            "Close if trend breaks (50MA < 200MA)",
            "Close if IVR drops < 20%",
            "Force close at 2 DTE",
        ]
    )


# ============================================================
# STRATEGY 3: Iron Butterfly (Range-Bound High IVR)
# ============================================================
def score_iron_butterfly(chain: OptionChainSnapshot, ref_date: pd.Timestamp) -> Optional[ScoredSetup]:
    """
    7-10 DTE, IVR > 50%, HV/IV < 0.8, ATM center, symbol-appropriate wings, credit >= 40% width
    """
    if chain.iv_rank < 50:
        return None
    if chain.dte < 7 or chain.dte > 10:
        return None
    if has_earnings_before_expiry(chain.earnings_date, chain.dte, ref_date):
        return None
    
    hv_iv_ratio = chain.hv_20 / chain.iv_rank if chain.iv_rank > 0 else 1
    if hv_iv_ratio > 0.8:
        return None
    
    cfg = get_symbol_config(chain.symbol)
    wing_width = cfg['ibf_width']
    
    # ATM center strike
    atm_strike = round(chain.underlying_price)
    # Ensure strike exists in chain
    if atm_strike not in chain.calls['strike'].values:
        atm_strike = chain.calls.iloc[(chain.calls['strike'] - chain.underlying_price).abs().argmin()]['strike']
    
    put_long = atm_strike - wing_width
    call_long = atm_strike + wing_width
    
    credit = (get_mid_price(chain.puts, atm_strike) or 0) + \
             (get_mid_price(chain.calls, atm_strike) or 0) - \
             (get_mid_price(chain.puts, put_long) or 0) - \
             (get_mid_price(chain.calls, call_long) or 0)
    
    if credit <= 0:
        return None
    
    credit_ratio = credit / wing_width
    if credit_ratio < 0.40:
        return None
    
    score = (credit_ratio * 30) + (chain.iv_rank * 0.3) + ((1 - hv_iv_ratio) * 20)
    
    return ScoredSetup(
        strategy="IRON_BUTTERFLY",
        symbol=chain.symbol,
        score=score,
        details={
            'center_strike': atm_strike,
            'put_long': put_long, 'call_long': call_long,
            'wing_width': wing_width, 'credit': round(credit, 2),
            'credit_ratio': round(credit_ratio, 3),
            'max_profit': round(credit, 2),
            'max_loss': round(wing_width - credit, 2),
        },
        management_rules=[
            "Close at 25% profit (fast decay near center)",
            "If price moves > 0.5 * wing_width from center: close or roll center to new ATM",
            "Close at 5 DTE (gamma risk)",
            "Close if IVR drops < 30%",
            "Force close at 2 DTE",
        ]
    )


# ============================================================
# MAIN SCORING PIPELINE
# ============================================================
def score_all_strategies(chains: list[OptionChainSnapshot], ref_date: pd.Timestamp) -> list[ScoredSetup]:
    """Run all three strategy models on a list of chain snapshots"""
    results = []
    for chain in chains:
        for scorer in [score_iron_condor, score_put_credit_spread, score_iron_butterfly]:
            setup = scorer(chain, ref_date)
            if setup:
                results.append(setup)
    # Sort by score descending
    return sorted(results, key=lambda x: x.score, reverse=True)


def print_top_setups(setups: list[ScoredSetup], top_n: int = 5):
    """Pretty print top setups"""
    print(f"\n{'='*80}")
    print(f"TOP {top_n} SETUPS")
    print(f"{'='*80}")
    for i, s in enumerate(setups[:top_n], 1):
        print(f"\n#{i} [{s.strategy}] {s.symbol}  Score: {s.score:.1f}")
        for k, v in s.details.items():
            print(f"    {k}: {v}")
        print("    Management:")
        for rule in s.management_rules:
            print(f"      - {rule}")


# ============================================================
# EXAMPLE USAGE (integrate with your existing pipeline)
# ============================================================
if __name__ == "__main__":
    # This shows how to connect with your existing tools
    print("Strategy models loaded. Import and use with your chain data.")
    print("Example:")
    print("  from strategy_models import score_all_strategies, OptionChainSnapshot")
    print("  chains = [fetch_chain_from_ib(sym) for sym in candidate_symbols]")
    print("  setups = score_all_strategies(chains, pd.Timestamp.now())")
    print("  print_top_setups(setups)")