"""Private, atomic application configuration managed exclusively through the web UI."""
import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import secrets
from storage import write_json_atomic
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG = logging.getLogger('bitcoin-dca.settings')
CONFIG_PATH = Path('/webui-data/config.json')

SECRET_FIELDS = tuple(f'{exchange}_api_{part}' for exchange in ('kraken', 'bitvavo') for part in ('key', 'secret'))
DEFAULTS = dict(exchange='KRAKEN', quote='EUR', buy_amount='0', deposit_day=1,
                startup_delay_seconds=60, order_status_check_seconds=30, check_interval_seconds=300,
                timezone='Europe/Berlin', live=False, withdrawals_enabled=False,
                withdraw_threshold='1000', withdraw_address='', withdraw_network='BTC', kraken_withdraw_key='',
                manual_withdrawal_enabled=True, parqet_enabled=True, display_currency='EUR', expected_monthly_deposit='',
                **{key: '' for key in SECRET_FIELDS})


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
    for key in ('buy_amount', 'withdraw_threshold', 'expected_monthly_deposit'):
        if key == 'expected_monthly_deposit' and result[key] == '':
            continue
        try:
            number = Decimal(result[key])
            if not number.is_finite() or number < 0 or (key == 'withdraw_threshold' and number <= 0):
                raise InvalidOperation
            result[key] = str(number)
        except InvalidOperation:
            if key == 'expected_monthly_deposit':
                raise ValueError('Expected monthly deposit must be a finite non-negative amount.') from None
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
    if require_credentials and previous and result['exchange'] != previous['exchange']:
        keys = [f'{prefix}_api_key', f'{prefix}_api_secret']
        if any(not isinstance(values.get(key), str) or not values[key].strip() for key in keys):
            raise ValueError('Changing exchanges requires entering the API key and API secret for the selected exchange again.')
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
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else CONFIG_PATH
        self.lock = threading.RLock()
        if self.path.exists():
            self.data = self._read(self.path)
            LOG.debug('Stored application configuration loaded; setup complete=%s', self.data['complete'])
            return
        self.data = dict(version=1, complete=False, account=None, settings=validate({}, require_credentials=False))

    @staticmethod
    def _read(path):
        os.chmod(path, 0o600)
        data = json.loads(path.read_text())
        data['settings'] = validate(data['settings'], require_credentials=data['complete'])
        return data

    def _write(self, data):
        write_json_atomic(self.path, data)

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
    result.update(buy_amount=Decimal(values['buy_amount']), withdraw_threshold=Decimal(values['withdraw_threshold']))
    return result
