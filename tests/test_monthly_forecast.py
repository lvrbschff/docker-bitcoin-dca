"""Monthly withdrawal forecasts use real balances and never place orders."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal as D
from web_ui import withdrawal_forecast

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
