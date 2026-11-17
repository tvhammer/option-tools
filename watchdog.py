#!/usr/bin/env python3
"""
Options Watchdog Daemon - Application-level background service
Runs continuously, evaluates positions on interval during market hours.

Supports multiple brokers: tastytrade (primary), ib (legacy)

Usage:
    python watchdog.py start --mode paper --interval 900   # Start daemon (15 min)
    python watchdog.py stop                                # Stop daemon
    python watchdog.py status                              # Check status
    python watchdog.py run-once --mode paper               # Single evaluation (for testing)
    python watchdog.py start --force-market-hours          # Bypass market hours (testing)
"""
import sys
import os
import json
import time
import signal
import logging
import argparse
from datetime import datetime, time as dt_time, timezone, timedelta
from pathlib import Path
from typing import Optional
import threading

sys.path.insert(0, str(Path(__file__).parent))

from manage import run_evaluation, run_execute, run_journal_sync
import yaml


# Configuration
PID_FILE = Path(__file__).parent / 'watchdog.pid'
LOG_FILE = Path(__file__).parent / 'watchdog.log'
STOP_FILE = Path(__file__).parent / 'STOP'  # Kill switch

# US Market Hours (Eastern Time)
MARKET_OPEN_ET = dt_time(9, 30)
MARKET_CLOSE_ET = dt_time(16, 0)

# Default to paper trading
DEFAULT_MODE = 'paper'
DEFAULT_INTERVAL = 900  # 15 minutes


class WatchdogDaemon:
    def __init__(self, mode: str = DEFAULT_MODE, interval: int = DEFAULT_INTERVAL, 
                 force_market_hours: bool = False, broker: str = None):
        self.mode = mode
        self.interval = interval
        self.force_market_hours = force_market_hours
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.logger = self._setup_logger()
        
        # Load config
        from config import load_config
        self.config = load_config()
        
        # Determine broker
        self.broker = broker or self.config.get('broker', 'tastytrade')
        self.logger.info(f"Watchdog initialized: broker={self.broker}, mode={mode}")
        
        # Initialize broker client
        self.broker_client = self._init_broker_client()
    
    def _init_broker_client(self):
        """Initialize the appropriate broker client"""
        if self.broker == 'tastytrade':
            try:
                from tasty_chain import create_tasty_client_from_env
                client = create_tasty_client_from_env()
                if client.login():
                    self.logger.info(f"TastyTrade connected (paper={client.creds.is_paper})")
                    return client
                else:
                    self.logger.error("TastyTrade login failed")
            except ImportError:
                self.logger.error("tastytrade-sdk not installed. Run: pip install tastytrade")
            except Exception as e:
                self.logger.error(f"TastyTrade init failed: {e}")
        
        # Fallback to IB
        if self.broker == 'ib' or self.broker == 'tastytrade':
            self.logger.warning("Falling back to IB (not implemented in watchdog yet)")
            # TODO: Add IB client initialization
        
        return None
    
    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger('watchdog')
        logger.setLevel(logging.INFO)
        
        # File handler
        fh = logging.FileHandler(LOG_FILE)
        fh.setLevel(logging.INFO)
        
        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(message)s')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        logger.addHandler(fh)
        logger.addHandler(ch)
        
        return logger
    
    def _is_market_hours(self) -> bool:
        if self.force_market_hours:
            return True
        et = timezone(timedelta(hours=-5))  # EST (simplified, no DST handling)
        now_et = datetime.now(et).time()
        return MARKET_OPEN_ET <= now_et <= MARKET_CLOSE_ET
    
    def _check_stop_file(self) -> bool:
        return STOP_FILE.exists()
    
    def _write_pid(self):
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
    
    def _remove_pid(self):
        if PID_FILE.exists():
            PID_FILE.unlink()
    
    def _get_pid(self) -> Optional[int]:
        if PID_FILE.exists():
            try:
                with open(PID_FILE) as f:
                    return int(f.read().strip())
            except:
                return None
        return None
    
    def _run_cycle(self):
        """Single evaluation + execution cycle"""
        cycle_id = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.logger.info(f"=== Cycle {cycle_id} started (broker={self.broker}, mode={self.mode}) ===")
        
        try:
            # 1. Evaluate
            self.logger.info("Evaluating positions...")
            eval_result = run_evaluation(self.mode)
            
            positions_checked = eval_result.get('positions_checked', 0)
            self.logger.info(f"Positions checked: {positions_checked}")
            if eval_result.get('message'):
                self.logger.info(eval_result['message'])
            if eval_result.get('alerts'):
                for alert in eval_result['alerts']:
                    self.logger.warning(f"ALERT: {alert}")
            
            if not eval_result.get('actions'):
                self.logger.info("No actions needed")
                return
            
            # 2. Execute actions
            self.logger.info(f"Actions needed: {len(eval_result['actions'])}")
            for action in eval_result['actions']:
                self.logger.info(f"  {action['action']} {action['symbol']} #{action['trade_id']}: {action['reason']}")
            
            # 3. Execute
            run_id = f"wd-{cycle_id}"
            params = {'actions': eval_result['actions'], 'overrides': {}}
            exec_result = run_execute(run_id, self.mode, json.dumps(params))
            
            if exec_result['success']:
                self.logger.info(f"Execution successful: {len(exec_result['executed'])} actions")
                for ex in exec_result['executed']:
                    fill = ex['result']
                    self.logger.info(f"  Filled: {ex['action']} #{ex['trade_id']} @ ${fill['fill_price']:.2f} (net P&L: ${fill['net_pnl']:.2f})")
            else:
                self.logger.error(f"Execution failed: {exec_result.get('error')}")
                for fail in exec_result.get('failed', []):
                    self.logger.error(f"  Failed: {fail['action']} #{fail['trade_id']}: {fail['error']}")
            
            # 4. Journal sync
            sync_result = run_journal_sync(run_id, self.mode)
            if sync_result['success']:
                self.logger.info(f"Journal synced: {sync_result['synced']} trades")
            
        except Exception as e:
            self.logger.exception(f"Cycle error: {e}")
        
        self.logger.info(f"=== Cycle {cycle_id} completed ===")
    
    def run_forever(self):
        self.logger.info(f"Watchdog started (broker={self.broker}, mode={self.mode}, interval={self.interval}s, force_market_hours={self.force_market_hours})")
        self._write_pid()
        self.running = True
        
        while self.running:
            try:
                if self._check_stop_file():
                    self.logger.warning("STOP file detected, shutting down")
                    break
                
                if not self._is_market_hours():
                    self.logger.debug("Outside market hours, sleeping...")
                    time.sleep(60)
                    continue
                
                self._run_cycle()
                
                self.logger.debug(f"Sleeping {self.interval}s until next cycle")
                time.sleep(self.interval)
                
            except KeyboardInterrupt:
                self.logger.info("Interrupted, shutting down")
                break
            except Exception as e:
                self.logger.exception(f"Unexpected error: {e}")
                time.sleep(30)
        
        self.running = False
        self._remove_pid()
        self.logger.info("Watchdog stopped")
    
    def start(self, foreground: bool = False):
        if self.is_running():
            self.logger.warning("Watchdog already running")
            return False
        
        self.logger.info("Starting watchdog daemon...")
        
        if foreground:
            self.run_forever()
            return True
        else:
            self.thread = threading.Thread(target=self.run_forever, daemon=False)
            self.thread.start()
            time.sleep(2)
            if self.is_running():
                self.logger.info("Watchdog started successfully")
                return True
            else:
                self.logger.error("Watchdog failed to start")
                return False
    
    def stop(self):
        if not self.is_running():
            self.logger.warning("Watchdog not running")
            return False
        
        self.logger.info("Stopping watchdog...")
        self.running = False
        
        if self.thread:
            self.thread.join(timeout=10)
        
        self._remove_pid()
        self.logger.info("Watchdog stopped")
        return True
    
    def is_running(self) -> bool:
        pid = self._get_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    
    def status(self) -> dict:
        return {
            'running': self.is_running(),
            'pid': self._get_pid(),
            'broker': self.broker,
            'mode': self.mode,
            'interval': self.interval,
            'force_market_hours': self.force_market_hours,
            'market_hours': self._is_market_hours(),
            'stop_file': self._check_stop_file(),
            'pid_file': PID_FILE.exists(),
            'log_file': str(LOG_FILE),
        }
    
    def run_once(self):
        self.logger.info("Running single evaluation cycle...")
        self._run_cycle()


def main():
    parser = argparse.ArgumentParser(description='Options Watchdog Daemon')
    parser.add_argument('command', choices=['start', 'stop', 'restart', 'status', 'run-once'])
    parser.add_argument('--mode', choices=['paper', 'live'], default=DEFAULT_MODE)
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL)
    parser.add_argument('--broker', choices=['tastytrade', 'ib'], help='Override config broker')
    parser.add_argument('--foreground', action='store_true')
    parser.add_argument('--force-market-hours', action='store_true')
    
    args = parser.parse_args()
    
    daemon = WatchdogDaemon(
        mode=args.mode, 
        interval=args.interval, 
        force_market_hours=args.force_market_hours,
        broker=args.broker,
    )
    
    if args.command == 'start':
        success = daemon.start(foreground=args.foreground)
        sys.exit(0 if success else 1)
    elif args.command == 'stop':
        success = daemon.stop()
        sys.exit(0 if success else 1)
    elif args.command == 'restart':
        daemon.stop()
        time.sleep(2)
        success = daemon.start()
        sys.exit(0 if success else 1)
    elif args.command == 'status':
        status = daemon.status()
        print(json.dumps(status, indent=2))
        sys.exit(0 if status['running'] else 1)
    elif args.command == 'run-once':
        daemon.run_once()
        sys.exit(0)


if __name__ == '__main__':
    main()