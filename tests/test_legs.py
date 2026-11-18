"""Tests for legs.py - pure function tests."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import legs
import trade_journal
import manage


class LegKeyTests(unittest.TestCase):
    def setUp(self):
        self.today = date.today()
        self.exp = (self.today + timedelta(days=10)).isoformat()

    def test_440_and_440_0_same_key(self):
        leg1 = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        leg2 = {'type': 'put', 'strike': 440.0, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        self.assertEqual(legs.leg_key(leg1), legs.leg_key(leg2))
        self.assertEqual(legs.leg_key(leg1), f"P440@{self.exp}")

    def test_key_contains_expiry(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        self.assertIn(self.exp, legs.leg_key(leg))

    def test_legacy_no_expiry(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1}
        self.assertEqual(legs.leg_key(leg), "P440")

    def test_calendar_same_strike_different_keys(self):
        front = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        back_exp = (self.today + timedelta(days=30)).isoformat()
        back = {'type': 'put', 'strike': 440, 'action': 'buy', 'qty': 1, 'expiry': back_exp}
        self.assertNotEqual(legs.leg_key(front), legs.leg_key(back))

    def test_call_prefix(self):
        leg = {'type': 'call', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        self.assertTrue(legs.leg_key(leg).startswith('C'))

    def test_strike_decimal(self):
        leg = {'type': 'put', 'strike': 440.5, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        self.assertEqual(legs.leg_key(leg), f"P440.5@{self.exp}")


class NormalizeLegTests(unittest.TestCase):
    def test_lowercases_type_action(self):
        leg = {'type': 'PUT', 'strike': 440, 'action': 'SELL', 'qty': 1, 'price': 1.2}
        norm = legs.normalize_leg(leg)
        self.assertEqual(norm['type'], 'put')
        self.assertEqual(norm['action'], 'sell')

    def test_strike_float_qty_int(self):
        leg = {'type': 'put', 'strike': '440', 'action': 'sell', 'qty': '1', 'price': '1.2'}
        norm = legs.normalize_leg(leg)
        self.assertIsInstance(norm['strike'], float)
        self.assertIsInstance(norm['qty'], int)
        self.assertEqual(norm['strike'], 440.0)
        self.assertEqual(norm['qty'], 1)

    def test_input_not_mutated(self):
        leg = {'type': 'PUT', 'strike': 440, 'action': 'SELL', 'qty': 1}
        original_type = leg['type']
        legs.normalize_leg(leg)
        self.assertEqual(leg['type'], original_type)

    def test_unknown_keys_preserved(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'extra': 'value'}
        norm = legs.normalize_leg(leg)
        self.assertEqual(norm['extra'], 'value')

    def test_empty_expiry_dropped(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': ''}
        norm = legs.normalize_leg(leg)
        self.assertNotIn('expiry', norm)

    def test_none_expiry_dropped(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': None}
        norm = legs.normalize_leg(leg)
        self.assertNotIn('expiry', norm)

    def test_bad_strike_raises(self):
        leg = {'type': 'put', 'strike': 'abc', 'action': 'sell', 'qty': 1}
        with self.assertRaisesRegex(ValueError, 'strike'):
            legs.normalize_leg(leg)

    def test_bool_strike_rejected(self):
        leg = {'type': 'put', 'strike': True, 'action': 'sell', 'qty': 1}
        with self.assertRaisesRegex(ValueError, 'bool'):
            legs.normalize_leg(leg)

    def test_qty_float_2_5_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 2.5}
        with self.assertRaisesRegex(ValueError, 'qty'):
            legs.normalize_leg(leg)

    def test_qty_zero_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 0}
        with self.assertRaisesRegex(ValueError, 'qty'):
            legs.normalize_leg(leg)

    def test_bool_qty_rejected(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': True}
        with self.assertRaisesRegex(ValueError, 'bool'):
            legs.normalize_leg(leg)


class ValidateLegsTests(unittest.TestCase):
    def setUp(self):
        self.today = date.today()
        self.exp = (self.today + timedelta(days=10)).isoformat()

    def make_condor(self):
        return [
            {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.20, 'expiry': self.exp},
            {'type': 'put', 'strike': 438, 'action': 'buy', 'qty': 1, 'price': 0.55, 'expiry': self.exp},
            {'type': 'call', 'strike': 452, 'action': 'sell', 'qty': 1, 'price': 1.15, 'expiry': self.exp},
            {'type': 'call', 'strike': 454, 'action': 'buy', 'qty': 1, 'price': 0.50, 'expiry': self.exp},
        ]

    def test_valid_condor_passes(self):
        legs.validate_legs(self.make_condor())

    def test_valid_calendar_passes(self):
        front = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.20, 'expiry': self.exp}
        back_exp = (self.today + timedelta(days=30)).isoformat()
        back = {'type': 'put', 'strike': 440, 'action': 'buy', 'qty': 1, 'price': 1.80, 'expiry': back_exp}
        legs.validate_legs([front, back])

    def test_missing_expiry_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1}
        with self.assertRaisesRegex(ValueError, 'expiry required'):
            legs.validate_legs([leg])

    def test_bad_date_format_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': '2026-13-40'}
        with self.assertRaisesRegex(ValueError, 'YYYY-MM-DD'):
            legs.validate_legs([leg])

    def test_us_date_format_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': '11/27/2026'}
        with self.assertRaisesRegex(ValueError, 'YYYY-MM-DD'):
            legs.validate_legs([leg])

    def test_strike_zero_raises(self):
        leg = {'type': 'put', 'strike': 0, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        with self.assertRaisesRegex(ValueError, 'strike must be > 0'):
            legs.validate_legs([leg])

    def test_strike_negative_raises(self):
        leg = {'type': 'put', 'strike': -1, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        with self.assertRaisesRegex(ValueError, 'strike must be > 0'):
            legs.validate_legs([leg])

    def test_qty_zero_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 0, 'expiry': self.exp}
        with self.assertRaisesRegex(ValueError, 'qty must be > 0'):
            legs.validate_legs([leg])

    def test_duplicate_type_strike_expiry_raises(self):
        legs_list = self.make_condor()
        # Duplicate the first leg
        legs_list.append(dict(legs_list[0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            legs.validate_legs(legs_list)

    def test_five_legs_raises(self):
        legs_list = self.make_condor()
        legs_list.append({'type': 'put', 'strike': 435, 'action': 'buy', 'qty': 1, 'expiry': self.exp})
        with self.assertRaisesRegex(ValueError, 'at most 4'):
            legs.validate_legs(legs_list)

    def test_unknown_type_raises(self):
        leg = {'type': 'future', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.exp}
        with self.assertRaisesRegex(ValueError, 'type must be one of'):
            legs.validate_legs([leg])

    def test_unknown_action_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'hold', 'qty': 1, 'expiry': self.exp}
        with self.assertRaisesRegex(ValueError, 'action must be one of'):
            legs.validate_legs([leg])

    def test_non_list_raises(self):
        with self.assertRaisesRegex(ValueError, 'legs must be a list'):
            legs.validate_legs("not a list")

    def test_empty_list_raises(self):
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            legs.validate_legs([])


class TradeDteTests(unittest.TestCase):
    def setUp(self):
        self.today = date.today()
        self.exp = (self.today + timedelta(days=10)).isoformat()

    def make_condor(self):
        return [
            {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.exp},
            {'type': 'put', 'strike': 438, 'action': 'buy', 'qty': 1, 'expiry': self.exp},
            {'type': 'call', 'strike': 452, 'action': 'sell', 'qty': 1, 'expiry': self.exp},
            {'type': 'call', 'strike': 454, 'action': 'buy', 'qty': 1, 'expiry': self.exp},
        ]

    def test_condor_shared_expiry(self):
        legs_list = self.make_condor()
        self.assertEqual(legs.trade_dte(legs_list, self.today), 10)

    def test_calendar_gives_front_dte(self):
        front_exp = (self.today + timedelta(days=10)).isoformat()
        back_exp = (self.today + timedelta(days=30)).isoformat()
        legs_list = [
            {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': front_exp},
            {'type': 'put', 'strike': 440, 'action': 'buy', 'qty': 1, 'expiry': back_exp},
        ]
        self.assertEqual(legs.trade_dte(legs_list, self.today), 10)

    def test_today_equals_expiry_gives_zero(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.today.isoformat()}
        self.assertEqual(legs.trade_dte([leg], self.today), 0)

    def test_empty_list_raises(self):
        with self.assertRaisesRegex(ValueError, 'no legs'):
            legs.trade_dte([], self.today)


class FrontAndBackTests(unittest.TestCase):
    def setUp(self):
        self.today = date.today()
        self.front_exp = (self.today + timedelta(days=10)).isoformat()
        self.back_exp = (self.today + timedelta(days=30)).isoformat()

    def test_calendar_splits(self):
        front = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.front_exp}
        back = {'type': 'put', 'strike': 440, 'action': 'buy', 'qty': 1, 'expiry': self.back_exp}
        f, b = legs.front_and_back([front, back])
        self.assertEqual(len(f), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(f[0]['expiry'], self.front_exp)
        self.assertEqual(b[0]['expiry'], self.back_exp)

    def test_condor_all_front(self):
        legs_list = [
            {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.front_exp},
            {'type': 'put', 'strike': 438, 'action': 'buy', 'qty': 1, 'expiry': self.front_exp},
            {'type': 'call', 'strike': 452, 'action': 'sell', 'qty': 1, 'expiry': self.front_exp},
            {'type': 'call', 'strike': 454, 'action': 'buy', 'qty': 1, 'expiry': self.front_exp},
        ]
        f, b = legs.front_and_back(legs_list)
        self.assertEqual(len(f), 4)
        self.assertEqual(len(b), 0)

    def test_missing_expiry_raises(self):
        leg = {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1}
        with self.assertRaisesRegex(ValueError, 'all legs must have expiry'):
            legs.front_and_back([leg])


class SaveTradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = trade_journal.DB_PATH
        trade_journal.DB_PATH = Path(self.tmp.name) / 'j.db'
        trade_journal.init_db()
        self.today = date.today()
        self.exp = (self.today + timedelta(days=10)).isoformat()

    def tearDown(self):
        trade_journal.DB_PATH = self.orig
        self.tmp.cleanup()

    def count_rows(self):
        import sqlite3
        with sqlite3.connect(trade_journal.DB_PATH) as conn:
            return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    def test_rejects_open_trade_no_expiry(self):
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json=json.dumps([
                {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.2},
                {'type': 'put', 'strike': 439, 'action': 'buy', 'qty': 1, 'price': 0.6},
            ]),
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json='{}',
        )
        with self.assertRaisesRegex(ValueError, 'expiry required'):
            trade_journal.save_trade(trade)
        self.assertEqual(self.count_rows(), 0)

    def test_rejects_open_trade_bad_json(self):
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json='not valid json',
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json='{}',
        )
        with self.assertRaisesRegex(ValueError, 'SPY.*PUT_CREDIT_SPREAD'):
            trade_journal.save_trade(trade)
        self.assertEqual(self.count_rows(), 0)

    def test_accepts_closed_trade_without_expiry(self):
        legs = [
            {'type': 'put', 'strike': 440.0, 'action': 'sell', 'qty': 1, 'price': 1.2},
            {'type': 'put', 'strike': 439.0, 'action': 'buy', 'qty': 1, 'price': 0.6},
        ]
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json=json.dumps(legs),
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json='{}',
            exit_date=self.today.isoformat(),
            exit_reason='PROFIT_TARGET',
        )
        trade_journal.save_trade(trade)
        self.assertEqual(self.count_rows(), 1)
        # legs_json should be byte-identical (no normalization for closed trades)
        saved = trade_journal.get_trade(trade.id)
        self.assertEqual(saved.legs_json, json.dumps(legs))

    def test_open_trade_normalizes_strike_float_qty_int(self):
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json=json.dumps([
                {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': '1', 'price': 1.2, 'expiry': self.exp},
                {'type': 'put', 'strike': 439, 'action': 'buy', 'qty': '1', 'price': 0.6, 'expiry': self.exp},
            ]),
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json='{}',
        )
        trade_journal.save_trade(trade)
        saved = trade_journal.get_trade(trade.id)
        parsed = json.loads(saved.legs_json)
        self.assertIsInstance(parsed[0]['strike'], float)
        self.assertIsInstance(parsed[0]['qty'], int)

    def test_update_open_trade_revalidates(self):
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json=json.dumps([
                {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.2, 'expiry': self.exp},
                {'type': 'put', 'strike': 439, 'action': 'buy', 'qty': 1, 'price': 0.6, 'expiry': self.exp},
            ]),
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json='{}',
        )
        trade_journal.save_trade(trade)
        
        # Now update with invalid legs (no expiry)
        trade.legs_json = json.dumps([
            {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.2},
            {'type': 'put', 'strike': 439, 'action': 'buy', 'qty': 1, 'price': 0.6},
        ])
        with self.assertRaisesRegex(ValueError, 'expiry required'):
            trade_journal.save_trade(trade)
        # Original should be unchanged
        saved = trade_journal.get_trade(trade.id)
        parsed = json.loads(saved.legs_json)
        self.assertIn('expiry', parsed[0])

    def test_legs_of_returns_empty_for_none(self):
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json=None,
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json='{}',
            exit_date=self.today.isoformat(),
        )
        trade_journal.save_trade(trade)
        self.assertEqual(trade_journal.legs_of(trade), [])

    def test_legs_of_returns_parsed(self):
        legs = [
            {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'expiry': self.exp},
        ]
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json=json.dumps(legs),
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json='{}',
            exit_date=self.today.isoformat(),
        )
        trade_journal.save_trade(trade)
        self.assertEqual(trade_journal.legs_of(trade), legs)


class RunEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = trade_journal.DB_PATH
        trade_journal.DB_PATH = Path(self.tmp.name) / 'j.db'
        trade_journal.init_db()
        self.today = date.today()
        self.front_exp = (self.today + timedelta(days=1)).isoformat()
        self.far_exp = (self.today + timedelta(days=30)).isoformat()

    def tearDown(self):
        trade_journal.DB_PATH = self.orig
        self.tmp.cleanup()
        manage.MARKET_DATA_PROVIDERS.clear()

    def make_trade(self, expiry, dte_stored=45):
        legs = [
            {'type': 'put', 'strike': 440, 'action': 'sell', 'qty': 1, 'price': 1.2, 'expiry': expiry},
            {'type': 'put', 'strike': 439, 'action': 'buy', 'qty': 1, 'price': 0.6, 'expiry': expiry},
        ]
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=dte_stored,
            iv_rank=50.0,
            legs_json=json.dumps(legs),
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json=json.dumps({'profit_target_pct': 0.3, 'close_dte': 2, 'breach': {'action': 'close'}, 'trend_break': False, 'min_iv_rank': 20}),
        )
        trade_journal.save_trade(trade)
        return trade

    def test_close_dte_triggered_when_expiry_near(self):
        """Trade has stale dte=45 but nearest expiry is today+1 -> close_dte should trigger"""
        trade = self.make_trade(self.front_exp, dte_stored=45)
        
        def provider(symbol, leg_list, mode, config):
            parsed = manage.parse_legs(trade.legs_json)
            front_key = legs.leg_key(parsed[0])
            back_key = legs.leg_key(parsed[1])
            # Credit to close = 0.4 - 0.3 = 0.1 (sell leg mid - buy leg mid)
            # Entry credit = 0.6, profit target 30% -> target = 0.42
            # 0.1 < 0.42 so profit target IS hit... wait, we want close_dte to trigger first
            # The evaluator checks profit target first, so we need credit > profit target
            # Profit target: credit_received * (1 - profit_target_pct) = 0.6 * 0.7 = 0.42
            # So we need current_credit > 0.42 to avoid profit target
            # current_credit = sell_mid - buy_mid
            # Let's use sell_mid=0.5, buy_mid=0.05 -> current_credit = 0.45 > 0.42
            return {
                'underlying_price': 450.0,
                'option_mids': {front_key: 0.5, back_key: 0.05},
                'option_deltas': {},
            }
        
        manage.MARKET_DATA_PROVIDERS['tastytrade'] = provider
        try:
            r = manage.run_evaluation('paper')
            actions = [a['action'] for a in r['actions']]
            self.assertIn('close_dte', actions, f"Expected close_dte, got {actions}")
        finally:
            manage.MARKET_DATA_PROVIDERS.clear()

    def test_no_close_dte_when_expiry_far(self):
        """Trade with far expiry (+30) should not trigger close_dte"""
        trade = self.make_trade(self.far_exp, dte_stored=45)
        
        def provider(symbol, leg_list, mode, config):
            parsed = manage.parse_legs(trade.legs_json)
            front_key = legs.leg_key(parsed[0])
            back_key = legs.leg_key(parsed[1])
            return {
                'underlying_price': 450.0,
                'option_mids': {front_key: 0.3, back_key: 0.2},
                'option_deltas': {},
            }
        
        manage.MARKET_DATA_PROVIDERS['tastytrade'] = provider
        try:
            r = manage.run_evaluation('paper')
            actions = [a['action'] for a in r['actions']]
            self.assertNotIn('close_dte', actions, f"Did not expect close_dte, got {actions}")
        finally:
            manage.MARKET_DATA_PROVIDERS.clear()

    def test_legacy_path_no_expiry_uses_stored_dte(self):
        """Legacy trade without expiry should fall back to stored dte minus elapsed days"""
        leg_list = [
            {'type': 'put', 'strike': 440.0, 'action': 'sell', 'qty': 1, 'price': 1.2},
            {'type': 'put', 'strike': 439.0, 'action': 'buy', 'qty': 1, 'price': 0.6},
        ]
        trade = trade_journal.Trade(
            entry_date=self.today.isoformat(),
            strategy='PUT_CREDIT_SPREAD',
            symbol='SPY',
            dte=10,
            iv_rank=50.0,
            legs_json=json.dumps(leg_list),
            credit_received=0.6,
            max_loss=0.4,
            max_profit=0.6,
            buying_power_used=40.0,
            rules_json=json.dumps({'profit_target_pct': 0.3, 'close_dte': 2, 'breach': {'action': 'close'}, 'trend_break': False, 'min_iv_rank': 20}),
        )
        # Closed trade - no expiry required
        trade.exit_date = self.today.isoformat()
        trade_journal.save_trade(trade)
        
        def provider(symbol, leg_list_param, mode, config):
            parsed = manage.parse_legs(trade.legs_json)
            front_key = legs.leg_key(parsed[0])
            back_key = legs.leg_key(parsed[1])
            return {
                'underlying_price': 450.0,
                'option_mids': {front_key: 0.3, back_key: 0.2},
                'option_deltas': {},
            }
        
        manage.MARKET_DATA_PROVIDERS['tastytrade'] = provider
        try:
            r = manage.run_evaluation('paper')
            self.assertEqual(r['errors'], [])
        finally:
            manage.MARKET_DATA_PROVIDERS.clear()


if __name__ == '__main__':
    unittest.main()