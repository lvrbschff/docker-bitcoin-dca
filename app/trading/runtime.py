"""Serialize configuration changes with the single exchange account worker."""
import json
import logging
import time
from pathlib import Path
from app.core.configuration import ConfigStore, account_values, runtime_settings, validate
from app.exchanges.exchange import create_exchange
from app.data.storage import STATE_PATH
from app.core.security import AttemptLimiter, PasswordConfirmationError

LOG = logging.getLogger('bitcoin-dca.settings')
IDENTITY_FIELDS = ('exchange', 'quote', 'kraken_api_key', 'kraken_api_secret', 'bitvavo_api_key', 'bitvavo_api_secret')


class Runtime:
    def __init__(self, store=None, state_path=None):
        from app.trading.bot import BOT_LOCK
        from app.trading.worker import Worker
        self.lock = BOT_LOCK
        self.store = store or ConfigStore()
        self.state_path = Path(state_path) if state_path is not None else STATE_PATH
        self.settings = runtime_settings(self.store.snapshot()['settings'])
        self.api = None
        self.service = None
        self.session_validator = lambda token: False
        self.password_attempts = AttemptLimiter(capacity=1)
        self.startup_pending = True
        self.exchange_check_pending = True
        self.ready_at = time.monotonic() + self.settings['startup_delay_seconds']
        self.worker = Worker(self.cycle, self.action, self.settings['check_interval_seconds'])
        if self.store.snapshot()['complete']:
            self.api = create_exchange(self.settings)
            self._synchronize_state()
        LOG.info('Application configuration loaded' if self.api else 'Waiting for first-run configuration; purchases are paused')

    def _check_exchange_activity(self):
        """Read-only startup gate; never submit or retry financial requests here."""
        from app.data.activity import withdrawal_records, write_json_atomic
        from app.trading.withdrawals import reconcile_withdrawals
        from app.core.logging_utils import log_transition
        orders = self.api.open_orders()
        if not isinstance(orders, list):
            raise ValueError('Open exchange orders could not be verified.')
        transfers = self.api.btc_withdrawals(0, int(time.time() * 1000))
        if not isinstance(transfers, list):
            raise ValueError('Complete withdrawal status could not be verified.')
        terminal = {'completed', 'success', 'failed', 'failure', 'canceled', 'cancelled'}
        pending = any(not isinstance(item, dict) or str(item.get('status', '')).lower() not in terminal for item in transfers)
        reconcile_withdrawals(self.api, self.settings, force=True, submitted_only=True)
        recorded = any(row.get('withdrawalStatus') == 'submitted' and row.get('exchange') in ('', None, self.settings['exchange']) for row in withdrawal_records(unresolved=True))
        if orders or pending or recorded:
            log_transition(LOG, self, 'startup-exchange-check', 'waiting', logging.INFO,
                           'Startup exchange check: waiting for open orders or Bitcoin withdrawals to close; new transactions are paused')
            return False
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        # Retire the former manual flag without resetting pending requests or history.
        if 'recoveryRequired' in state:
            state.pop('recoveryRequired')
            write_json_atomic(self.state_path, state)
        self.exchange_check_pending = False
        LOG.info('Startup exchange check passed: no open orders or Bitcoin withdrawals; scheduling may resume')
        return True

    def unresolved(self):
        from app.data.activity import withdrawal_records
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        return bool(state.get('pending_order') or state.get('pending_withdrawal') or withdrawal_records(unresolved=True))

    def _synchronize_state(self):
        from app.data.activity import write_json_atomic
        identity = [self.settings['exchange'], self.settings['quote']]
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        if (state.get('configuration_account', identity) != identity
                or state.get('configuration_live', self.settings['live']) != self.settings['live']):
            if self.unresolved():
                raise ValueError('Resolve pending transactions before changing the exchange or purchase currency.')
            # Keep historical activity untouched; old account scheduling must not carry over.
            write_json_atomic(self.state_path.with_name('previous-account-state.json'), state)
            state = {}
        if state.get('configuration_account') != identity or state.get('configuration_live') != self.settings['live']:
            state['configuration_account'] = identity
            state['configuration_live'] = self.settings['live']
            write_json_atomic(self.state_path, state)

    def cycle(self):
        from app.trading.bot import run_once, update_dashboard_schedule, latest_recorded_buy_time
        with self.lock:
            if not self.api:
                return self.worker.interval
            remaining = self.ready_at - time.monotonic()
            if remaining > 0:
                update_dashboard_schedule(None, 'Waiting for startup delay')
                return max(1, remaining)
            self._synchronize_state()
            if self.exchange_check_pending:
                update_dashboard_schedule(None, 'Checking exchange for open orders and withdrawals')
                if not self._check_exchange_activity():
                    update_dashboard_schedule(None, 'Waiting for open exchange orders or withdrawals')
                    return self.worker.interval
            if self.startup_pending:
                self.settings['startup_last_buy_at'] = latest_recorded_buy_time(self.settings['exchange'])
                self.settings['startup_schedule_log_pending'] = True
                LOG.info('Startup history: last real Bitcoin purchase on %s=%s; aligning the purchase schedule',
                         self.settings['exchange'], self.settings['startup_last_buy_at'] or 'none')
                self.startup_pending = False
            return run_once(self.api, self.settings, self.worker.interval)

    def action(self, action, session, token=None):
        from app.trading.bot import manual_withdrawal_service
        with self.lock:
            if not self.api or not self.settings['manual_withdrawal_enabled']:
                raise ValueError('Manual withdrawals are disabled in Settings.')
            if self.exchange_check_pending:
                raise ValueError('Waiting for the automatic exchange check and any open orders or withdrawals to close.')
            if self.store.snapshot()['account'].get('password_change_required'):
                raise ValueError('Change your initial dashboard password in Settings first.')
            if not self.session_validator(session):
                raise ValueError("Your session expired. Sign in again before requesting a withdrawal.")
            if not self.service:
                self.service = manual_withdrawal_service(self.api, self.settings)
            return self.service(action, session, token)

    def save(self, payload):
        with self.lock, self.store.lock:
            if not isinstance(payload, dict) or set(payload) - {'settings', 'username', 'password', 'current_password'}:
                raise ValueError('Invalid settings request.')
            previous = self.store.snapshot()
            values = validate(payload.get('settings', {}), previous['settings'])
            account = previous['account']
            if not account: raise ValueError('Create a dashboard account first.')
            account_changed = bool(payload.get('password') or payload.get('username', account['username']) != account['username'])
            protected_fields = IDENTITY_FIELDS + ('withdraw_address', 'withdraw_network', 'kraken_withdraw_key', 'withdraw_threshold')
            sensitive = previous['complete'] and (any(values[k] != previous['settings'][k] for k in protected_fields)
                         or values['live'] and not previous['settings']['live']
                         or values['withdrawals_enabled'] and not previous['settings']['withdrawals_enabled'])
            if account.get('password_change_required') or account_changed or sensitive:
                retry = self.password_attempts.consume(account['username'])
                if retry:
                    raise PasswordConfirmationError('Too many password confirmation attempts. Try again later.', retry)
                if not self.store.authenticate(account['username'], payload.get('current_password', '')):
                    LOG.info('Sensitive settings update rejected: current password verification failed')
                    raise PasswordConfirmationError('Your current password is incorrect.')
                self.password_attempts.clear(account['username'])
                LOG.info('Current password verified for sensitive settings update')
            if account.get('password_change_required') or account_changed:
                username = payload.get('username') or account['username']
                password = payload.get('password', '')
                if password or account.get('password_change_required'):
                    account = account_values(username, password)
                else:
                    if not isinstance(username, str) or not 3 <= len(username.strip()) <= 64:
                        raise ValueError('Username must contain 3–64 characters.')
                    account = {**account, 'username': username.strip()}
            identity_changed = any(values[key] != previous['settings'][key] for key in IDENTITY_FIELDS)
            financial_mode_changed = values['live'] != previous['settings']['live']
            if (identity_changed or financial_mode_changed) and (self.unresolved() or self.worker.queue.unfinished_tasks):
                LOG.info('Settings update blocked: unresolved transactions or queued account actions; configuration unchanged')
                raise ValueError('Resolve pending orders, withdrawals and queued requests before changing exchange, credentials, currency or trading mode.')
            settings = runtime_settings(values)
            adapter_changed = self.api is None or identity_changed or values['kraken_withdraw_key'] != previous['settings']['kraken_withdraw_key']
            adapter = create_exchange(settings) if adapter_changed else self.api
            try:
                if values['withdrawals_enabled']:
                    adapter.validate_withdrawal_settings(settings)
                self.store.commit(values, account)
            except Exception:
                if adapter_changed and hasattr(adapter, 'session'): adapter.session.close()
                raise
            # A shared mapping keeps existing HTTP handlers up to date. Preserve running
            # scheduling metadata for cosmetic feature toggles and account edits.
            schedule_fields = ('exchange', 'quote', 'buy_amount', 'deposit_day', 'timezone', 'live', 'check_interval_seconds', 'startup_delay_seconds', 'order_status_check_seconds', 'adaptive_dca_enabled', 'adaptive_dca_strength')
            schedule_changed = not previous['complete'] or identity_changed or any(values[k] != previous['settings'][k] for k in schedule_fields)
            self.settings.update(settings)
            old_api = self.api
            self.api = adapter
            if adapter_changed and old_api and hasattr(old_api, 'session'): old_api.session.close()
            self.service = None  # Invalidate all previews after any configuration change.
            self.worker.interval = values['check_interval_seconds']
            if identity_changed:
                from app.data.activity import write_dashboard_snapshot
                write_dashboard_snapshot({'exchange': values['exchange'], 'quote': values['quote'], 'nextPurchaseAt': None, 'nextPurchaseStatus': 'Waiting for account check'})
            if not previous['complete'] or identity_changed or financial_mode_changed:
                self.exchange_check_pending = True
            if schedule_changed:
                self.settings.pop('startup_last_buy_at', None)
                self.startup_pending = True
                self.ready_at = time.monotonic() + (values['startup_delay_seconds'] if not previous['complete'] else 0)
                self.worker.reconfigure.set()
            changed_fields = sorted(key for key in values if values[key] != previous['settings'][key])
            if not previous['complete']:
                LOG.info('First-run bot configuration completed: exchange=%s; live trading=%s; startup delay=%ss', values['exchange'], values['live'], values['startup_delay_seconds'])
            LOG.info('Settings applied: changed fields=%s; account credentials changed=%s; purchase schedule refresh requested=%s', ','.join(changed_fields) or 'none', account_changed, schedule_changed)
            LOG.info('Application settings saved: exchange=%s; live trading=%s; automatic withdrawals=%s; manual withdrawals=%s; Parqet=%s', values['exchange'], values['live'], values['withdrawals_enabled'], values['manual_withdrawal_enabled'], values['parqet_enabled'])
            return account_changed

    def save_forecast_deposit(self, payload):
        """Persist a reporting assumption without touching the exchange worker."""
        if not isinstance(payload, dict) or set(payload) != {'monthlyDeposit'}:
            raise ValueError('Invalid monthly deposit request.')
        with self.store.lock:
            previous = self.store.snapshot()
            if not previous['complete']:
                raise ValueError('Complete initial configuration first.')
            values = validate({'expected_monthly_deposit': payload['monthlyDeposit']}, previous['settings'])
            amount = values['expected_monthly_deposit']
            if amount != previous['settings'].get('expected_monthly_deposit', ''):
                self.store.commit(values)
                self.settings['expected_monthly_deposit'] = amount
                LOG.info('Expected monthly deposit saved: amount=%s %s; forecast only; purchase schedule unchanged', amount or 'unset', values['quote'])
            return {'monthlyDeposit': amount}

    def test_connection(self, payload):
        # Read-only and serialized so a connection test cannot race a financial action.
        with self.lock:
            values = validate(payload, self.store.snapshot()['settings'])
            LOG.info('Exchange connection test started: exchange=%s; quote currency=%s; read-only balance access', values['exchange'], values['quote'])
            started = time.monotonic()
            api = create_exchange(runtime_settings(values))
            try:
                api.balance(values['quote'])
                api.balance('BTC')
                LOG.info('Exchange connection test succeeded: exchange=%s; duration=%.2fs; no orders or withdrawals submitted', values['exchange'], time.monotonic() - started)
                return {'message': 'Connection successful. Account balances are accessible; no order or withdrawal was submitted.'}
            finally:
                if hasattr(api, 'session'): api.session.close()
