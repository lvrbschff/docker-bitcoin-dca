"""UI audit events are authenticated, bounded and never trigger account actions."""
import io
import json
import unittest
from unittest.mock import Mock, patch
from app.webpage import web_ui

class UiEventLogTests(unittest.TestCase):
    def handler(self, event, authenticated=True, header="ui-event"):
        h=object.__new__(web_ui.Handler)
        h.path="/api/ui-events"
        body=json.dumps({"event":event}).encode()
        h.headers={"Content-Type":"application/json","X-DCA-Action":header,"Content-Length":str(len(body))}
        h.rfile=io.BytesIO(body)
        h._require_authentication=Mock(return_value=authenticated)
        h._json=Mock()
        h.withdrawal_service=Mock(side_effect=AssertionError("Logging must not submit a withdrawal"))
        return h

    def test_whitelisted_actions_have_readable_info_logs(self):
        for event in ["withdrawal_clicked","withdrawal_confirmed","withdrawal_cancelled","withdrawal_expired","export_clicked"]:
            with self.subTest(event=event):
                h=self.handler(event)
                with self.assertLogs(web_ui.LOG,level="INFO") as logs:h.do_POST()
                self.assertEqual(h._json.call_args.args[0],200)
                self.assertEqual(len(logs.output),1)
                h.withdrawal_service.assert_not_called()

    def test_invalid_event_or_header_is_not_logged(self):
        for event,header,status in [("arbitrary secret text","ui-event",400),("withdrawal_clicked","",403)]:
            h=self.handler(event,header=header)
            with patch.object(web_ui.LOG,"info") as log:h.do_POST()
            log.assert_not_called()
            self.assertEqual(h._json.call_args.args[0],status)

    def test_anonymous_clients_cannot_log_events(self):
        h=self.handler("withdrawal_clicked",authenticated=False)
        with patch.object(web_ui.LOG,"info") as log:h.do_POST()
        log.assert_not_called()
        h.withdrawal_service.assert_not_called()

    def test_export_rejection_is_logged(self):
        h=self.handler("unused")
        h.path="/api/export.csv?start=0&end=1000"
        with patch.object(web_ui,"parqet_interval_records",return_value=[]):
            with self.assertLogs(web_ui.LOG,level="INFO") as logs:h.do_GET()
        self.assertEqual(h._json.call_args.args[0],400)
        self.assertTrue(any("export rejected" in line for line in logs.output))
