#!/usr/bin/env python3
"""Prepare exactly two stopped mainnet observers. Never deploys an Adiri runtime."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION = '8a149d4d-c52a-4432-929e-de754584e269'
GROUP = 'oprail-observer-mainnet'
VAULT = 'oprail-mainnet-keys'
BACKUP_VAULT = 'oprail-mainnet-backup'
NODES = ['mainnet-observer-1', 'mainnet-observer-2']


def az(*args):
    result = subprocess.run(['az', *args, '-o', 'json'], capture_output=True)
    if result.returncode: raise ValueError('Azure operation failed: ' + result.stderr.decode()[:1200])
    return json.loads(result.stdout or 'null')


def gh(path):
    return json.loads(subprocess.check_output(['gh', 'api', path]))


def cloud_init():
    files = [(ROOT / 'infra/vm/prepare-disks.sh', '/usr/local/sbin/telcoin-prepare-disks', '0700'),
             (ROOT / 'scripts/mainnet/prepare-identity.py', '/usr/local/sbin/oprail-prepare-mainnet-identity', '0700'),
             (ROOT / 'infra/vm/grow-disks.py', '/usr/local/sbin/telcoin-grow-disks', '0700'),
             (ROOT / 'infra/vm/telcoin-disk-growth.service', '/etc/systemd/system/telcoin-disk-growth.service', '0644'),
             (ROOT / 'infra/vm/telcoin-disk-growth.timer', '/etc/systemd/system/telcoin-disk-growth.timer', '0644')]
    text = '#cloud-config\npackage_update: true\npackages: [docker.io, e2fsprogs, python3, curl, gnupg, azure-vm-utils, nvme-cli, chrony]\nwrite_files:\n'
    for source, target, mode in files:
        text += f'  - path: {target}\n    permissions: "{mode}"\n    encoding: b64\n    content: ' + base64.b64encode(source.read_bytes()).decode() + '\n'
    commands = [['systemctl', 'mask', 'telcoin-observer.service'], ['udevadm', 'control', '--reload-rules'],
                ['udevadm', 'trigger', '--subsystem-match=block'], ['udevadm', 'settle', '--timeout=60'],
                ['/usr/local/sbin/telcoin-prepare-disks'], ['systemctl', 'enable', '--now', 'docker', 'chrony'],
                ['swapoff', '-a'], ['bash', '-c', "sed -i '/^[^#].*[[:space:]]swap[[:space:]]/s/^/# /' /etc/fstab"],
                ['systemctl', 'daemon-reload']]
    text += 'runcmd:\n' + ''.join('  - ' + json.dumps(command) + '\n' for command in commands)
    if len(text.encode()) > 65535: raise ValueError('Cloud-init exceeds Azure size limit')
    return text


def run_vm(node, script):
    result = az('vm', 'run-command', 'invoke', '-g', GROUP, '-n', node, '--command-id', 'RunShellScript', '--scripts', script)
    text = '\n'.join(item.get('message', '') for item in result.get('value', []))
    output, _, errors = text.split('[stdout]', 1)[-1].partition('[stderr]')
    if errors.strip(): raise ValueError('Guest preparation failed; inspect safe diagnostics on VM: ' + errors[:300])
    return output.strip()


def release_keys():
    release = gh('repos/Telcoin-Association/telcoin-network/contents/release?ref=main')['sha']
    path = 'repos/Telcoin-Association/tn-release-signing/contents/'
    table = base64.b64decode(gh(path + 'KEYS.md?ref=' + release)['content']).decode()
    result = []
    for line in table.splitlines():
        if not line.startswith('| @'): continue
        values = [s.strip() for s in line.split('|')[1:-1]]
        if values[5] != 'active': continue
        holder = values[0][1:]; fingerprint = values[1]
        result.append({'holder': holder, 'fingerprint': fingerprint,
                       'url': f'https://raw.githubusercontent.com/Telcoin-Association/tn-release-signing/{release}/keys/{holder}.asc'})
    if not result: raise ValueError('No active upstream release keys')
    return result


def ensure_passphrase(name):
    existing = az('keyvault', 'secret', 'list', '--vault-name', VAULT)
    if any(item['name'] == name for item in existing): return
    # Never create a plaintext file, environment variable or ARM secret parameter.
    password = secrets.token_hex(32).encode()
    result = subprocess.run(['az', 'keyvault', 'secret', 'set', '--vault-name', VAULT, '-n', name,
                             '--file', '/dev/stdin', '--output', 'none'], input=password, capture_output=True)
    if result.returncode: raise ValueError('Passphrase creation failed; private output withheld')


def pending_nodes(inventory):
    # Run Command can temporarily report a healthy VM as Updating. Never
    # redeploy one merely because a guest inspection is in progress.
    return [name for name in NODES if not any(v['name'] == name for v in inventory) or
            any(v['name'] == name and v.get('provisioningState') == 'Failed' for v in inventory)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--ssh-public-key-file', type=Path, default=Path.home() / '.ssh/id_ed25519.pub')
    p.add_argument('--keygen-release', default='v0.18.0-adiri', choices=['v0.18.0-adiri'])
    p.add_argument('--output-dir', type=Path, default=ROOT / '.mainnet-preparation')
    p.add_argument('--node', choices=NODES, help='Prepare only one identity; never changes observer count')
    p.add_argument('--resume-infrastructure', action='store_true', help='Retry only failed/missing VMs in this dedicated preparation group')
    args = p.parse_args()
    if not args.apply:
        print(json.dumps({'network': 'mainnet', 'group': GROUP, 'region': 'centralus', 'nodes': NODES,
                          'vm_size': 'Standard_D4s_v6', 'data_disks_gib': [50, 50], 'runtime_enabled': False}, indent=2)); return
    os.umask(0o077)
    if az('account', 'show')['id'] != SUBSCRIPTION: raise ValueError('Wrong Azure subscription')
    if az('group', 'exists', '-n', GROUP):
        group = az('group', 'show', '-n', GROUP)
        if group.get('tags', {}).get('purpose') != 'oprail-mainnet-preparation': raise ValueError('Unexpected target group')
    else:
        az('group', 'create', '-n', GROUP, '-l', 'centralus', '--tags', 'purpose=oprail-mainnet-preparation', 'network=mainnet')
    token = az('account', 'get-access-token', '--resource', 'https://management.azure.com/')['accessToken']
    part = token.split('.')[1]; operator = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))['oid']
    inventory = az('vm', 'list', '-g', GROUP)
    if any(node['name'] not in NODES or node.get('tags', {}).get('telcoin-chain') != 'mainnet' for node in inventory):
        raise ValueError('Unexpected existing VM; refusing changes')
    pending = pending_nodes(inventory)
    if not inventory or (args.resume_infrastructure and pending):
        values = {'sshPublicKey': args.ssh_public_key_file.read_text().strip(), 'operatorPrincipalId': operator,
                  'customData': base64.b64encode(cloud_init().encode()).decode(), 'provisionNodeNames': pending}
        with tempfile.TemporaryDirectory() as d:
            params = Path(d) / 'parameters.json'; params.write_text(json.dumps({'parameters': {k: {'value': v} for k, v in values.items()}}))
            print('Provisioning stopped production observers:', ', '.join(pending), flush=True)
            az('deployment', 'group', 'create', '-g', GROUP, '-n', 'mainnet-pre-genesis',
               '--template-file', str(ROOT / 'infra/mainnet/prepare.bicep'), '--parameters', '@' + str(params))
    elif not args.node and {node['name'] for node in inventory} != set(NODES):
        raise ValueError('Partial preparation; inspect deployment before resuming')
    # Allow data-plane RBAC propagation without printing secret values.
    for attempt in range(24):
        try:
            az('keyvault', 'secret', 'list', '--vault-name', VAULT); break
        except ValueError:
            if attempt == 23: raise
            time.sleep(5)
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    keys = release_keys(); reports = []
    keygen_commit = gh('repos/Telcoin-Association/telcoin-network/commits/' + args.keygen_release)['sha']
    for index, node in enumerate(NODES, 1):
        if args.node and args.node != node: continue
        vm = az('vm', 'show', '-g', GROUP, '-n', node)
        if vm['hardwareProfile']['vmSize'] != 'Standard_D4s_v6': raise ValueError('Unexpected VM sizing')
        state = az('vm', 'get-instance-view', '-g', GROUP, '-n', node)
        if not any(s['code'] == 'PowerState/running' for s in state['instanceView']['statuses']):
            raise ValueError('Preparation VM is not running')
        print('Checking cloud-init:', node, flush=True)
        run_vm(node, 'set -eu\ncloud-init status --wait >/dev/null\n')
        ip = az('network', 'public-ip', 'show', '-g', GROUP, '-n', node + '-ip')['ipAddress']
        passphrase = f'observer-{index:02d}-bls-passphrase'; backup = f'observer-{index:02d}-identity-backup'
        ensure_passphrase(passphrase)
        for vault, secret, role in [(VAULT, passphrase, 'Key Vault Secrets User'), (BACKUP_VAULT, backup, 'Key Vault Secrets Officer')]:
            scope = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.KeyVault/vaults/{vault}/secrets/{secret}'
            az('role', 'assignment', 'create', '--assignee-object-id', vm['identity']['principalId'],
               '--assignee-principal-type', 'ServicePrincipal', '--role', role, '--scope', scope)
        c = {'network': 'mainnet', 'node_name': node, 'public_ip': ip, 'vault': VAULT, 'backup_vault': BACKUP_VAULT,
             'passphrase_secret': passphrase, 'backup_secret': backup, 'keygen_release': args.keygen_release, 'keygen_commit': keygen_commit, 'release_keys': keys}
        # Only public configuration is sent through Azure Run Command.
        encoded = base64.b64encode(json.dumps(c).encode()).decode()
        growth = {'vm_id': vm['id'], 'threshold_percent': 75, 'increment_gb': 50,
                  'disks': [{'id': f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.Compute/disks/{node}-{kind}',
                             'lun': i, 'mount': mount} for i, (kind, mount) in enumerate([
                                 ('execution', '/var/lib/telcoin/db'), ('consensus', '/mnt/telcoin-consensus')])]}
        growth64 = base64.b64encode(json.dumps(growth).encode()).decode()
        helper64 = base64.b64encode((ROOT / 'scripts/mainnet/prepare-identity.py').read_bytes()).decode()
        script = f'''set -eu
install -d -m 700 /etc/telcoin
printf %s {helper64} | base64 --decode > /usr/local/sbin/oprail-prepare-mainnet-identity
chmod 700 /usr/local/sbin/oprail-prepare-mainnet-identity
printf %s {encoded} | base64 --decode > /etc/telcoin/mainnet-preparation.json
chmod 600 /etc/telcoin/mainnet-preparation.json
printf %s {growth64} | base64 --decode > /etc/telcoin/disk-growth.json
chmod 600 /etc/telcoin/disk-growth.json
systemctl enable --now telcoin-disk-growth.timer >/dev/null 2>&1
python3 /usr/local/sbin/oprail-prepare-mainnet-identity
'''
        print('Preparing independent offline identity and secure backup:', node, flush=True)
        report = json.loads(run_vm(node, script))
        path = args.output_dir / f'oprail-prod-observer-{index:02d}-node-info.yaml'
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != report['node_info_sha256']:
            raise ValueError('Exported public identity changed; inspect before overwriting')
        path.write_text(report.pop('node_info')); path.chmod(0o600)
        public_fields = re.findall(r'^\s*(bls_public_key|network_key):\s*(\S+)\s*$', path.read_text(), re.MULTILINE)
        report['bls_public_key'] = [v for k, v in public_fields if k == 'bls_public_key'][0]
        report['network_keys'] = [v for k, v in public_fields if k == 'network_key']
        if len(report['network_keys']) != 2: raise ValueError('Expected primary and one worker public key')
        az('lock', 'create', '-g', GROUP, '--name', 'preserve-mainnet-peer-ip', '--lock-type', 'CanNotDelete',
           '--resource-name', node + '-ip', '--resource-type', 'Microsoft.Network/publicIPAddresses')
        reports.append(report)
        (args.output_dir / 'preparation.json').write_text(json.dumps(reports, indent=2))
        print('Exported public node-info:', path, flush=True)
    if len(reports) == 2 and (reports[0]['node_info_sha256'] == reports[1]['node_info_sha256'] or reports[0]['public_ip'] == reports[1]['public_ip']):
        raise ValueError('Production observers must have independent identities/IPs')
    if len(reports) == 2 and (reports[0]['bls_public_key'] == reports[1]['bls_public_key'] or len({key for report in reports for key in report['network_keys']}) != 4):
        raise ValueError('Observers must have independent BLS and network keys')
    print('Selected mainnet identities prepared. Node services remain masked; no runtime image/genesis enabled.', flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        raise SystemExit(str(error) if isinstance(error, ValueError) else 'Preparation failed: ' + type(error).__name__)
