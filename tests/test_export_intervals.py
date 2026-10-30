"""Exports always cover one complete confirmed withdrawal cycle."""
import csv
import io
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import activity
import reporting
import web_ui


def record(name, day, kind='Buy', status='completed', amount='80', simulated=False):
    result={'id':name,'exchange':'KRAKEN','simulated':simulated,'row':{
        'datetime':f'2026-10-{day:02d}T12:00:00Z','type':kind,'identifier':'BTC',
        'assetType':'Crypto','price':'80000','shares':'.001','amount':amount,
        'fee':'.20','currency':'EUR','tax':'0'}}
    if kind=='TransferOut':result.update(withdrawalStatus=status,netWithdrawalAmount=True)
    return result


def stamp(day):return int(datetime.fromisoformat(f'2026-10-{day:02d}T12:00:00+00:00').timestamp()*1000)


class ExportIntervals(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        for name,value in [('ACTIVITY_PATH',Path(temp.name)/'activity.json'),('LEGACY_ACTIVITY_PATHS',())]:
            p=patch.object(activity,name,value);p.start();self.addCleanup(p.stop)
        self.records=[record('old-buy',1),record('previous',5,'TransferOut'),
                      record('buy-one',6),record('buy-two',8,amount='160'),
                      record('last',10,'TransferOut'),record('later-buy',11,amount='1000')]
        activity.append_activity_records(self.records)

    def export(self,start=1,end=12):
        return reporting.parqet_aggregate_rows(activity.parqet_interval_records(stamp(start),stamp(end)),'KRAKEN')

    def test_only_latest_cycle_even_when_selection_contains_multiple_withdrawals(self):
        row=self.export()[0]
        self.assertEqual(Decimal(row['amount']),Decimal('240.40'))
        self.assertEqual(row['datetime'],'2026-10-10T12:00:00Z')
        self.assertEqual(Decimal(row['shares']),Decimal('.001'))
        self.assertEqual(row['fee'],'0')

    def test_start_inside_cycle_restores_earlier_purchases_after_previous_withdrawal(self):
        self.assertEqual(self.export(9),self.export())

    def test_first_withdrawal_uses_all_prior_real_purchases(self):
        row=self.export(4,5)[0]
        self.assertEqual(Decimal(row['amount']),Decimal('80.20'))
        self.assertEqual(row['datetime'],'2026-10-05T12:00:00Z')

    def test_no_withdrawal_in_selected_range_means_no_export(self):
        self.assertEqual(self.export(10,12),[])

    def test_upper_bound_does_not_select_later_withdrawal(self):
        activity.append_activity_records([record('future',13,'TransferOut')])
        self.assertEqual(self.export()[0]['datetime'],'2026-10-10T12:00:00Z')

    def test_failed_pending_simulated_and_non_btc_do_not_split_cycle(self):
        other=record('eth',9,'TransferOut');other['row']['identifier']='ETH'
        activity.append_activity_records([record('failed',7,'TransferOut',status='failed'),
            record('pending',9,'TransferOut',status='submitted'),
            record('simulated',9,'TransferOut',simulated=True),other])
        self.assertEqual(Decimal(self.export()[0]['amount']),Decimal('240.40'))

    def test_purchases_at_previous_boundary_excluded_and_at_last_boundary_included(self):
        activity.append_activity_records([record('at-previous',5,amount='1000'),record('at-last',10)])
        self.assertEqual(Decimal(self.export()[0]['amount']),Decimal('320.60'))

    def test_aggregation_itself_excludes_previous_cycles_and_later_purchases(self):
        row=reporting.parqet_aggregate_rows(self.records,'KRAKEN')[0]
        self.assertEqual(Decimal(row['amount']),Decimal('240.40'))

    def test_exchange_switch_does_not_mix_withdrawal_cycles(self):
        buy=record('bitvavo-buy',7,amount='9999');buy['exchange']='BITVAVO'
        withdrawal=record('bitvavo-withdraw',9,'TransferOut');withdrawal['exchange']='BITVAVO'
        activity.append_activity_records([buy,withdrawal])
        rows=activity.parqet_interval_records(stamp(1),stamp(12))
        self.assertEqual({r['exchange'] for r in rows},{'KRAKEN'})
        self.assertEqual(Decimal(reporting.parqet_aggregate_rows(rows,'BITVAVO')[0]['amount']),Decimal('240.40'))
        all_records=activity.read_activity_records()
        self.assertEqual(Decimal(reporting.parqet_aggregate_rows(all_records,'KRAKEN')[0]['amount']),Decimal('240.40'))
        self.assertEqual(activity.latest_real_buy('KRAKEN').day,11)
        self.assertEqual(activity.latest_real_buy('BITVAVO').day,7)


    def test_http_export_uses_complete_cycle_instead_of_selected_activity_range(self):
        handler=object.__new__(web_ui.Handler)
        handler.path=f'/api/export.csv?start={stamp(9)}&end={stamp(12)}'
        handler.settings={'exchange':'KRAKEN'}
        handler._require_authentication=lambda:True
        sent=[];handler._send=lambda *args:sent.append(args)
        handler._json=lambda *args:self.fail(str(args))
        handler.do_GET()
        self.assertEqual(sent[0][0],200)
        row=list(csv.DictReader(io.StringIO(sent[0][1]),delimiter=';'))[0]
        self.assertEqual(Decimal(row['amount']),Decimal('240.40'))


if __name__=='__main__':unittest.main()
