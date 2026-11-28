import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/pool'))
from fleet import Fleet
from backup_strategy import capture
import snapshot_tick


class MainnetBackupTests(unittest.TestCase):
    def fleet(self):
        f = object.__new__(Fleet)
        f.apply = True
        f.c = {'network': 'mainnet', 'pool': 'mainnet', 'resource_group': 'mainnet-rg',
               'location': 'centralus', 'drain_seconds': 0, 'backup_mode': 'existing-fleet',
               'nodes': [{'name': 'first', 'ordinal': 1}, {'name': 'last', 'ordinal': 2}]}
        f.vm = Mock(return_value={'storageProfile': {'dataDisks': [
            {'lun': 0, 'managedDisk': {'id': 'execution'}},
            {'lun': 1, 'managedDisk': {'id': 'consensus'}}]}})
        f.synced = Mock(return_value=True)
        f.events = []
        def ssh(node, script):
            f.events.append(('ssh', node['name'], script))
            return json.dumps({'ready': True}) if 'curl' in script else ''
        f.ssh = Mock(side_effect=ssh)
        f.wait_synced = Mock(side_effect=lambda node: f.events.append(('synced', node['name'])))
        f.save = Mock()
        f.pairs = Mock(return_value=[{'capture': 'new', 'created': datetime.now(timezone.utc)}])
        def az(*args):
            f.events.append(args)
            if args[:2] == ('vm', 'get-instance-view'):
                return {'instanceView': {'statuses': [{'code': 'PowerState/running'}]}}
            if args[:2] == ('snapshot', 'create'):
                return {'id': args[args.index('-n') + 1]}
            if args[:2] == ('snapshot', 'show'):
                return {'completionPercent': 100, 'provisioningState': 'Succeeded'}
            return {}
        f.az = Mock(side_effect=az)
        return f

    @patch('fleet.time.sleep')
    def test_last_node_only_is_drained_stopped_and_restored_before_publication(self, sleep):
        f = self.fleet()
        capture(f)
        mutations = [c for c in f.ssh.call_args_list if 'curl' not in c.args[1]]
        self.assertEqual({c.args[0]['name'] for c in mutations}, {'last'})
        self.assertIn('noclobber', mutations[0].args[1])
        self.assertTrue(any('systemctl stop telcoin-observer' in c.args[1] for c in f.ssh.call_args_list))
        self.assertEqual([e[-1] for e in f.events if e[:2] == ('vm', 'deallocate')], ['last'])
        restart = next(i for i, e in enumerate(f.events) if e[:2] == ('vm', 'start'))
        creates = [i for i, e in enumerate(f.events) if e[:2] == ('snapshot', 'create')]
        self.assertEqual(len(creates), 2)
        self.assertGreater(restart, max(creates))
        sync = f.events.index(('synced', 'last'))
        clear = next(i for i, e in enumerate(f.events) if e[0] == 'ssh' and 'rm -f /run/telcoin/draining' in e[2])
        complete = next(i for i, e in enumerate(f.events) if e[:2] == ('tag', 'update'))
        self.assertLess(sync, clear)
        self.assertLess(clear, complete)
        f.save.assert_called_once()

    def test_mainnet_never_uses_single_node_downtime_override(self):
        f = self.fleet()
        f.c['nodes'] = [f.c['nodes'][0]]
        f.c['allow_single_node_maintenance'] = True
        with self.assertRaisesRegex(RuntimeError, 'second ready'):
            capture(f)
        f.ssh.assert_not_called()
        self.assertFalse(any(e[:2] == ('vm', 'deallocate') for e in f.events))

    def test_unsynced_remaining_node_prevents_drain(self):
        f = self.fleet()
        f.synced.side_effect = lambda node: node['name'] == 'last'
        with self.assertRaisesRegex(RuntimeError, 'second ready'):
            capture(f)
        f.ssh.assert_not_called()

    def test_synced_but_drained_remaining_node_prevents_source_shutdown(self):
        f = self.fleet()
        f.ssh.side_effect = lambda node, script: json.dumps({'ready': False})
        with self.assertRaisesRegex(RuntimeError, 'second ready'):
            capture(f)
        self.assertTrue(all('curl' in c.args[1] for c in f.ssh.call_args_list))
        self.assertFalse(any(e[:2] == ('vm', 'deallocate') for e in f.events))

    @patch('fleet.time.sleep')
    def test_remaining_node_lost_during_drain_keeps_source_running(self, sleep):
        f = self.fleet()
        f.synced.side_effect = [True, True, False]
        with self.assertRaisesRegex(RuntimeError, 'after drain'):
            capture(f)
        self.assertFalse(any('systemctl stop telcoin-observer' in c.args[1] for c in f.ssh.call_args_list))
        self.assertFalse(any(e[:2] == ('vm', 'deallocate') for e in f.events))
        self.assertIn('rm -f /run/telcoin/draining', f.ssh.call_args_list[-1].args[1])
        self.assertFalse(any(e[:2] == ('vm', 'start') for e in f.events))
        f.wait_synced.assert_not_called()
        f.save.assert_not_called()

    def test_failed_capture_or_restart_never_prunes_old_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'pool.json'
            config.write_text(json.dumps({'automation_enabled': True, 'backup_mode': 'existing-fleet'}))
            f = Mock(); f.c = {'backup_mode': 'existing-fleet'}; f.state = Path(directory)
            f.pairs.return_value = []
            f.snapshot.side_effect = RuntimeError('restart sync failed')
            with patch('snapshot_tick.Fleet', return_value=f), patch.object(sys, 'argv', ['snapshot_tick', '--config', str(config)]):
                with self.assertRaisesRegex(RuntimeError, 'restart sync failed'):
                    snapshot_tick.main()
            f.prune.assert_not_called()

    def test_remaining_public_proxy_must_be_active(self):
        f = self.fleet()
        f.c['public_rpc'] = {'hostname': 'rpc.oprail.com'}
        f.ssh.side_effect = subprocess.CalledProcessError(3, 'inactive proxy')
        with self.assertRaisesRegex(RuntimeError, 'second ready'):
            capture(f)
        self.assertIn('systemctl is-active --quiet telcoin-rpc-tls.service', f.ssh.call_args.args[1])
        self.assertFalse(any(e[:2] == ('vm', 'deallocate') for e in f.events))

    def test_adiri_keeps_its_temporary_observer_strategy(self):
        f = Mock(); f.c = {'network': 'adiri', 'backup_mode': 'temporary-observer'}
        with patch('backup_helper.backup') as helper:
            capture(f, 'source')
            helper.assert_called_once_with(f, source_name='source')
        f.snapshot.assert_not_called()

    def test_manual_source_and_unknown_modes(self):
        f = Mock(); f.c = {'backup_mode': 'existing-fleet'}
        capture(f, 'last')
        f.snapshot.assert_called_once_with('last')
        f.c['backup_mode'] = 'typo'
        f.snapshot.reset_mock()
        with self.assertRaises(ValueError): capture(f)
        f.snapshot.assert_not_called()

    def test_one_off_mainnet_preview_uses_last_node_without_azure(self):
        with tempfile.TemporaryDirectory() as directory:
            config = json.loads((ROOT / 'infra/pool/mainnet.json').read_text())
            config['nodes'] = [{'name': 'first', 'ordinal': 1}, {'name': 'last', 'ordinal': 2}]
            file = Path(directory) / 'mainnet.json'
            file.write_text(json.dumps(config))
            env = dict(os.environ, OBSERVER_STATE_DIR=directory, PATH='')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/pool/backup_now.py'),
                                     '--config', str(file)], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Capture paired data disks from: last', result.stdout)

    def test_production_profile_and_scheduler_remain_launch_gated(self):
        config = json.loads((ROOT / 'infra/pool/mainnet.json').read_text())
        self.assertEqual(config['backup_mode'], 'existing-fleet')
        self.assertFalse(config['allow_single_node_maintenance'])
        self.assertFalse(config['automation_enabled'])
        workflow = (ROOT / '.github/workflows/observer-mainnet-backups.yml').read_text()
        for requirement in ['OBSERVER_MAINNET_ENABLED', 'OBSERVER_MAINNET_AUTOMATION_ENABLED',
                            'observer-operations-mainnet', 'observer-lifecycle-mainnet',
                            '/etc/oprail-observers/mainnet/pool.json']:
            self.assertIn(requirement, workflow)
