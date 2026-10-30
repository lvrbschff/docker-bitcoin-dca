"""Security regressions use local certificates and fake exchange adapters only."""
import io
import ssl
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
import activity
import configuration
import runtime
import security
import tls
import web_ui

PASSWORD = 'a-long-private-password'


class AttemptLimitTests(unittest.TestCase):
    def test_parallel_attempts_are_reserved_and_limits_expire(self):
        limiter=security.AttemptLimiter()
        with patch.object(security.time,'monotonic',return_value=100):
            with ThreadPoolExecutor(8) as pool: results=list(pool.map(lambda _:limiter.consume('peer'),range(12)))
        self.assertEqual(results.count(0),5)
        with patch.object(security.time,'monotonic',return_value=1000):self.assertEqual(limiter.consume('peer'),0)
        limiter.clear('peer');self.assertEqual(limiter.attempts,{})

    def test_sources_are_bounded_and_forwarded_headers_cannot_bypass_limits(self):
        limiter=security.AttemptLimiter(capacity=1)
        self.assertEqual(limiter.consume('first'),0)
        self.assertGreater(limiter.consume('second'),0)
        self.assertEqual(len(limiter.attempts),1)
        self.assertNotIn('X-Forwarded-For',web_ui.Handler._handle_login.__code__.co_consts)

    def login(self, valid=False):
        handler=object.__new__(web_ui.Handler)
        handler.client_address=('192.0.2.5',1000)
        handler.runtime=Mock();handler.runtime.store.authenticate.return_value=valid
        body=b'username=operator&password=private-password'
        handler.headers={'Content-Length':str(len(body))}
        handler.rfile=io.BytesIO(body);handler.wfile=io.BytesIO()
        handler.statuses=[];handler.response_headers={}
        handler.send_response=handler.statuses.append
        handler.send_header=lambda key,value:handler.response_headers.update({key:value})
        handler.end_headers=lambda:None
        handler._handle_login()
        return handler

    def test_login_lockout_does_not_hash_more_passwords(self):
        with patch.object(web_ui,'_LOGIN_ATTEMPTS',security.AttemptLimiter()):
            for _ in range(5):self.assertEqual(self.login().statuses,[401])
            last=self.login(True)
        self.assertEqual(last.statuses,[429]);last.runtime.store.authenticate.assert_not_called()
        self.assertIn('Retry-After',last.response_headers)

    def test_login_cookie_is_secure_and_session_has_absolute_30_minute_expiry(self):
        with patch.object(web_ui,'_LOGIN_ATTEMPTS',security.AttemptLimiter()),patch.object(web_ui,'_SESSIONS',{}),patch.object(web_ui.time,'monotonic',return_value=100):
            handler=self.login(True)
            cookie=handler.response_headers['Set-Cookie']
            self.assertIn('Secure;',cookie);self.assertIn('HttpOnly;',cookie);self.assertIn('SameSite=Strict',cookie);self.assertIn('Max-Age=1800',cookie)
            handler.headers={'Cookie':cookie.split(';')[0]}
            self.assertTrue(handler._has_valid_session())
            with patch.object(web_ui.time,'monotonic',return_value=1900):self.assertFalse(handler._has_valid_session())


class SensitiveSettingsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        for name,value in [('ACTIVITY_PATH',root/'activity.json'),('SNAPSHOT_PATH',root/'dashboard.json')]:
            p=patch.object(activity,name,value);p.start();self.addCleanup(p.stop)
        factory=patch.object(runtime,'create_exchange',return_value=Mock());factory.start();self.addCleanup(factory.stop)
        self.store=configuration.ConfigStore(root/'config.json');self.store.create_account('operator',PASSWORD)
        self.app=runtime.Runtime(self.store,root/'state.json')
        self.app.save({'settings':{'kraken_api_key':'fictional','kraken_api_secret':'c2VjcmV0'}})

    def test_sensitive_changes_cannot_be_saved_without_current_password(self):
        before=self.store.path.read_bytes()
        changes=[{'kraken_api_key':'new-key'},{'kraken_api_secret':'bmV3'},{'kraken_withdraw_key':'wallet'},
                 {'withdraw_address':'bc1example'},{'live':True},{'withdraw_threshold':'500'}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(security.PasswordConfirmationError):
                self.app.save({'settings':change})
            self.assertEqual(self.store.path.read_bytes(),before)
        self.assertGreater(self.app.password_attempts.consume('operator'),0)

    def test_valid_password_allows_change_and_cosmetic_changes_need_none(self):
        self.app.save({'settings':{'kraken_withdraw_key':'wallet'},'current_password':PASSWORD})
        self.assertEqual(self.store.snapshot()['settings']['kraken_withdraw_key'],'wallet')
        self.app.save({'settings':{'display_currency':'USD'}})
        self.assertEqual(self.store.snapshot()['settings']['display_currency'],'USD')


class LocalHttpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.hosts=tls.certificate_hosts('192.0.2.62')
        cls.context=tls.https_context(cls.hosts,cls.root)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_persisted_ca_is_reused_and_relocation_renews_only_server_certificate(self):
        ca=(self.root/'ca.pem').read_bytes();cert=(self.root/'server.pem').read_bytes()
        tls.https_context(self.hosts,self.root)
        self.assertEqual((self.root/'server.pem').read_bytes(),cert)
        tls.https_context(tls.certificate_hosts('192.0.2.10'),self.root)
        self.assertEqual((self.root/'ca.pem').read_bytes(),ca)
        self.assertNotEqual((self.root/'server.pem').read_bytes(),cert)
        self.assertEqual((self.root/'server-key.pem').stat().st_mode&0o777,0o600)
        self.assertEqual(self.root.stat().st_mode&0o777,0o700)
        tls.https_context(self.hosts,self.root)

    def test_tls_handshake_validates_local_ca_and_ip_without_network(self):
        incoming,outgoing=ssl.MemoryBIO(),ssl.MemoryBIO()
        server=self.context.wrap_bio(incoming,outgoing,server_side=True)
        client_in,client_out=ssl.MemoryBIO(),ssl.MemoryBIO()
        client_context=ssl.create_default_context(cafile=str(self.root/'ca.pem'))
        client=client_context.wrap_bio(client_in,client_out,server_hostname='192.0.2.62')
        complete=set()
        for _ in range(20):
            for name,peer in [('client',client),('server',server)]:
                if name in complete:continue
                try:peer.do_handshake();complete.add(name)
                except ssl.SSLWantReadError:pass
            data=client_out.read()
            if data:incoming.write(data)
            data=outgoing.read()
            if data:client_in.write(data)
            if len(complete)==2:break
        self.assertEqual(complete,{'client','server'})
        client.write(b'GET / HTTP/1.1\r\n\r\n');incoming.write(client_out.read())
        self.assertEqual(server.read(),b'GET / HTTP/1.1\r\n\r\n')
        self.assertEqual(self.context.minimum_version,ssl.TLSVersion.TLSv1_2)

    def test_daily_renewal_updates_context_and_retains_old_context_on_error(self):
        handler=Mock();before=handler.tls_context
        stop=Mock();stop.wait.side_effect=[False,True]
        with patch.object(tls,'https_context',return_value=self.context):tls.renew_certificates(handler,self.hosts,stop)
        self.assertIs(handler.tls_context,self.context)
        stop.wait.side_effect=[False,True]
        with patch.object(tls,'https_context',side_effect=ValueError('broken')):
            with self.assertLogs(tls.LOG,level='ERROR'):tls.renew_certificates(handler,self.hosts,stop)
        self.assertIs(handler.tls_context,self.context)

    def test_hostnames_are_validated_before_running_openssl(self):
        for value in ['bad/name','host\nDNS:evil','-bad.local','https://host:6890']:
            with self.assertRaises(ValueError):tls.certificate_hosts(value)
        self.assertIn('bot.local',tls.certificate_hosts('bot.local,192.0.2.62'))


class HandshakeFailureTests(unittest.TestCase):
    def test_certificate_rejection_closes_request_before_http_processing(self):
        handler=object.__new__(web_ui.Handler)
        handler.request=Mock();handler.tls_context=Mock()
        error=ssl.SSLError('rejected');error.reason='SSLV3_ALERT_CERTIFICATE_UNKNOWN'
        handler.tls_context.wrap_socket.side_effect=error
        with patch.object(web_ui.BaseHTTPRequestHandler,'setup') as setup:
            with self.assertRaises(tls.TLSHandshakeError):handler.setup()
        handler.request.close.assert_called_once();setup.assert_not_called()

    def test_untrusted_certificate_logs_once_without_a_traceback(self):
        server=object.__new__(tls.HTTPSWebServer)
        error=ssl.SSLError('rejected');error.reason='SSLV3_ALERT_CERTIFICATE_UNKNOWN'
        with patch.object(tls.ThreadingHTTPServer,'handle_error') as fallback:
            with self.assertLogs(tls.LOG,level='DEBUG') as logs:
                for port in (1234,1235):
                    try:raise tls.TLSHandshakeError(error)
                    except tls.TLSHandshakeError:server.handle_error(Mock(),('192.0.2.83',port))
        fallback.assert_not_called()
        self.assertEqual([entry.levelname for entry in logs.records],['WARNING','DEBUG'])
        self.assertTrue(all(entry.exc_info is None for entry in logs.records))
        self.assertIn('Trust tls/ca.pem',logs.output[0])

    def test_disconnections_and_timeouts_are_debug_only(self):
        server=object.__new__(tls.HTTPSWebServer)
        with self.assertLogs(tls.LOG,level='DEBUG') as logs:
            for error in (TimeoutError(),ConnectionResetError(),ssl.SSLEOFError()):
                try:raise tls.TLSHandshakeError(error)
                except tls.TLSHandshakeError:server.handle_error(Mock(),('192.0.2.83',1234))
        self.assertTrue(all(entry.levelname=='DEBUG' and entry.exc_info is None for entry in logs.records))

    def test_unexpected_internal_errors_keep_the_standard_error_handler(self):
        server=object.__new__(tls.HTTPSWebServer);request=Mock();peer=('192.0.2.83',1234)
        with patch.object(tls.ThreadingHTTPServer,'handle_error') as fallback:
            try:raise RuntimeError('internal failure')
            except RuntimeError:server.handle_error(request,peer)
        fallback.assert_called_once_with(request,peer)


class ConnectionLimitTests(unittest.TestCase):
    def test_saturated_server_closes_connections_and_releases_slots_on_failure(self):
        import threading
        server=object.__new__(tls.HTTPSWebServer)
        server._connections=threading.BoundedSemaphore(1)
        server.shutdown_request=Mock()
        request=Mock();peer=('192.0.2.1',1234)
        with patch.object(tls.ThreadingHTTPServer,'process_request') as dispatch:
            server.process_request(request,peer)
            dispatch.assert_called_once()
            server.process_request(request,peer)
            server.shutdown_request.assert_called_once_with(request)
        with patch.object(tls.ThreadingHTTPServer,'process_request_thread',side_effect=RuntimeError('handler failed')):
            with self.assertRaises(RuntimeError):server.process_request_thread(request,peer)
        with patch.object(tls.ThreadingHTTPServer,'process_request',side_effect=RuntimeError('thread failed')):
            with self.assertRaises(RuntimeError):server.process_request(request,peer)
        self.assertTrue(server._connections.acquire(blocking=False))

    def test_queued_withdrawal_rechecks_session_before_accessing_exchange(self):
        app=object.__new__(runtime.Runtime)
        import threading
        app.lock=threading.RLock();app.api=Mock();app.settings={'manual_withdrawal_enabled':True}
        app.exchange_check_pending=False;app.store=Mock();app.store.snapshot.return_value={'account':{}}
        app.service=Mock(return_value={'ok':True});app.session_validator=web_ui.session_is_valid
        with patch.object(web_ui,'_SESSIONS',{'active':200,'expired':99}),patch.object(web_ui.time,'monotonic',return_value=100):
            for token in ('expired','logged-out'):
                with self.subTest(token=token),self.assertRaisesRegex(ValueError,'session expired'):
                    app.action('confirm',token,'preview')
            app.service.assert_not_called()
            self.assertEqual(app.action('confirm','active','preview'),{'ok':True})
            app.service.assert_called_once_with('confirm','active','preview')
