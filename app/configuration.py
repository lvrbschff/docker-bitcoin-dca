"""Private, atomic application configuration; environment import is migration-only."""
import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG = logging.getLogger('bitcoin-dca.settings')

SECRET_FIELDS = tuple(f'{exchange}_api_{part}' for exchange in ('kraken', 'bitvavo') for part in ('key', 'secret'))
DEFAULTS = dict(exchange='KRAKEN', quote='EUR', buy_amount='0', deposit_day=1,
                startup_delay_seconds=60, order_status_check_seconds=30, check_interval_seconds=300,
                timezone='Europe/Berlin', live=False, withdrawals_enabled=False,
                withdraw_threshold='1000', withdraw_address='', withdraw_network='BTC', kraken_withdraw_key='',
                manual_withdrawal_enabled=True, parqet_enabled=True, display_currency='EUR',
                **{key: '' for key in SECRET_FIELDS})
ENV_NAMES = dict(exchange='EXCHANGE', quote='QUOTE_CURRENCY', buy_amount='BUY_AMOUNT', deposit_day='DEPOSIT_DAY',
                 startup_delay_seconds='startup_delay_seconds', order_status_check_seconds='ORDER_STATUS_CHECK_SECONDS',
                 check_interval_seconds='CHECK_INTERVAL_SECONDS', timezone='TIMEZONE', live='LIVE_TRADING',
                 withdrawals_enabled='WITHDRAWALS_ENABLED', withdraw_threshold='WITHDRAW_THRESHOLD_QUOTE',
                 withdraw_address='WITHDRAW_ADDRESS', withdraw_network='WITHDRAW_NETWORK', kraken_withdraw_key='KRAKEN_WITHDRAW_KEY',
                 **{key: key.upper() for key in SECRET_FIELDS})


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + digest).decode()


def password_matches(password, encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        return hmac.compare_digest(hashlib.scrypt(password.encode(), salt=raw[:16], n=16384, r=8, p=1), raw[16:])
    except (ValueError, TypeError, AttributeError):
        return False


def account_values(username, password):
    if not isinstance(username, str) or not 3 <= len(username.strip()) <= 64:
        raise ValueError('Username must contain 3–64 characters.')
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError('Password must contain 12–1024 characters.')
    return dict(username=username.strip(), password_hash=password_hash(password), password_change_required=False)


def validate(values, previous=None, require_credentials=True):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError('Unknown configuration field.')
    result = copy.deepcopy(previous or DEFAULTS)
    for key, value in values.items():
        if key in SECRET_FIELDS and value == '':
            continue  # Empty password fields never erase stored credentials.
        result[key] = value
    for key, default in DEFAULTS.items():
        value = result[key]
        if isinstance(default, bool):
            if type(value) is not bool:
                raise ValueError(f'{key} must be enabled or disabled.')
        elif isinstance(default, int):
            if type(value) is not int:
                raise ValueError(f'{key} must be an integer.')
        elif not isinstance(value, str) or len(value) > 2048:
            raise ValueError(f'Invalid {key}.')
    result['exchange'] = result['exchange'].upper()
    result['quote'] = result['quote'].upper()
    result['display_currency'] = result['display_currency'].upper()
    if result['display_currency'] not in ('EUR', 'USD'):
        raise ValueError('Display currency must be EUR or USD.')
    result['withdraw_network'] = result['withdraw_network'].strip().upper()
    for key in SECRET_FIELDS + ('withdraw_address', 'kraken_withdraw_key'):
        result[key] = result[key].strip()
    if result['exchange'] not in ('KRAKEN', 'BITVAVO') or result['quote'] not in ('EUR', 'USD'):
        raise ValueError('Select Kraken or Bitvavo and EUR or USD.')
    if result['exchange'] == 'BITVAVO' and result['quote'] != 'EUR':
        raise ValueError('Bitvavo supports EUR purchases only.')
    for key in ('buy_amount', 'withdraw_threshold'):
        try:
            number = Decimal(result[key])
            if not number.is_finite() or number < 0 or (key == 'withdraw_threshold' and number <= 0):
                raise InvalidOperation
            result[key] = str(number)
        except InvalidOperation:
            raise ValueError(f'{key} must be a finite positive amount (purchase amount may be zero).') from None
    if not 1 <= result['deposit_day'] <= 31 or not 0 <= result['startup_delay_seconds'] <= 86400:
        raise ValueError('Deposit day must be 1–31; startup delay must be 0–86400 seconds.')
    if not 60 <= result['check_interval_seconds'] <= 86400 or not 1 <= result['order_status_check_seconds'] <= 3600:
        raise ValueError('Account checks must be 60–86400 seconds; order checks 1–3600 seconds.')
    try:
        ZoneInfo(result['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Enter a valid timezone, for example Europe/Berlin.') from None
    prefix = result['exchange'].lower()
    if require_credentials and not all(result[f'{prefix}_api_{part}'] for part in ('key', 'secret')):
        raise ValueError('API key and secret are required for the selected exchange.')
    if prefix == 'kraken' and result['kraken_api_secret']:
        try:
            if not base64.b64decode(result['kraken_api_secret'], validate=True):
                raise ValueError
        except ValueError:
            raise ValueError('Kraken API secret must be base64 encoded.') from None
    if result['withdrawals_enabled']:
        destination = result['kraken_withdraw_key'] if prefix == 'kraken' else result['withdraw_address'] and result['withdraw_network']
        if not destination:
            raise ValueError('Configure a Bitcoin withdrawal destination before enabling automatic withdrawals.')
    return result


class ConfigStore:
    def __init__(self, path='/data/config.json', environ=None):
        self.path = Path(path)
        self.lock = threading.RLock()
        env = os.environ if environ is None else environ
        if self.path.exists():
            os.chmod(self.path, 0o600)
            self.data = json.loads(self.path.read_text())
            self.data['settings'] = validate(self.data['settings'], require_credentials=self.data['complete'])
            LOG.debug('Stored application configuration loaded; setup complete=%s', self.data['complete'])
            return
        values = dict(DEFAULTS)
        for key, env_name in ENV_NAMES.items():
            if env_name in env:
                raw = env[env_name]
                values[key] = raw.lower() == 'true' if isinstance(DEFAULTS[key], bool) else int(raw) if isinstance(DEFAULTS[key], int) else raw
        prefix = values['exchange'].lower()
        legacy = bool(values.get(f'{prefix}_api_key') and values.get(f'{prefix}_api_secret'))
        self.data = dict(version=1, complete=legacy, account=None, settings=validate(values, require_credentials=legacy))
        if legacy:
            password = env.get('WEB_UI_PASSWORD') or 'admin'
            self.data['account'] = dict(username=env.get('WEB_UI_USERNAME') or 'admin', password_hash=password_hash(password), password_change_required=len(password) < 12 or password == 'admin')
            self._write(self.data)
            LOG.info('Legacy environment configuration imported: exchange=%s; password change required=%s', self.data['settings']['exchange'], self.data['account']['password_change_required'])

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.config-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w') as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name): os.unlink(name)

    def snapshot(self):
        with self.lock: return copy.deepcopy(self.data)

    def public(self):
        data = self.snapshot()
        settings = data['settings']
        for key in SECRET_FIELDS:
            settings[key + '_configured'] = bool(settings.pop(key))
        account = data['account']
        return dict(complete=data['complete'], settings=settings,
                    username=account['username'] if account else '',
                    passwordChangeRequired=bool(account and account.get('password_change_required')))

    def authenticate(self, username, password):
        with self.lock:
            account = self.data['account']
            # Always run the expensive password derivation for existing accounts.
            return bool(account and password_matches(password, account['password_hash']) and hmac.compare_digest(username.encode(), account['username'].encode()))

    def create_account(self, username, password):
        with self.lock:
            if self.data['account']:
                raise ValueError('An account already exists. Sign in to change settings.')
            data = self.snapshot()
            data['account'] = account_values(username, password)
            self._write(data)
            self.data = data
            LOG.info('Initial dashboard account created; bot configuration is required')

    def commit(self, settings, account=None):
        with self.lock:
            data = self.snapshot()
            data.update(settings=settings, complete=True)
            if account: data['account'] = account
            self._write(data)
            self.data = data


def runtime_settings(values):
    result = dict(values)
    result.update(buy_amount=Decimal(values['buy_amount']), withdraw_threshold=Decimal(values['withdraw_threshold']), first_purchase_done=False)
    return result
