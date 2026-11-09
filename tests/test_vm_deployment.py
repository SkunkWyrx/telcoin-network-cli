"""Local checks of generated cloud-init and fail-closed mount initialization.

No Azure resources, mounts, or block devices are modified. OS commands are mocked.
Run: python3 -m unittest discover -s tests -p 'test_vm_deployment.py'
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class VmDeploymentTests(unittest.TestCase):
    def setUp(self):
        # Deployment previews must not inherit the operator's real .env, image
        # digest, snapshots or passphrase. Individual tests override ENV_FILE.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Path(directory.name, 'empty.env')
        settings.write_text('')
        environment = patch.dict(os.environ, {'ENV_FILE': str(settings)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_private_pool_preview_contains_digest_and_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp, 'id.pub')
            key.write_text('ssh-ed25519 AAAATEST preview')
            digest = 'sha256:' + 'a' * 64
            env = dict(os.environ, ACR_NAME='testregistry', VM_DNS_LABEL='pool-observer',
                       SSH_SOURCE_CIDR='192.0.2.1/32', SSH_PUBLIC_KEY_FILE=str(key),
                       PREVIEW_DIR=tmp, IMAGE_DIGEST=digest, ACR_RESOURCE_GROUP='registry-group',
                       EXISTING_SUBNET_ID='/subscriptions/test/resourceGroups/pool/providers/Microsoft.Network/virtualNetworks/pool/subnets/observer',
                       BACKEND_POOL_ID='/subscriptions/test/resourceGroups/pool/providers/Microsoft.Network/loadBalancers/rpc/backendAddressPools/observers',
                       PRIVATE_RPC_SOURCE_CIDRS='10.51.0.0/16', EXPECTED_CHAIN_ID='0x7e1',
                       NETWORK_RPC_URL='https://rpc.adiri.tel', PUBLIC_RPC_ENABLED='false')
            subprocess.run(['bash', str(ROOT / 'scripts/deploy-azure-vm.sh'), '--preview'],
                           env=env, check=True, capture_output=True)
            params = json.loads(Path(tmp, 'parameters.json').read_text())['parameters']
            self.assertEqual(params['acrResourceGroup']['value'], 'registry-group')
            self.assertEqual(params['privateRpcSourceCidrs']['value'], ['10.51.0.0/16'])
            self.assertFalse(params['publicRpcEnabled']['value'])
            cloud = base64.b64decode(params['customData']['value']).decode()
            self.assertIn('/etc/telcoin/readiness.json', cloud)
            self.assertIn('/usr/local/lib/telcoin/health.py', cloud)
            payloads = [base64.b64decode(line.split(': ', 1)[1]).decode()
                        for line in cloud.splitlines() if line.startswith('    content: ')]
            self.assertIn('@' + digest, payloads[-1])
            settings = next(p for p in payloads if '"chain_id"' in p and p.startswith('{'))
            self.assertEqual(json.loads(settings)['chain_id'], '0x7e1')

    def test_preview_contains_layout_but_no_passphrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp, 'id.pub')
            key.write_text('ssh-ed25519 AAAATEST preview')
            env = dict(os.environ, ACR_NAME='testregistry', VM_DNS_LABEL='test-observer',
                       SSH_SOURCE_CIDR='192.0.2.1/32', SSH_PUBLIC_KEY_FILE=str(key),
                       VM_SIZE='Standard_D4s_v6', VM_ZONE='1', DATA_DISK_IOPS='5000',
                       DATA_DISK_MBPS='125', PREVIEW_DIR=tmp, TN_BLS_PASSPHRASE='must-not-be-rendered',
                       GIT_TAG='v0.16.0-adiri', IMAGE_TAG='v0.16.0-adiri')
            subprocess.run(['bash', str(ROOT / 'scripts/deploy-azure-vm.sh'), '--preview'],
                           env=env, check=True, capture_output=True)
            params = json.loads(Path(tmp, 'parameters.json').read_text())['parameters']
            cloud = base64.b64decode(params['customData']['value']).decode()
            self.assertEqual(cloud, Path(tmp, 'cloud-init.yaml').read_text())
            self.assertNotIn('must-not-be-rendered', cloud)
            self.assertEqual(params['vmSize']['value'], 'Standard_D4s_v6')
            self.assertEqual(params['availabilityZone']['value'], '1')
            self.assertEqual(params['dataDiskIops']['value'], 5000)
            self.assertEqual(params['dataDiskMbps']['value'], 125)
            self.assertIn('azure-vm-utils', cloud)
            # Check the embedded service, not just the source file.
            payloads = [base64.b64decode(line.split(': ', 1)[1]).decode()
                        for line in cloud.splitlines() if line.startswith('    content: ')]
            service = next(p for p in payloads if '[Unit]' in p)
            for name in ['db', 'consensus-db', 'static_files']:
                self.assertIn('/var/lib/telcoin/' + name, service)
                self.assertIn('mountpoint -q /var/lib/telcoin/' + name, service)
            self.assertIn('RequiresMountsFor=', service)
            self.assertIn('v0.16.0-adiri', payloads[-1])

    def test_env_file_settings_and_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp, 'id.pub')
            key.write_text('ssh-ed25519 AAAATEST preview')
            settings = Path(tmp, 'settings.env')
            settings.write_text(
                "ACR_NAME=testregistry\nVM_DNS_LABEL=from-env-file\n"
                "SSH_SOURCE_CIDR=192.0.2.1/32\n"
                f'SSH_PUBLIC_KEY_FILE="{key}"\n'
                "VM_SIZE=Standard_D8as_v5\nGIT_TAG=file-tag\n"
                "TN_BLS_PASSPHRASE='secret with spaces $literal'\n")
            env = dict(os.environ, ENV_FILE=str(settings), PREVIEW_DIR=tmp)
            for name in ['ACR_NAME', 'VM_DNS_LABEL', 'SSH_SOURCE_CIDR',
                         'SSH_PUBLIC_KEY_FILE', 'VM_SIZE', 'GIT_TAG', 'TN_BLS_PASSPHRASE']:
                env.pop(name, None)
            command = ['bash', str(ROOT / 'scripts/deploy-azure-vm.sh'), '--preview']
            subprocess.run(command, env=env, check=True, capture_output=True)
            params = json.loads(Path(tmp, 'parameters.json').read_text())['parameters']
            self.assertEqual(params['dnsLabel']['value'], 'from-env-file')
            self.assertEqual(params['vmSize']['value'], 'Standard_D8as_v5')
            cloud = Path(tmp, 'cloud-init.yaml').read_text()
            self.assertNotIn('secret with spaces', cloud)
            env.update(VM_DNS_LABEL='explicit-override', GIT_TAG='')
            subprocess.run(command, env=env, check=True, capture_output=True)
            params = json.loads(Path(tmp, 'parameters.json').read_text())['parameters']
            self.assertEqual(params['dnsLabel']['value'], 'explicit-override')
            cloud = Path(tmp, 'cloud-init.yaml').read_text()
            payloads = [base64.b64decode(line.split(': ', 1)[1]).decode()
                        for line in cloud.splitlines() if line.startswith('    content: ')]
            self.assertIn(':branch-build', payloads[-1])

    def test_snapshot_parameters_and_missing_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp, 'id.pub')
            key.write_text('ssh-ed25519 AAAATEST preview')
            settings = Path(tmp, 'settings.env')
            settings.write_text('')
            env = dict(os.environ, ENV_FILE=str(settings), ACR_NAME='testregistry',
                       VM_DNS_LABEL='test-observer', SSH_SOURCE_CIDR='192.0.2.1/32',
                       SSH_PUBLIC_KEY_FILE=str(key), PREVIEW_DIR=tmp,
                       EXECUTION_SNAPSHOT_ID='/subscriptions/test/execution',
                       CONSENSUS_SNAPSHOT_ID='/subscriptions/test/consensus', PUBLIC_RPC_ENABLED='true',
                       PUBLIC_RPC_SOURCE_CIDR='192.0.2.0/24')
            command = ['bash', str(ROOT / 'scripts/deploy-azure-vm.sh'), '--preview']
            subprocess.run(command, env=env, check=True, capture_output=True)
            params = json.loads(Path(tmp, 'parameters.json').read_text())['parameters']
            self.assertTrue(params['publicRpcEnabled']['value'])
            self.assertEqual(params['publicRpcSourceCidr']['value'], '192.0.2.0/24')
            self.assertEqual(params['executionSnapshotId']['value'], env['EXECUTION_SNAPSHOT_ID'])
            self.assertEqual(params['consensusSnapshotId']['value'], env['CONSENSUS_SNAPSHOT_ID'])
            env['CONSENSUS_SNAPSHOT_ID'] = ''
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Provide both snapshot IDs', result.stderr)

    def test_capture_failure_restarts_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            log = directory / 'calls'
            settings = directory / 'settings.env'
            settings.write_text('')
            key = directory / 'key'
            key.touch()
            az = directory / 'az'
            az.write_text("""#!/usr/bin/env bash
 echo "az $*" >> "$CALL_LOG"
 case "$1 $2" in
   'vm get-instance-view') echo PowerState/running ;;
   'vm show')
     if [[ "$*" == *'--query location'* ]]; then echo centralus
     else echo '{"storageProfile":{"dataDisks":[{"lun":0,"managedDisk":{"id":"execution"}},{"lun":1,"managedDisk":{"id":"consensus"}}]}}'; fi ;;
   'network public-ip') echo observer.example.com ;;
   'snapshot create') exit 1 ;;
 esac
""")
            ssh = directory / 'ssh'
            ssh.write_text('#!/usr/bin/env bash\necho "ssh $*" >> "$CALL_LOG"\n')
            az.chmod(0o755)
            ssh.chmod(0o755)
            env = dict(os.environ, ENV_FILE=str(settings), PATH=tmp + ':' + os.environ['PATH'],
                       CALL_LOG=str(log), AZURE_RESOURCE_GROUP='test-rg', VM_NAME='test-vm',
                       SSH_PRIVATE_KEY_FILE=str(key), SNAPSHOT_PREFIX='test-capture',
                       SNAPSHOT_OUTPUT_DIR=str(directory / 'snapshots'))
            result = subprocess.run(['bash', str(ROOT / 'scripts/snapshot-azure-vm.sh')],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(log.exists(), result.stderr)
            calls = log.read_text()
            self.assertLess(calls.index('systemctl stop'), calls.index('vm deallocate'))
            self.assertLess(calls.index('vm deallocate'), calls.index('snapshot create'))
            self.assertLess(calls.index('snapshot create'), calls.index('vm start'))
            self.assertIn('systemctl start telcoin-observer', calls)

    def run_disk_setup(self, fs='ext4', signature='', populated=False, nvme=False):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cmds = home / 'commands'
            cmds.mkdir()
            log = home / 'calls'
            # Execute the actual script against isolated paths and command shims.
            source = (ROOT / 'infra/vm/prepare-disks.sh').read_text()
            for old in ['/var/lib/telcoin', '/mnt/telcoin-consensus', '/etc/fstab', '/dev/disk/azure']:
                source = source.replace(old, str(home) + old)
            source = source.replace('[[ -b ', '[[ -f ')
            source = source.replace('[[ -b "$device" ]]', '[[ -f "$device" ]]')
            fstab = home / 'etc/fstab'
            fstab.parent.mkdir(parents=True)
            fstab.touch()
            for lun in (0, 1):
                dev = home / (f'dev/disk/azure/data/by-lun/{lun}' if nvme else f'dev/disk/azure/scsi1/lun{lun}')
                dev.parent.mkdir(parents=True, exist_ok=True)
                dev.touch()
            if populated:
                directory = home / 'var/lib/telcoin/db'
                directory.mkdir(parents=True)
                (directory / 'existing-db').touch()
            shims = {
                'mountpoint': 'exit 1',
                'mount': 'echo "mount $*" >> "$CALL_LOG"',
                'systemctl': 'exit 0',
                'blkid': 'if [[ "$*" == *TYPE* ]]; then printf "%s" "$TEST_FS"; else echo "uuid-${*: -1}" | tr / _; fi',
                'wipefs': 'printf "%s" "$TEST_SIGNATURE"',
                'lsblk': 'echo disk',
                'mkfs.ext4': 'echo "FORMAT $*" >> "$CALL_LOG"',
            }
            for name, body in shims.items():
                p = cmds / name
                p.write_text('#!/usr/bin/env bash\n' + body + '\n')
                p.chmod(0o755)
            env = dict(os.environ, PATH=str(cmds) + ':' + os.environ['PATH'],
                       CALL_LOG=str(log), TEST_FS=fs, TEST_SIGNATURE=signature)
            result = subprocess.run(['bash', '-c', source], env=env, capture_output=True, text=True)
            return result, log.read_text() if log.exists() else '', fstab.read_text()

    def test_existing_ext4_is_not_formatted(self):
        result, calls, fstab = self.run_disk_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('FORMAT', calls)
        self.assertIn('consensus-db', fstab)
        self.assertIn('static_files', fstab)
        self.assertIn('x-systemd.requires-mounts-for=', fstab)

    def test_nvme_lun_links_are_used(self):
        result, calls, _ = self.run_disk_setup(fs='', nvme=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count('FORMAT'), 2)
        self.assertIn('data/by-lun/0', calls)
        self.assertIn('data/by-lun/1', calls)

    def test_blank_disks_are_formatted(self):
        result, calls, _ = self.run_disk_setup(fs='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count('FORMAT'), 2)

    def test_unknown_signature_is_not_formatted(self):
        result, calls, _ = self.run_disk_setup(fs='', signature='gpt')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unexpected disk signature', result.stderr)
        self.assertNotIn('FORMAT', calls)

    def test_existing_underlying_data_blocks_setup(self):
        result, calls, _ = self.run_disk_setup(populated=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nonempty directory', result.stderr)
        self.assertNotIn('FORMAT', calls)

    def test_missing_mount_blocks_container_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'image.conf')
            p.write_text('REGISTRY=test.azurecr.io\nIMAGE=test.azurecr.io/node:tag\n')
            shim = Path(tmp, 'mountpoint')
            shim.write_text('#!/bin/bash\nexit 1\n')
            shim.chmod(0o755)
            source = (ROOT / 'infra/vm/start-observer.sh').read_text().replace('/etc/telcoin/image.conf', str(p))
            env = dict(os.environ, PATH=tmp + ':' + os.environ['PATH'])
            result = subprocess.run(['bash', '-c', source], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Required mount missing', result.stderr)


if __name__ == '__main__':
    unittest.main()
