import sys, types, unittest, tempfile, json, io
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import patch
import requests
import bot, activity, web_ui, reporting

class API:
    supports_withdrawal_idempotency=False
    def __init__(self, exchange): self.exchange=exchange;self.sent=[];self.available=D('.0045');self.fail=False
    def validate_withdrawal_settings(self,s):
        if not s['kraken_withdraw_key'] and self.exchange=='KRAKEN': raise ValueError('Missing destination')
    def balance(self,a): return self.available
    def price(self,s): return D('81000')
    def withdrawal_request(self,amount,key,s):
        if self.exchange=='KRAKEN': return {'asset':'XBT','amount':str(amount-D('.0001')),'fee':'.0001','key':'wallet'}
        return {'symbol':'BTC','amount':str(amount),'idempotencyKey':key,'address':'bc1test'}
    def btc_withdrawals(self,start,end):
        if self.exchange=='KRAKEN':return [{'id':'test-1','status':'completed','amount':'.0044','fee':'.0001'}]
        return [{'address':'bc1test','timestamp':self.timestamp,'status':'completed','amount':'.0044','fee':'.0001'}]
    def withdraw_btc(self,r):
        self.sent.append(r)
        if self.fail: raise requests.RequestException('timeout')
        import time
        self.timestamp=int(time.time()*1000)
        return {'id':'test-1','fee':'.0001'}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state={}
        self.patches=[patch.object(bot,'read_state',lambda *a:self.state),patch.object(bot,'save_state',lambda *a:None),patch.object(activity,'ACTIVITY_PATH',Path(self.tmp.name)/'activity.json'),patch.object(activity,'LEGACY_ACTIVITY_PATHS',())]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.s={'live':True,'quote':'EUR','exchange':'KRAKEN','timezone':'Europe/Berlin','deposit_day':1,'withdrawals_enabled':False,'withdraw_threshold':D('1000'),'kraken_withdraw_key':'wallet','withdraw_address':'bc1test','withdraw_network':'BTC'}
    def service(self, exchange='KRAKEN'):
        self.s['exchange']=exchange;self.api=API(exchange);return bot.manual_withdrawal_service(self.api,self.s)
    def test_both_exchanges_activity_and_export(self):
        for exchange in ['KRAKEN','BITVAVO']:
            with self.subTest(exchange=exchange):
                self.state.clear();activity.ACTIVITY_PATH.unlink(missing_ok=True);activity.ACTIVITY_PATH.with_suffix('.sqlite3').unlink(missing_ok=True)
                service=self.service(exchange);p=service('preview','session');self.assertEqual(self.api.sent,[])
                service('confirm','session',p['token']);self.assertEqual(len(self.api.sent),1)
                record=activity.read_activity_records()[0];self.assertTrue(record['manual']);self.assertEqual(D(record['row']['shares']),D('.0044'));self.assertEqual(D(record['networkFeeBtc']),D('.0001'))
                self.assertEqual(record['withdrawalStatus'],'submitted')
                self.assertEqual(reporting.parqet_aggregate_rows([{'row':{'type':'Buy','identifier':'BTC','shares':'.0045','amount':'359','fee':'.8975','currency':'EUR','datetime':'2026-10-20T00:00:00Z'}},record],exchange),[])
                from withdrawals import reconcile_withdrawals
                reconcile_withdrawals(self.api,self.s,force=True)
                record=activity.read_activity_records()[0]
                self.assertEqual(record['withdrawalStatus'],'completed')
                buys={'row':{'type':'Buy','identifier':'BTC','datetime':'2026-10-20T00:00:00Z','shares':'.0045','amount':'359','fee':'.8975','currency':'EUR'}}
                exported=reporting.parqet_aggregate_rows([buys,record],exchange)[0];self.assertEqual(D(exported['amount']),D('359.8975'));self.assertEqual(D(exported['shares']),D('.0044'))
                with self.assertRaises(ValueError):service('confirm','session',p['token'])
                self.assertEqual(len(self.api.sent),1)
    def test_guards(self):
        service=self.service();p=service('preview','session')
        with self.assertRaises(ValueError):service('confirm','other',p['token'])
        self.api.available=D('.0046')
        with self.assertRaises(ValueError):service('confirm','session',p['token'])
        self.assertEqual(self.api.sent,[])
        for key in ['pending_order','pending_withdrawal']:
            self.state[key]={'id':'pending'}
            with self.assertRaises(ValueError):service('preview','session')
            self.state.clear()
        self.s['live']=False
        with self.assertRaises(ValueError):service('preview','session')
    def test_uncertain_submission_blocks_retry(self):
        service=self.service();p=service('preview','session');self.api.fail=True
        with self.assertRaises(ValueError):service('confirm','session',p['token'])
        self.assertTrue(self.state['pending_withdrawal'])
        with self.assertRaises(ValueError):service('preview','session')
        self.assertEqual(len(self.api.sent),1);self.assertEqual(activity.read_activity_records(),[])
    def test_expiry_and_stale_parallel_previews(self):
        service=self.service()
        with patch.object(bot.time,'monotonic',return_value=100):p=service('preview','session')
        with patch.object(bot.time,'monotonic',return_value=221):
            with self.assertRaises(ValueError):service('confirm','session',p['token'])
        first=service('preview','session');second=service('preview','session')
        service('confirm','session',first['token'])
        with self.assertRaises(ValueError):service('confirm','session',second['token'])
        self.assertEqual(len(self.api.sent),1)
    def test_recording_recovery_does_not_resubmit(self):
        service=self.service();p=service('preview','session')
        with patch.object(bot,'persist_bot_withdrawal',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):service('confirm','session',p['token'])
        self.assertIn('result',self.state['pending_withdrawal_context'])
        bot.maybe_withdraw_btc(self.api,self.state,self.s)
        self.assertEqual(len(self.api.sent),1);self.assertEqual(len(activity.read_activity_records()),1)

    def test_recovery_after_activity_insert_is_idempotent(self):
        service=self.service();preview=service('preview','session')
        original=bot.persist_bot_withdrawal
        def inserted_then_crashed(*args):
            original(*args)
            raise OSError('crash after ledger insert')
        with patch.object(bot,'persist_bot_withdrawal',side_effect=inserted_then_crashed):
            with self.assertRaises(OSError):service('confirm','session',preview['token'])
        bot.maybe_withdraw_btc(self.api,self.state,self.s)
        self.assertEqual(len(self.api.sent),1)
        self.assertEqual(len(activity.read_activity_records()),1)
        self.assertNotIn('pending_withdrawal',self.state)

    def test_failed_withdrawal_rearms_and_allows_manual_preview(self):
        service=self.service();preview=service('preview','session')
        service('confirm','session',preview['token'])
        record=activity.read_activity_records()[0]
        record['withdrawalStatus']='failed';activity.update_activity_record(record)
        self.assertIn('token',service('preview','session'))
        bot.maybe_withdraw_btc(self.api,self.state,self.s)
        self.assertTrue(self.state['withdrawal_armed'])
        self.assertEqual(len(self.api.sent),1)

    def test_withdrawal_configuration_reports_missing_settings(self):
        for exchange, missing in [('KRAKEN', ['LIVE_TRADING', 'KRAKEN_WITHDRAW_KEY']),
                                  ('BITVAVO', ['LIVE_TRADING', 'WITHDRAW_ADDRESS', 'WITHDRAW_NETWORK'])]:
            handler = object.__new__(web_ui.Handler)
            handler.settings = {**self.s, 'exchange': exchange, 'live': False,
                                'kraken_withdraw_key': '', 'withdraw_address': '', 'withdraw_network': ''}
            handler.withdrawal_service = lambda *args: None
            handler.path = '/api/withdrawal/config'
            handler._require_authentication = lambda: True
            result = []
            handler._json = lambda status, body: result.append((status, body))
            handler.do_GET()
            self.assertEqual(result[0][0], 200)
            self.assertFalse(result[0][1]['enabled'])
            for setting in missing:
                self.assertIn(setting, result[0][1]['reason'])

    def test_manual_withdrawal_configuration_ignores_automatic_toggle(self):
        handler = object.__new__(web_ui.Handler)
        handler.settings = {**self.s, 'withdrawals_enabled': False}
        handler.withdrawal_service = lambda *args: None
        handler.path = '/api/withdrawal/config'
        handler._require_authentication = lambda: True
        result = []
        handler._json = lambda status, body: result.append((status, body))
        handler.do_GET()
        self.assertEqual(result[0][0], 200)
        self.assertTrue(result[0][1]['enabled'])
        self.assertEqual(result[0][1]['reason'], '')

    def test_route_auth_and_csrf(self):
        handler=object.__new__(web_ui.Handler);handler.path='/api/withdrawal/confirm';handler.headers={};handler._json=lambda status,result:statuses.append(status)
        statuses=[];handler._require_authentication=lambda:False;handler.do_POST();self.assertEqual(statuses,[])
        handler._require_authentication=lambda:True;handler.do_POST();self.assertEqual(statuses,[403])
        handler.headers={'Content-Type':'application/json','X-DCA-Action':'withdrawal','Content-Length':'2','Cookie':'dca_session=test'};handler.rfile=io.BytesIO(b'{}');handler.do_POST();self.assertEqual(statuses[-1],400)

if __name__=='__main__':unittest.main()

