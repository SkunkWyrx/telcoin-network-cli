import base64
import copy
import importlib.util
import json
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/validators'))
from profile import load_profile, validate
from deploy import cloud_init, parameters, validate_phase, main, configure_network
spec=importlib.util.spec_from_file_location('validator_guest',ROOT/'scripts/validators/guest.py')
guest=importlib.util.module_from_spec(spec);spec.loader.exec_module(guest)


class ValidatorDeploymentTests(unittest.TestCase):
    def profile(self,network='adiri'):
        return load_profile(ROOT/'infra/validators'/(network+'.json'))

    def test_adiri_starts_at_50_gib_while_mainnet_remains_100(self):
        for network,size in [('adiri',50),('mainnet',100)]:
            c=self.profile(network)
            values=parameters(c,'ssh-ed25519 '+base64.b64encode(b'public-test-key').decode(),'11111111-1111-1111-1111-111111111111')['parameters']
            self.assertEqual(values['executionSize']['value'],size)
            self.assertEqual(values['consensusSize']['value'],size)
            self.assertTrue(values['autoGrow']['value'])
            data=base64.b64decode(values['customData']['value']).decode()
            self.assertNotIn('TN_BLS_PASSPHRASE=',data)
            self.assertNotIn('telcoin-observer.service',data)
            self.assertIn('/etc/oprail-validator/profile.json',data)
            self.assertEqual(c['storage']['auto_grow']['increment_gb'],50)

    def test_network_resource_and_secret_boundaries_cannot_be_crossed(self):
        for field in ['resource_group','vault','approval']:
            c=self.profile()
            if field=='resource_group':c[field]='oprail-observer-adiri'
            elif field=='vault':c['identity']['passphrase_vault']='oprail-val-mainnet-keys'
            else:c['operations']['approval_environment']='validator-operations-mainnet'
            with self.subTest(field=field),self.assertRaises(ValueError):validate(c)

    def test_mainnet_refuses_open_ingress_and_missing_identity_inputs(self):
        c=self.profile('mainnet');c['networking']['p2p_source_cidrs']=['0.0.0.0/0']
        with self.assertRaises(ValueError):validate(c)
        c=self.profile('mainnet')
        with self.assertRaises(ValueError):validate_phase(c,'prepare')
        c['identity']['execution_address']='0x'+'1'*40;c['release']['tag']='v1.0.0'
        with self.assertRaises(ValueError):validate_phase(c,'finalize',backup=False)
        c['networking']['p2p_source_cidrs']=['192.0.2.1/32']
        validate_phase(c,'finalize',backup=True)

    def test_plans_have_no_azure_side_effects(self):
        for network in ['adiri','mainnet']:
            with patch('sys.argv',['deploy','--network',network]),patch('deploy.az') as azure,patch('builtins.print'):
                main();azure.assert_not_called()

    def test_mainnet_cannot_load_adiri_profile_even_in_plan_mode(self):
        with patch('sys.argv',['deploy','--network','mainnet','--config',str(ROOT/'infra/validators/adiri.json')]),patch('deploy.az') as azure:
            with self.assertRaises(ValueError):main()
            azure.assert_not_called()

    def test_existing_vm_is_retained_without_template_redeployment(self):
        c=self.profile()
        node={'name':'adiri-validator-1','tags':{'telcoin-role':'validator','telcoin-chain':'adiri'}}
        responses=[{'id':c['subscription']},True,{'tags':{'purpose':'oprail-validator','network':'adiri'}},[node]]
        with patch('sys.argv',['deploy','--network','adiri','--phase','provision','--apply']),patch('deploy.az',side_effect=responses) as azure,patch('builtins.print'):
            main()
            self.assertFalse(any(call.args[:3]==('deployment','group','create') for call in azure.call_args_list))

    def test_p2p_rule_never_opens_rpc_or_other_network_resources(self):
        c=self.profile();c['networking']['p2p_source_cidrs']=['192.0.2.1/32']
        with patch('deploy.az',side_effect=[{'tags':{'purpose':'oprail-validator','network':'adiri'}},{}]) as azure:
            configure_network(c,'adiri-validator-1')
        args=azure.call_args.args
        self.assertIn('oprail-validator-adiri',args)
        self.assertEqual(args[-2:],('49590','49594'))
        self.assertNotIn('8545',args)
        self.assertNotIn('observer',args)

    def test_guest_keygen_uses_correct_network_and_never_forces_keys(self):
        for network,tag in [('adiri','v0.17.0-adiri'),('mainnet','v1.0.0')]:
            c=self.profile(network);c['identity']['execution_address']='0x'+'1'*40;c['release']['tag']=tag
            args=guest.setup_args(c,'keygen','192.0.2.1')
            self.assertEqual(args[args.index('--network')+1],c['release']['scripts_network'])
            self.assertIn('--passphrase-stdin',args)
            self.assertIn('--no-public-rpc',args)
            self.assertNotIn('--force',args)
            self.assertNotIn('--allow-no-tpm',args)

    def test_private_installer_failure_keeps_password_out_of_error_and_public_output(self):
        with tempfile.TemporaryDirectory() as d,patch.object(guest,'LOG',Path(d)/'log'),patch.object(
                guest.subprocess,'run',return_value=Mock(returncode=1,stdout=b'secret-value',stderr=b'failed')):
            with self.assertRaises(RuntimeError) as error:guest.private_run(['setup'],b'secret-value\n')
            self.assertNotIn('secret-value',str(error.exception))
            self.assertNotIn(b'secret-value',guest.LOG.read_bytes())
            self.assertEqual(guest.LOG.stat().st_mode & 0o777,0o600)

    def test_cloud_init_does_not_generate_keys_or_start_validator(self):
        text=cloud_init(self.profile())
        commands=text.split('runcmd:\n',1)[1]
        self.assertNotIn('keygen',commands)
        self.assertNotIn('telcoin.service',commands)
        self.assertNotIn('telcoin-observer',commands)
        self.assertIn('chrony',commands)
        self.assertIn('telcoin-disk-growth.timer',commands)

    def test_template_uses_retained_disks_static_ip_and_no_observer_load_balancer(self):
        text=(ROOT/'infra/validators/main.bicep').read_text()
        self.assertIn("publicIPAllocationMethod: 'Static'",text)
        self.assertIn("deleteOption: 'Detach'",text)
        self.assertIn("securityType: 'TrustedLaunch'",text)
        self.assertNotIn('loadBalancer',text)
        self.assertNotIn('oprail-observer-',text)
        self.assertIn('Deny-Other-Inbound',text)


    def test_prepare_retry_preserves_keys_and_rejects_changed_public_identity(self):
        c=self.profile();c['identity']['execution_address']='0x'+'1'*40;c['release']['tag']='v0.17.0-adiri'
        state={'network':'adiri','address':c['identity']['execution_address'],'ip':'192.0.2.1',
               'tag':c['release']['tag'],'phase':'prepared','node_info_sha256':hashlib.sha256(b'original').hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config';config.write_text(json.dumps(c))
            saved=Path(directory)/'state';saved.write_text(json.dumps(state))
            with patch.object(guest,'CONFIG',config),patch.object(guest,'STATE',saved),patch.object(guest,'mounts'),patch.object(guest,'private_run') as run,patch.object(guest,'secret') as secret,patch('sys.argv',['guest','--phase','prepare','--expected-network','adiri','--public-ip','192.0.2.1']),patch('builtins.print'):
                with patch.object(Path,'read_bytes',return_value=b'original'):
                    guest.main()
                run.assert_not_called();secret.assert_not_called()
                with patch.object(Path,'read_bytes',return_value=b'changed'),self.assertRaisesRegex(ValueError,'public identity changed'):
                    guest.main()
                run.assert_not_called();secret.assert_not_called()

    def test_wrong_reference_chain_blocks_finalize_before_network_or_node_changes(self):
        c=self.profile('mainnet');c['identity']['execution_address']='0x'+'1'*40;c['release']['tag']='v1.0.0'
        c['networking']['p2p_source_cidrs']=['192.0.2.1/32']
        node={'tags':{'telcoin-role':'validator','telcoin-chain':'mainnet'}}
        responses=[{'id':c['subscription']},True,{'tags':{'purpose':'oprail-validator','network':'mainnet'}},node]
        with patch('deploy.load_profile',return_value=c),patch('deploy.az',side_effect=responses) as azure,patch('deploy.configure_network') as network,patch('deploy.urllib.request.urlopen') as rpc,patch('sys.argv',['deploy','--network','mainnet','--phase','finalize','--apply','--acknowledge-key-backup']):
            rpc.return_value.__enter__.return_value=io.StringIO('{"result":"0x7e1"}')
            with self.assertRaisesRegex(ValueError,'another chain'):main()
            network.assert_not_called()
            self.assertFalse(any('run-command' in call.args for call in azure.call_args_list))


    def test_adiri_inbound_peers_are_exact_hosts_and_mainnet_is_independent(self):
        expected={'35.203.41.59/32','34.105.191.161/32','34.102.122.126/32',
                  '34.141.203.172/32','34.40.242.29/32','104.43.128.43/32'}
        self.assertEqual(set(self.profile()['networking']['p2p_source_cidrs']),expected)
        self.assertEqual(self.profile('mainnet')['networking']['p2p_source_cidrs'],[])
        values=parameters(self.profile(),'ssh-ed25519 '+base64.b64encode(b'public-test-key').decode(),
                          '11111111-1111-1111-1111-111111111111')['parameters']
        self.assertEqual(set(values['p2pSources']['value']),expected)
