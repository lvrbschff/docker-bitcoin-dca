"""Bitcoin-only trading, legacy-history isolation, and configuration regressions."""
import os
import time
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import activity
import bitvavo
import bot
import kraken
import reporting
import web_ui


class BitcoinOnlyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        for name, value in [('ACTIVITY_PATH', self.directory / 'activity.json'),
                            ('SNAPSHOT_PATH', self.directory / 'dashboard.json'),
                            ('LEGACY_ACTIVITY_PATHS', ()), ('LEGACY_SNAPSHOT_PATHS', ())]:
            replacement = patch.object(activity, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.settings = {'exchange': 'KRAKEN', 'quote': 'EUR', 'live': True,
                         'timezone': 'Europe/Berlin', 'deposit_day': 1,
                         'buy_amount': Decimal('0'), 'withdrawals_enabled': False,
                         'withdraw_threshold': Decimal('1000'), 'first_purchase_done': False,
                         'startup_delay_seconds': 0, 'order_status_check_seconds': 30}

    @staticmethod
    def record(identifier, kind='Buy', simulated=False):
        return {'id': identifier + ':' + kind + ':' + str(simulated), 'simulated': simulated,
                'netWithdrawalAmount': True, 'withdrawalStatus': 'completed', 'row': {
                    'identifier': identifier, 'assetType': 'Crypto', 'type': kind,
                    'datetime': '2026-10-20T12:00:00Z', 'shares': '.001',
                    'amount': '80', 'fee': '.2', 'price': '80000', 'currency': 'EUR'}}

    def test_web_ui_always_starts_using_the_saved_runtime_settings(self):
        with patch.dict(os.environ,{'WEB_UI_ENABLED':'false','EXCHANGE':'BITVAVO','LIVE_TRADING':'false'},clear=True), \
                patch('runtime.Runtime') as runtime_class, patch('web_ui.serve') as serve, \
                patch.object(bot,'prepare_storage'),patch.object(bot.threading,'Thread'):
            app=runtime_class.return_value;app.settings=self.settings
            bot.main()
        runtime_class.assert_called_once_with()
        app.worker.start.assert_called_once_with()
        serve.assert_called_once_with(self.settings,6890,app.worker.submit,worker=app.worker,runtime=app)


    def test_live_purchase_uses_bitcoin_interface_and_market(self):
        # Even a legacy caller's base setting cannot select another asset.
        self.settings['base'] = 'ETH'
        api = Mock()
        api.balance.return_value = Decimal('100')
        api.estimate_taker_fee.return_value = Decimal('.0025')
        api.market.return_value = {'status': 'trading', 'minOrderInQuoteAsset': '5',
                                   'minOrderInBaseAsset': '.0001', 'quantityDecimals': 8,
                                   'notionalDecimals': 2}
        api.price.return_value = Decimal('80000')
        api.buy_btc.return_value = {'orderId': 'btc-order'}
        with patch.object(bot, 'read_state', return_value={}), \
                patch.object(bot, 'save_state'), patch.object(bot, 'update_dashboard_schedule'), \
                patch.object(bot, 'update_dashboard_snapshot'):
            self.assertEqual(bot.run_once(api, self.settings, 300), 30)
        api.market.assert_called_once_with('BTC-EUR', refresh=True)
        api.price.assert_called_once_with('BTC-EUR')
        self.assertEqual(api.buy_btc.call_args.args[:2], ('EUR', Decimal('.0001')))
        api.buy_base.assert_not_called()

    def test_dry_run_records_only_bitcoin_without_submitting_orders(self):
        self.settings['live'] = False
        api = Mock()
        api.balance.return_value = Decimal('100')
        api.estimate_taker_fee.return_value = Decimal('.0025')
        api.market.return_value = {'status': 'trading', 'minOrderInQuoteAsset': '5',
                                   'minOrderInBaseAsset': '.0001', 'quantityDecimals': 8,
                                   'notionalDecimals': 2}
        api.price.return_value = Decimal('80000')
        api.estimate_taker_fee.return_value = Decimal('.0025')
        state = {}
        with patch.object(bot, 'read_state', return_value=state), patch.object(bot, 'save_state'):
            wait = bot.run_once(api, self.settings, 300)
        self.assertGreater(wait, 0)
        records = activity.read_activity_records()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]['simulated'])
        self.assertEqual(records[0]['row']['identifier'], 'BTC')
        api.buy_btc.assert_not_called()
        api.withdraw_btc.assert_not_called()
        api.estimate_taker_fee.assert_called_once_with('BTC-EUR', Decimal('1'))

    def test_pending_other_market_blocks_bitcoin_purchase(self):
        api = Mock()
        state = {'pending_order': {'symbol': 'ETH-EUR', 'exchange': 'KRAKEN'}}
        with patch.object(bot, 'read_state', return_value=state), \
                patch.object(bot, 'update_dashboard_schedule'):
            self.assertEqual(bot.run_once(api, self.settings, 300), 30)
        api.buy_btc.assert_not_called()
        api.get_order.assert_not_called()
        api.withdraw_btc.assert_not_called()

    def test_adapter_orders_are_bitcoin_only(self):
        adapter = object.__new__(bitvavo.Bitvavo)
        adapter.request = Mock(return_value={})
        adapter.buy_btc('eur', Decimal('.001'), 'ref')
        payload = adapter.request.call_args.kwargs['body']
        self.assertEqual(payload['market'], 'BTC-EUR')
        self.assertEqual(payload['side'], 'buy')
        adapter = object.__new__(kraken.Kraken)
        adapter._pairs = {
            'ETHPAIR': {'base': 'XETH', 'quote': 'ZEUR', 'altname': 'ETHEUR'},
            'XXBTZEUR': {'base': 'XXBT', 'quote': 'ZEUR', 'altname': 'XBTEUR'}}
        adapter._pairs_at=time.monotonic()
        adapter.private = Mock(return_value={'txid': ['btc-order']})
        adapter.buy_btc('eur', Decimal('.001'), '00000000-0000-0000-0000-000000000001')
        self.assertEqual(adapter.private.call_args.args[0], 'AddOrder')
        self.assertEqual(adapter.private.call_args.args[1]['pair'], 'XBTEUR')
        with self.assertRaises(ValueError):
            adapter._pair_info('ETH-EUR')

    def test_adapter_withdrawals_reject_other_assets_without_submission(self):
        adapter = object.__new__(bitvavo.Bitvavo)
        adapter.request = Mock(return_value={})
        with self.assertRaises(ValueError):
            adapter.withdraw_btc({'symbol': 'ETH'})
        adapter.request.assert_not_called()
        adapter.withdraw_btc({'symbol': 'BTC', 'amount': '.001'})
        self.assertEqual(adapter.request.call_count, 1)
        adapter = object.__new__(kraken.Kraken)
        adapter.private = Mock(return_value={'refid': 'btc-withdrawal'})
        with self.assertRaises(ValueError):
            adapter.withdraw_btc({'asset': 'ETH'})
        adapter.private.assert_not_called()
        adapter.withdraw_btc({'asset': 'XBT', 'amount': '.001', 'key': 'wallet'})
        self.assertEqual(adapter.private.call_args.args[1]['asset'], 'XBT')

    def test_saved_non_bitcoin_withdrawal_is_not_retried_or_recorded(self):
        api = Mock()
        state = {'pending_withdrawal': {'asset': 'ETH'},
                 'pending_withdrawal_context': {'result': {'id': 'old-withdrawal'}}}
        with self.assertRaises(ValueError):
            bot.maybe_withdraw_btc(api, state, self.settings)
        api.withdraw_btc.assert_not_called()
        self.assertEqual(activity.read_activity_records(), [])

    def test_normalized_purchases_always_record_bitcoin(self):
        adapter = object.__new__(bitvavo.Bitvavo)
        rows = adapter.normalize_bot_buy({'filledAmount': '.001', 'filledAmountQuote': '80',
                                         'feePaid': '.000001', 'feeCurrency': 'BTC'},
                                        'EUR', 'test', 1)
        self.assertEqual(rows[0]['row']['identifier'], 'BTC')
        self.assertEqual(Decimal(rows[0]['row']['fee']), Decimal('.08'))
        adapter = object.__new__(kraken.Kraken)
        rows = adapter.normalize_bot_buy({'vol_exec': '.001', 'cost': '80', 'fee': '.2'},
                                        'EUR', 'test', 1)
        self.assertEqual(rows[0]['row']['identifier'], 'BTC')

    def test_legacy_history_is_preserved_but_not_counted(self):
        bitcoin, ether = self.record('BTC'), self.record('ETH')
        ether['row']['datetime'] = '2026-10-25T12:00:00Z'
        activity.append_activity_records([bitcoin, ether])
        self.assertEqual(activity.read_bitcoin_activity_records(), [bitcoin])
        self.assertEqual(bot.latest_recorded_buy_time(), datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
        bot.update_dashboard_snapshot(self.settings, Decimal('10'), Decimal('80000'))
        self.assertEqual(Decimal(activity.read_dashboard_snapshot()['trackedBtc']), Decimal('.001'))
        activity.append_activity_records([self.record('BTC', simulated=True)])
        self.assertIn(ether, activity.read_activity_records())

    def test_exports_and_analytics_exclude_other_assets(self):
        records = [self.record('BTC'), self.record('BTC', 'TransferOut'),
                   self.record('ETH'), self.record('ETH', 'TransferOut'),
                   self.record('BTC', simulated=True)]
        result = reporting.parqet_aggregate_rows(records, 'KRAKEN')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['identifier'], 'BTC')
        self.assertEqual(Decimal(result[0]['amount']), Decimal('80.2'))
        self.assertEqual(Decimal(result[0]['shares']), Decimal('.001'))
        self.assertEqual(result[0]['assetType'], 'Crypto')  # Required by Parqet's format.
        self.assertEqual(reporting.parqet_aggregate_rows([self.record('ETH'), self.record('ETH', 'TransferOut')], 'KRAKEN'), [])
        analytics = web_ui._analytics_from_rows(self.settings, records, {})
        self.assertEqual(analytics['summary']['buyCount'], 1)
        self.assertEqual(analytics['summary']['withdrawalCount'], 1)
        self.assertEqual(len(analytics['activities']), 3)
        self.assertNotIn('baseAsset', analytics['configuration'])

    def test_http_report_replaces_stale_mixed_asset_balance(self):
        activity.append_activity_records([self.record('BTC'), self.record('ETH')])
        activity.write_dashboard_snapshot({'trackedBtc': '10', 'price': '80000', 'value': '800000'})
        handler = object.__new__(web_ui.Handler)
        handler.settings = self.settings
        handler.path = '/api/analytics?start=0&end=' + str(int(datetime.now(timezone.utc).timestamp() * 1000))
        handler._has_valid_session = lambda: True
        handler._require_authentication = lambda: True
        handler._json = Mock()
        handler.do_GET()
        status, result = handler._json.call_args.args
        self.assertEqual(status, 200)
        self.assertEqual(Decimal(result['snapshot']['balance']), Decimal('.001'))
        self.assertEqual(Decimal(result['snapshot']['value']), Decimal('80'))


if __name__ == '__main__':
    unittest.main()

