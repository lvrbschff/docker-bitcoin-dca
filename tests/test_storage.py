"""Volume-only startup preserves pending financial state and fails closed on data loss."""
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
        self.root=Path(self.temp.name);self.volume=self.root/'volume';self.legacy=self.root/'legacy'
        self.legacy.mkdir()

    def put(self, directory, name, value):
        directory.mkdir(parents=True,exist_ok=True)
        (directory/name).write_text(json.dumps(value))

    def test_new_deployment_requires_only_named_volume(self):
        storage.prepare_storage(self.volume,self.root/'unavailable-host-directory')
        self.assertEqual(json.loads((self.volume/'state.json').read_text()),{})
        self.assertTrue((self.volume/storage.MARKER_NAME).exists())
        self.assertEqual((self.volume/'state.json').stat().st_mode & 0o777,0o600)
        self.put(self.volume,'state.json',{'last_order_at':'2026-10-30T10:00:00Z'})
        storage.prepare_storage(self.volume,self.root/'another-missing-directory')
        self.assertIn('last_order_at',json.loads((self.volume/'state.json').read_text()))

    def test_migration_keeps_pending_orders_withdrawals_and_schedule(self):
        state={'cycle':'2026-10-01','spent':'20','pending_order':{'id':'order-1'},
               'pending_withdrawal':{'id':'withdrawal-1'},'pending_withdrawal_context':{'request':{'amount':'.001'}},
               'last_order_at':'2026-10-30T10:00:00Z','withdrawal_armed':False,'schedule':{'interval':300}}
        self.put(self.legacy,'state.json',state)
        self.put(self.legacy,'config.json',{'complete':True,'account':{'password_hash':'existing-hash'}})
        self.put(self.legacy,'activity.json',{'records':[]})
        self.put(self.legacy,'dashboard.json',{'exchange':'KRAKEN'})
        with self.assertLogs(storage.LOG,level='INFO') as logs:storage.prepare_storage(self.volume,self.legacy)
        self.assertIn('state.json','\n'.join(logs.output))
        self.assertEqual(bot.read_state(self.volume/'state.json','2026-10-01'),state)
        self.assertEqual(json.loads((self.legacy/'state.json').read_text()),state)
        for path in self.legacy.iterdir():path.unlink()
        self.legacy.rmdir()
        storage.prepare_storage(self.volume,self.legacy)
        self.assertEqual(bot.read_state(self.volume/'state.json','2026-10-01'),state)

    def test_existing_volume_configuration_wins_over_legacy_config(self):
        self.put(self.volume,'config.json',{'complete':True,'username':'current'})
        self.put(self.legacy,'config.json',{'complete':True,'username':'old'})
        self.put(self.legacy,'state.json',{'pending_order':{'id':'pending'}})
        storage.prepare_storage(self.volume,self.legacy)
        self.assertEqual(json.loads((self.volume/'config.json').read_text())['username'],'current')

    def test_existing_account_without_old_state_does_not_start_fresh(self):
        self.put(self.volume,'config.json',{'complete':True})
        with self.assertRaisesRegex(RuntimeError,'without scheduling state'):
            storage.prepare_storage(self.volume,self.legacy)
        self.assertFalse((self.volume/'state.json').exists())
        self.assertFalse((self.volume/storage.MARKER_NAME).exists())

    def test_legacy_activity_without_state_is_not_treated_as_a_fresh_install(self):
        self.put(self.volume,'activity.json',{'records':[]})
        with self.assertRaisesRegex(RuntimeError,'without scheduling state'):
            storage.prepare_storage(self.volume,self.legacy)
        self.assertFalse((self.volume/storage.MARKER_NAME).exists())

    def test_malformed_or_conflicting_state_is_not_overwritten(self):
        self.put(self.legacy,'state.json',[])
        with self.assertRaises(ValueError):storage.prepare_storage(self.volume,self.legacy)
        self.assertFalse((self.volume/storage.MARKER_NAME).exists())
        self.put(self.legacy,'state.json',{'pending_order':{'id':'old'}})
        self.put(self.volume,'state.json',{'pending_order':{'id':'new'}})
        with self.assertRaisesRegex(RuntimeError,'Conflicting'):
            storage.prepare_storage(self.volume,self.legacy)
        self.assertEqual(json.loads((self.volume/'state.json').read_text())['pending_order']['id'],'new')

    def test_failed_copy_is_retryable_and_never_deletes_the_source(self):
        self.put(self.legacy,'state.json',{'pending_order':{'id':'pending'}})
        with patch.object(storage.os,'replace',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):storage.prepare_storage(self.volume,self.legacy)
        self.assertTrue((self.legacy/'state.json').exists())
        self.assertFalse((self.volume/storage.MARKER_NAME).exists())
        self.assertEqual(list(self.volume.glob('.storage-*')),[])
        storage.prepare_storage(self.volume,self.legacy)
        self.assertEqual(json.loads((self.volume/'state.json').read_text())['pending_order']['id'],'pending')

    def test_ready_volume_never_restores_stale_legacy_state_after_data_loss(self):
        storage.prepare_storage(self.volume,self.legacy)
        (self.volume/'state.json').unlink()
        self.put(self.legacy,'state.json',{'pending_order':{'id':'stale'}})
        with self.assertRaisesRegex(RuntimeError,'scheduling state is missing'):
            storage.prepare_storage(self.volume,self.legacy)
        self.assertFalse((self.volume/'state.json').exists())

    def test_worker_and_healthcheck_use_the_same_named_volume(self):
        self.assertEqual(bot.STATE_PATH,Path('/webui-data/state.json'))
        self.assertEqual(runtime.STATE_PATH,bot.STATE_PATH)
        self.assertEqual(worker.HEALTH_PATH,Path('/webui-data/worker-health.json'))
        self.assertEqual(healthcheck.healthy.__defaults__[0],worker.HEALTH_PATH)
