"""Offline regression coverage grouped by worker scheduling and health."""
import unittest
from unittest.mock import Mock, patch
import worker
import queue
from unittest import TestCase


class HeartbeatTests(unittest.TestCase):
    def test_first_write_is_immediate_and_subsequent_writes_are_five_minutes_apart(self):
        account=worker.Worker(Mock(),Mock(),300)
        account.running=True
        with patch.object(worker,'write_json_atomic') as write,patch.object(worker.time,'time',return_value=1000):
            with patch.object(worker.time,'monotonic',return_value=0):account.heartbeat()
            self.assertEqual(write.call_count,1)
            with patch.object(worker.time,'monotonic',return_value=299):account.heartbeat()
            self.assertEqual(write.call_count,1)
            self.assertEqual(account.beat,299)
            with patch.object(worker.time,'monotonic',return_value=300):account.heartbeat()
            self.assertEqual(write.call_count,2)
            self.assertTrue(write.call_args.args[1]['running'])

    def test_stop_is_persisted_immediately_without_waiting_for_next_write_interval(self):
        account=worker.Worker(Mock(),Mock(),300)
        account.stop.set()
        with patch.object(worker,'write_json_atomic') as write:account.run()
        self.assertEqual(write.call_count,1)
        self.assertFalse(write.call_args.args[1]['running'])

    def test_ten_minute_stall_threshold_is_preserved(self):
        account=worker.Worker(Mock(),Mock(),300)
        account.running=True;account.beat=100
        with patch.object(worker.time,'monotonic',return_value=699):self.assertTrue(account.healthy())
        with patch.object(worker.time,'monotonic',return_value=700):self.assertFalse(account.healthy())


class PreviewCheckTimingTests(TestCase):
    def test_manual_withdrawals_preserve_the_next_check(self):
        for action,expected_checks in [("preview",1),("confirm",1)]:
            with self.subTest(action=action):
                clock=[100.0]
                with patch.object(worker.time,"monotonic",side_effect=lambda:clock[0]),patch.object(worker,"write_json_atomic"):
                    cycle=Mock(return_value=300)
                    w=worker.Worker(cycle,Mock(return_value={"ok":True}),300)
                    w.jobs["job"]={"state":"queued"}
                    calls=[0]
                    def get(timeout):
                        calls[0]+=1
                        if calls[0]==1:raise queue.Empty
                        if calls[0]==2:return ("job",action,"session",None)
                        clock[0]=102.0
                        w.stop.set()
                        raise queue.Empty
                    w.queue=Mock()
                    w.queue.get.side_effect=get
                    w.run()
                    self.assertEqual(cycle.call_count,expected_checks)
                    w.action.assert_called_once_with(action,"session",None)
                    self.assertEqual(w.jobs["job"]["state"],"completed")


class WorkerErrorTests(TestCase):
    def test_unexpected_job_details_stay_out_of_browser_responses(self):
        import requests
        for error in (RuntimeError('private internal detail'), requests.ReadTimeout('private request detail'), ValueError('Action unavailable')):
            with self.subTest(error=type(error).__name__):
                account=worker.Worker(Mock(),None,300)
                account.running=True
                job=account.submit('preview','owner')
                def action(*args):
                    account.stop.set()
                    raise error
                account.action=action
                with patch.object(worker,'write_json_atomic'),patch.object(worker.LOG,'exception'),patch.object(worker.LOG,'warning'),patch.object(worker.LOG,'info'):
                    account.run()
                result=account.get_job(job['jobId'],'owner')
                self.assertEqual(result['state'],'failed')
                if isinstance(error,ValueError):self.assertEqual(result['error'],'Action unavailable')
                else:self.assertNotIn('private',result['error'])
