import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('validator_profile', ROOT / 'scripts/validators/profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class ValidatorProfileTests(unittest.TestCase):
    def load(self, network):
        return profile.load_profile(ROOT / 'infra/validators' / (network + '.json'))

    def test_environments_have_separate_groups_networks_and_release_packages(self):
        adiri, mainnet = self.load('adiri'), self.load('mainnet')
        self.assertNotEqual(adiri['resource_group'], mainnet['resource_group'])
        self.assertNotEqual(adiri['networking']['address_prefix'], mainnet['networking']['address_prefix'])
        self.assertNotEqual(adiri['release']['image_repository'], mainnet['release']['image_repository'])
        self.assertNotEqual(adiri['operations']['runner_label'], mainnet['operations']['runner_label'])

    def test_wrong_network_features_and_tags_are_rejected(self):
        p = self.load('mainnet')
        for field, value in [('features', 'adiri'), ('tag', 'v0.17.0-adiri'), ('chain_id', '0x7e1')]:
            with self.subTest(field=field):
                changed = copy.deepcopy(p)
                changed['release'][field] = value
                with self.assertRaises(ValueError):
                    profile.validate(changed)

    def test_public_rpc_and_overlapping_management_network_are_rejected(self):
        p = self.load('adiri')
        p['networking']['public_rpc'] = True
        with self.assertRaises(ValueError):
            profile.validate(p)
        p = self.load('adiri')
        p['networking']['management_subnet']['prefix'] = p['networking']['node_subnet']['prefix']
        with self.assertRaises(ValueError):
            profile.validate(p)

    def test_normal_cli_can_preview_larger_disks_without_changing_the_profile(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/validators/profile.py'),
                                 '--network', 'mainnet', '--execution-disk-gb', '1024',
                                 '--consensus-disk-gb', '256'], capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output['storage']['execution']['size_gb'], 1024)
        self.assertEqual(output['storage']['consensus']['size_gb'], 256)
        self.assertEqual(output['node_names'], ['mainnet-validator-1'])
        self.assertEqual(self.load('mainnet')['storage']['execution']['size_gb'], 256)

    def test_capacity_cannot_be_reduced_below_configured_iops_support(self):
        p = self.load('mainnet')
        p['storage']['execution']['size_gb'] = 6
        with self.assertRaises(ValueError):
            profile.validate(p)

    def test_observer_identity_and_replacement_are_not_inherited(self):
        for network in ['adiri', 'mainnet']:
            p = self.load(network)
            self.assertEqual(p['identity']['execution_address'], '')
            self.assertTrue(p['identity']['preserve_registered_keys'])
            self.assertTrue(p['operations']['preserve_consensus_signing_state'])
            self.assertFalse(p['operations']['automatic_staking'])
            self.assertFalse(p['operations']['automatic_activation'])


if __name__ == '__main__':
    unittest.main()
