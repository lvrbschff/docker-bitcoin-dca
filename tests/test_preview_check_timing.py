"""Manual withdrawal actions must not advance periodic account checks."""
import queue
from unittest import TestCase
from unittest.mock import Mock, patch
import worker

class PreviewCheckTimingTests(TestCase):
    def test_manual_withdrawals_preserve_the_next_check(self):
        for action,expected_checks in [("preview",0),("confirm",0)]:
            with self.subTest(action=action):
                clock=[100.0]
                with patch.object(worker.time,"monotonic",side_effect=lambda:clock[0]),patch.object(worker,"write_json_atomic"):
                    cycle=Mock(return_value=300)
                    w=worker.Worker(cycle,Mock(return_value={"ok":True}),Mock(),300,300)
                    w.jobs["job"]={"state":"queued"}
                    calls=[0]
                    def get(timeout):
                        calls[0]+=1
                        if calls[0]==1:return ("job",action,"session",None)
                        clock[0]=102.0
                        w.stop.set()
                        raise queue.Empty
                    w.queue=Mock()
                    w.queue.get.side_effect=get
                    w.run()
                    self.assertEqual(cycle.call_count,expected_checks)
                    w.action.assert_called_once_with(action,"session",None)
                    self.assertEqual(w.jobs["job"]["state"],"completed")
