#!/usr/bin/env python3
"""
Position Management Engine - Pure Python Rule Evaluation
Phase 1: Read-only evaluation (no order execution)
Phase 2: Execution via ib_executor.py

Market data and order execution have NO fallback to made-up data. If no real
provider is available, evaluate exits 1 and execute refuses. Simulation exists
only behind an explicit --mock flag (never with --mode live, never touches the journal).

Exit codes (evaluate): 0 = nothing to do, 2 = actions proposed, 1 = error (no usable data).

Usage:
    python manage.py evaluate --mode paper
    python manage.py execute --mode paper --run <run_id>
    python manage.py journal-sync --mode paper --run <run_id>
"""
import sys
import json
import argparse
from datetime import date, datetime
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from trade_journal import get_open_trades, get_trade, Trade, save_trade, DB_PATH
import sqlite3
import yaml


class MarketDataError(Exception):
    """Real market data is missing or inconsistent. Never papered over."""


class ActionType(Enum):
    NONE = "none"
    CLOSE_PROFIT = "close_profit"
    CLOSE_STOP = "close_stop"
    CLOSE_DTE = "close_dte"
    CLOSE_BREACH = "close_breach"
    CLOSE_TREND = "close_trend"
    CLOSE_IVR = "close_ivr"
    ROLL_UNTESTED = "roll_untested"
    ROLL_TESTED = "roll_tested"
    ALERT_MISMATCH = "alert_mismatch"


@dataclass
class Position:
    """Enriched position for rule evaluation"""
    trade: Trade
    underlying_price: float
    option_mids: Dict[str, float]      # key: "C450" or "P440" -> mid price
    option_deltas: Dict[str, float]    # key: "C450" -> delta
    dte: int
    current_credit: float              # Credit to close entire position (sum of mids)
    legs: List[Dict]                   # Parsed from trade.legs_json
    rules: Dict                        # Parsed from trade.rules_json


@dataclass
class ManagementAction:
    action: ActionType
    reason: str
    params: Dict[str, Any]
    trade_id: int
    strategy: str
    symbol: str


def load_config() -> dict:
    from config import load_config as _load
    return _load()


def parse_legs(legs_json: str) -> List[Dict]:
    if not legs_json:
        return []
    return json.loads(legs_json)


def parse_rules(rules_json: str) -> Dict:
    if not rules_json:
        return {}
    return json.loads(rules_json)


def get_ib_port(mode: str, config: dict) -> int:
    if mode == 'live':
        return config['ib_port_live']
    return config['ib_port_paper']


# ============================================================
# RULE ENGINE - Pure functions, no side effects
# ============================================================

def evaluate_iron_condor(pos: Position, config: dict) -> List[ManagementAction]:
    """Evaluate IC management rules"""
    actions = []
    rules = pos.rules
    strat_cfg = config['iron_condor']
    
    # 1. Profit target (checked via resting order, but verify here)
    profit_target = rules.get('profit_target_pct', strat_cfg['profit_target_pct'])
    if pos.current_credit <= pos.trade.credit_received * (1 - profit_target):
        actions.append(ManagementAction(
            action=ActionType.CLOSE_PROFIT,
            reason=f"Profit target {profit_target:.0%} reached (credit to close: ${pos.current_credit:.2f} <= ${pos.trade.credit_received * (1-profit_target):.2f})",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions  # Profit target takes priority
    
    # 2. Stop loss
    stop_mult = rules.get('stop_loss_multiple', strat_cfg['stop_loss_multiple'])
    if pos.current_credit >= pos.trade.credit_received * stop_mult:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_STOP,
            reason=f"Stop loss {stop_mult}x hit (credit to close: ${pos.current_credit:.2f} >= ${pos.trade.credit_received * stop_mult:.2f})",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 3. DTE force close
    close_dte = rules.get('close_dte', strat_cfg['close_dte'])
    if pos.dte <= close_dte:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_DTE,
            reason=f"DTE {pos.dte} <= force-close {close_dte}",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2), 'urgent': True},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 4. Breach of short strike
    put_short = None
    call_short = None
    for leg in pos.legs:
        if leg['type'] == 'put' and leg['action'] == 'sell':
            put_short = leg['strike']
        elif leg['type'] == 'call' and leg['action'] == 'sell':
            call_short = leg['strike']
    
    breach_action = rules.get('breach', {}).get('action', 'close')
    if put_short and pos.underlying_price <= put_short:
        if pos.dte > 7 and breach_action == 'roll':
            actions.append(ManagementAction(
                action=ActionType.ROLL_UNTESTED,
                reason=f"Put short strike {put_short} breached (price: {pos.underlying_price}), DTE {pos.dte} > 7, rolling untested side",
                params={'breached_side': 'put', 'untested_side': 'call', 'new_delta': 0.16, 'min_credit': 0.10},
                trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
            ))
        else:
            reason = f"Put short strike {put_short} breached (price: {pos.underlying_price})"
            if pos.dte <= 7:
                reason += f", DTE {pos.dte} <= force-close {7}"
            else:
                reason += f", breach_action={breach_action}"
            actions.append(ManagementAction(
                action=ActionType.CLOSE_BREACH,
                reason=reason,
                params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
                trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
            ))
        return actions
    
    if call_short and pos.underlying_price >= call_short:
        if pos.dte > 7 and breach_action == 'roll':
            actions.append(ManagementAction(
                action=ActionType.ROLL_UNTESTED,
                reason=f"Call short strike {call_short} breached (price: {pos.underlying_price}), DTE {pos.dte} > 7, rolling untested side",
                params={'breached_side': 'call', 'untested_side': 'put', 'new_delta': 0.16, 'min_credit': 0.10},
                trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
            ))
        else:
            reason = f"Call short strike {call_short} breached (price: {pos.underlying_price})"
            if pos.dte <= 7:
                reason += f", DTE {pos.dte} <= force-close {7}"
            else:
                reason += f", breach_action={breach_action}"
            actions.append(ManagementAction(
                action=ActionType.CLOSE_BREACH,
                reason=reason,
                params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
                trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
            ))
        return actions
    
    return actions


def evaluate_put_credit_spread(pos: Position, config: dict) -> List[ManagementAction]:
    """Evaluate PCS management rules"""
    actions = []
    rules = pos.rules
    strat_cfg = config['put_credit_spread']
    
    # 1. Profit target
    profit_target = rules.get('profit_target_pct', strat_cfg['profit_target_pct'])
    if pos.current_credit <= pos.trade.credit_received * (1 - profit_target):
        actions.append(ManagementAction(
            action=ActionType.CLOSE_PROFIT,
            reason=f"Profit target {profit_target:.0%} reached",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 2. Stop loss
    stop_mult = rules.get('stop_loss_multiple', strat_cfg['stop_loss_multiple'])
    if pos.current_credit >= pos.trade.credit_received * stop_mult:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_STOP,
            reason=f"Stop loss {stop_mult}x hit",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 3. DTE force close
    close_dte = rules.get('close_dte', strat_cfg['close_dte'])
    if pos.dte <= close_dte:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_DTE,
            reason=f"DTE {pos.dte} <= force-close {close_dte}",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2), 'urgent': True},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 4. Trend break
    if rules.get('trend_break', True):
        pass  # Need SMA data
    
    # 5. IVR drop
    min_ivr = rules.get('min_iv_rank', 20)
    if pos.trade.iv_rank and pos.trade.iv_rank < min_ivr:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_IVR,
            reason=f"IVR {pos.trade.iv_rank:.1f}% dropped below minimum {min_ivr}%",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 6. Breach of short strike
    short_strike = None
    for leg in pos.legs:
        if leg['type'] == 'put' and leg['action'] == 'sell':
            short_strike = leg['strike']
            break
    
    if short_strike and pos.underlying_price <= short_strike:
        if pos.dte > 7:
            actions.append(ManagementAction(
                action=ActionType.ROLL_TESTED,
                reason=f"Short put {short_strike} breached, rolling down",
                params={'new_strike': short_strike - 1, 'min_credit': 0.05},
                trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
            ))
        else:
            actions.append(ManagementAction(
                action=ActionType.CLOSE_BREACH,
                reason=f"Short put {short_strike} breached, DTE {pos.dte} <= 7",
                params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
                trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
            ))
        return actions
    
    return actions


def evaluate_iron_butterfly(pos: Position, config: dict) -> List[ManagementAction]:
    """Evaluate IBF management rules"""
    actions = []
    rules = pos.rules
    strat_cfg = config['iron_butterfly']
    
    # 1. Profit target
    profit_target = rules.get('profit_target_pct', strat_cfg['profit_target_pct'])
    if pos.current_credit <= pos.trade.credit_received * (1 - profit_target):
        actions.append(ManagementAction(
            action=ActionType.CLOSE_PROFIT,
            reason=f"Profit target {profit_target:.0%} reached",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 2. Stop loss
    stop_mult = rules.get('stop_loss_multiple', strat_cfg['stop_loss_multiple'])
    if pos.current_credit >= pos.trade.credit_received * stop_mult:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_STOP,
            reason=f"Stop loss {stop_mult}x hit",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 3. DTE force close (IBF: 5 DTE)
    close_dte = rules.get('close_dte', strat_cfg['close_dte'])
    if pos.dte <= close_dte:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_DTE,
            reason=f"DTE {pos.dte} <= force-close {close_dte} (IBF gamma risk)",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2), 'urgent': True},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 4. Price moved beyond tent
    center = None
    wing_width = strat_cfg['width_usd']
    for leg in pos.legs:
        if leg['action'] == 'sell':
            center = leg['strike']
            break
    
    if center and abs(pos.underlying_price - center) > wing_width * 0.5:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_BREACH,
            reason=f"Price {pos.underlying_price} moved > 0.5*wing from center {center}",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    # 5. IVR drop
    min_ivr = rules.get('min_iv_rank', 30)
    if pos.trade.iv_rank and pos.trade.iv_rank < min_ivr:
        actions.append(ManagementAction(
            action=ActionType.CLOSE_IVR,
            reason=f"IVR {pos.trade.iv_rank:.1f}% dropped below minimum {min_ivr}%",
            params={'type': 'close', 'limit_price': round(pos.current_credit, 2)},
            trade_id=pos.trade.id, strategy=pos.trade.strategy, symbol=pos.trade.symbol
        ))
        return actions
    
    return actions


STRATEGY_EVALUATORS = {
    'IRON_CONDOR': evaluate_iron_condor,
    'PUT_CREDIT_SPREAD': evaluate_put_credit_spread,
    'IRON_BUTTERFLY': evaluate_iron_butterfly,
}


def leg_key(leg: Dict) -> str:
    return f"{leg['type'][0].upper()}{leg['strike']}"


def credit_to_close(legs: List[Dict], mids: Dict[str, float], trade: Trade) -> float:
    """Net amount to pay to close all legs (sell legs are bought back, buy legs sold).
    Raises MarketDataError instead of guessing when a quote is missing or implausible."""
    missing = [leg_key(l) for l in legs if mids.get(leg_key(l)) is None]
    if missing:
        raise MarketDataError(f"trade {trade.id} {trade.symbol}: no quote for {missing}")
    total = 0.0
    for leg in legs:
        mid = mids[leg_key(leg)]
        total += mid if leg['action'] == 'sell' else -mid
    if total < 0:
        raise MarketDataError(f"trade {trade.id} {trade.symbol}: negative credit-to-close {total:.2f} (stale or crossed quotes?)")
    return total


def evaluate_position(trade: Trade, market_data: dict, config: dict) -> List[ManagementAction]:
    """Evaluate a single position, return list of actions. Raises MarketDataError on bad data."""
    if not market_data.get('underlying_price') or market_data['underlying_price'] <= 0:
        raise MarketDataError(f"trade {trade.id} {trade.symbol}: no underlying price")
    # Build Position object
    legs = parse_legs(trade.legs_json)
    rules = parse_rules(trade.rules_json)
    
    current_credit = credit_to_close(legs, market_data.get('option_mids', {}), trade)

    pos = Position(
        trade=trade,
        underlying_price=market_data['underlying_price'],
        option_mids=market_data.get('option_mids', {}),
        option_deltas=market_data.get('option_deltas', {}),
        dte=trade.dte - (date.today() - datetime.fromisoformat(trade.entry_date).date()).days,
        current_credit=current_credit,
        legs=legs,
        rules=rules,
    )
    
    evaluator = STRATEGY_EVALUATORS.get(trade.strategy)
    if not evaluator:
        return [ManagementAction(
            action=ActionType.ALERT_MISMATCH,
            reason=f"No evaluator for strategy {trade.strategy}",
            params={}, trade_id=trade.id, strategy=trade.strategy, symbol=trade.symbol
        )]
    
    return evaluator(pos, config)


def reconcile_positions(journal_trades: List[Trade], ib_positions: List[dict]) -> List[str]:
    """Compare journal vs IB positions, return list of alerts"""
    alerts = []
    
    # Group IB positions by underlying
    ib_by_symbol = {}
    for p in ib_positions:
        sym = p.get('symbol', '')
        if sym not in ib_by_symbol:
            ib_by_symbol[sym] = []
        ib_by_symbol[sym].append(p)
    
    # Check each journal trade
    for trade in journal_trades:
        sym = trade.symbol
        ib_legs = ib_by_symbol.get(sym, [])
        
        if not ib_legs:
            alerts.append(f"JOURNAL-ONLY: {trade.id} {trade.strategy} {sym} has no IB position")
            continue
        
        journal_legs = len(parse_legs(trade.legs_json))
        if len(ib_legs) != journal_legs:
            alerts.append(f"LEG-MISMATCH: {trade.id} {sym} journal={journal_legs} legs, IB={len(ib_legs)} legs")
    
    # Check for IB positions not in journal
    journal_symbols = {t.symbol for t in journal_trades}
    for sym, legs in ib_by_symbol.items():
        if sym not in journal_symbols:
            alerts.append(f"IB-ONLY: {sym} has {len(legs)} legs not in journal")
    
    return alerts


# ============================================================
# MARKET DATA - real providers only. Mock requires an explicit flag.
# ============================================================

def _mock_market_data(symbol: str, legs: List[Dict]) -> dict:
    """RANDOM test data. Only reachable via --mock (see fetch_market_data)."""
    import random
    base_price = {'SPY': 450, 'QQQ': 380, 'IWM': 190}.get(symbol, 100)
    price = base_price * (1 + random.uniform(-0.02, 0.02))
    mids, deltas = {}, {}
    for leg in legs:
        key = leg_key(leg)
        mids[key] = round(random.uniform(0.1, 2.0), 2)
        deltas[key] = round(random.uniform(0.05, 0.5) * (1 if leg['type'] == 'call' else -1), 3)
    return {'underlying_price': price, 'option_mids': mids, 'option_deltas': deltas}


# broker name -> callable(symbol, legs, mode, config) -> {'underlying_price', 'option_mids', 'option_deltas'}
# Register real providers here (none implemented yet).
MARKET_DATA_PROVIDERS: Dict[str, Any] = {}


def fetch_market_data(symbol: str, legs: List[Dict], mode: str, config: dict, allow_mock: bool = False) -> dict:
    if allow_mock:
        return _mock_market_data(symbol, legs)
    broker = config.get('broker')
    provider = MARKET_DATA_PROVIDERS.get(broker)
    if provider is None:
        raise MarketDataError(f"no real market data provider implemented for broker '{broker}' (use --mock only for tests)")
    return provider(symbol, legs, mode, config)


# ============================================================
# MAIN EVALUATION
# ============================================================

def run_evaluation(mode: str = 'paper', allow_mock: bool = False) -> dict:
    """Main evaluation entry point.

    Returns dict with 'actions', 'alerts' and 'errors'. A trade whose data is
    unusable produces an entry in 'errors' (and no action); other trades are still
    evaluated so a stop-loss on one position is not blocked by bad data on another."""
    config = load_config()
    trades = get_open_trades()

    if not trades:
        return {'actions': [], 'alerts': [], 'errors': [], 'positions_checked': 0,
                'message': 'No open positions', 'mock': allow_mock}

    ib_positions = []  # TODO: fetch real broker positions for reconciliation
    alerts = reconcile_positions(trades, ib_positions)

    all_actions, errors = [], []
    for trade in trades:
        try:
            legs = parse_legs(trade.legs_json)
            market_data = fetch_market_data(trade.symbol, legs, mode, config, allow_mock)
            entry_date = datetime.fromisoformat(trade.entry_date).date()
            trade.dte = trade.dte - (date.today() - entry_date).days
            all_actions.extend(evaluate_position(trade, market_data, config))
        except MarketDataError as e:
            errors.append(str(e))

    actions_json = [{
        'trade_id': a.trade_id, 'strategy': a.strategy, 'symbol': a.symbol,
        'action': a.action.value, 'reason': a.reason, 'params': a.params,
    } for a in all_actions]

    return {
        'actions': actions_json,
        'alerts': alerts,
        'errors': errors,
        'positions_checked': len(trades),
        'timestamp': datetime.now().isoformat(),
        'mock': allow_mock,
    }


# ============================================================
# PHASE 2: EXECUTION
# ============================================================

def run_execute(run_id: str, mode: str, params_json: str = None, allow_mock: bool = False) -> dict:
    """Execute management actions from a previous evaluation run.
    
    There is no real execution backend wired in yet: without allow_mock this refuses
    to do anything. With allow_mock it simulates fills and does NOT touch the journal.
    
    Args:
        run_id: Unique identifier for this execution run
        mode: 'paper' or 'live'
        params_json: Optional JSON with keys:
            - 'actions': list of actions to execute (bypasses re-evaluation)
            - 'overrides': dict of trade_id -> params overrides
    """
    if not allow_mock:
        return {'success': False, 'run_id': run_id, 'executed': [], 'failed': [],
                'error': 'no real execution backend implemented; refusing to simulate (use --mock in paper mode for tests)'}

    config = load_config()
    
    # Parse optional params
    overrides = {}
    actions = None
    if params_json:
        params = json.loads(params_json)
        overrides = params.get('overrides', {})
        actions = params.get('actions')
    
    # If actions not provided via params, re-run evaluation
    if actions is None:
        eval_result = run_evaluation(mode, allow_mock=allow_mock)
        actions = eval_result['actions']
    
    if not actions:
        return {'success': False, 'error': 'No actions to execute', 'run_id': run_id}
    
    executed = []
    failed = []
    
    for action in actions:
        trade_id = action['trade_id']
        action_type = action['action']
        params = action.get('params', {})
        
        # Apply overrides if provided
        if str(trade_id) in overrides:
            params.update(overrides[str(trade_id)])
        
        try:
            # In Phase 2 (paper), simulate execution
            # In Phase 3+, call ib_executor.py
            result = execute_action_mock(trade_id, action_type, params, mode)
            executed.append({
                'trade_id': trade_id,
                'action': action_type,
                'success': True,
                'result': result
            })
        except Exception as e:
            failed.append({
                'trade_id': trade_id,
                'action': action_type,
                'success': False,
                'error': str(e)
            })
    
    return {
        'success': len(failed) == 0,
        'mock': True,
        'run_id': run_id,
        'executed': executed,
        'failed': failed,
        'timestamp': datetime.now().isoformat(),
    }


def execute_action_mock(trade_id: int, action_type: str, params: dict, mode: str) -> dict:
    """SIMULATED fill for tests. Never writes to the journal."""
    import random
    
    # Get trade from journal
    trade = get_trade(trade_id)
    if not trade:
        raise ValueError(f"Trade {trade_id} not found")
    
    limit_price = params.get('limit_price', 0)
    
    # Simulate fill at limit price (or slightly better/worse)
    fill_price = limit_price + random.uniform(-0.02, 0.02)
    fill_price = max(0.01, round(fill_price, 2))
    
    # Calculate commission
    commission_per_leg = trade.commission_per_leg or 0.65
    legs_count = len(parse_legs(trade.legs_json))
    total_commission = commission_per_leg * legs_count * 2  # Entry + exit
    
    # Calculate P&L
    if trade.credit_received and limit_price > 0:
        gross_pnl = trade.credit_received - limit_price  # Credit received - debit paid
    else:
        gross_pnl = -limit_price  # Debit paid
    
    net_pnl = gross_pnl - total_commission / 100  # Convert to per-contract
    
    return {
        'trade_id': trade_id,
        'fill_price': fill_price,
        'limit_price': limit_price,
        'commission': total_commission,
        'gross_pnl': gross_pnl,
        'net_pnl': net_pnl,
        'status': 'simulated',
    }


def run_journal_sync(run_id: str, mode: str, allow_mock: bool = False) -> dict:
    """Sync journal with real broker fills. Not implemented: reports failure instead of faking success."""
    if not allow_mock:
        return {'success': False, 'run_id': run_id, 'synced': 0,
                'error': 'journal sync against a real broker is not implemented'}
    return {'success': True, 'mock': True, 'run_id': run_id, 'synced': 0,
            'timestamp': datetime.now().isoformat()}


# ============================================================
# MAIN
# ============================================================

def load_config() -> dict:
    from config import load_config as _load
    return _load()


def main():
    parser = argparse.ArgumentParser(description='Options Position Management')
    parser.add_argument('command', choices=['evaluate', 'execute', 'journal-sync'], help='Command to run')
    parser.add_argument('--mode', choices=['paper', 'live'], default='paper', help='Trading mode')
    parser.add_argument('--run', type=str, help='Run ID for execute/journal-sync command')
    parser.add_argument('--params', type=str, help='JSON params override for execute')
    parser.add_argument('--output', choices=['json', 'text'], default='json', help='Output format')
    parser.add_argument('--mock', action='store_true', help='TEST ONLY: random market data / simulated fills (paper mode only)')
    
    args = parser.parse_args()
    if args.mock and args.mode == 'live':
        print('Error: --mock is not allowed with --mode live', file=sys.stderr)
        sys.exit(1)
    
    if args.command == 'evaluate':
        result = run_evaluation(args.mode, allow_mock=args.mock)
        for err in result.get('errors', []):
            print(f'ERROR: {err}', file=sys.stderr)
        
        if args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"Positions checked: {result['positions_checked']}")
            print(f"Alerts: {len(result['alerts'])}")
            for alert in result['alerts']:
                print(f"  ALERT: {alert}")
            print(f"Actions: {len(result['actions'])}")
            for a in result['actions']:
                print(f"  {a['action']} {a['symbol']} #{a['trade_id']}: {a['reason']}")
        
        # Exit codes for stepflow
        if result['actions']:
            sys.exit(2)  # Actions needed (errors for other trades are on stderr)
        elif result.get('errors'):
            sys.exit(1)  # No usable data for at least one open position
        else:
            sys.exit(0)  # Nothing to do (alerts are informational)
    
    elif args.command == 'execute':
        if not args.run:
            print("Error: --run required for execute")
            sys.exit(1)
        result = run_execute(args.run, args.mode, args.params, allow_mock=args.mock)
        if args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"Success: {result['success']}")
            for fill in result.get('fills', []):
                print(f"  Fill: {fill}")
        sys.exit(0 if result['success'] else 1)
    
    elif args.command == 'journal-sync':
        if not args.run:
            print("Error: --run required for journal-sync")
            sys.exit(1)
        result = run_journal_sync(args.run, args.mode, allow_mock=args.mock)
        if args.output == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"Synced: {result['synced']} trades")
        sys.exit(0 if result['success'] else 1)


if __name__ == '__main__':
    main()