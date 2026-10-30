"""Adapter refresh, error classification, status and queued-API contracts."""
import io
import os
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock,patch
import requests
import activity,bitvavo,bot,kraken,web_ui,healthcheck
from exchange_errors import DefinitiveRejection


class AdapterReviewTests(unittest.TestCase):
    def setUp(self):
        session=patch.object(requests,'Session',return_value=Mock())
        session.start();self.addCleanup(session.stop)

    def test_exchange_credentials_never_fall_back_to_environment(self):
        with patch.dict(os.environ,{'KRAKEN_API_KEY':'environment-key','KRAKEN_API_SECRET':'dGVzdA==',
                                   'BITVAVO_API_KEY':'environment-key','BITVAVO_API_SECRET':'environment-secret'}):
            with self.assertRaisesRegex(ValueError,'Settings'):kraken.Kraken('EUR')
            with self.assertRaisesRegex(ValueError,'Settings'):bitvavo.Bitvavo()
            configured=kraken.Kraken('EUR',api_key='saved-key',api_secret='dGVzdA==')
            self.assertEqual(configured.key,'saved-key')

    def test_recovery_open_order_queries_are_read_only_and_validate_responses(self):
        kraken_api=kraken.Kraken('EUR',api_key='fictional',api_secret='dGVzdA==')
        kraken_api.private=Mock(return_value={'open':{'id':{'status':'open'}}})
        self.assertEqual(kraken_api.open_orders(),[{'status':'open'}])
        kraken_api.private.assert_called_once_with('OpenOrders',{})
        kraken_api.private.return_value={}
        with self.assertRaises(ValueError):kraken_api.open_orders()
        bitvavo_api=bitvavo.Bitvavo(api_key='fictional',api_secret='fictional')
        bitvavo_api.request=Mock(return_value=[])
        self.assertEqual(bitvavo_api.open_orders(),[])
        bitvavo_api.request.assert_called_once_with('GET','/ordersOpen',private=True)
        bitvavo_api.request.return_value={}
        with self.assertRaises(ValueError):bitvavo_api.open_orders()

    def pair(self,minimum,status='online'):
        return {'XXBTZEUR':{'base':'XXBT','quote':'ZEUR','altname':'XBTEUR','ordermin':minimum,'status':status}}

    def test_kraken_market_minimum_status_and_force_refresh(self):
        api=kraken.Kraken('EUR',api_key='fictional',api_secret='dGVzdA==');api.public=Mock(side_effect=[self.pair('.0001'),self.pair('.0002','cancel_only'),self.pair('.0003')])
        with patch.object(kraken.time,'monotonic',return_value=100):self.assertEqual(api.market('BTC-EUR')['minOrderInBaseAsset'],'.0001')
        with patch.object(kraken.time,'monotonic',return_value=130):self.assertEqual(api.market('BTC-EUR')['minOrderInBaseAsset'],'.0001')
        with patch.object(kraken.time,'monotonic',return_value=161):self.assertEqual(api.market('BTC-EUR')['status'],'cancel_only')
        with patch.object(kraken.time,'monotonic',return_value=162):self.assertEqual(api.market('BTC-EUR',refresh=True)['minOrderInBaseAsset'],'.0003')
        self.assertEqual(api.public.call_count,3)
        self.assertEqual(api.public.call_args.args[1]['pair'],'XBTEUR')

    def test_bitvavo_queries_only_selected_market(self):
        api=bitvavo.Bitvavo(api_key='fictional',api_secret='fictional');api.request=Mock(side_effect=[[{'market':'BTC-EUR','minOrderInBaseAsset':'.0001'}],[{'market':'BTC-EUR','minOrderInBaseAsset':'.0002'}]])
        first=api.market('BTC-EUR');self.assertEqual(api.market('BTC-EUR'),first)
        self.assertEqual(api.market('BTC-EUR',refresh=True)['minOrderInBaseAsset'],'.0002')
        self.assertEqual(api.request.call_count,2)
        self.assertEqual(api.request.call_args.kwargs['params'],{'market':'BTC-EUR'})

    def test_bitvavo_cycle_reuses_balance_and_price_but_not_next_cycle(self):
        api=bitvavo.Bitvavo(api_key='fictional',api_secret='fictional')
        def read(method,path,**kwargs):
            if path=='/balance':return [{'symbol':'BTC','available':'.001'},{'symbol':'EUR','available':'100'}]
            return {'price':'80000'}
        api.request=Mock(side_effect=read)
        with api.balance_snapshot():
            self.assertEqual(api.balance('BTC'),D('.001'));self.assertEqual(api.balance('EUR'),D(100))
            self.assertEqual(api.price('BTC-EUR'),api.price('BTC-EUR'))
        self.assertEqual(api.request.call_count,2)
        with api.balance_snapshot():api.balance('EUR')
        self.assertEqual(api.request.call_count,3)

    def test_fee_rate_cache_expires(self):
        api=bitvavo.Bitvavo(api_key='fictional',api_secret='fictional');api.request=Mock(side_effect=[{'fees':{'taker':'.0025'}},{'fees':{'taker':'.003'}}])
        with patch.object(bitvavo.time,'monotonic',return_value=100):self.assertEqual(api.estimate_taker_fee('BTC-EUR',D(100)),D('.25'))
        with patch.object(bitvavo.time,'monotonic',return_value=200):self.assertEqual(api.estimate_taker_fee('BTC-EUR',D(200)),D('.5'))
        with patch.object(bitvavo.time,'monotonic',return_value=401):self.assertEqual(api.estimate_taker_fee('BTC-EUR',D(100)),D('.3'))
        self.assertEqual(api.request.call_count,2)

    def test_bitvavo_known_rejection_is_distinct_from_duplicate_and_timeout(self):
        api=bitvavo.Bitvavo(api_key='fictional',api_secret='fictional');response=Mock();api.session.request.return_value=response
        response.status_code=400;response.json.return_value={'errorCode':216,'error':'Insufficient funds'}
        with self.assertRaises(DefinitiveRejection):api.buy_btc('EUR',D('.001'),'client')
        response.json.return_value={'errorCode':220,'error':'Duplicate client reference'}
        response.raise_for_status.side_effect=requests.HTTPError('400 duplicate')
        with self.assertRaises(requests.HTTPError):api.buy_btc('EUR',D('.001'),'client')
        api.session.request.side_effect=requests.ReadTimeout('unknown result')
        with self.assertRaises(requests.ReadTimeout):api.buy_btc('EUR',D('.001'),'client')

    def test_bitvavo_withdrawal_transports_asset_and_accepts_empty_201(self):
        api=bitvavo.Bitvavo(api_key='fictional',api_secret='fictional');response=Mock(status_code=201,content=b'')
        response.json.side_effect=ValueError('empty accepted response')
        api.session.request.return_value=response
        self.assertEqual(api.withdraw_btc({'symbol':'BTC','amount':'.001','address':'fictional','network':'BTC','idempotencyKey':'unique-example'}),{})
        import json
        payload=json.loads(api.session.request.call_args.kwargs['data'])
        self.assertEqual(payload['asset'],'BTC');self.assertNotIn('symbol',payload)

    def test_kraken_unknown_server_error_is_not_definitive(self):
        api=kraken.Kraken('EUR',api_key='fictional',api_secret='dGVzdA==')
        with self.assertRaises(kraken.KrakenAPIError):api._check_response(Mock(json=lambda:{'error':['EGeneral:Internal error']}))
        with self.assertRaises(DefinitiveRejection):api._check_response(Mock(json=lambda:{'error':['EOrder:Insufficient funds']}))

    def test_kraken_settled_and_success_status_are_distinct(self):
        api=kraken.Kraken('EUR',api_key='fictional',api_secret='dGVzdA==');api.private=Mock(return_value=[{'time':1,'refid':'a','txid':'tx-a','status':'Settled','amount':'.001','fee':'.0001'},{'time':2,'refid':'b','status':'Success','amount':'.001','fee':'.0001'}])
        rows=api.btc_withdrawals()
        self.assertEqual(rows[0]['status'],'settled');self.assertEqual(rows[1]['status'],'completed')
        self.assertEqual(rows[0]['id'],'a');self.assertEqual(rows[0]['txId'],'tx-a')

    def test_queued_http_response_returns_job_without_waiting_for_exchange(self):
        handler=object.__new__(web_ui.Handler);handler.path='/api/withdrawal/preview'
        handler.headers={'X-DCA-Action':'withdrawal','Content-Type':'application/json','Content-Length':'2','Cookie':'dca_session=owner'}
        handler.rfile=io.BytesIO(b'{}');handler._require_authentication=lambda:True
        handler.withdrawal_service=Mock(return_value={'jobId':'job','state':'queued'});handler._json=Mock()
        handler.do_POST();self.assertEqual(handler._json.call_args.args[0],202)
        handler.withdrawal_service.assert_called_once_with('preview','owner',None)

    def test_http_job_polling_passes_authenticated_owner_to_worker(self):
        handler=object.__new__(web_ui.Handler);handler.path='/api/withdrawal/jobs/job';handler.headers={'Cookie':'dca_session=owner'}
        handler._require_authentication=lambda:True;handler.worker=Mock();handler.worker.get_job.return_value={'state':'queued'};handler._json=Mock()
        handler.do_GET();handler.worker.get_job.assert_called_once_with('job','owner')

    def test_healthcheck_requires_fresh_running_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'health.json'
            self.assertFalse(healthcheck.healthy(p))
            import json,time
            for state,expected in [({'running':True,'heartbeat':time.time()},True),({'running':True,'heartbeat':time.time()-601},True),({'running':True,'heartbeat':time.time()-901},False),({'running':False,'heartbeat':time.time()},False)]:
                p.write_text(json.dumps(state));self.assertEqual(healthcheck.healthy(p),expected)


if __name__=='__main__':unittest.main()
