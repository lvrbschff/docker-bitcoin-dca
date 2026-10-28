"""Heartbeat persistence is throttled without weakening worker health checks."""
import unittest
from unittest.mock import Mock, patch
import worker


class HeartbeatTests(unittest.TestCase):
    def test_first_write_is_immediate_and_subsequent_writes_are_one_minute_apart(self):
        account=worker.Worker(Mock(),Mock(),Mock(),300,60)
        account.running=True
        with patch.object(worker,'write_json_atomic') as write,patch.object(worker.time,'time',return_value=1000):
            with patch.object(worker.time,'monotonic',return_value=0):account.heartbeat()
            self.assertEqual(write.call_count,1)
            with patch.object(worker.time,'monotonic',return_value=59):account.heartbeat()
            self.assertEqual(write.call_count,1)
            self.assertEqual(account.beat,59)
            with patch.object(worker.time,'monotonic',return_value=60):account.heartbeat()
            self.assertEqual(write.call_count,2)
            self.assertTrue(write.call_args.args[1]['running'])

    def test_stop_is_persisted_immediately_without_waiting_for_next_write_interval(self):
        account=worker.Worker(Mock(),Mock(),Mock(),300,60)
        account.stop.set()
        with patch.object(worker,'write_json_atomic') as write:account.run()
        self.assertEqual(write.call_count,1)
        self.assertFalse(write.call_args.args[1]['running'])

    def test_ten_minute_stall_threshold_is_preserved(self):
        account=worker.Worker(Mock(),Mock(),Mock(),300,60)
        account.running=True;account.beat=100
        with patch.object(worker.time,'monotonic',return_value=699):self.assertTrue(account.healthy())
        with patch.object(worker.time,'monotonic',return_value=700):self.assertFalse(account.healthy())


if __name__=='__main__':unittest.main()
