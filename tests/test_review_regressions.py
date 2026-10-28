"""Review regressions with synthetic data; all exchange IO is mocked."""
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock, patch
import activity, analytics, bot, reporting, web_ui, withdrawals, price_cache, worker, healthcheck
from exchange_errors import DefinitiveRejection


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name,value in [('ACTIVITY_PATH',self.root/'activity.json'),('SNAPSHOT_PATH',self.root/'dashboard.json'),('LEGACY_ACTIVITY_PATHS',()),('LEGACY_SNAPSHOT_PATHS',())]:
            p=patch.object(activity,name,value);p.start();self.addCleanup(p.stop)
        self.settings={'exchange':'KRAKEN','quote':'EUR','live':True,'timezone':'Europe/Berlin','deposit_day':1,
                       'buy_amount':D(0),'withdrawals_enabled':False,'withdraw_threshold':D(1000),
                       'first_purchase_done':False,'startup_delay_seconds':0,'order_status_check_seconds':30}
        price_cache._values.clear()

    def record(self, id='buy', kind='Buy', currency='EUR', status=None, when='2026-10-20T12:00:00Z'):
        r={'id':id,'exchange':'KRAKEN','row':{'datetime':when,'type':kind,'identifier':'BTC','assetType':'Crypto',
                                          'shares':'.0045','amount':'359','fee':'.8975','price':'80000','currency':currency,'tax':'0'}}
        if kind=='TransferOut':
            r.update(netWithdrawalAmount=True,networkFeeBtc='.0001',grossWithdrawalAmountBtc='.0045')
            r['row'].update(shares='.0044',amount='352',fee='8')
            if status:r['withdrawalStatus']=status
        return r

    def test_json_migration_is_once_and_preserves_source_and_metadata(self):
        r=self.record('old','TransferOut');r['manual']=True
        text=json.dumps({'records':[r]});activity.ACTIVITY_PATH.write_text(text)
        got=activity.read_activity_records()[0]
        self.assertEqual(got['id'],'old');self.assertEqual(got['row'],r['row']);self.assertTrue(got['manual'])
        self.assertEqual(got['networkFeeBtc'],'.0001');self.assertEqual(got['withdrawalStatus'],'legacy_unverified')
        self.assertEqual(activity.ACTIVITY_PATH.read_text(),text)
        self.assertEqual(activity.append_activity_records([r]),0)
        activity.ACTIVITY_PATH.write_text('not valid JSON any longer')
        self.assertEqual(len(activity.read_activity_records()),1)

    def test_net_wallet_amount_and_gross_balance_have_distinct_meanings(self):
        buys=self.record();out=self.record('withdraw','TransferOut',status='completed')
        activity.append_activity_records([buys,out])
        self.assertEqual(activity.ledger_balance(),D(0))
        self.assertEqual(activity.tracked_bitcoin_balance([buys,out]),D(0))
        row=reporting.parqet_aggregate_rows([buys,out],'KRAKEN')[0]
        self.assertEqual(D(row['shares']),D('.0044'))
        out['withdrawalStatus']='failed';activity.update_activity_record(out)
        self.assertEqual(activity.ledger_balance(),D('.0045'))

    def test_only_confirmed_withdrawals_can_be_exported(self):
        for status in (None,'legacy_unverified','submitted','failed','canceled'):
            self.assertEqual(reporting.parqet_aggregate_rows([self.record(),self.record('w','TransferOut',status=status)],'KRAKEN'),[])

    def test_status_lookup_does_not_treat_settled_as_completed(self):
        record=self.record('withdrawal:ref','TransferOut',status='submitted');record['withdrawalId']='ref'
        activity.append_activity_records([record])
        api=Mock();api.btc_withdrawals.return_value=[{'id':'ref','status':'settled','amount':'.0044','fee':'.0001'}]
        withdrawals.reconcile_withdrawals(api,self.settings,force=True)
        self.assertEqual(activity.get_activity_record(record['id'])['withdrawalStatus'],'submitted')
        api.btc_withdrawals.return_value=[{'id':'ref','status':'completed','amount':'.0044','fee':'.0001'}]
        withdrawals.reconcile_withdrawals(api,self.settings,force=True)
        self.assertEqual(activity.get_activity_record(record['id'])['withdrawalStatus'],'completed')

    def test_ambiguous_history_does_not_confirm_or_duplicate_withdrawals(self):
        record=self.record('withdrawal:ref','TransferOut',status='submitted');record['withdrawalId']='ref'
        activity.append_activity_records([record])
        api=Mock();api.btc_withdrawals.return_value=[{'id':'ref','status':'completed'}]*2
        withdrawals.reconcile_withdrawals(api,self.settings,force=True)
        self.assertEqual(activity.get_activity_record(record['id'])['withdrawalStatus'],'submitted')
        api.withdraw_btc.assert_not_called()

    def test_pagination_transfers_only_requested_rows_and_keeps_exact_totals(self):
        records=[self.record(str(i)) for i in range(105)]
        activity.append_activity_records(records)
        page,total,n=activity.activity_page(0,2**62,3,20)
        self.assertEqual((len(page),total,n),(20,105,3))
        groups=activity.activity_totals()
        self.assertEqual(D(groups[0]['amount']),D(359)*105)
        self.assertEqual(D(groups[0]['fee']),D('.8975')*105)
        summary=analytics.report(self.settings,groups,{},page)
        self.assertEqual(summary['summary']['buyCount'],105)
        self.assertEqual(len(summary['activities']),20)
        page,total,n=activity.activity_page(0,2**62,100,20)
        self.assertEqual((len(page),n),(5,6))

    def test_mixed_currency_totals_are_separate(self):
        a=self.record('eur');b=self.record('usd',currency='USD')
        activity.append_activity_records([a,b])
        result=analytics.report(self.settings,activity.activity_totals(),{},[a,b])
        self.assertIsNone(result['summary']['buyVolume'])
        self.assertEqual(result['summaryByCurrency']['EUR']['buyVolume'],'359')
        self.assertEqual(result['summaryByCurrency']['USD']['buyVolume'],'359')

    def test_eur_only_price_does_not_depend_on_usd(self):
        with patch.object(web_ui,'_kraken_public_btc_price',return_value=D(80000)) as fetch:
            result=web_ui._public_market_price('EUR','EUR')
            again=web_ui._public_market_price('EUR','EUR')
        fetch.assert_called_once_with('EUR')
        self.assertEqual(result['conversionRate'],'1');self.assertFalse(again['stale'])

    def test_stale_price_is_marked_and_expires(self):
        with patch.object(price_cache.time,'time',return_value=100),patch.object(web_ui,'_kraken_public_btc_price',return_value=D(80000)):
            web_ui._public_market_price('EUR','EUR')
        with patch.object(price_cache.time,'time',return_value=140),patch.object(web_ui,'_kraken_public_btc_price',side_effect=RuntimeError('offline')):
            self.assertTrue(web_ui._public_market_price('EUR','EUR')['stale'])
        with patch.object(price_cache.time,'time',return_value=500),patch.object(web_ui,'_kraken_public_btc_price',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):web_ui._public_market_price('EUR','EUR')

    def test_historical_rates_are_cached_once_per_trade_day(self):
        record=self.record(currency='USD',when='2026-10-25T12:00:00Z')
        response=Mock();response.json.return_value={'rates':{'2026-10-23':{'EUR':'.9'}}}
        with patch.object(reporting.requests,'get',return_value=response) as fetch:
            first=reporting._usd_eur_rates([record['row']]*100)
            second=reporting._usd_eur_rates([record['row']]*100)
        self.assertEqual(fetch.call_count,1);self.assertEqual(first,second)
        self.assertEqual(reporting._purchase_cost_eur(record['row'],first),D('359.8975')*D('.9'))

    def cycle_api(self):
        api=Mock();api.balance.return_value=D(100);api.price.return_value=D(80000)
        api.market.return_value={'status':'trading','minOrderInQuoteAsset':'5','minOrderInBaseAsset':'.0001','quantityDecimals':8,'notionalDecimals':2}
        api.estimate_taker_fee.return_value=D('.0025')
        return api

    def test_definitive_rejection_clears_pending_but_timeout_keeps_it(self):
        for exception, retained in [(DefinitiveRejection('Rejected: insufficient funds'),False),(__import__('requests').ReadTimeout('unknown'),True)]:
            state={};api=self.cycle_api();api.buy_btc.side_effect=exception
            with patch.object(bot,'read_state',return_value=state),patch.object(bot,'save_state'),patch.object(bot,'update_dashboard_snapshot'),patch.object(bot,'update_dashboard_schedule'):
                bot.run_once(api,dict(self.settings),300)
            self.assertEqual(bool(state.get('pending_order')),retained)
            self.assertEqual(api.buy_btc.call_count,1)

    def test_dry_run_debits_fees_and_reads_market_once(self):
        state={};api=self.cycle_api()
        with patch.object(bot,'read_state',return_value=state),patch.object(bot,'save_state'),patch.object(bot,'update_dashboard_snapshot'),patch.object(bot,'update_dashboard_schedule'):
            bot.run_once(api,{**self.settings,'live':False},300)
        self.assertEqual(D(state['spent']),D('8.02'))
        self.assertEqual(api.balance.call_count,1);self.assertEqual(api.market.call_count,1);self.assertEqual(api.price.call_count,1)

    def test_periodic_checks_do_not_move_due_time_when_inputs_are_unchanged(self):
        now=datetime(2026,10,28,12,tzinfo=timezone.utc)
        state={'last_order_at':(now-timedelta(hours=1)).isoformat()}
        settings={**self.settings,'first_purchase_done':True}
        api=self.cycle_api()
        with patch.object(bot,'read_state',return_value=state),patch.object(bot,'save_state'),patch.object(bot,'update_dashboard_snapshot'),patch.object(bot,'update_dashboard_schedule'):
            with patch.object(bot,'local_now',return_value=now):bot.run_once(api,settings,300)
            first=state['schedule']['dueAt']
            with patch.object(bot,'local_now',return_value=now+timedelta(minutes=5)):bot.run_once(api,settings,300)
        self.assertEqual(state['schedule']['dueAt'],first)
        api.buy_btc.assert_not_called()

    def test_confirmed_purchase_replans_even_if_balance_has_not_updated(self):
        now=datetime(2026,10,28,12,tzinfo=timezone.utc)
        state={};api=self.cycle_api()
        with patch.object(bot,'read_state',return_value=state),patch.object(bot,'save_state'),patch.object(bot,'update_dashboard_snapshot'),patch.object(bot,'update_dashboard_schedule'):
            with patch.object(bot,'local_now',return_value=now):
                bot.run_once(api,self.settings,300,schedule_only=True)
                state['last_order_at']=now.isoformat()
                bot.run_once(api,self.settings,300)
        self.assertGreater(datetime.fromisoformat(state['schedule']['dueAt']),now)
        api.buy_btc.assert_not_called()

    def test_restart_logs_purchase_anchor_elapsed_due_and_wait_once(self):
        now=datetime(2026,10,28,12,tzinfo=timezone.utc)
        last=now-timedelta(hours=1)
        settings={**self.settings,'startup_last_buy_at':last,'startup_schedule_log_pending':True}
        state={};api=self.cycle_api()
        with patch.object(bot,'read_state',return_value=state),patch.object(bot,'save_state'),patch.object(bot,'update_dashboard_snapshot'),patch.object(bot,'update_dashboard_schedule'),patch.object(bot,'local_now',return_value=now):
            with self.assertLogs(bot.LOG,level='INFO') as logs:
                bot.run_once(api,settings,300)
                bot.run_once(api,settings,300)
        lines=[line for line in logs.output if 'Restart alignment:' in line]
        self.assertEqual(len(lines),1)
        self.assertIn('last purchase=2026-10-28T12:00:00+01:00',lines[0])
        self.assertIn('elapsed since purchase=60.0 minutes',lines[0])
        self.assertIn('calculated purchase interval=',lines[0])
        self.assertIn('next purchase=',lines[0]);self.assertIn('remaining wait=',lines[0])
        api.buy_btc.assert_not_called()

    def test_startup_logs_history_and_delay(self):
        last=datetime(2026,10,28,11,tzinfo=timezone.utc)
        with patch.object(bot,'latest_recorded_buy_time',return_value=last),patch.object(bot,'update_dashboard_schedule'):
            with self.assertLogs(bot.LOG,level='INFO') as logs:
                account=bot.make_worker(self.cycle_api(),self.settings,300)
                account.startup()
        self.assertTrue(any('Startup: waiting 0s' in line for line in logs.output))
        self.assertTrue(any('latest recorded real Bitcoin purchase=2026-10-28T12:00:00+01:00' in line for line in logs.output))
        self.assertTrue(self.settings['startup_schedule_log_pending'])

    def test_each_periodic_check_logs_actual_due_time_and_remaining_minutes(self):
        now=datetime(2026,10,28,12,tzinfo=timezone.utc)
        state={'last_order_at':(now-timedelta(hours=1)).isoformat()}
        settings={**self.settings,'first_purchase_done':True}
        api=self.cycle_api()
        with patch.object(bot,'read_state',return_value=state),patch.object(bot,'save_state'),patch.object(bot,'update_dashboard_snapshot'),patch.object(bot,'update_dashboard_schedule'):
            with self.assertLogs(bot.LOG,level='INFO') as logs:
                with patch.object(bot,'local_now',return_value=now):bot.run_once(api,settings,300)
                first=state['schedule']['dueAt']
                with patch.object(bot,'local_now',return_value=now+timedelta(minutes=5)):bot.run_once(api,settings,300)
        lines=[line for line in logs.output if 'Purchase schedule:' in line]
        self.assertEqual(len(lines),2)
        self.assertIn('elapsed since purchase=60.0 minutes',lines[0])
        self.assertIn('elapsed since purchase=65.0 minutes',lines[1])
        self.assertIn('remaining wait=',lines[1])
        self.assertIn('saved due time retained for unchanged inputs',lines[1])
        due=datetime.fromisoformat(first).astimezone(bot.ZoneInfo('Europe/Berlin')).isoformat(timespec='seconds')
        self.assertTrue(all('next purchase='+due in line for line in lines))
        api.buy_btc.assert_not_called()

    def test_worker_enqueues_without_waiting_for_slow_account_cycle(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def cycle():entered.set();release.wait(2);return 100
        def action(*args):finished.set();return {'token':'preview'}
        account=worker.Worker(cycle,action,lambda:None,100,0)
        with patch.object(worker,'HEALTH_PATH',self.root/'health.json'):
            account.start();self.assertTrue(entered.wait(1))
            begin=time.monotonic();job=account.submit('preview','owner')
            self.assertLess(time.monotonic()-begin,.2)
            with self.assertRaises(ValueError):account.get_job(job['jobId'],'different-session')
            self.assertEqual(account.get_job(job['jobId'],'owner')['state'],'queued')
            release.set();self.assertTrue(finished.wait(2))
            account.stop.set();account.thread.join(2)
            self.assertFalse(account.thread.is_alive())
            self.assertFalse(healthcheck.healthy(self.root/'health.json'))

    def test_worker_retries_failed_startup_instead_of_silently_stopping(self):
        succeeded=threading.Event();calls=[]
        def startup():
            calls.append(1)
            if len(calls)==1:raise ValueError('corrupt input')
        def cycle():succeeded.set();return 100
        account=worker.Worker(cycle,Mock(),startup,.01,0)
        with patch.object(worker,'HEALTH_PATH',self.root/'health.json'):
            account.start();self.assertTrue(succeeded.wait(2));self.assertEqual(len(calls),2)
            account.stop.set();account.thread.join(2)


if __name__=='__main__':unittest.main()
