"""The dashboard reports quote funds only from the configured exchange and currency."""
import unittest
from unittest.mock import Mock, patch
import web_ui


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


if __name__=='__main__':unittest.main()
