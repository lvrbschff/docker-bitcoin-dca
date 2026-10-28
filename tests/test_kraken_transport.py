"""Offline regression tests for Kraken connection recovery and balance scope."""
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import requests
import activity
import bot
import kraken
from exchange_errors import DefinitiveRejection


class KrakenTransportTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        def new_session():
            session = Mock()
            session.post.return_value = self.response({'ZEUR': {'balance': '100'}, 'XXBT': {'balance': '.001'}})
            session.get.return_value = self.response({'status': 'online'})
            self.sessions.append(session)
            return session
        self.factory = patch.object(kraken.requests, 'Session', side_effect=new_session)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        credentials = patch.dict(os.environ, {'KRAKEN_API_KEY': 'fictional-key', 'KRAKEN_API_SECRET': 'dGVzdA=='})
        credentials.start()
        self.addCleanup(credentials.stop)
        self.api = kraken.Kraken('EUR', 'fictional-wallet')

    @staticmethod
    def response(result):
        response = Mock()
        response.json.return_value = {'error': [], 'result': result}
        return response

    def test_idle_pool_is_replaced_before_sending(self):
        old = self.api.session
        self.api._last_http_activity = 1
        with patch.object(kraken.time, 'monotonic', return_value=62):
            result = self.api.private('BalanceEx')
        old.close.assert_called_once()
        old.post.assert_not_called()
        self.assertEqual(len(self.sessions), 2)
        self.assertIn('ZEUR', result)
        self.assertEqual(self.sessions[1].post.call_args.kwargs['timeout'], (10, 30))

    def test_recent_connection_is_reused(self):
        current = self.api.session
        self.api._last_http_activity = 50
        with patch.object(kraken.time, 'monotonic', return_value=62):
            self.api.private('BalanceEx')
        self.assertEqual(len(self.sessions), 1)
        current.close.assert_not_called()

    def test_read_timeout_recovers_on_new_connection_with_new_nonce(self):
        failed = self.api.session
        failed.post.side_effect = requests.ReadTimeout('simulated stalled response')
        with patch.object(kraken.time, 'sleep') as sleep, \
                patch.object(kraken, '_next_nonce', side_effect=['1000', '1001']), \
                self.assertLogs(kraken.LOG, level='INFO') as logs:
            result = self.api.private('BalanceEx')
        self.assertIn('ZEUR', result)
        failed.close.assert_called_once()
        self.assertEqual(failed.post.call_count, 1)
        self.assertEqual(self.sessions[1].post.call_count, 1)
        first_nonce = parse_qs(failed.post.call_args.kwargs['data'])['nonce']
        second_nonce = parse_qs(self.sessions[1].post.call_args.kwargs['data'])['nonce']
        self.assertNotEqual(first_nonce, second_nonce)
        sleep.assert_called_once_with(1)
        self.assertTrue(any('WARNING' in entry and 'fresh HTTP connection' in entry for entry in logs.output))
        self.assertTrue(any('recovered on read attempt 2/3' in entry for entry in logs.output))

    def test_three_failed_reads_raise_and_do_not_return_old_balance(self):
        with self.api.balance_snapshot():
            self.assertEqual(self.api.balance('EUR'), Decimal('100'))
        def broken():
            session = Mock()
            session.post.side_effect = requests.ReadTimeout('simulated outage')
            self.sessions.append(session)
            return session
        self.api.session.post.side_effect = requests.ReadTimeout('simulated outage')
        with patch.object(kraken.requests, 'Session', side_effect=broken), patch.object(kraken.time, 'sleep') as sleep:
            with self.api.balance_snapshot(), self.assertRaises(kraken.KrakenAPIError):
                self.api.balance('EUR')
        self.assertEqual(sum(session.post.call_count for session in self.sessions), 4)  # One successful read, three failed reads.
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertIsNone(self.api._balance_cache)

    def test_order_and_withdrawal_timeouts_are_never_retried(self):
        for method in ('AddOrder', 'Withdraw'):
            with self.subTest(method=method):
                session = self.api.session
                session.post.side_effect = requests.ReadTimeout('simulated lost response')
                before = len(self.sessions)
                with patch.object(kraken.time, 'sleep') as sleep, self.assertRaises(kraken.KrakenAPIError):
                    self.api.private(method, {'amount': '.001'})
                self.assertEqual(session.post.call_count, 1)
                self.assertEqual(len(self.sessions), before + 1)
                sleep.assert_not_called()

    def test_connection_error_also_retries_on_fresh_session(self):
        failed = self.api.session
        failed.post.side_effect = requests.ConnectionError('simulated broken socket')
        with patch.object(kraken.time, 'sleep'):
            self.api.private('BalanceEx')
        self.assertEqual(len(self.sessions), 2)
        failed.close.assert_called_once()

    def test_definitive_api_error_is_not_retried(self):
        self.api.session.post.return_value = Mock()
        self.api.session.post.return_value.json.return_value = {'error': ['EAPI:Invalid key']}
        with patch.object(kraken.time, 'sleep') as sleep, self.assertRaises(DefinitiveRejection):
            self.api.private('BalanceEx')
        self.assertEqual(self.api.session.post.call_count, 1)
        sleep.assert_not_called()

    def test_one_balance_read_per_nested_cycle_and_fresh_data_next_cycle(self):
        with self.api.balance_snapshot():
            self.assertEqual(self.api.balance('EUR'), Decimal('100'))
            with self.api.balance_snapshot():
                self.assertEqual(self.api.balance('BTC'), Decimal('.001'))
            self.assertEqual(self.api.balance('EUR'), Decimal('100'))
        self.assertEqual(self.api.session.post.call_count, 1)
        self.api.session.post.return_value = self.response({'ZEUR': {'balance': '25'}})
        with self.api.balance_snapshot():
            self.assertEqual(self.api.balance('EUR'), Decimal('25'))
        self.assertEqual(self.api.session.post.call_count, 2)

    def test_slow_cycle_does_not_reuse_an_aged_balance(self):
        with self.api.balance_snapshot():
            with patch.object(kraken.time, 'monotonic', return_value=10):
                self.assertEqual(self.api.balance('EUR'), Decimal('100'))
            self.api.session.post.return_value = self.response({'ZEUR': {'balance': '25'}})
            with patch.object(kraken.time, 'monotonic', return_value=16):
                self.assertEqual(self.api.balance('EUR'), Decimal('25'))
        self.assertEqual(self.api.session.post.call_count, 2)

    def test_confirmation_balance_reads_outside_cycle_are_always_fresh(self):
        self.api.balance('BTC')
        self.api.balance('BTC')
        self.assertEqual(self.api.session.post.call_count, 2)

    def test_write_invalidates_balance_even_when_response_is_api_error(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                self.api.session.post.reset_mock()
                self.api.session.post.return_value = self.response({'ZEUR': {'balance': '100'}})
                with self.api.balance_snapshot():
                    self.assertEqual(self.api.balance('EUR'), Decimal('100'))
                    self.api.session.post.return_value = self.response({'txid': ['fictional-order']})
                    if failed:
                        self.api.session.post.return_value.json.return_value = {'error': ['EOrder:Insufficient funds']}
                        with self.assertRaises(DefinitiveRejection):
                            self.api.private('AddOrder')
                    else:
                        self.api.private('AddOrder')
                    self.api.session.post.return_value = self.response({'ZEUR': {'balance': '80'}})
                    self.assertEqual(self.api.balance('EUR'), Decimal('80'))
                self.assertEqual(self.api.session.post.call_count, 3)

    def test_dry_run_recalculation_shares_balance_only_within_cycle(self):
        self.api.market = Mock(return_value={'status': 'trading', 'minOrderInQuoteAsset': '5',
                                           'minOrderInBaseAsset': '.0001', 'quantityDecimals': 8, 'notionalDecimals': 2})
        self.api.price = Mock(return_value=Decimal('80000'))
        self.api.estimate_taker_fee = Mock(return_value=Decimal('.02'))
        settings = {'exchange': 'KRAKEN', 'quote': 'EUR', 'live': False, 'timezone': 'Europe/Berlin',
                    'deposit_day': 1, 'buy_amount': Decimal(0), 'withdrawals_enabled': False,
                    'withdraw_threshold': Decimal('1000'), 'first_purchase_done': False,
                    'first_buy_delay_seconds': 0, 'order_status_check_seconds': 30}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(activity, 'ACTIVITY_PATH', Path(directory) / 'activity.json'), \
                patch.object(activity, 'LEGACY_ACTIVITY_PATHS', ()), \
                patch.object(bot, 'read_state', return_value={}), patch.object(bot, 'save_state'), \
                patch.object(bot, 'update_dashboard_snapshot'), patch.object(bot, 'update_dashboard_schedule'):
            bot.run_once(self.api, settings, 300)
        self.assertEqual(self.api.session.post.call_count, 1)
        self.assertTrue(self.api.session.post.call_args.args[0].endswith('/BalanceEx'))
        self.assertIsNone(self.api._balance_cache)
        self.assertEqual(self.api._balance_scope_depth, 0)

    def test_available_funds_formula_is_unchanged(self):
        self.api.session.post.return_value = self.response({'ZEUR': {
            'balance': '100', 'credit': '20', 'credit_used': '5', 'hold_trade': '30'}})
        self.assertEqual(self.api.balance('EUR'), Decimal('85'))


if __name__ == '__main__':
    unittest.main()

