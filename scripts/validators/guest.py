#!/usr/bin/env python3
"""Validator-only identity/finalize helper. Reads secrets inside the VM, never from ARM."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import recovery

CONFIG = Path('/etc/oprail-validator/profile.json')
STATE = Path('/etc/oprail-validator/state.json')
LOG = Path('/var/log/oprail-validator-setup.log')
SETUP = Path('/home/azureuser/telcoin-node-scripts/setup-node.sh')


def mounts():
    for path in ['/var/lib/telcoin/db', '/var/lib/telcoin/consensus-db', '/var/lib/telcoin/static_files']:
        if not os.path.ismount(path):
            raise ValueError('Required validator storage is not mounted: ' + path)


def secret(c, secret_id=None, with_id=False):
    uri = ('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01'
           '&resource=https%3A%2F%2Fvault.azure.net')
    with urllib.request.urlopen(urllib.request.Request(uri, headers={'Metadata': 'true'}), timeout=10) as r:
        token = json.load(r)['access_token']
    identity = c['identity']
    vault, name = identity['passphrase_vault'], identity['passphrase_secret']
    if vault != 'oprail-val-' + c['network'] + '-keys' or not re.fullmatch(r'[A-Za-z0-9-]+', name):
        raise ValueError('Invalid network-specific passphrase secret')
    if secret_id and not re.fullmatch(re.escape(f'https://{vault}.vault.azure.net/secrets/{name}/') + r'[a-f0-9]{32}', secret_id):
        raise ValueError('Invalid pinned passphrase secret version')
    uri = secret_id or f'https://{vault}.vault.azure.net/secrets/{name}'
    req = urllib.request.Request(uri + '?api-version=7.4',
                                 headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=15) as r:
        record = json.load(r)
        value = record['value']
    if not re.fullmatch(re.escape(f'https://{vault}.vault.azure.net/secrets/{name}/') + r'[a-f0-9]{32}', record['id']) or (secret_id and record['id'] != secret_id):
        raise ValueError('Unexpected passphrase secret version')
    if len(value.encode()) < 16 or value != value.strip() or '\n' in value or '\r' in value:
        raise ValueError('The validator passphrase must be at least 16 bytes with no surrounding whitespace or line breaks')
    return ((value + '\n').encode(), record['id']) if with_id else (value + '\n').encode()


def setup_args(c, phase, ip):
    address, tag = c['identity']['execution_address'], c['release']['tag']
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', address) or int(address, 16) == 0:
        raise ValueError('Supply the confirmed execution/staking address')
    pattern = r'v[0-9]+\.[0-9]+\.[0-9]+' + ('-adiri' if c['network'] == 'adiri' else '')
    if not re.fullmatch(pattern, tag):
        raise ValueError('Supply a stable signed release for this network')
    args = ['bash', str(SETUP), '--json', '--phase=' + phase, '--network', c['release']['scripts_network'],
            '--install-method', 'docker', '--release-tag', tag, '--data-dir', '/var/lib/telcoin',
            '--address', address, '--public-ip', ip, '--no-public-rpc', '--log-target', 'journal',
            '--external-primary', f'/ip4/{ip}/udp/49590/quic-v1',
            '--external-worker', f'/ip4/{ip}/udp/49594/quic-v1',
            '--listener-primary', '/ip4/0.0.0.0/udp/49590/quic-v1',
            '--listener-worker', '/ip4/0.0.0.0/udp/49594/quic-v1', '--passphrase-stdin']
    if c['networking']['advertised_transaction_rpc_url']:
        args += ['--rpc-http', c['networking']['advertised_transaction_rpc_url']]
    return args


def private_run(args, password=None):
    result = subprocess.run(args, input=password, capture_output=True)
    if result.returncode:
        # Keep diagnostic output on the VM only and redact the supplied passphrase.
        output = result.stdout + result.stderr
        if password:
            output = output.replace(password.rstrip(b'\r\n'), b'[REDACTED]')
        LOG.touch(mode=0o600, exist_ok=True)
        LOG.chmod(0o600)
        LOG.write_bytes(output)
        raise RuntimeError('Verified upstream setup failed (exit ' + str(result.returncode) + '); inspect /var/log/oprail-validator-setup.log locally')
    return result.stdout


def save(state):
    with tempfile.NamedTemporaryFile('w', dir=STATE.parent, delete=False) as f:
        os.chmod(f.name, 0o600)
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    Path(f.name).replace(STATE)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=['prepare', 'backup', 'finalize', 'export', 'status'], required=True)
    p.add_argument('--public-ip', required=True)
    p.add_argument('--expected-network', choices=['adiri', 'mainnet'], required=True)
    p.add_argument('--acknowledge-key-backup', action='store_true')
    args = p.parse_args()
    import ipaddress
    ip = str(ipaddress.IPv4Address(args.public_ip))
    c = json.loads(CONFIG.read_text())
    if c['role'] != 'validator' or c['network'] != args.expected_network:
        raise ValueError('Wrong validator network or role')
    if args.phase == 'status':
        print(json.dumps({'network': c['network'], 'state': json.loads(STATE.read_text()) if STATE.exists() else {'phase': 'infrastructure'}}))
        return
    mounts()
    if args.phase == 'export':
        state = json.loads(STATE.read_text())
        if hashlib.sha256(Path('/var/lib/telcoin/node-info.yaml').read_bytes()).hexdigest() != state['node_info_sha256']:
            raise ValueError('Public validator identity changed; inspect before export')
        if state['network'] != c['network'] or state['address'] != c['identity']['execution_address'] or state['ip'] != ip:
            raise ValueError('Validator identity does not match this network profile')
        metadata = {}
        for line in Path('/etc/telcoin/.node-meta').read_text().splitlines():
            name, separator, value = line.partition('=')
            if separator:
                metadata[name] = value.strip().strip('"').strip("'")
        image = metadata.get('DOCKER_IMAGE', '')
        if not re.fullmatch(re.escape(c['release']['image_repository']) + r'@sha256:[a-f0-9]{64}', image):
            raise ValueError('Expected the signed network-specific container digest')
        public = private_run(['docker', 'run', '--rm', '--network', 'none', '--user', '0',
                              '--entrypoint', '/usr/local/bin/telcoin', '-v',
                              '/var/lib/telcoin/node-info.yaml:/node-info.yaml:ro', image,
                              '-q', '--bls-passphrase-source', 'no-passphrase', 'keytool',
                              'export-staking-args', '--node-info', '/node-info.yaml', '--json'])
        payload = json.loads(public)
        if not re.fullmatch(r'0x[a-fA-F0-9]{192}', payload['blsPubkey']) or not re.fullmatch(r'0x[a-fA-F0-9]{96}', payload['signature']):
            raise ValueError('Unexpected public key/proof serialization')
        print(json.dumps({'network': c['network'], 'executionAddress': state['address'], 'publicIp': ip,
                          'primaryAddress': f'/ip4/{ip}/udp/49590/quic-v1',
                          'workerAddress': f'/ip4/{ip}/udp/49594/quic-v1',
                          'releaseTag': state['tag'], 'image': image, **payload}))
        return
    if args.phase == 'backup':
        state = json.loads(STATE.read_text())
        if state['network'] != c['network'] or state['address'] != c['identity']['execution_address']:
            raise ValueError('Wrong validator identity')
        # Initial recovery feature is deliberately pre-activation only. A backup of
        # active signing state needs coordinated cold snapshots, not an identity copy.
        if state['phase'] != 'prepared' or Path('/etc/systemd/system/telcoin.service').exists():
            raise ValueError('Only prepared validators may use identity backup; active validators require coordinated cold recovery')
        credential, secret_id = secret(c, state['passphrase_secret_id'], with_id=True)
        certificates = [Path('/etc/oprail-validator/recovery') / (kind + '.pem') for kind in ('identity', 'passphrase')]
        with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='validator-backup-') as directory:
            index = recovery.create(c, state, credential, secret_id, datadir='/var/lib/telcoin', config_dir='/etc/telcoin',
                                    state_path=STATE, profile_path=CONFIG, directory=directory, certificates=certificates)
        (STATE.parent / 'backup-index.json').write_bytes(recovery.canonical(index))
        (STATE.parent / 'backup-index.json').chmod(0o600)
        print(json.dumps({'backup_id': index['backup_id'], 'network': c['network'],
                          'storage_account': c['backup']['storage_account'], 'recovery_verified': False}))
        return
    command = setup_args(c, 'keygen' if args.phase == 'prepare' else 'finalize', ip)
    old = json.loads(STATE.read_text()) if STATE.exists() else None
    identity = {'network': c['network'], 'address': c['identity']['execution_address'], 'ip': ip,
                'tag': c['release']['tag']}
    if old and any(old.get(k) != v for k, v in identity.items()):
        raise ValueError('Existing validator identity/release differs; refusing to replace it')
    if old and hashlib.sha256(Path('/var/lib/telcoin/node-info.yaml').read_bytes()).hexdigest() != old['node_info_sha256']:
        raise ValueError('Existing public identity changed; refusing to continue')
    if args.phase == 'prepare':
        if old:
            print(json.dumps(old))
            return
        if Path('/var/lib/telcoin/node-info.yaml').exists() or any(Path('/var/lib/telcoin/node-keys').glob('*')):
            raise ValueError('Existing keys require recovery; never generate over them')
        if not SETUP.exists():
            # The official bootstrap verifies the signed script release it installs.
            with tempfile.NamedTemporaryFile(delete=False) as f:
                installer = Path(f.name)
                with urllib.request.urlopen('https://install.telcoin.network', timeout=30) as response:
                    f.write(response.read())
                os.chmod(installer, 0o644)
            try:
                private_run(['sudo', '-u', 'azureuser', '-H', 'bash', str(installer)])
            finally:
                installer.unlink(missing_ok=True)
        credential, secret_id = secret(c, with_id=True)
        private_run(command, credential)
        if not Path('/var/lib/telcoin/node-keys/bls.kw').exists() or Path('/var/lib/telcoin/node-keys/bls.key').exists():
            raise ValueError('Encrypted validator identity was not created')
        state = {**identity, 'passphrase_secret_id': secret_id, 'phase': 'prepared', 'node_info': '/var/lib/telcoin/node-info.yaml'}
    else:
        if not old or old['phase'] != 'prepared' or not args.acknowledge_key_backup:
            raise ValueError('Prepare and securely back up the identity before finalizing')
        # A boolean acknowledgment is not evidence: require persisted ciphertext,
        # a matching identity, and a signed result from an offline key/passphrase drill.
        certificates = [Path('/etc/oprail-validator/recovery') / (kind + '.pem') for kind in ('identity', 'passphrase')]
        with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='validator-check-') as directory:
            recovery.check_gate(c, old, '/var/lib/telcoin', json.loads((STATE.parent / 'backup-index.json').read_text()), certificates, directory)
        if not c['networking']['p2p_source_cidrs']:
            raise ValueError('Configure approved P2P sources before starting the node')
        if Path('/etc/systemd/system/telcoin.service').exists():
            raise ValueError('An installed validator requires its dedicated maintenance process')
        # These mount checks remain in force whenever the upstream service starts.
        dropin = Path('/etc/systemd/system/telcoin.service.d')
        dropin.mkdir(parents=True, exist_ok=True)
        (dropin / 'storage.conf').write_text('[Unit]\nRequiresMountsFor=/var/lib/telcoin/db /var/lib/telcoin/consensus-db /var/lib/telcoin/static_files\n[Service]\nExecStartPre=/usr/bin/mountpoint -q /var/lib/telcoin/db\nExecStartPre=/usr/bin/mountpoint -q /var/lib/telcoin/consensus-db\nExecStartPre=/usr/bin/mountpoint -q /var/lib/telcoin/static_files\n')
        private_run(command, secret(c, old['passphrase_secret_id']))
        state = {**old, 'phase': 'running'}
    state['node_info_sha256'] = hashlib.sha256(Path('/var/lib/telcoin/node-info.yaml').read_bytes()).hexdigest()
    save(state)
    print(json.dumps(state))


if __name__ == '__main__':
    try:
        os.umask(0o077)
        with open('/run/oprail-validator.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except Exception as error:
        raise SystemExit('Validator operation failed: ' + type(error).__name__ + ': ' + str(error) if isinstance(error, (ValueError, RuntimeError)) else 'Validator operation failed: ' + type(error).__name__)
