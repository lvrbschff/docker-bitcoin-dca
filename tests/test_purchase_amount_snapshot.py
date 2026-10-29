"""Account-check purchase quantities reach both dashboard endpoints."""
import unittest
from decimal import Decimal
from analytics import report, totals_from_records

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
