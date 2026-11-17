"""Guards against silent use of made-up market data or simulated fills."""
import json, subprocess, sys, tempfile, unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trade_journal
import manage


def open_trade(**kw):
    legs = [
        {'type': 'put', 'strike': 440.0, 'action': 'sell', 'qty': 1, 'price': 1.2},
        {'type': 'put', 'strike': 439.0, 'action': 'buy', 'qty': 1, 'price': 0.6},
    ]
    t = trade_journal.Trade(entry_date=date.today().isoformat(), strategy='PUT_CREDIT_SPREAD', symbol='SPY',
                            dte=10, iv_rank=50.0, legs_json=json.dumps(legs), credit_received=0.6,
                            max_loss=0.4, max_profit=0.6, buying_power_used=40.0, rules_json='{}')
    for k, v in kw.items():
        setattr(t, k, v)
    return trade_journal.save_trade(t)


class NoMockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = trade_journal.DB_PATH
        trade_journal.DB_PATH = Path(self.tmp.name) / 'j.db'
        trade_journal.init_db()

    def tearDown(self):
        trade_journal.DB_PATH = self.orig
        self.tmp.cleanup()

    def test_no_open_trades_is_fine(self):
        r = manage.run_evaluation('paper')
        self.assertEqual((r['actions'], r['errors']), ([], []))

    def test_without_provider_evaluate_reports_error_not_random_actions(self):
        open_trade()
        r = manage.run_evaluation('paper')
        self.assertEqual(r['actions'], [])
        self.assertEqual(len(r['errors']), 1)
        self.assertIn('no real market data provider', r['errors'][0])

    def test_mock_is_explicit_and_labelled(self):
        open_trade()
        r = manage.run_evaluation('paper', allow_mock=True)
        self.assertTrue(r['mock'])  # random data may legitimately trip the strict quote checks

    def test_missing_quote_is_an_error_not_zero(self):
        t = trade_journal.get_trade(open_trade())
        with self.assertRaises(manage.MarketDataError):
            manage.credit_to_close(json.loads(t.legs_json), {'P440.0': 0.5}, t)

    def test_negative_credit_is_an_error(self):
        t = trade_journal.get_trade(open_trade())
        with self.assertRaises(manage.MarketDataError):
            manage.credit_to_close(json.loads(t.legs_json), {'P440.0': 0.2, 'P439.0': 0.5}, t)

    def test_good_quotes_give_credit(self):
        t = trade_journal.get_trade(open_trade())
        self.assertAlmostEqual(manage.credit_to_close(json.loads(t.legs_json), {'P440.0': 0.5, 'P439.0': 0.2}, t), 0.3)

    def test_provider_is_used_when_registered(self):
        open_trade()
        manage.MARKET_DATA_PROVIDERS['tastytrade'] = lambda s, l, m, c: {
            'underlying_price': 450.0, 'option_mids': {'P440.0': 0.1, 'P439.0': 0.05}, 'option_deltas': {}}
        try:
            r = manage.run_evaluation('paper')
        finally:
            manage.MARKET_DATA_PROVIDERS.clear()
        self.assertEqual(r['errors'], [])
        self.assertEqual(r['actions'][0]['action'], 'close_profit')  # 0.05 credit vs 0.6 entry

    def test_execute_refuses_without_backend(self):
        r = manage.run_execute('x', 'paper', json.dumps({'actions': [{'trade_id': 1, 'action': 'close_dte', 'params': {}}]}))
        self.assertFalse(r['success'])
        self.assertIn('refusing to simulate', r['error'])

    def test_mock_execute_never_writes_journal(self):
        tid = open_trade()
        manage.run_execute('x', 'paper', json.dumps({'actions': [{'trade_id': tid, 'action': 'close_dte', 'params': {'limit_price': 0.3}}]}), allow_mock=True)
        t = trade_journal.get_trade(tid)
        self.assertIsNone(t.exit_date)
        self.assertIsNone(t.realized_pnl)

    def test_journal_sync_does_not_fake_success(self):
        self.assertFalse(manage.run_journal_sync('x', 'paper')['success'])

    def test_cli_rejects_mock_with_live(self):
        p = subprocess.run([sys.executable, str(ROOT / 'manage.py'), 'evaluate', '--mode', 'live', '--mock'],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('not allowed', p.stderr)


if __name__ == '__main__':
    unittest.main()
