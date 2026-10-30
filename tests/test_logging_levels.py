"""Operational logs distinguish state changes, external failures and internal faults."""
import logging
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import requests
from app.trading import worker
from app.webpage import web_ui
from app.trading import withdrawals
from app.core.logging_utils import log_transition, clear_transition

class LoggingLevelTests(TestCase):
    def test_waiting_state_is_info_once_and_debug_until_it_changes(self):
        logger=Mock();owner=SimpleNamespace()
        log_transition(logger,owner,'wait','funds',logging.INFO,'Waiting')
        log_transition(logger,owner,'wait','funds',logging.INFO,'Waiting')
        self.assertEqual([c.args[0] for c in logger.log.call_args_list],[logging.INFO,logging.DEBUG])
        clear_transition(owner,'wait')
        log_transition(logger,owner,'wait','funds',logging.INFO,'Waiting')
        self.assertEqual(logger.log.call_args.args[0],logging.INFO)

    def test_worker_network_failures_warn_but_programming_failures_have_error_trace(self):
        for error,level,trace in [(requests.ReadTimeout('offline'),logging.WARNING,False),(RuntimeError('bug'),logging.ERROR,True)]:
            w=worker.Worker(None,None,300)
            def cycle():w.stop.set();raise error
            w.cycle=cycle
            with patch.object(worker,'write_json_atomic'):
                with self.assertLogs(worker.LOG,level='INFO') as captured:w.run()
            self.assertEqual(captured.records[0].levelno,level)
            self.assertEqual(bool(captured.records[0].exc_info),trace)

    def test_http_access_is_debug(self):
        h=object.__new__(web_ui.Handler);h.address_string=lambda:'127.0.0.1'
        with self.assertLogs(web_ui.LOG,level='DEBUG') as captured:h.log_message('%s','GET /api/schedule 200')
        self.assertEqual(captured.records[0].levelno,logging.DEBUG)

    def test_withdrawal_terminal_status_is_logged_once(self):
        for exchange_status,expected,level in [('Success','completed',logging.INFO),('Failure','failed',logging.WARNING)]:
            row={'datetime':'2026-10-30T10:00:00+00:00','shares':'.01','price':'80000'}
            record={'id':'withdrawal:abc','withdrawalId':'abc','exchange':'KRAKEN','withdrawalStatus':'submitted','row':row}
            api=Mock();api.btc_withdrawals.return_value=[{'id':'abc','status':exchange_status,'fee':'.0001','amount':'.01'}]
            with patch.object(withdrawals,'withdrawal_records',return_value=[record]),patch.object(withdrawals,'update_activity_record'):
                with self.assertLogs(withdrawals.LOG,level='INFO') as captured:withdrawals.reconcile_withdrawals(api,{'exchange':'KRAKEN'},force=True)
                self.assertEqual(captured.records[0].levelno,level)
                self.assertEqual(record['withdrawalStatus'],expected)
                with patch.object(withdrawals.LOG,'log') as log:withdrawals.reconcile_withdrawals(api,{'exchange':'KRAKEN'},force=True)
                log.assert_not_called()
