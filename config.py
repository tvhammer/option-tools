"""Central, validated loader for config.yaml (non-secret settings only). Unknown keys are errors."""
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).parent / 'config.yaml'

STRATEGY_KEYS = {
    'min_iv_rank', 'target_dte', 'delta_short', 'width_usd', 'min_credit_ratio', 'profit_target_pct',
    'close_dte', 'stop_loss_multiple', 'require_uptrend', 'max_hv_iv_ratio',
}
CALENDAR_KEYS = {
    'profit_target_pct_of_debit', 'stop_loss_pct_of_debit', 'front_close_dte', 'tent_break_pct', 'min_back_dte',
    'front_dte', 'back_dte', 'max_debit_usd',
}
TOP_KEYS = {
    'universe', 'dte_range', 'min_iv_rank', 'max_positions', 'max_loss_per_trade_eur', 'account_size_eur',
    'broker', 'commission', 'ib_mode', 'ib_port_paper', 'ib_port_live', 'tastytrade',
    'iron_condor', 'put_credit_spread', 'iron_butterfly', 'calendar_spread', 'force_close_dte', 'data_sources',
}
REQUIRED = {'universe', 'max_positions', 'max_loss_per_trade_eur', 'broker', 'force_close_dte',
            'iron_condor', 'put_credit_spread', 'iron_butterfly'}
SECRET_LIKE = ('password', 'secret', 'token', 'username', 'client_id')


class ConfigError(Exception):
    pass


def _check_keys(section: str, d: dict, allowed: set):
    unknown = set(d) - allowed
    if unknown:
        raise ConfigError(f"config.yaml: unknown key(s) in '{section}': {sorted(unknown)}")


def validate(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ConfigError("config.yaml: not a mapping")
    _check_keys('top level', cfg, TOP_KEYS)
    missing = REQUIRED - set(cfg)
    if missing:
        raise ConfigError(f"config.yaml: missing key(s): {sorted(missing)}")
    for name in ('iron_condor', 'put_credit_spread', 'iron_butterfly'):
        _check_keys(name, cfg[name], STRATEGY_KEYS)
    if 'calendar_spread' in cfg:
        _check_keys('calendar_spread', cfg['calendar_spread'], CALENDAR_KEYS | STRATEGY_KEYS)
    if 'tastytrade' in cfg:
        _check_keys('tastytrade', cfg['tastytrade'], {'mode'})  # credentials never belong here
    for k in cfg:
        if any(s in k.lower() for s in SECRET_LIKE):
            raise ConfigError(f"config.yaml: '{k}' looks like a secret; secrets belong in ~/.config/option-tools/")
    if cfg.get('broker') not in ('tastytrade', 'ib'):
        raise ConfigError(f"config.yaml: broker must be 'tastytrade' or 'ib', got {cfg.get('broker')!r}")
    for name in ('iron_condor', 'put_credit_spread', 'iron_butterfly'):
        p = cfg[name].get('profit_target_pct')
        if p is not None and not 0 < p < 1:
            raise ConfigError(f"config.yaml: {name}.profit_target_pct must be between 0 and 1")
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        return validate(yaml.safe_load(f))
