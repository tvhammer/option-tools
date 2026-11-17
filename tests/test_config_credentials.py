import os, stat, sys, tempfile, unittest, copy
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config, credentials as sec


class ConfigTests(unittest.TestCase):
    def good(self):
        return copy.deepcopy(config.load_config())

    def test_real_config_is_valid(self):
        self.assertIn('iron_condor', self.good())

    def test_unknown_top_key_rejected(self):
        c = self.good(); c['profit_target'] = 1
        with self.assertRaisesRegex(config.ConfigError, 'unknown key'):
            config.validate(c)

    def test_typo_in_strategy_rejected(self):
        c = self.good(); c['iron_condor']['profit_targt_pct'] = 0.3
        with self.assertRaisesRegex(config.ConfigError, 'iron_condor'):
            config.validate(c)

    def test_credentials_in_config_rejected(self):
        c = self.good(); c['tastytrade']['password'] = 'x'
        with self.assertRaises(config.ConfigError):
            config.validate(c)

    def test_bad_profit_target_rejected(self):
        c = self.good(); c['iron_condor']['profit_target_pct'] = 30
        with self.assertRaises(config.ConfigError):
            config.validate(c)


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        os.chmod(self.d, 0o700)
        os.environ['OPTION_TOOLS_SECRETS_DIR'] = str(self.d)

    def tearDown(self):
        os.environ.pop('OPTION_TOOLS_SECRETS_DIR', None)
        self.tmp.cleanup()

    def write(self, name, text, mode=0o600):
        p = self.d / f'{name}.env'
        p.write_text(text)
        os.chmod(p, mode)

    def test_loads_and_redacts(self):
        self.write('tasty-sandbox', '# c\nTASTY_ENV=sandbox\nTASTY_CLIENT_SECRET=s3cret\nTASTY_REFRESH_TOKEN=tok\n')
        c = sec.tasty_credentials('tasty-sandbox')
        self.assertEqual(c.base_url, 'https://api.cert.tastyworks.com')
        self.assertNotIn('s3cret', repr(c))
        self.assertNotIn('tok', repr(c))

    def test_group_readable_file_refused(self):
        self.write('tasty-sandbox', 'TASTY_ENV=sandbox\n', mode=0o640)
        with self.assertRaisesRegex(sec.SecretsError, 'chmod 600'):
            sec.tasty_credentials('tasty-sandbox')

    def test_env_must_match_profile(self):
        self.write('tasty-prod-read', 'TASTY_ENV=sandbox\nTASTY_CLIENT_SECRET=a\nTASTY_REFRESH_TOKEN=b\n')
        with self.assertRaisesRegex(sec.SecretsError, 'TASTY_ENV=production'):
            sec.tasty_credentials('tasty-prod-read')

    def test_empty_template_is_not_usable(self):
        self.write('tasty-sandbox', 'TASTY_ENV=sandbox\nTASTY_CLIENT_SECRET=\nTASTY_REFRESH_TOKEN=\n')
        with self.assertRaisesRegex(sec.SecretsError, 'missing'):
            sec.tasty_credentials('tasty-sandbox')

    def test_profile_selection(self):
        self.assertEqual(sec.profile_for('data', 'paper'), 'tasty-prod-read')
        self.assertEqual(sec.profile_for('data', 'live'), 'tasty-prod-read')
        self.assertEqual(sec.profile_for('orders', 'paper'), 'tasty-sandbox')
        self.assertEqual(sec.profile_for('orders', 'live'), 'tasty-prod-trade')

    def test_missing_file(self):
        with self.assertRaisesRegex(sec.SecretsError, 'missing'):
            sec.tasty_credentials('tasty-prod-read')


if __name__ == '__main__':
    unittest.main()
