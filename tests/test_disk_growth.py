import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('disk_growth', ROOT / 'infra/vm/grow-disks.py')
growth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(growth)


class DiskGrowthTests(unittest.TestCase):
    config = {'vm_id': '/vm/observer', 'threshold_percent': 75, 'increment_gb': 50}
    disk = {'id': '/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/disks/observer-execution',
            'mount': '/var/lib/telcoin/db', 'lun': 0}

    def resource(self, size=50):
        return {'managedBy': '/vm/observer', 'sku': {'name': 'PremiumV2_LRS'},
                'properties': {'diskSizeGB': size, 'provisioningState': 'Succeeded'}}

    def test_threshold_grows_in_fixed_50_gib_steps(self):
        self.assertEqual(growth.next_size(50, 74.9, 75, 50), 50)
        self.assertEqual(growth.next_size(50, 75, 75, 50), 100)
        self.assertEqual(growth.next_size(100, 90, 75, 50), 150)

    def test_growth_changes_capacity_and_expands_filesystem(self):
        state = {}
        usage = SimpleNamespace(f_blocks=100, f_bfree=25, f_bavail=25)
        with patch.object(growth, 'verify_device', return_value=Path('/dev/nvme0n2')), patch.object(
                growth, 'expand_filesystem') as resize, patch.object(growth.os, 'statvfs', return_value=usage), patch.object(
                growth, 'request', side_effect=[self.resource(), {}, self.resource(100)]) as arm:
            growth.grow_disk(self.config, self.disk, 'token', state)
        self.assertEqual(arm.call_args_list[1].args[2], {'properties': {'diskSizeGB': 100}})
        self.assertEqual(resize.call_args_list[-1].args, (Path('/dev/nvme0n2'), 100))
        self.assertEqual(state[self.disk['id']], 100)

    def test_retry_finishes_filesystem_after_interrupted_azure_resize(self):
        state = {self.disk['id']: 50}
        usage = SimpleNamespace(f_blocks=100, f_bfree=60, f_bavail=60)
        with patch.object(growth, 'verify_device', return_value=Path('/dev/nvme0n2')), patch.object(
                growth, 'expand_filesystem') as resize, patch.object(growth.os, 'statvfs', return_value=usage), patch.object(
                growth, 'request', return_value=self.resource(100)) as arm:
            growth.grow_disk(self.config, self.disk, 'token', state)
        arm.assert_called_once()
        resize.assert_called_once_with(Path('/dev/nvme0n2'), 100)
        self.assertEqual(state[self.disk['id']], 100)

    def test_wrong_attachment_and_background_copy_never_resize(self):
        foreign = self.resource()
        foreign['managedBy'] = '/vm/another'
        copying = self.resource()
        copying['properties']['completionPercent'] = 80
        for resource in [foreign, copying]:
            with self.subTest(resource=resource), patch.object(growth, 'verify_device'), patch.object(
                    growth, 'request', return_value=resource) as arm, patch.object(growth, 'expand_filesystem') as resize:
                with self.assertRaises(ValueError):
                    growth.grow_disk(self.config, self.disk, 'token', {})
                arm.assert_called_once()
                resize.assert_not_called()

    def test_missing_mount_fails_before_any_azure_write(self):
        with patch.object(growth.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(ValueError, 'mount is missing'):
                growth.verify_device('/var/lib/telcoin/db', 0)

    def test_failed_filesystem_growth_keeps_last_successful_size(self):
        state = {self.disk['id']: 50}
        with patch.object(growth, 'verify_device'), patch.object(growth, 'request', return_value=self.resource(100)), patch.object(
                growth, 'expand_filesystem', side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):
                growth.grow_disk(self.config, self.disk, 'token', state)
        self.assertEqual(state[self.disk['id']], 50)
