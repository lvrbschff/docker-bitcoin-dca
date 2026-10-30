"""Configuration lifecycle and financial guards, without live exchange requests."""
import copy
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
import activity
import bot
import configuration as config
import runtime
import settings_ui
import web_ui

PASSWORD = 'a-long-private-password'
CREDENTIALS = {'kraken_api_key': 'fictional-key', 'kraken_api_secret': 'c2VjcmV0'}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = config.ConfigStore(self.root / 'config.json', environ={})
        for name, value in [('ACTIVITY_PATH', self.root / 'activity.json'), ('LEGACY_ACTIVITY_PATHS', ()),
                            ('SNAPSHOT_PATH', self.root / 'dashboard.json'), ('LEGACY_SNAPSHOT_PATHS', ())]:
            p = patch.object(activity, name, value)
            p.start(); self.addCleanup(p.stop)
        self.factory = patch.object(runtime, 'create_exchange', side_effect=lambda settings: Mock())
        self.api_factory = self.factory.start(); self.addCleanup(self.factory.stop)
        self.app = runtime.Runtime(self.store, self.root / 'state.json')

    def account(self): self.store.create_account('operator', PASSWORD)
    def ready(self, **settings):
        self.account()
        self.app.save({'settings': {**CREDENTIALS, **settings}})
        self.app.worker.reconfigure.clear()

    def test_no_default_account_or_exchange_calls_before_configuration(self):
        self.assertIsNone(self.store.snapshot()['account'])
        self.assertFalse(self.store.authenticate('admin', 'admin'))
        self.app.cycle()
        self.api_factory.assert_not_called()
        self.assertFalse(self.store.path.exists())

    def test_account_is_hashed_private_and_can_only_be_created_once(self):
        self.account()
        raw = self.store.path.read_text()
        self.assertNotIn(PASSWORD, raw)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.store.authenticate('operator', PASSWORD))
        self.assertFalse(self.store.authenticate('operator', 'wrong'))
        with self.assertRaises(ValueError): self.account()
        public = self.store.public()
        self.assertNotIn('password_hash', json.dumps(public))
        self.assertFalse(public['complete'])

    def test_parallel_first_account_creation_claims_only_one_account(self):
        def create(name):
            try: self.store.create_account(name, PASSWORD); return True
            except ValueError: return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(create, ['first', 'second'])), 1)

    def test_saved_credentials_are_masked_and_blank_preserves_them(self):
        self.ready()
        self.app.save({'settings': {'kraken_api_key': '', 'kraken_api_secret': '', 'parqet_enabled': False}})
        self.assertEqual(self.store.snapshot()['settings']['kraken_api_key'], CREDENTIALS['kraken_api_key'])
        public = self.store.public()
        self.assertTrue(public['settings']['kraken_api_key_configured'])
        self.assertNotIn('fictional-key', json.dumps(public))
        self.assertNotIn('c2VjcmV0', settings_ui.settings_page(self.store))
        self.assertNotIn(PASSWORD, settings_ui.settings_page(self.store))
        reloaded = config.ConfigStore(self.store.path, environ={'KRAKEN_API_KEY': 'different'})
        self.assertEqual(reloaded.snapshot(), self.store.snapshot())

    def test_failed_atomic_save_leaves_memory_and_file_unchanged(self):
        self.ready()
        old = self.store.snapshot(); raw = self.store.path.read_bytes()
        with patch.object(config.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError): self.app.save({'settings': {'buy_amount': '.001'}})
        self.assertEqual(self.store.snapshot(), old)
        self.assertEqual(self.store.path.read_bytes(), raw)
        self.assertEqual(self.app.settings['buy_amount'], 0)
        self.assertEqual(list(self.root.glob('.config-*')), [])

    def test_validation_rejects_bad_values_before_persisting(self):
        self.ready()
        for values in [{'deposit_day': 32}, {'check_interval_seconds': 5}, {'live': 'true'}, {'buy_amount': 'NaN'},
                       {'withdraw_threshold': '-1'}, {'timezone': '../wrong'}, {'exchange': 'OTHER'},
                       {'kraken_api_secret': 'invalid'}, {'withdrawals_enabled': True}, {'unknown': 1},
                       {'exchange': 'BITVAVO', 'quote': 'USD'}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.app.save({'settings': values})

    def test_legacy_environment_is_imported_once_with_password_upgrade(self):
        env = {'KRAKEN_API_KEY': 'old-key', 'KRAKEN_API_SECRET': 'c2VjcmV0', 'LIVE_TRADING': 'true',
               'BUY_AMOUNT': '.0006', 'WEB_UI_USERNAME': 'old-user', 'WEB_UI_PASSWORD': 'admin'}
        store = config.ConfigStore(self.root / 'legacy.json', environ=env)
        self.assertTrue(store.snapshot()['complete'])
        self.assertTrue(store.public()['passwordChangeRequired'])
        self.assertTrue(store.authenticate('old-user', 'admin'))
        self.assertTrue(store.snapshot()['settings']['live'])
        self.assertEqual(store.snapshot()['settings']['buy_amount'], '0.0006')
        self.assertEqual(config.ConfigStore(store.path, environ={}).snapshot(), store.snapshot())

    def test_pending_and_queued_actions_block_identity_or_live_changes(self):
        self.ready()
        for state in [{'pending_order': {'id': 'x'}}, {'pending_withdrawal': {'id': 'x'}}]:
            self.app.state_path.write_text(json.dumps(state))
            for values in [{'live': True}, {'quote': 'USD'}, {'kraken_api_key': 'new-key'}]:
                with self.subTest(state=state, values=values), self.assertRaisesRegex(ValueError, 'Resolve pending'):
                    self.app.save({'settings': values})
        self.app.state_path.unlink()
        with patch.object(activity, 'withdrawal_records', return_value=[{'withdrawalStatus': 'submitted'}]):
            with self.assertRaises(ValueError): self.app.save({'settings': {'live': True}})
        self.app.worker.queue.put('queued-action')
        with self.assertRaises(ValueError): self.app.save({'settings': {'live': True}})

    def test_feature_toggles_do_not_recalculate_purchase_schedule(self):
        self.ready()
        self.app.state_path.write_text(json.dumps({'pending_withdrawal': {'id': 'x'}}))
        self.app.save({'settings': {'withdrawals_enabled': False, 'manual_withdrawal_enabled': False, 'parqet_enabled': False}})
        self.assertFalse(self.app.worker.reconfigure.is_set())
        with self.assertRaisesRegex(ValueError, 'disabled'): self.app.action('preview', 'session')
        self.assertIn('pending_withdrawal', json.loads(self.app.state_path.read_text()))

    def test_startup_delay_is_applied_after_setup_not_before(self):
        self.ready(startup_delay_seconds=60)
        with patch.object(bot, 'run_once') as run:
            self.app.cycle(); run.assert_not_called()
            self.app.ready_at = 0
            with patch.object(bot, 'latest_recorded_buy_time', return_value=None) as history:
                self.app.cycle()
            history.assert_called_once_with('KRAKEN')
            run.assert_called_once()

    def test_exchange_switch_resets_old_schedule_without_changing_history(self):
        self.ready(startup_delay_seconds=0)
        self.app._synchronize_state()
        self.app.state_path.write_text(json.dumps({'configuration_account': ['KRAKEN', 'EUR'], 'last_order_at': '2026-10-01', 'spent': '20'}))
        self.app.save({'settings': {'exchange': 'BITVAVO', 'bitvavo_api_key': 'b-key', 'bitvavo_api_secret': 'b-secret'}})
        with patch.object(bot, 'run_once'), patch.object(bot, 'latest_recorded_buy_time', return_value=None) as latest:
            self.app.cycle()
        self.assertNotIn('last_order_at', json.loads(self.app.state_path.read_text()))
        self.assertEqual(json.loads((self.root / 'previous-account-state.json').read_text())['spent'], '20')
        self.assertEqual(activity.read_dashboard_snapshot()['exchange'], 'BITVAVO')
        latest.assert_called_once_with('BITVAVO')

    def test_enabling_live_trading_does_not_reuse_simulated_execution_state(self):
        self.ready(startup_delay_seconds=0)
        self.app._synchronize_state()
        state=json.loads(self.app.state_path.read_text())
        state.update(spent='100',last_order_at='2026-10-30T10:00:00Z')
        self.app.state_path.write_text(json.dumps(state))
        self.app.save({'settings': {'live':True}})
        with patch.object(bot,'run_once'),patch.object(bot,'latest_recorded_buy_time',return_value=None):
            self.app.cycle()
        state=json.loads(self.app.state_path.read_text())
        self.assertNotIn('spent',state)
        self.assertNotIn('last_order_at',state)
        self.assertTrue(state['configuration_live'])


    def test_connection_test_only_reads_balances_and_does_not_save(self):
        self.account()
        before = self.store.snapshot()
        adapter = Mock(); self.api_factory.side_effect = None; self.api_factory.return_value = adapter
        self.app.test_connection(CREDENTIALS)
        self.assertEqual(adapter.balance.call_args_list, [unittest.mock.call('EUR'), unittest.mock.call('BTC')])
        adapter.buy.assert_not_called(); adapter.withdraw_btc.assert_not_called()
        self.assertEqual(before, self.store.snapshot())
        adapter.session.close.assert_called_once()

    def test_account_update_requires_current_password(self):
        self.ready()
        with self.assertRaisesRegex(ValueError, 'current password'):
            self.app.save({'password': 'new-password-is-long', 'current_password': 'wrong'})
        self.assertTrue(self.app.save({'password': 'new-password-is-long', 'current_password': PASSWORD}))
        self.assertFalse(self.store.authenticate('operator', PASSWORD))
        self.assertTrue(self.store.authenticate('operator', 'new-password-is-long'))

    def test_username_can_change_without_replacing_password(self):
        self.ready()
        self.assertTrue(self.app.save({'username': 'renamed', 'current_password': PASSWORD}))
        self.assertTrue(self.store.authenticate('renamed', PASSWORD))
        self.assertFalse(self.store.authenticate('operator', PASSWORD))

    def test_legacy_weak_password_cannot_complete_settings_without_upgrade(self):
        self.ready()
        with self.store.lock:
            self.store.data['account']['password_change_required']=True
        with self.assertRaises(ValueError): self.app.save({'settings': {'parqet_enabled': False}})
        self.app.save({'current_password': PASSWORD,'password':'another-private-password'})
        self.assertFalse(self.store.public()['passwordChangeRequired'])


    def test_setup_html_escapes_embedded_data(self):
        self.ready(kraken_withdraw_key='</script><script>alert(1)</script>')
        page = settings_ui.settings_page(self.store)
        self.assertNotIn('</script><script>alert(1)', page)
        self.assertIn('\\u003c/script', page)

    def server(self):
        # Exercise the real HTTP handlers in memory; no listening socket or exchange.
        self.cookie = ''
        def request(path, payload=None, header=True):
            h = object.__new__(web_ui.Handler)
            h.runtime = self.app; h.settings = self.app.settings
            h.worker = self.app.worker; h.withdrawal_service = self.app.worker.submit
            h.path = path; h.headers = {'Cookie': self.cookie}
            if payload is not None:
                body = json.dumps(payload).encode()
                h.headers.update({'Content-Length': str(len(body)), 'Content-Type': 'application/json'})
                if header: h.headers['X-DCA-Action'] = 'settings'
                h.rfile = io.BytesIO(body)
            h.wfile = io.BytesIO()
            result = {}; headers = {}
            h.send_response = lambda status: result.update(status=status)
            h.send_header = lambda key, value: headers.update({key: value})
            h.end_headers = lambda: None
            h.do_GET() if payload is None else h.do_POST()
            if 'Set-Cookie' in headers: self.cookie = headers['Set-Cookie'].split(';')[0]
            return result['status'], h.wfile.getvalue().decode()
        return request

    def test_settings_dialog_uses_authenticated_embedded_page(self):
        request=self.server()
        request('/api/setup/account',{'username':'operator','password':PASSWORD})
        status,page=request('/settings?embedded=1')
        self.assertEqual(status,200)
        self.assertIn('"embedded": true',page)
        self.cookie=''
        status,page=request('/settings?embedded=1')
        self.assertEqual(status,401)
        self.assertIn('dca-settings-auth-required',page)
        status,page=request('/')
        self.assertEqual(status,200)
        self.assertIn('Sign in',page)

    def test_http_setup_login_settings_and_feature_enforcement(self):
        request = self.server()
        status, page = request('/')
        self.assertEqual(status, 200); self.assertIn('1. Create your account', page)
        self.assertEqual(request('/api/settings')[0], 401)
        credentials = {'username': 'operator', 'password': PASSWORD}
        self.assertEqual(request('/api/setup/account', credentials, header=False)[0], 400)
        self.assertEqual(request('/api/setup/account', credentials)[0], 200)
        self.assertEqual(request('/api/setup/account', credentials)[0], 400)
        self.assertEqual(request('/api/schedule')[0], 403)
        self.assertEqual(request('/api/settings', {'settings': {**CREDENTIALS, 'manual_withdrawal_enabled': False, 'parqet_enabled': False}})[0], 200)
        status, page = request('/')
        self.assertIn('Settings</button>', page)
        self.assertIn('#manualWithdraw,#withdrawal-note', page)
        self.assertIn('#parqet-note', page)
        self.assertEqual(request('/api/withdrawal/preview', {})[0], 403)
        self.assertEqual(request('/api/export.csv?start=0&end=1000')[0], 403)
        public = request('/api/settings')[1]
        self.assertNotIn('fictional-key', public)
        result = request('/api/settings', {'password': 'new-password-is-long', 'current_password': PASSWORD})
        self.assertEqual(result[0], 200)
        self.assertEqual(request('/api/settings')[0], 401)
        self.assertIn('Sign in', request('/')[1])


if __name__ == '__main__': unittest.main()
