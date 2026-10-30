"""Volume initialization preserves existing state and detects missing/corrupt data."""
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch
import bot
import healthcheck
import runtime
import storage
import worker


class StorageTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.volume=Path(self.temp.name)/'volume'

    def put(self,name,value):
        self.volume.mkdir(parents=True,exist_ok=True)
        (self.volume/name).write_text(json.dumps(value))

    def test_fresh_volume_initializes_private_state_without_extra_files(self):
        storage.prepare_storage(self.volume)
        self.assertEqual(json.loads((self.volume/'state.json').read_text()),{})
        self.assertEqual((self.volume/'state.json').stat().st_mode & 0o777,0o600)
        self.assertEqual({p.name for p in self.volume.iterdir()},{'state.json'})

    def test_restart_preserves_pending_transactions_and_schedule(self):
        state={'pending_order':{'id':'order-1'},'pending_withdrawal':{'id':'withdrawal-1'},
               'last_order_at':'2026-10-30T10:00:00Z','schedule':{'interval':300}}
        self.put('state.json',state)
        storage.prepare_storage(self.volume)
        self.assertEqual(json.loads((self.volume/'state.json').read_text()),state)

    def test_missing_state_initializes_without_manual_recovery(self):
        for filename,value in [('config.json',{'complete':True}),('activity.json',{'records':[]})]:
            with self.subTest(filename=filename):
                self.put(filename,value)
                with self.assertLogs(storage.LOG,level='WARNING'):storage.prepare_storage(self.volume)
                self.assertEqual(json.loads((self.volume/'state.json').read_text()), {})
                storage.prepare_storage(self.volume)
                self.assertEqual(json.loads((self.volume/'state.json').read_text()), {})
                (self.volume/'state.json').unlink()
                (self.volume/filename).unlink()

    def test_corrupt_state_is_not_overwritten(self):
        self.put('state.json',[])
        with self.assertRaises(ValueError):storage.prepare_storage(self.volume)
        self.assertEqual(json.loads((self.volume/'state.json').read_text()),[])

    def test_atomic_initialization_failure_is_retryable(self):
        with patch.object(storage.os,'replace',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):storage.prepare_storage(self.volume)
        self.assertFalse((self.volume/'state.json').exists())
        self.assertEqual(list(self.volume.glob('.storage-*')),[])
        storage.prepare_storage(self.volume)
        self.assertTrue((self.volume/'state.json').exists())

    def test_worker_and_healthcheck_use_the_named_volume(self):
        self.assertEqual(bot.STATE_PATH,Path('/webui-data/state.json'))
        self.assertEqual(runtime.STATE_PATH,bot.STATE_PATH)
        self.assertEqual(worker.HEALTH_PATH,Path('/webui-data/worker-health.json'))
        self.assertEqual(healthcheck.healthy.__defaults__[0],worker.HEALTH_PATH)


class AtomicFileTests(TestCase):
    def test_atomic_writers_are_shared_private_and_preserve_previous_data_on_error(self):
        import activity
        import configuration
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'nested'/'data.json'
            self.assertIs(activity.write_json_atomic,storage.write_json_atomic)
            self.assertIs(configuration.write_json_atomic,storage.write_json_atomic)
            storage.write_json_atomic(path,{'value':'€'})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with patch.object(storage.os,'replace',side_effect=OSError('full disk')):
                with self.assertRaises(OSError):storage.write_json_atomic(path,{'value':'new'})
            self.assertEqual(json.loads(path.read_text()),{'value':'€'})
            self.assertEqual([p.name for p in path.parent.iterdir()],['data.json'])
