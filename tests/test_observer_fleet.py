import contextlib
import io
import json
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('fleet', Path(__file__).resolve().parents[1] / 'scripts/watch-observers.py')
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)


class FleetTests(unittest.TestCase):
    def test_disabled_endpoint_is_not_queried(self):
        with patch.object(fleet.sync, 'height') as height:
            result = fleet.check({'name': 'one', 'tags': {'telcoin-rpc-enabled': 'false'}}, 10, 3)
            self.assertEqual(result[3], 'RPC DISABLED')
            height.assert_not_called()

    def test_missing_endpoint(self):
        self.assertEqual(fleet.check({'name': 'one'}, 10, 3)[3], 'NO RPC URL')

    def test_rpc_failure_is_unknown(self):
        with patch.object(fleet.sync, 'height', side_effect=TimeoutError):
            self.assertEqual(fleet.check({'name': 'one', 'tags': {'telcoin-rpc-url': 'https://example.com'}}, 10, 3)[3], 'RPC UNAVAILABLE')

    def test_comparison(self):
        resource = {'name': 'one', 'tags': {'telcoin-rpc-url': 'https://example.com'}}
        with patch.object(fleet.sync, 'height', return_value=100):
            self.assertEqual(fleet.check(resource, 120, 3), ('one', '100', '20', 'BEHIND'))
            self.assertEqual(fleet.check(resource, None, 3)[3], 'REFERENCE UNAVAILABLE')
            self.assertEqual(fleet.check(resource, 101, 3)[3], 'NEAR HEAD')
            self.assertEqual(fleet.check(resource, 90, 3)[3], 'AHEAD OF REFERENCE')


class DiscoveryTests(unittest.TestCase):
    def test_group_filter_does_not_use_cli_tag_and_ignores_os_disk(self):
        tags = {'telcoin-role': 'observer', 'telcoin-chain': 'adiri', 'telcoin-rpc-url': 'https://example.com'}
        resources = [{'name': 'node', 'type': 'Microsoft.Compute/virtualMachines', 'tags': tags},
                     {'name': 'os-disk', 'type': 'Microsoft.Compute/disks', 'tags': tags}]
        with patch('sys.argv', ['watch-observers.py', '--resource-group', 'test']), \
             patch.object(fleet.subprocess, 'check_output', return_value=json.dumps(resources)) as discover, \
             patch.object(fleet.sync, 'height', return_value=100), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(fleet.main(), 0)
        self.assertNotIn('--tag', discover.call_args[0][0])
        self.assertIn('--resource-group', discover.call_args[0][0])
        self.assertNotIn('os-disk', output.getvalue())
