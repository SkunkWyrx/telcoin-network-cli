import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/pool'))
from management_operation import validate, reconcile, run

class ManagementTests(unittest.TestCase):
    def fleet(self):
        f = Mock()
        f.c = {'network': 'adiri', 'subscription': 'sub', 'resource_group': 'rg',
               'nodes': [{'name': 'one'}]}
        f.az.side_effect = [
            {'backendIPConfigurations': [{'id': '/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/networkInterfaces/nic/ipConfigurations/ip'}]},
            {'virtualMachine': {'id': '/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/one'}}]
        return f

    def test_rejects_paths_shell_and_mutable_images(self):
        for operation, argument in [('add', '../secret.env'), ('remove', 'one; reboot'),
                                    ('build', 'main'), ('upgrade', 'repo:latest'), ('delete-group', 'rg')]:
            with self.assertRaises(ValueError): validate(operation, argument)

    def test_discovers_actual_members(self):
        self.assertEqual(reconcile(self.fleet()), {'one'})

    def test_azure_duplicate_nic_references_are_not_ip_only_backends(self):
        f = self.fleet()
        pool, nic = list(f.az.side_effect)
        identifier = pool['backendIPConfigurations'][0]['id']
        pool['loadBalancerBackendAddresses'] = [{'networkInterfaceIPConfiguration': {'id': identifier.upper()}}]
        f.az.side_effect = [pool, nic]
        self.assertEqual(reconcile(f), {'one'})
        self.assertEqual(f.az.call_count, 2)

    def test_address_collection_can_supply_nic_identity(self):
        f = self.fleet()
        pool, nic = list(f.az.side_effect)
        pool['loadBalancerBackendAddresses'] = [{'networkInterfaceIPConfiguration': pool.pop('backendIPConfigurations')[0]}]
        f.az.side_effect = [pool, nic]
        self.assertEqual(reconcile(f), {'one'})

    def test_unmapped_ip_address_still_rejected(self):
        f = self.fleet()
        f.az.side_effect = [{'loadBalancerBackendAddresses': [{'ipAddress': '10.52.0.4'}]}]
        with self.assertRaisesRegex(ValueError, 'IP-based'): reconcile(f)

    def test_stale_inventory_blocks_operation(self):
        f = self.fleet()
        f.c['nodes'] = [{'name': 'other'}]
        with self.assertRaises(ValueError): run(f, 'remove', 'other')
        f.remove.assert_not_called()

    def test_final_node_cannot_be_removed(self):
        f = self.fleet()
        with self.assertRaises(ValueError): run(f, 'remove', 'one')
        f.remove.assert_not_called()

    def test_storage_replacement_reuses_promoted_image_without_rebuild(self):
        f = self.fleet()
        with tempfile.TemporaryDirectory() as directory:
            f.state = Path(directory)
            image = 'registry.azurecr.io/telcoin-observer/adiri@sha256:' + 'a' * 64
            (f.state / 'latest-release.json').write_text(json.dumps(
                {'network': 'adiri', 'image': image, 'validated': True}))
            run(f, 'replace', image)
        f.replace_fresh.assert_called_once_with(image)
        f.build.assert_not_called()
        f.upgrade.assert_not_called()

    def test_build_never_mutates_membership(self):
        f = self.fleet()
        run(f, 'build', 'refs/tags/v0.16.0-adiri')
        f.az.assert_not_called()
        f.build.assert_called_once()

    def test_read_only_check_validates_sync_without_node_changes(self):
        f = self.fleet()
        f.synced.return_value = True
        run(f, 'check', 'cluster')
        f.vm.assert_called_once_with(f.c['nodes'][0])
        f.synced.assert_called_once_with(f.c['nodes'][0])
        f.upgrade.assert_not_called()
        f.snapshot.assert_not_called()
        f.remove.assert_not_called()
        f.add.assert_not_called()

    def test_read_only_check_rejects_unsynced_nodes(self):
        f = self.fleet()
        f.synced.return_value = False
        with self.assertRaisesRegex(ValueError, 'not synced'): run(f, 'check', 'cluster')

    def test_unpromoted_upgrade_is_rejected(self):
        f = self.fleet()
        image = 'registry.azurecr.io/telcoin-observer/adiri@sha256:' + 'a' * 64
        with tempfile.TemporaryDirectory() as temp:
            f.state = Path(temp)
            (f.state / 'latest-release.json').write_text(json.dumps({'network': 'adiri', 'image': image, 'validated': False}))
            with self.assertRaises(ValueError): run(f, 'upgrade', image)
        f.upgrade.assert_not_called()

    def test_cross_group_backend_rejected(self):
        f = self.fleet()
        f.az.side_effect = [{'backendIPConfigurations': [{'id': '/subscriptions/sub/resourceGroups/other/providers/Microsoft.Network/networkInterfaces/nic/ipConfigurations/ip'}]}]
        with self.assertRaises(ValueError): reconcile(f)

class TagUpgradeTests(unittest.TestCase):
    def run_trial(self, tmp, failed=False):
        image = 'registry.azurecr.io/telcoin-observer/adiri@sha256:' + 'b' * 64
        f = Mock()
        f.c = {'network': 'adiri'}
        f.state = Path(tmp)
        def build(ref):
            (f.state / 'candidate.json').write_text(json.dumps({'network': 'adiri', 'ref': ref, 'image': image}))
        f.build.side_effect = build
        if failed:
            f.upgrade.side_effect = RuntimeError('Sync gate failed')
        return f, image

    def test_tag_promotes_only_after_successful_rollout(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            f, image = self.run_trial(tmp)
            with patch('management_operation.reconcile', return_value={'one', 'two'}):
                run(f, 'upgrade-tag', 'v0.16.0-adiri')
            f.build.assert_called_once_with('refs/tags/v0.16.0-adiri')
            self.assertEqual([c[0] for c in f.method_calls], ['build', 'upgrade', 'promote'])
            f.promote.assert_called_once_with(image)

    def test_failed_canary_never_promotes(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            f, _ = self.run_trial(tmp, failed=True)
            with patch('management_operation.reconcile', return_value={'one', 'two'}):
                with self.assertRaises(RuntimeError):
                    run(f, 'upgrade-tag', 'v0.16.0-adiri')
            f.promote.assert_not_called()

    def test_recovery_uses_built_candidate_and_explicit_recovery_path(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            f, image = self.run_trial(tmp)
            (f.state / 'candidate.json').write_text(json.dumps({'network': 'adiri', 'ref': 'refs/tags/v0.17.0-adiri', 'image': image}))
            with patch('management_operation.reconcile', return_value={'one', 'two'}):
                run(f, 'recover-tag', 'v0.17.0-adiri')
            f.build.assert_not_called()
            f.upgrade.assert_called_once_with(image, recovery=True)
            f.promote.assert_called_once_with(image)

    def test_recovery_rejects_candidate_for_another_tag(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            f, image = self.run_trial(tmp)
            (f.state / 'candidate.json').write_text(json.dumps({'network': 'adiri', 'ref': 'refs/tags/v0.16.0-adiri', 'image': image}))
            with patch('management_operation.reconcile', return_value={'one', 'two'}):
                with self.assertRaisesRegex(ValueError, 'does not match'): run(f, 'recover-tag', 'v0.17.0-adiri')
            f.upgrade.assert_not_called()

    def test_wrong_network_tag_does_not_build(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            f, _ = self.run_trial(tmp)
            with patch('management_operation.reconcile', return_value={'one', 'two'}):
                with self.assertRaises(ValueError):
                    run(f, 'upgrade-tag', 'v0.16.0')
            f.build.assert_not_called()

    def test_issuer_cannot_be_removed(self):
        from unittest.mock import patch
        f = Mock()
        f.c = {'public_rpc': {'issuer': 'one'}}
        with patch('management_operation.reconcile', return_value={'one', 'two'}):
            with self.assertRaisesRegex(ValueError, 'issuer'):
                run(f, 'remove', 'one')
        f.remove.assert_not_called()


if __name__ == '__main__': unittest.main()

class FreshBranchTests(unittest.TestCase):
    image = 'registry.azurecr.io/telcoin-observer/adiri@sha256:' + 'c' * 64

    def fleet(self, directory, ref='refs/heads/main', network='adiri'):
        f = Mock()
        f.c = {'network': network}
        f.state = Path(directory)
        (f.state / 'candidate.json').write_text(json.dumps(
            {'network': network, 'ref': ref, 'image': self.image, 'commit': 'd' * 40}))
        return f

    def test_consumes_built_digest_and_promotes_after_fresh_rollout(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            f = self.fleet(directory)
            with patch('management_operation.reconcile', return_value={'one'}):
                run(f, 'fresh-ref', 'refs/heads/main')
            self.assertEqual([c[0] for c in f.method_calls], ['replace_fresh', 'promote'])
            f.replace_fresh.assert_called_once_with(self.image)
            f.promote.assert_called_once_with(self.image)

    def test_failed_sync_does_not_promote(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            f = self.fleet(directory)
            f.replace_fresh.side_effect = RuntimeError('Sync gate failed')
            with patch('management_operation.reconcile', return_value={'one'}), self.assertRaises(RuntimeError):
                run(f, 'fresh-ref', 'refs/heads/main')
            f.promote.assert_not_called()
            f.build.assert_not_called()

    def test_wrong_branch_or_network_candidate_is_rejected(self):
        from unittest.mock import patch
        for ref, network in [('refs/heads/other', 'adiri'), ('refs/heads/main', 'mainnet')]:
            with self.subTest(ref=ref, network=network), tempfile.TemporaryDirectory() as directory:
                f = self.fleet(directory, ref, network)
                f.c['network'] = 'adiri'
                with patch('management_operation.reconcile', return_value={'one'}), self.assertRaises(ValueError):
                    run(f, 'fresh-ref', 'refs/heads/main')
                f.replace_fresh.assert_not_called()
                f.promote.assert_not_called()

    def test_mainnet_rejected_before_any_azure_call(self):
        with tempfile.TemporaryDirectory() as directory:
            f = self.fleet(directory, network='mainnet')
            with self.assertRaisesRegex(ValueError, 'limited to Adiri'):
                run(f, 'fresh-ref', 'refs/heads/main')
            f.az.assert_not_called()
            f.replace_fresh.assert_not_called()

    def test_resume_reuses_existing_replacement_without_rebuilding_branch(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            f = self.fleet(directory)
            (f.state / 'replacement.json').write_text(json.dumps(
                {'status': 'running', 'image': self.image, 'steps': []}))
            with patch('management_operation.reconcile', return_value={'one', 'replacement'}):
                run(f, 'fresh-ref', 'refs/heads/main')
            f.replace_fresh.assert_called_once_with(self.image)
            f.build.assert_not_called()
            f.promote.assert_called_once_with(self.image)

    def test_changed_candidate_cannot_resume_another_image(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            f = self.fleet(directory)
            (f.state / 'replacement.json').write_text(json.dumps(
                {'status': 'running', 'image': self.image.replace('c' * 64, 'e' * 64), 'steps': []}))
            with patch('management_operation.reconcile', return_value={'one'}), self.assertRaises(ValueError):
                run(f, 'fresh-ref', 'refs/heads/main')
            f.replace_fresh.assert_not_called()
            f.promote.assert_not_called()

    def test_retirement_retry_validates_image_and_waits_before_old_node_cleanup(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            f = self.fleet(directory)
            new = {'name': 'replacement'}
            f.c['nodes'] = [new]
            (f.state / 'replacement.json').write_text(json.dumps(
                {'status': 'running', 'image': self.image,
                 'steps': [{'stage': 'retiring', 'name': 'replacement', 'old': {'name': 'old'}}]}))
            with patch('management_operation.reconcile', return_value={'replacement'}):
                run(f, 'fresh-ref', 'refs/heads/main')
            self.assertEqual([c[0] for c in f.method_calls],
                             ['wait_synced', 'remove', 'save', 'replace_fresh', 'promote'])
            f.wait_synced.assert_called_once_with(new)
            f.remove.assert_called_once_with('old')
            f.build.assert_not_called()

    def test_wrong_image_blocks_retirement_retry_before_deletion(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            f = self.fleet(directory)
            (f.state / 'replacement.json').write_text(json.dumps(
                {'status': 'running', 'image': self.image.replace('c' * 64, 'e' * 64),
                 'steps': [{'stage': 'retiring', 'name': 'replacement', 'old': {'name': 'old'}}]}))
            with patch('management_operation.reconcile') as reconcile, self.assertRaises(ValueError):
                run(f, 'fresh-ref', 'refs/heads/main')
            f.remove.assert_not_called()
            reconcile.assert_not_called()

    def test_branch_reference_validation(self):
        validate('fresh-ref', 'refs/heads/main')
        for ref in ['main', 'refs/tags/v0.17.0-adiri', 'refs/heads/main;reboot', 'refs/heads/main\n']:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                validate('fresh-ref', ref)
