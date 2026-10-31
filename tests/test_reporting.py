"""Offline regression coverage grouped by reporting and forecasts."""
from activity_helpers import totals_from_records
from unittest import TestCase
from unittest.mock import Mock, patch
from app.core.configuration import DEFAULTS, runtime_settings
from app.data import analytics
from app.trading import bot
import unittest
from datetime import datetime, timezone
from decimal import Decimal as D
from app.webpage.web_ui import withdrawal_forecast
from decimal import Decimal
from app.data.analytics import report
from app.webpage import web_ui


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

    def test_failed_foreign_currency_withdrawal_does_not_hide_euro_totals(self):
        failed=self.record('failed','TransferOut',withdrawalStatus='failed')
        failed['row']['currency']='USD'
        records=[self.record('buy'),failed]
        result=analytics.report(runtime_settings(DEFAULTS),totals_from_records(records),{},records)
        self.assertEqual(result['summary']['buyVolume'],'80')
        self.assertEqual(set(result['summaryByCurrency']),{'EUR'})

    def test_new_buy_record_persists_confirmation_after_fill_normalization(self):
        api=Mock();record=self.record('fill');api.normalize_bot_buy.return_value=[record]
        with patch.object(bot,'append_activity_records',return_value=1) as append:
            records,count=bot.persist_bot_buy(api,{'filledAmount':'.001'},runtime_settings(DEFAULTS),'fill',0)
        self.assertEqual(records[0]['purchaseStatus'],'confirmed')
        self.assertEqual(count,1)
        append.assert_called_once_with(records)


class MonthlyForecastTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,29,tzinfo=timezone.utc)
        self.settings={"live":True,"withdrawals_enabled":True,"withdraw_threshold":D(500),"deposit_day":1,"timezone":"UTC"}
        self.snapshot={"btcBalance":".001","price":"100000","nextPurchaseAt":"2026-10-30T00:00:00+00:00",
            "withdrawalForecastPlan":{"orderAmount":".001","plannedCost":"101","budget":"202","intervalSeconds":86400,
            "nextDepositAt":"2026-11-01T00:00:00+00:00"}}
    def test_monthly_funding_extends_past_current_budget(self):
        target,reason=withdrawal_forecast(self.settings,self.snapshot,"202",self.now)
        self.assertEqual(datetime.fromisoformat(target),datetime(2026,12,1,tzinfo=timezone.utc))
        self.assertIn("monthly",reason)
    def test_no_assumed_funding_means_no_date_beyond_budget(self):
        self.assertIsNone(withdrawal_forecast(self.settings,self.snapshot,"0",self.now)[0])
    def test_existing_balance_and_funded_purchases(self):
        self.settings["withdraw_threshold"]=D(300)
        target,_=withdrawal_forecast(self.settings,self.snapshot,"0",self.now)
        self.assertEqual(datetime.fromisoformat(target),datetime(2026,10,31,tzinfo=timezone.utc))
        self.settings["withdraw_threshold"]=D(100)
        self.assertEqual(withdrawal_forecast(self.settings,self.snapshot,"0",self.now)[0],self.now.isoformat())
    def test_disabled_and_invalid_inputs(self):
        for amount in ["-1","NaN","Infinity","invalid"]:
            self.assertIsNone(withdrawal_forecast(self.settings,self.snapshot,amount,self.now)[0])
        self.settings["withdrawals_enabled"]=False
        self.assertEqual(withdrawal_forecast(self.settings,self.snapshot,"202",self.now)[1],"Automatic withdrawals disabled")


class PurchaseAmountSnapshotTests(unittest.TestCase):
    def test_report_preserves_exchange_identity_and_calculated_purchase_amount(self):
        settings=dict(quote="EUR",exchange="KRAKEN",live=True,buy_amount=Decimal(0),
                      deposit_day=1,withdrawals_enabled=False,withdraw_threshold=Decimal(1000))
        snapshot={"exchange":"KRAKEN","quote":"EUR","price":"84000",
                  "withdrawalForecastPlan":{"orderAmount":"0.00006"}}
        result=report(settings,totals_from_records([]),snapshot,[])
        self.assertEqual(result["snapshot"]["exchange"],"KRAKEN")
        self.assertEqual(result["snapshot"]["quote"],"EUR")
        self.assertEqual(result["snapshot"]["withdrawalForecastPlan"]["orderAmount"],"0.00006")



class ScheduleBalanceTests(unittest.TestCase):
    def response(self,snapshot):
        handler=object.__new__(web_ui.Handler)
        handler.path='/api/schedule'
        handler.settings={'quote':'EUR','exchange':'KRAKEN','timezone':'Europe/Berlin'}
        handler._require_authentication=lambda:True
        handler._json=Mock()
        with patch.object(web_ui,'read_dashboard_snapshot',return_value=snapshot):handler.do_GET()
        status,body=handler._json.call_args.args
        self.assertEqual(status,200)
        return body

    def test_matching_balance_is_returned_with_original_currency_and_timestamp(self):
        data=self.response({'quote':'EUR','exchange':'KRAKEN','quoteBalance':'72.7719','updatedAt':'2026-10-29T12:00:00Z'})
        self.assertEqual(data['quoteBalance'],'72.7719')
        self.assertEqual(data['quote'],'EUR');self.assertEqual(data['exchange'],'KRAKEN')
        self.assertEqual(data['balanceUpdatedAt'],'2026-10-29T12:00:00Z')

    def test_other_currency_exchange_or_missing_snapshot_is_not_shown_as_current_balance(self):
        for snapshot in ({},{'quote':'USD','exchange':'KRAKEN','quoteBalance':'100'},
                         {'quote':'EUR','exchange':'BITVAVO','quoteBalance':'100'}):
            with self.subTest(snapshot=snapshot):
                data=self.response(snapshot)
                self.assertIsNone(data['quoteBalance']);self.assertIsNone(data['balanceUpdatedAt'])

    def test_zero_is_a_valid_balance(self):
        data=self.response({'quote':'EUR','exchange':'KRAKEN','quoteBalance':'0'})
        self.assertEqual(data['quoteBalance'],'0')
