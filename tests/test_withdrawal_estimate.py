import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from bot import estimate_withdrawal

class WithdrawalEstimateTests(unittest.TestCase):
    def test_estimate_counts_existing_bitcoin_and_rounds_up_purchases(self):
        now=datetime(2026,10,29,tzinfo=timezone.utc)
        settings={"withdrawals_enabled":True,"live":True,"withdraw_threshold":D(100)}
        due=now+timedelta(minutes=10)
        at, status=estimate_withdrawal(settings,D(".0005"),D(100000),D(".0002"),10,due,3600,now)
        self.assertEqual(at,due+timedelta(hours=2))
        self.assertIn("3 purchase",status)
        self.assertIsNone(estimate_withdrawal(settings,D(".0005"),D(100000),D(".0002"),2,due,3600,now)[0])
        self.assertEqual(estimate_withdrawal(settings,D(".001"),D(100000),D(".0002"),2,due,3600,now)[0],now)
        for overrides in [{"live":False},{"withdrawals_enabled":False}]:
            self.assertIsNone(estimate_withdrawal({**settings,**overrides},D(0),D(100000),D(".0002"),10,due,3600,now)[0])
