"""Only executed buys and exchange-confirmed withdrawals receive confirmation labels."""
from activity_helpers import totals_from_records
from unittest import TestCase
from unittest.mock import Mock, patch
from configuration import DEFAULTS, runtime_settings
import analytics
import bot


class ActivityStatusTests(TestCase):
    def record(self, name, kind='Buy', **extra):
        return dict(id=name,exchange='KRAKEN',row=dict(type=kind,identifier='BTC',shares='.001',
                    amount='80',price='80000',fee='.2',currency='EUR',datetime='2026-10-30T10:00:00Z'),**extra)

    def test_report_distinguishes_executions_simulations_and_withdrawal_states(self):
        records=[self.record('buy'),self.record('simulation',simulated=True),
                 self.record('completed','TransferOut',withdrawalStatus='completed'),
                 self.record('pending','TransferOut',withdrawalStatus='submitted'),
                 self.record('failed','TransferOut',withdrawalStatus='failed'),
                 self.record('legacy','TransferOut',withdrawalStatus='legacy_unverified')]
        settings=runtime_settings(DEFAULTS);settings['display_currency']='USD'
        report=analytics.report(settings,totals_from_records(records),{},records)
        self.assertEqual(report['configuration']['displayCurrency'],'USD')
        rows={r['activityId']:r for r in report['activities']}
        self.assertEqual(rows['buy']['purchaseStatus'],'confirmed')
        self.assertIsNone(rows['simulation']['purchaseStatus'])
        self.assertEqual(rows['pending']['withdrawalStatus'],'submitted')
        self.assertEqual(rows['failed']['withdrawalStatus'],'failed')
        self.assertEqual(rows['legacy']['withdrawalStatus'],'legacy_unverified')

    def test_new_buy_record_persists_confirmation_after_fill_normalization(self):
        api=Mock();record=self.record('fill');api.normalize_bot_buy.return_value=[record]
        with patch.object(bot,'append_activity_records',return_value=1) as append:
            records,count=bot.persist_bot_buy(api,{'filledAmount':'.001'},runtime_settings(DEFAULTS),'fill',0)
        self.assertEqual(records[0]['purchaseStatus'],'confirmed')
        self.assertEqual(count,1)
        append.assert_called_once_with(records)
