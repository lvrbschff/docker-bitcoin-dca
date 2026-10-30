"""Serialize configuration changes with the single exchange account worker."""
import json
import logging
import time
from pathlib import Path
from configuration import ConfigStore, account_values, runtime_settings, validate
from exchange import create_exchange

LOG = logging.getLogger('bitcoin-dca.settings')
IDENTITY_FIELDS = ('exchange', 'quote', 'kraken_api_key', 'kraken_api_secret', 'bitvavo_api_key', 'bitvavo_api_secret')


class Runtime:
    def __init__(self, store=None, state_path='/data/state.json'):
        from bot import BOT_LOCK
        from worker import Worker
        self.lock = BOT_LOCK
        self.store = store or ConfigStore()
        self.state_path = Path(state_path)
        self.settings = runtime_settings(self.store.snapshot()['settings'])
        self.api = None
        self.service = None
        self.startup_pending = True
        self.ready_at = time.monotonic() + self.settings['startup_delay_seconds']
        self.worker = Worker(self.cycle, self.action, lambda: None, self.settings['check_interval_seconds'], 0)
        if self.store.snapshot()['complete']:
            self.api = create_exchange(self.settings)
            self._synchronize_state()
        LOG.info('Application configuration loaded' if self.api else 'Waiting for first-run configuration; purchases are paused')

    def unresolved(self):
        from activity import withdrawal_records
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        return bool(state.get('pending_order') or state.get('pending_withdrawal') or withdrawal_records(unresolved=True))

    def _synchronize_state(self):
        from activity import write_json_atomic
        identity = [self.settings['exchange'], self.settings['quote']]
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        if (state.get('configuration_account', identity) != identity
                or state.get('configuration_live', self.settings['live']) != self.settings['live']):
            if self.unresolved():
                raise ValueError('Resolve pending transactions before changing the exchange or purchase currency.')
            # Keep historical activity untouched; old account scheduling must not carry over.
            write_json_atomic(self.state_path.with_name('previous-account-state.json'), state)
            state = {}
        state['configuration_account'] = identity
        state['configuration_live'] = self.settings['live']
        write_json_atomic(self.state_path, state)

    def cycle(self):
        from bot import run_once, update_dashboard_schedule, latest_recorded_buy_time
        with self.lock:
            if not self.api:
                return self.worker.interval
            remaining = self.ready_at - time.monotonic()
            if remaining > 0:
                update_dashboard_schedule(None, 'Waiting for startup delay')
                return max(1, remaining)
            self._synchronize_state()
            if self.startup_pending:
                self.settings['startup_last_buy_at'] = latest_recorded_buy_time(self.settings['exchange'])
                self.settings['startup_schedule_log_pending'] = True
                LOG.info('Startup history: last real Bitcoin purchase on %s=%s; aligning the purchase schedule',
                         self.settings['exchange'], self.settings['startup_last_buy_at'] or 'none')
                self.startup_pending = False
            return run_once(self.api, self.settings, self.worker.interval)

    def action(self, action, session, token=None):
        from bot import manual_withdrawal_service
        with self.lock:
            if not self.api or not self.settings['manual_withdrawal_enabled']:
                raise ValueError('Manual withdrawals are disabled in Settings.')
            if self.store.snapshot()['account'].get('password_change_required'):
                raise ValueError('Change your initial dashboard password in Settings first.')
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
            if account.get('password_change_required') or account_changed:
                if not self.store.authenticate(account['username'], payload.get('current_password', '')):
                    raise ValueError('Your current password is incorrect.')
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
                raise ValueError('Resolve pending orders, withdrawals and queued requests before changing exchange, credentials, currency or trading mode.')
            settings = runtime_settings(values)
            adapter = create_exchange(settings)
            if values['withdrawals_enabled']:
                adapter.validate_withdrawal_settings(settings)
            try:
                self.store.commit(values, account)
            except Exception:
                if hasattr(adapter, 'session'): adapter.session.close()
                raise
            # A shared mapping keeps existing HTTP handlers up to date. Preserve running
            # scheduling metadata for cosmetic feature toggles and account edits.
            schedule_fields = ('exchange', 'quote', 'buy_amount', 'deposit_day', 'timezone', 'live', 'check_interval_seconds', 'startup_delay_seconds', 'order_status_check_seconds')
            schedule_changed = not previous['complete'] or identity_changed or any(values[k] != previous['settings'][k] for k in schedule_fields)
            for key, value in settings.items():
                if key != 'first_purchase_done': self.settings[key] = value
            old_api = self.api
            self.api = adapter
            if old_api and hasattr(old_api, 'session'): old_api.session.close()
            self.service = None  # Invalidate all previews after any configuration change.
            self.worker.interval = values['check_interval_seconds']
            if identity_changed:
                from activity import write_dashboard_snapshot
                write_dashboard_snapshot({'exchange': values['exchange'], 'quote': values['quote'], 'nextPurchaseAt': None, 'nextPurchaseStatus': 'Waiting for account check'})
            if schedule_changed:
                self.settings['first_purchase_done'] = False
                self.settings.pop('startup_last_buy_at', None)
                self.startup_pending = True
                self.ready_at = time.monotonic() + (values['startup_delay_seconds'] if not previous['complete'] else 0)
                self.worker.reconfigure.set()
            LOG.info('Application settings saved: exchange=%s; live trading=%s; automatic withdrawals=%s; manual withdrawals=%s; Parqet=%s', values['exchange'], values['live'], values['withdrawals_enabled'], values['manual_withdrawal_enabled'], values['parqet_enabled'])
            return account_changed

    def test_connection(self, payload):
        # Read-only and serialized so a connection test cannot race a financial action.
        with self.lock:
            values = validate(payload, self.store.snapshot()['settings'])
            api = create_exchange(runtime_settings(values))
            try:
                api.balance(values['quote'])
                api.balance('BTC')
                return {'message': 'Connection successful. Account balances are accessible; no order or withdrawal was submitted.'}
            finally:
                if hasattr(api, 'session'): api.session.close()
