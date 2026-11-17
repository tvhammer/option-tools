"""
Credential loading for option-tools (not named secrets.py: that would shadow the stdlib module numpy imports). Secrets live OUTSIDE the repo:
    ~/.config/option-tools/<profile>.env     (dir mode 700, files mode 600)
or $OPTION_TOOLS_SECRETS_DIR/<profile>.env. Plain KEY=value lines.

Profiles (tastytrade OAuth2; the old username/password/2FA login was decommissioned):
    tasty-prod-read    production app with ONLY the 'read' scope: market data + positions, all modes
    tasty-sandbox      sandbox app: orders in paper mode
    tasty-prod-trade   production app with 'trade' scope: orders in live mode (create only when going live)

Rules: values are never logged or printed (repr is redacted); files readable by group/others are refused;
a profile's TASTY_ENV must match what the profile name promises.
"""
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

PROFILE_ENV = {
    'tasty-prod-read': 'production',
    'tasty-prod-trade': 'production',
    'tasty-sandbox': 'sandbox',
}
BASE_URL = {'production': 'https://api.tastyworks.com', 'sandbox': 'https://api.cert.tastyworks.com'}


class SecretsError(Exception):
    pass


def secrets_dir() -> Path:
    return Path(os.environ.get('OPTION_TOOLS_SECRETS_DIR') or Path.home() / '.config' / 'option-tools')


def parse_env_file(path: Path) -> Dict[str, str]:
    out = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise SecretsError(f"{path.name}:{n}: expected KEY=value")
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def load_profile(profile: str) -> Dict[str, str]:
    path = secrets_dir() / f'{profile}.env'
    if not path.exists():
        raise SecretsError(f"secrets file missing: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SecretsError(f"{path} is accessible by group/others (mode {mode:o}); run: chmod 600 {path}")
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise SecretsError(f"{path.parent} is accessible by group/others; run: chmod 700 {path.parent}")
    return parse_env_file(path)


@dataclass(frozen=True)
class TastyCredentials:
    profile: str
    env: str                                  # 'production' | 'sandbox'
    base_url: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    client_id: str = field(default='', repr=False)

    def __repr__(self):
        return f"TastyCredentials(profile={self.profile!r}, env={self.env!r}, secrets=<redacted>)"


def tasty_credentials(profile: str) -> TastyCredentials:
    if profile not in PROFILE_ENV:
        raise SecretsError(f"unknown profile '{profile}', expected one of {sorted(PROFILE_ENV)}")
    d = load_profile(profile)
    env = d.get('TASTY_ENV', '')
    if env != PROFILE_ENV[profile]:
        raise SecretsError(f"profile '{profile}' must have TASTY_ENV={PROFILE_ENV[profile]} (found '{env or 'unset'}')")
    missing = [k for k in ('TASTY_CLIENT_SECRET', 'TASTY_REFRESH_TOKEN') if not d.get(k)]
    if missing:
        raise SecretsError(f"profile '{profile}' is missing {missing}")
    return TastyCredentials(profile=profile, env=env, base_url=BASE_URL[env],
                            client_secret=d['TASTY_CLIENT_SECRET'], refresh_token=d['TASTY_REFRESH_TOKEN'],
                            client_id=d.get('TASTY_CLIENT_ID', ''))


def profile_for(role: str, mode: str) -> str:
    """role: 'data' -> always the read-only production profile; 'orders' -> sandbox (paper) or prod-trade (live)."""
    if role == 'data':
        return 'tasty-prod-read'
    if role == 'orders':
        return {'paper': 'tasty-sandbox', 'live': 'tasty-prod-trade'}[mode]
    raise SecretsError(f"unknown role '{role}'")
