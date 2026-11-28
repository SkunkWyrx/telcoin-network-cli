import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mainnet_preparation', ROOT / 'scripts/mainnet/prepare.py')
preparation = importlib.util.module_from_spec(spec); spec.loader.exec_module(preparation)


class MainnetPreparationTests(unittest.TestCase):
    def test_plan_has_no_azure_calls(self):
        with patch('sys.argv', ['prepare']), patch.object(preparation, 'az') as azure, patch('builtins.print'):
            preparation.main(); azure.assert_not_called()

    def test_cloud_init_cannot_start_node_or_carry_secrets(self):
        text = preparation.cloud_init()
        self.assertLess(len(text.encode()), 65536)
        commands = text.split('runcmd:\n')[1]
        self.assertIn('["systemctl", "mask", "telcoin-observer.service"]', commands)
        self.assertNotIn('TN_BLS_PASSPHRASE', text)
        self.assertNotIn('start-observer', text)
        self.assertNotIn('docker run', commands)
        self.assertNotIn('telcoin-observer.service"]', commands.replace('["systemctl", "mask", "telcoin-observer.service"]', ''))

    def test_runtime_registry_is_not_deployed_and_disks_match_adiri(self):
        text = (ROOT / 'infra/mainnet/prepare.bicep').read_text()
        self.assertIn("['mainnet-observer-1', 'mainnet-observer-2']", text)
        self.assertIn("param vmSize string = 'Standard_D4s_v6'", text)
        self.assertIn('executionDiskSizeGB: 50', text)
        self.assertIn('consensusDiskSizeGB: 50', text)
        self.assertIn('configureRegistryPull: false', text)
        self.assertIn("sshSourceCidr: ''", text)
        self.assertIn('enableFloatingIP: true', text)
        self.assertNotIn('oprail-observer-adiri', text)

    def test_vm_default_behavior_remains_compatible(self):
        text = (ROOT / 'infra/vm/main.bicep').read_text()
        self.assertIn('param configureRegistryPull bool = true', text)
        self.assertIn("param publicBackendPoolId string = ''", text)
        self.assertIn("param publicHttpsFrontendIp string = ''", text)
        self.assertIn("destinationAddressPrefix: '${publicHttpsFrontendIp}/32'", text)

    def test_keygen_has_no_node_command_and_no_force(self):
        text = (ROOT / 'scripts/mainnet/prepare-identity.py').read_text()
        self.assertNotIn("'node',", text)
        self.assertNotIn("'--force'", text)
        self.assertIn("'--network', 'none'", text)
        self.assertIn("'generate', 'observer'", text)
        self.assertIn("'generate', 'pop'", text)
        self.assertIn('refusing overwrite', text)
        self.assertIn('BADSIG', text)
        self.assertIn('Backup readback failed', text)
        self.assertIn("'--log.file.max-files', '0'", text)

    def test_signed_manifest_accepts_exact_release_assets_not_traversal_or_duplicates(self):
        spec = importlib.util.spec_from_file_location('mainnet_identity', ROOT / 'scripts/mainnet/prepare-identity.py')
        identity = importlib.util.module_from_spec(spec); spec.loader.exec_module(identity)
        names = ['IMAGE_DIGEST', 'telcoin-network-v0.18.0-adiri-x86_64-unknown-linux-gnu.tar.gz',
                 'chain-configs/testnet/committee.yaml', 'chain-configs/testnet/genesis.yaml',
                 'chain-configs/testnet/parameters.yaml']
        data = ''.join('a' * 64 + '  ' + name + '\n' for name in names).encode()
        self.assertEqual(set(identity.parse_manifest(data, 'v0.18.0-adiri')), set(names))
        for bad in [data + data, data.replace(b'IMAGE_DIGEST', b'../IMAGE_DIGEST'), data.replace(b'a' * 64, b'invalid', 1)]:
            with self.assertRaises(ValueError): identity.parse_manifest(bad, 'v0.18.0-adiri')


    def test_resume_never_redeploys_a_vm_updating_due_to_run_command(self):
        self.assertEqual(preparation.pending_nodes([
            {'name':'mainnet-observer-1','provisioningState':'Updating'}]), ['mainnet-observer-2'])
        self.assertEqual(preparation.pending_nodes([
            {'name':'mainnet-observer-1','provisioningState':'Succeeded'},
            {'name':'mainnet-observer-2','provisioningState':'Failed'}]), ['mainnet-observer-2'])
        self.assertEqual(preparation.pending_nodes([
            {'name':name,'provisioningState':'Succeeded'} for name in preparation.NODES]), [])
