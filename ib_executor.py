"""
Interactive Brokers Order Execution for Options Strategies
Places combo (BAG) orders for spreads, condors, butterflies.
Requires real-time market data subscription for option pricing.
"""
from ib_insync import IB, Stock, Option, Order, ComboLeg, Contract
from ib_insync import util
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
import sys
import asyncio

# CRITICAL: Set event loop BEFORE importing ib_insync
if sys.version_info >= (3, 10):
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

util.patchAsyncio()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class LegOrder:
    """Single leg order specification"""
    symbol: str
    expiry: str          # YYYYMMDD
    strike: float
    right: str           # 'C' or 'P'
    action: str          # 'BUY' or 'SELL'
    quantity: int
    exchange: str
    currency: str = 'USD'
    trading_class: str = ''


@dataclass
class ComboOrder:
    """Complete combo order"""
    legs: List[LegOrder]
    order_type: str      # 'LMT'
    limit_price: float   # Net credit (positive) or debit (negative)
    tif: str = 'GTC'
    outside_rth: bool = False


@dataclass
class ExecutionResult:
    """Result of order execution"""
    success: bool
    order_id: Optional[int] = None
    fills: List[Dict] = None
    error: Optional[str] = None
    combo_contract: Optional[Contract] = None


class IBExecutor:
    def __init__(self, host: str = '127.0.0.1', port: int = 4002, client_id: int = 10):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self.connected = False
    
    def connect(self):
        if not self.connected:
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            self.connected = True
            logger.info(f"Connected to IB on {self.host}:{self.port} (clientId={self.client_id})")
    
    def disconnect(self):
        if self.connected:
            self.ib.disconnect()
            self.connected = False
    
    def qualify_legs(self, legs: List[LegOrder]) -> List[Option]:
        """Qualify all option legs and return list of Option contracts"""
        options = []
        for leg in legs:
            opt = Option(
                leg.symbol, leg.expiry, leg.strike, leg.right,
                leg.exchange, currency=leg.currency,
                tradingClass=leg.trading_class or leg.symbol
            )
            self.ib.qualifyContracts(opt)
            options.append(opt)
        return options
    
    def create_combo_contract(self, legs: List[LegOrder]) -> Contract:
        """Create a BAG contract from legs"""
        combo = Contract()
        combo.symbol = legs[0].symbol
        combo.secType = 'BAG'
        combo.currency = legs[0].currency
        combo.exchange = legs[0].exchange
        combo.comboLegs = []
        
        for leg in legs:
            opt = Option(
                leg.symbol, leg.expiry, leg.strike, leg.right,
                leg.exchange, currency=leg.currency,
                tradingClass=leg.trading_class or leg.symbol
            )
            self.ib.qualifyContracts(opt)
            
            cl = ComboLeg()
            cl.conId = opt.conId
            cl.ratio = leg.quantity
            cl.action = leg.action
            cl.exchange = leg.exchange
            combo.comboLegs.append(cl)
        
        return combo
    
    def get_market_price(self, combo: Contract) -> Tuple[Optional[float], Optional[float]]:
        """Get bid/ask for combo contract"""
        ticker = self.ib.reqMktData(combo, '', False, False)
        self.ib.sleep(1)
        
        bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
        ask = ticker.ask if ticker.ask and ticker.ask > 0 else None
        
        self.ib.cancelMktData(combo)
        return bid, ask
    
    def place_combo_order(self, combo: Contract, order: Order) -> Tuple[Optional[int], List[Dict]]:
        """Place combo order, wait for fill, return order_id and fills"""
        trade = self.ib.placeOrder(combo, order)
        
        # Wait for fill or timeout
        timeout = 30  # seconds
        start = util.time()
        while not trade.isDone():
            if util.time() - start > timeout:
                self.ib.cancelOrder(trade.order)
                raise TimeoutError(f"Order {trade.order.orderId} not filled within {timeout}s")
            self.ib.sleep(0.5)
        
        order_id = trade.order.orderId
        fills = []
        for fill in trade.fills:
            fills.append({
                'order_id': order_id,
                'contract': fill.contract.localSymbol if fill.contract else '',
                'execution_id': fill.execution.execId,
                'time': fill.execution.time,
                'side': fill.execution.side,
                'shares': fill.execution.shares,
                'price': fill.execution.price,
                'commission': fill.commissionReport.commission if fill.commissionReport else 0,
            })
        
        return order_id, fills
    
    def execute_close_position(self, combo_order: ComboOrder, max_slippage: float = 0.05) -> ExecutionResult:
        """
        Execute a closing order for an existing position.
        Walks the limit price from mid to natural within max_slippage.
        """
        self.connect()
        
        try:
            # Create combo contract
            combo = self.create_combo_contract(combo_order.legs)
            
            # Get current market
            bid, ask = self.get_market_price(combo)
            
            if bid is None or ask is None:
                return ExecutionResult(
                    success=False,
                    error="No market data for combo (need real-time OPRA subscription)"
                )
            
            mid = (bid + ask) / 2
            
            # Determine limit price based on order direction
            # For closing a credit position: we pay debit (positive limit)
            # For closing a debit position: we receive credit (negative limit)
            target_price = combo_order.limit_price
            
            # Walk price: start at mid, move toward natural in steps
            # For credit spread close (paying debit): start at mid, walk up to ask
            # For debit spread close (receiving credit): start at mid, walk down to bid
            
            if target_price > 0:  # Paying debit to close
                walk_start = mid
                walk_end = min(target_price * (1 + max_slippage), ask * 1.01)
            else:  # Receiving credit to close
                walk_start = mid
                walk_end = max(target_price * (1 + max_slippage), bid * 0.99)
            
            # Create order
            order = Order()
            order.action = 'BUY' if target_price > 0 else 'SELL'
            order.orderType = 'LMT'
            order.lmtPrice = round(walk_start, 2)
            order.totalQuantity = combo_order.legs[0].quantity
            order.tif = combo_order.tif
            order.outsideRth = combo_order.outside_rth
            order.transmit = True
            
            # Place and walk
            trade = self.ib.placeOrder(combo, order)
            
            # Walk price toward target
            steps = 5
            for i in range(steps):
                if trade.isDone():
                    break
                
                new_price = walk_start + (walk_end - walk_start) * (i + 1) / steps
                new_price = round(new_price, 2)
                
                if (target_price > 0 and new_price <= walk_end) or \
                   (target_price < 0 and new_price >= walk_end):
                    order.lmtPrice = new_price
                    self.ib.placeOrder(combo, order)  # Modify
                    self.ib.sleep(2)
            
            # Final wait
            self.ib.sleep(5)
            
            if not trade.isDone():
                self.ib.cancelOrder(trade.order)
                return ExecutionResult(
                    success=False,
                    error="Order not filled after price walk",
                    combo_contract=combo
                )
            
            # Get fills
            fills = []
            for fill in trade.fills:
                fills.append({
                    'order_id': trade.order.orderId,
                    'contract': fill.contract.localSymbol if fill.contract else '',
                    'execution_id': fill.execution.execId,
                    'time': fill.execution.time,
                    'side': fill.execution.side,
                    'shares': fill.execution.shares,
                    'price': fill.execution.price,
                    'commission': fill.commissionReport.commission if fill.commissionReport else 0,
                })
            
            return ExecutionResult(
                success=True,
                order_id=trade.order.orderId,
                fills=fills,
                combo_contract=combo
            )
            
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return ExecutionResult(success=False, error=str(e))
        finally:
            self.disconnect()
    
    def execute_roll(self, close_order: ComboOrder, open_order: ComboOrder) -> Tuple[ExecutionResult, ExecutionResult]:
        """
        Execute a roll: close existing position, then open new one.
        Both legs must fill or roll is aborted.
        """
        # Execute close first
        close_result = self.execute_close_position(close_order)
        
        if not close_result.success:
            return close_result, ExecutionResult(success=False, error="Close leg failed, roll aborted")
        
        # Execute open
        open_result = self.execute_close_position(open_order)  # Same logic, different legs
        
        if not open_result.success:
            logger.warning("Open leg of roll failed after close filled!")
        
        return close_result, open_result


def legs_from_trade(trade_legs: List[Dict], action_override: Dict[str, str] = None) -> List[LegOrder]:
    """
    Convert trade journal legs to LegOrder objects.
    action_override: {'put_short': 'BUY', 'call_short': 'BUY', ...} to reverse actions for closing
    """
    legs = []
    for leg in trade_legs:
        # Determine action for closing
        if action_override:
            # Build key from leg
            key = f"{leg['type']}_{leg['action']}"  # e.g., 'put_sell'
            action = action_override.get(key, 'BUY' if leg['action'] == 'sell' else 'SELL')
        else:
            action = 'BUY' if leg['action'] == 'sell' else 'SELL'
        
        legs.append(LegOrder(
            symbol=leg.get('symbol', 'SPY'),
            expiry=leg.get('expiry', ''),
            strike=leg['strike'],
            right=leg['type'][0].upper(),
            action=action,
            quantity=leg.get('qty', 1),
            exchange=leg.get('exchange', 'SMART'),
            currency=leg.get('currency', 'USD'),
            trading_class=leg.get('trading_class', '')
        ))
    return legs


# Example usage
if __name__ == "__main__":
    executor = IBExecutor(port=4002)
    try:
        # Test connection
        executor.connect()
        print("Connected to IB paper account")
    finally:
        executor.disconnect()