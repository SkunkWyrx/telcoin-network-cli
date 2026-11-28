import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/pool'))
from bootstrap import bootstrap, validate_release
from fleet import Fleet
from replacement import replace_fresh

IMAGE = 'registry.azurecr.io/telcoin-observer/mainnet@sha256:' + 'a' * 64


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.template = Path(self.directory.name) / 'template.env'
        self.template.write_text('VM_SIZE=Standard_D4s_v6\n')

    def fleet(self, count):
        f = Mock()
        f.apply = True
        f.state = Path(self.directory.name)
        f.c = {'network': 'mainnet', 'registry': 'registry', 'repository': 'telcoin-observer/mainnet',
               'nodes': [{'name': 'old-' + str(n), 'ordinal': n, 'host': 'old-' + str(n)}
                         for n in range(1, count + 1)]}
        f.save.side_effect = lambda name, body: (f.state / name).write_text(json.dumps(body))
        def add(env_file, image, fresh):
            self.assertTrue(fresh)
            self.assertEqual(image, IMAGE)
            name = next(line.split('=', 1)[1] for line in Path(env_file).read_text().splitlines()
                        if line.startswith('VM_NAME='))
            f.c['nodes'].append({'name': name, 'host': name, 'ordinal': max(n['ordinal'] for n in f.c['nodes']) + 1})
        def remove(name):
            self.assertGreater(len(f.c['nodes']), 1)
            f.c['nodes'] = [n for n in f.c['nodes'] if n['name'] != name]
        f.add.side_effect = add
        f.remove.side_effect = remove
        return f

    def test_one_and_two_node_fleets_preserve_count_and_replace_every_node(self):
        for count in (1, 2):
            with self.subTest(count=count):
                f = self.fleet(count)
                replace_fresh(f, IMAGE, self.template)
                self.assertEqual(len(f.c['nodes']), count)
                self.assertTrue(all(not n['name'].startswith('old-') for n in f.c['nodes']))
                self.assertEqual(f.add.call_count, count)
                self.assertEqual(f.remove.call_count, count)

    def test_sync_failure_keeps_original_and_retry_reuses_replacement(self):
        f = self.fleet(2)
        original = copy.deepcopy(f.c['nodes'])
        f.wait_synced.side_effect = TimeoutError('catching up')
        with self.assertRaises(TimeoutError):
            replace_fresh(f, IMAGE, self.template)
        f.remove.assert_not_called()
        self.assertTrue(all(n in f.c['nodes'] for n in original))
        f.wait_synced.side_effect = None
        replace_fresh(f, IMAGE, self.template)
        self.assertEqual(f.add.call_count, 2)
        self.assertEqual(len(f.c['nodes']), 2)

    def test_failed_provision_is_not_blindly_repeated(self):
        f = self.fleet(1)
        f.add.side_effect = RuntimeError('deployment interrupted')
        with self.assertRaises(RuntimeError):
            replace_fresh(f, IMAGE, self.template)
        with self.assertRaisesRegex(ValueError, 'Provisioning was interrupted'):
            replace_fresh(f, IMAGE, self.template)
        self.assertEqual(f.add.call_count, 1)
        f.remove.assert_not_called()

    def test_active_rollout_cannot_switch_images(self):
        f = self.fleet(1)
        f.wait_synced.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            replace_fresh(f, IMAGE, self.template)
        with self.assertRaisesRegex(ValueError, 'same image'):
            replace_fresh(f, IMAGE.replace('a' * 64, 'b' * 64), self.template)

    def test_issuer_handoff_failure_preserves_original(self):
        f = self.fleet(1)
        f.c['public_rpc'] = {'issuer': 'old-1'}
        f.handoff_issuer.side_effect = RuntimeError('renewal verification failed')
        with self.assertRaises(RuntimeError):
            replace_fresh(f, IMAGE, self.template)
        f.remove.assert_not_called()
        f.handoff_issuer.assert_called_once()

    def test_wrong_network_image_is_rejected_before_provisioning(self):
        f = self.fleet(1)
        with self.assertRaises(ValueError):
            replace_fresh(f, IMAGE.replace('/mainnet@', '/adiri@'), self.template)
        f.add.assert_not_called()


class BootstrapTests(unittest.TestCase):
    def test_network_release_channels_are_separate(self):
        validate_release('mainnet', 'v1.0.0')
        validate_release('adiri', 'v1.0.0-adiri')
        for network, tag in [('mainnet', 'v1.0.0-adiri'), ('mainnet', 'v1.0.0-rc1'), ('adiri', 'v1.0.0')]:
            with self.assertRaises(ValueError):
                validate_release(network, tag)

    def test_wrong_reference_chain_creates_no_resources(self):
        with tempfile.TemporaryDirectory() as directory, patch('bootstrap.Fleet') as cls, patch('bootstrap.rpc', return_value='0x7e1'):
            cls.return_value.state = Path(directory)
            cls.return_value.c = {'nodes': []}
            with self.assertRaisesRegex(ValueError, 'different network'):
                bootstrap({'network': 'mainnet', 'chain_id': '0x2db', 'resource_group': 'mainnet'},
                          'node.env', 'v1.0.0', 'https://reference.example', apply=True)
            cls.return_value.az.assert_not_called()
            cls.return_value.build.assert_not_called()

class FreshProvisioningTests(unittest.TestCase):
    def test_snapshot_restore_preserves_capacity_floor_and_reports_larger_disks(self):
        for snapshot_size in (20, 50, 512):
            with self.subTest(snapshot_size=snapshot_size), tempfile.TemporaryDirectory() as directory:
                settings = Path(directory) / 'node.env'
                settings.write_text('VM_NAME=new-node\nVM_DNS_LABEL=new-node\n')
                f = object.__new__(Fleet)
                f.apply, f.state = True, Path(directory)
                f.c = {'network': 'mainnet', 'pool': 'mainnet', 'subscription': 'sub',
                       'resource_group': 'rg', 'registry': 'registry', 'registry_resource_group': 'rg',
                       'repository': 'telcoin-observer/mainnet', 'location': 'centralus',
                       'chain_id': '0x2db', 'reference_rpc': 'https://reference.example', 'nodes': [],
                       'storage': {'execution_size_gb': 50, 'consensus_size_gb': 50}}
                f.az = Mock(return_value={'diskSizeGB': snapshot_size})
                f.subnet_id, f.backend_id, f.wait_synced = Mock(return_value='subnet'), Mock(return_value='backend'), Mock()
                prefix = '/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/snapshots/'
                pair = {'execution': prefix+'execution', 'consensus': prefix+'consensus'}
                with patch('fleet.execute', side_effect=[json.dumps({'VM_NAME': 'new-node', 'VM_DNS_LABEL': 'new-node'}), '']) as execute, \
                     patch('builtins.print') as output:
                    f.add(str(settings), IMAGE, seed_pair=pair, serve_public=False)
                    env = execute.call_args_list[1].kwargs['env']
                    self.assertEqual(env['EXECUTION_DISK_SIZE_GB'], str(max(50, snapshot_size)))
                    self.assertEqual(env['CONSENSUS_DISK_SIZE_GB'], str(max(50, snapshot_size)))
                    notices = [str(c) for c in output.call_args_list if 'cannot shrink a restore' in str(c)]
                    self.assertEqual(len(notices), 2 if snapshot_size > 50 else 0)

    def test_pool_sizing_overrides_inherited_deployment_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / 'node.env'
            settings.write_text('VM_NAME=new-node\nVM_DNS_LABEL=new-node\n')
            f = object.__new__(Fleet)
            f.apply, f.state = True, Path(directory)
            f.c = {'network': 'mainnet', 'pool': 'mainnet', 'subscription': 'sub',
                   'resource_group': 'rg', 'registry': 'registry', 'registry_resource_group': 'rg',
                   'repository': 'telcoin-observer/mainnet',
                   'location': 'centralus', 'chain_id': '0x2db',
                   'reference_rpc': 'https://reference.example', 'nodes': [],
                   'compute': {'vm_size': 'Standard_D2s_v6'}, 'storage': {'iops': 3000}}
            f.subnet_id, f.backend_id, f.wait_synced = Mock(return_value='subnet'), Mock(return_value='backend'), Mock()
            with patch.dict('os.environ', {'VM_SIZE': 'Standard_D4s_v6', 'DATA_DISK_IOPS': '5000'}), \
                 patch('fleet.execute', side_effect=[json.dumps({'VM_NAME': 'new-node', 'VM_DNS_LABEL': 'new-node'}), '']) as execute:
                f.add(str(settings), IMAGE, fresh=True)
                env = execute.call_args_list[1].kwargs['env']
                self.assertEqual(env['VM_SIZE'], 'Standard_D2s_v6')
                self.assertEqual(env['DATA_DISK_IOPS'], '3000')

    def test_fresh_add_never_looks_up_snapshots_and_clears_restore_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / 'node.env'
            settings.write_text('VM_NAME=new-node\nVM_DNS_LABEL=new-node\n')
            f = object.__new__(Fleet)
            f.apply = True
            f.state = Path(directory)
            f.c = {'network': 'mainnet', 'pool': 'mainnet', 'subscription': 'sub',
                   'resource_group': 'rg', 'registry': 'registry', 'registry_resource_group': 'rg',
                   'repository': 'telcoin-observer/mainnet', 'location': 'centralus',
                   'chain_id': '0x2db', 'reference_rpc': 'https://reference.example', 'nodes': []}
            f.pairs = Mock(side_effect=AssertionError('Fresh deployments must not read snapshots'))
            f.subnet_id = Mock(return_value='subnet')
            f.backend_id = Mock(return_value='backend')
            f.wait_synced = Mock()
            with patch('fleet.execute', side_effect=[json.dumps({'VM_NAME': 'new-node', 'VM_DNS_LABEL': 'new-node'}), '']) as execute:
                f.add(str(settings), IMAGE, fresh=True)
                env = execute.call_args_list[1].kwargs['env']
                self.assertEqual(env['EXECUTION_SNAPSHOT_ID'], '')
                self.assertEqual(env['CONSENSUS_SNAPSHOT_ID'], '')
                self.assertEqual(env['CHAIN'], 'mainnet')
            self.assertEqual(json.loads((f.state / 'nodes.json').read_text())['nodes'][0]['name'], 'new-node')

class RetirementTests(unittest.TestCase):
    def test_partial_deletion_retry_uses_saved_manifest_and_skips_deleted_vm(self):
        with tempfile.TemporaryDirectory() as directory:
            f = object.__new__(Fleet)
            f.state = Path(directory)
            f.apply = True
            old = {'name': 'old', 'ordinal': 1, 'host': 'old'}
            new = {'name': 'new', 'ordinal': 2, 'host': 'new'}
            f.c = {'nodes': [old, new], 'resource_group': 'rg', 'drain_seconds': 0}
            f.synced = Mock(return_value=True)
            f.ssh = Mock()
            f.vm = Mock(return_value={'id': '/vm', 'networkProfile': {'networkInterfaces': [{'id': '/nic'}]},
                                     'storageProfile': {'osDisk': {'managedDisk': {'id': '/os'}},
                                                        'dataDisks': [{'managedDisk': {'id': '/db'}}]}})
            f.az = Mock(side_effect=[{'id': '/nic', 'ipConfigurations': []},
                                     [{'id': '/vm'}, {'id': '/nic'}, {'id': '/os'}, {'id': '/db'}],
                                     None, RuntimeError('NIC deletion failed')])
            with self.assertRaises(RuntimeError):
                f.remove('old')
            self.assertIn(old, f.c['nodes'])
            f.az = Mock(side_effect=[[{'id': '/nic'}, {'id': '/os'}, {'id': '/db'}], None, None, None])
            f.remove('old')
            self.assertEqual(f.c['nodes'], [new])
            f.vm.assert_called_once()
            self.assertEqual(f.ssh.call_count, 2)
            self.assertFalse(any(call.args[:2] == ('vm', 'delete') for call in f.az.call_args_list))


class IssuerHandoffTests(unittest.TestCase):
    def test_handoff_checks_only_configured_active_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            f = object.__new__(Fleet)
            f.state = Path(directory)
            f.c = {'subscription': 'sub', 'resource_group': 'rg',
                   'public_rpc': {'issuer': 'old', 'vault': 'vault', 'secret': 'bundle',
                                  'certificate_name': 'adiri-rpc-dual'}}
            f.vm = Mock(side_effect=[{'identity': {'principalId': 'old-id'}}, {'identity': {'principalId': 'new-id'}}])
            f.az = Mock(return_value=[])
            f.ssh = Mock(side_effect=['', '', 'encoded-archive', ''])
            f.handoff_issuer({'name': 'old'}, {'name': 'new'})
            self.assertIn('--cert-name adiri-rpc-dual', f.ssh.call_args.args[1])
            self.assertIn('--no-random-sleep-on-renew', f.ssh.call_args.args[1])
            self.assertEqual(f.c['public_rpc']['issuer'], 'new')
            self.assertEqual(json.loads((f.state / 'issuer.json').read_text()), {'issuer': 'new'})

    def test_failed_renewal_restores_old_issuer_timer_and_keeps_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            f = object.__new__(Fleet)
            f.state = Path(directory)
            f.c = {'subscription': 'sub', 'resource_group': 'rg',
                   'public_rpc': {'issuer': 'old', 'vault': 'vault', 'secret': 'bundle'}}
            f.vm = Mock(side_effect=[{'identity': {'principalId': 'old-id'}}, {'identity': {'principalId': 'new-id'}}])
            f.az = Mock(return_value=[])
            f.ssh = Mock(side_effect=['', '', 'encoded-archive', RuntimeError('renewal failed'), '', ''])
            with self.assertRaises(RuntimeError):
                f.handoff_issuer({'name': 'old'}, {'name': 'new'})
            self.assertEqual(f.c['public_rpc']['issuer'], 'old')
            self.assertFalse((f.state / 'issuer.json').exists())
            self.assertIn('systemctl start oprail-rpc-renew.timer', f.ssh.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
