"""
Simple Trade Journal for Options Trading
SQLite-based, tracks entries, exits, P&L, and strategy adherence.
All monetary values stored as PER-CONTRACT USD (credit=1.30, max_loss=70, not 0.013/7000).
"""
import sqlite3
import pandas as pd
from datetime import datetime, date
from dataclasses import dataclass, asdict, fields
from typing import Optional, List
from pathlib import Path
import json


DB_PATH = Path(__file__).parent / 'trade_journal.db'
SCHEMA_VERSION = 2


@dataclass
class Trade:
    id: Optional[int] = None
    # Entry
    entry_date: str = None
    strategy: str = None  # 'IRON_CONDOR', 'PUT_CREDIT_SPREAD', 'IRON_BUTTERFLY'
    symbol: str = None
    dte: int = None
    iv_rank: float = None
    # Legs: JSON list of {'type': 'put/call', 'strike': x, 'action': 'buy/sell', 'qty': 1, 'price': y}
    legs_json: str = None
    credit_received: float = None      # PER-CONTRACT USD (e.g., 1.30 = $130/contract)
    max_loss: float = None             # PER-CONTRACT USD
    max_profit: float = None           # PER-CONTRACT USD
    buying_power_used: float = None    # PER-CONTRACT USD (margin requirement)
    # Management rules snapshotted at entry
    rules_json: str = None             # JSON of rule params (profit_target_pct, close_dte, stop_loss_multiple, etc.)
    # IB identifiers
    ib_con_ids: str = None             # JSON list of contract IDs for each leg
    ib_order_ids: str = None           # JSON list of order IDs (entry + management)
    quantity: int = 1                  # Number of contracts
    currency: str = "USD"              # Settlement currency
    commission_per_leg: float = 0.65   # USD per leg per side
    # Metadata
    entry_notes: str = None
    model_score: float = None
    # Exit
    exit_date: str = None
    exit_reason: str = None  # 'PROFIT_TARGET', 'STOP_LOSS', 'MANAGEMENT_ROLL', 'DTE_FORCE_CLOSE', 'TREND_BREAK', 'IV_CRUSH', 'MANUAL'
    legs_exit_json: str = None
    debit_paid: float = None           # PER-CONTRACT USD to close
    realized_pnl: float = None         # PER-CONTRACT USD (net of commissions)
    return_on_capital: float = None    # realized_pnl / buying_power_used
    days_held: int = None
    exit_notes: str = None
    # Rule adherence
    followed_rules: bool = True
    rule_violations: str = None


def _get_field_names() -> List[str]:
    return [f.name for f in fields(Trade)]


def init_db():
    """Create tables if not exist, migrate if needed"""
    with sqlite3.connect(DB_PATH) as conn:
        # Create schema_version table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        # Use INSERT OR REPLACE to handle existing row
        conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (0)")
        
        # Get current version
        cur = conn.execute("SELECT version FROM schema_version")
        row = cur.fetchone()
        current_version = row[0] if row else 0
        
        # Check if trades table exists and its columns
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        table_exists = cur.fetchone() is not None
        
        if not table_exists:
            # Fresh install - create v2 schema directly
            _create_schema_v2(conn)
            conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (2)")
        elif current_version == 0:
            # Table exists but version 0 - assume v1, migrate
            _migrate_v1_to_v2(conn)
            conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (2)")
        elif current_version == 1:
            # Migrate v1 -> v2
            _migrate_v1_to_v2(conn)
            conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (2)")
        elif current_version >= 2:
            # Already at v2 or higher - ensure table exists
            _ensure_schema_v2(conn)
        
        conn.commit()


def _create_schema_v2(conn):
    """Create v2 schema from scratch"""
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            dte INTEGER,
            iv_rank REAL,
            legs_json TEXT,
            credit_received REAL,
            max_loss REAL,
            max_profit REAL,
            buying_power_used REAL,
            rules_json TEXT,
            ib_con_ids TEXT,
            ib_order_ids TEXT,
            quantity INTEGER DEFAULT 1,
            currency TEXT DEFAULT 'USD',
            commission_per_leg REAL DEFAULT 0.65,
            entry_notes TEXT,
            model_score REAL,
            exit_date TEXT,
            exit_reason TEXT,
            legs_exit_json TEXT,
            debit_paid REAL,
            realized_pnl REAL,
            return_on_capital REAL,
            days_held INTEGER,
            exit_notes TEXT,
            followed_rules BOOLEAN DEFAULT 1,
            rule_violations TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_entry_date ON trades(entry_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_date ON trades(exit_date)")


def _migrate_v1_to_v2(conn):
    """Migrate from v1 (old schema) to v2"""
    # Check existing columns
    cur = conn.execute("PRAGMA table_info(trades)")
    existing_cols = {row[1] for row in cur.fetchall()}
    
    # Add missing columns
    new_columns = {
        'rules_json': 'TEXT',
        'ib_con_ids': 'TEXT',
        'ib_order_ids': 'TEXT',
        'quantity': 'INTEGER DEFAULT 1',
        'currency': 'TEXT DEFAULT \'USD\'',
        'commission_per_leg': 'REAL DEFAULT 0.65',
    }
    
    for col, col_type in new_columns.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
    
    # Ensure indexes exist
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_date ON trades(exit_date)")


def _ensure_schema_v2(conn):
    """Ensure v2 schema exists (for future migrations)"""
    cur = conn.execute("PRAGMA table_info(trades)")
    existing_cols = {row[1] for row in cur.fetchall()}
    
    required = {
        'rules_json': 'TEXT',
        'ib_con_ids': 'TEXT',
        'ib_order_ids': 'TEXT',
        'quantity': 'INTEGER DEFAULT 1',
        'currency': 'TEXT DEFAULT \'USD\'',
        'commission_per_leg': 'REAL DEFAULT 0.65',
    }
    
    for col, col_type in required.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")


def save_trade(trade: Trade) -> int:
    """Insert or update trade. Returns trade ID."""
    field_names = _get_field_names()
    with sqlite3.connect(DB_PATH) as conn:
        if trade.id is None:
            # Insert
            insert_fields = [f for f in field_names if f != 'id']
            values = [getattr(trade, f) for f in insert_fields]
            placeholders = ','.join(['?'] * len(insert_fields))
            cur = conn.execute(
                f"INSERT INTO trades ({','.join(insert_fields)}) VALUES ({placeholders})", 
                values
            )
            trade.id = cur.lastrowid
        else:
            # Update
            update_fields = [f for f in field_names if f != 'id']
            values = [getattr(trade, f) for f in update_fields] + [trade.id]
            set_clause = ','.join([f"{f}=?" for f in update_fields])
            conn.execute(f"UPDATE trades SET {set_clause} WHERE id=?", values)
        conn.commit()
    return trade.id


def get_trade(trade_id: int) -> Optional[Trade]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row:
            return Trade(**dict(row))
    return None


def get_open_trades() -> List[Trade]:
    """Trades without exit_date"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trades WHERE exit_date IS NULL ORDER BY entry_date").fetchall()
        return [Trade(**dict(r)) for r in rows]


def get_closed_trades(limit: int = 100) -> List[Trade]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE exit_date IS NOT NULL ORDER BY exit_date DESC LIMIT ?", 
            (limit,)
        ).fetchall()
        return [Trade(**dict(r)) for r in rows]


def get_trades_df(filters: dict = None) -> pd.DataFrame:
    """Get trades as DataFrame with optional filters"""
    query = "SELECT * FROM trades WHERE 1=1"
    params = []
    if filters:
        if 'strategy' in filters:
            query += " AND strategy = ?"
            params.append(filters['strategy'])
        if 'symbol' in filters:
            query += " AND symbol = ?"
            params.append(filters['symbol'])
        if 'date_from' in filters:
            query += " AND entry_date >= ?"
            params.append(filters['date_from'])
        if 'date_to' in filters:
            query += " AND entry_date <= ?"
            params.append(filters['date_to'])
    query += " ORDER BY entry_date DESC"
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn, params=params)


def performance_summary() -> dict:
    """Calculate key performance metrics"""
    df = get_trades_df()
    if df.empty:
        return {}
    
    closed = df[df['exit_date'].notna()].copy()
    if closed.empty:
        return {'open_trades': len(df) - len(closed)}
    
    closed['realized_pnl'] = pd.to_numeric(closed['realized_pnl'], errors='coerce')
    closed['return_on_capital'] = pd.to_numeric(closed['return_on_capital'], errors='coerce')
    closed['max_loss'] = pd.to_numeric(closed['max_loss'], errors='coerce')
    
    winners = closed[closed['realized_pnl'] > 0]
    losers = closed[closed['realized_pnl'] <= 0]
    
    total_pnl = closed['realized_pnl'].sum()
    win_rate = len(winners) / len(closed) if len(closed) > 0 else 0
    avg_win = winners['realized_pnl'].mean() if len(winners) > 0 else 0
    avg_loss = losers['realized_pnl'].mean() if len(losers) > 0 else 0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    
    # Max drawdown
    closed = closed.sort_values('exit_date')
    closed['cum_pnl'] = closed['realized_pnl'].cumsum()
    closed['peak'] = closed['cum_pnl'].cummax()
    closed['drawdown'] = closed['peak'] - closed['cum_pnl']
    max_dd = closed['drawdown'].max()
    
    # By strategy
    by_strategy = closed.groupby('strategy').agg(
        trades=('id', 'count'),
        win_rate=('realized_pnl', lambda x: (x > 0).mean()),
        total_pnl=('realized_pnl', 'sum'),
        avg_pnl=('realized_pnl', 'mean'),
    ).to_dict('index')
    
    return {
        'total_trades': len(closed),
        'open_trades': len(df) - len(closed),
        'total_pnl': round(total_pnl, 2),
        'win_rate': round(win_rate, 3),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'expectancy_per_trade': round(expectancy, 2),
        'max_drawdown': round(max_dd, 2),
        'by_strategy': by_strategy,
    }


def print_performance():
    """Pretty print performance summary"""
    stats = performance_summary()
    if not stats:
        print("No trades recorded yet.")
        return
    
    print(f"\n{'='*50}")
    print(f"PERFORMANCE SUMMARY")
    print(f"{'='*50}")
    print(f"Total Trades:     {stats['total_trades']}")
    print(f"Open Trades:      {stats['open_trades']}")
    print(f"Total P&L:        ${stats['total_pnl']:.2f}")  # USD per contract
    print(f"Win Rate:         {stats['win_rate']:.1%}")
    print(f"Avg Winner:       ${stats['avg_win']:.2f}")
    print(f"Avg Loser:        ${stats['avg_loss']:.2f}")
    print(f"Profit Factor:    {stats['profit_factor']:.2f}")
    print(f"Expectancy/Trade: ${stats['expectancy_per_trade']:.2f}")
    print(f"Max Drawdown:     ${stats['max_drawdown']:.2f}")
    
    print(f"\nBy Strategy:")
    for strat, s in stats['by_strategy'].items():
        print(f"  {strat}: {s['trades']} trades, {s['win_rate']:.1%} WR, ${s['total_pnl']:.2f} P&L, ${s['avg_pnl']:.2f} avg")


# Initialize on import
init_db()


# Example usage
if __name__ == "__main__":
    from datetime import date, timedelta
    
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
        rules_json=json.dumps({
            'profit_target_pct': 0.30,
            'stop_loss_multiple': 1.5,
            'close_dte': 2,
            'breach': {'action': 'close'},
            'trend_break': False,
            'min_iv_rank': 20
        }),
        ib_con_ids=json.dumps([]),
        ib_order_ids=json.dumps([]),
        quantity=1,
        currency="USD",
        commission_per_leg=0.65,
        entry_notes="High IVR, 16Δ/84Δ, $1 wide",
        model_score=42.5,
    )
    save_trade(trade)
    print(f"Created trade ID: {trade.id}")
    
    # Close it
    trade.exit_date = (date.today() + timedelta(days=5)).isoformat()
    trade.exit_reason = "PROFIT_TARGET"
    trade.legs_exit_json = json.dumps([
        {'type': 'put', 'strike': 440, 'action': 'buy', 'qty': 1, 'price': 0.40},
        {'type': 'put', 'strike': 438, 'action': 'sell', 'qty': 1, 'price': 0.15},
        {'type': 'call', 'strike': 452, 'action': 'buy', 'qty': 1, 'price': 0.35},
        {'type': 'call', 'strike': 454, 'action': 'sell', 'qty': 1, 'price': 0.10},
    ])
    trade.debit_paid = 0.50
    trade.realized_pnl = 0.80 - (8 * 0.65 / 100)  # net of commissions
    trade.return_on_capital = trade.realized_pnl / 70.0
    trade.days_held = 5
    trade.exit_notes = "Closed at 38% profit"
    save_trade(trade)
    
    print_performance()