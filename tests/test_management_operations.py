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

    def test_stale_inventory_blocks_operation(self):
        f = self.fleet()
        f.c['nodes'] = [{'name': 'other'}]
        with self.assertRaises(ValueError): run(f, 'remove', 'other')
        f.remove.assert_not_called()

    def test_final_node_cannot_be_removed(self):
        f = self.fleet()
        with self.assertRaises(ValueError): run(f, 'remove', 'one')
        f.remove.assert_not_called()

    def test_build_never_mutates_membership(self):
        f = self.fleet()
        run(f, 'build', 'refs/tags/v0.16.0-adiri')
        f.az.assert_not_called()
        f.build.assert_called_once()

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
