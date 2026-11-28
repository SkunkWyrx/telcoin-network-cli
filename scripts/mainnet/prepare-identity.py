#!/usr/bin/env python3
"""Guest: generate offline observer identity; never runs a node or stores a runtime password."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path('/mnt/telcoin-consensus/identity')
CONFIG = Path('/etc/telcoin/mainnet-preparation.json')


def run(args, value=None):
    result = subprocess.run(args, input=value, capture_output=True)
    if result.returncode:
        raise ValueError('Preparation subprocess failed; private output withheld')
    return result.stdout


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response: return response.read()


def vault_request(vault, name, version='', value=None):
    token = json.loads(fetch_metadata())['access_token']
    url = f'https://{vault}.vault.azure.net/secrets/{name}' + ('/' + version if version else '') + '?api-version=7.4'
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    data = json.dumps({'value': value}).encode() if value is not None else None
    for attempt in range(24):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers, data=data,
                                        method='PUT' if value is not None else 'GET'), timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 403 or attempt == 23: raise
            time.sleep(5)


def fetch_metadata():
    request = urllib.request.Request('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net', headers={'Metadata': 'true'})
    with urllib.request.urlopen(request, timeout=15) as response: return response.read()


def parse_manifest(manifest, release):
    entries = {}
    for line in manifest.decode().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._/-]+)', line)
        if not match or match[2] in entries: raise ValueError('Malformed release manifest')
        entries[match[2]] = match[1]
    expected = {'IMAGE_DIGEST', 'telcoin-network-' + release + '-x86_64-unknown-linux-gnu.tar.gz',
                'chain-configs/testnet/committee.yaml', 'chain-configs/testnet/genesis.yaml',
                'chain-configs/testnet/parameters.yaml'}
    if set(entries) != expected: raise ValueError('Unexpected signed release assets')
    # Only IMAGE_DIGEST is fetched/used. The signed Adiri chain configs are NOT
    # installed on these pre-genesis mainnet hosts.
    return entries


def verify_tool(c):
    # Only this release's signed digest is used, and only for --version/keytool.
    with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
        d = Path(d); (d / 'home').mkdir(mode=0o700)
        keyring = d / 'keys.gpg'
        for key in c['release_keys']:
            armored = fetch(key['url'])
            for public in (armored, fetch('https://github.com/' + key['holder'] + '.gpg')):
                rows = run(['gpg', '--batch', '--homedir', str(d / 'home'), '--with-colons', '--show-keys'], public).decode()
                if key['fingerprint'] not in [row.split(':')[9] for row in rows.splitlines() if row.startswith('fpr:')]:
                    raise ValueError('Release signing key does not match official/GitHub identity')
            run(['gpg', '--batch', '--homedir', str(d / 'home'), '--no-default-keyring', '--keyring', str(keyring), '--import'], armored)
        base = 'https://github.com/Telcoin-Association/telcoin-network/releases/download/' + c['keygen_release'] + '/'
        manifest = fetch(base + 'SHA256SUMS'); signature = fetch(base + 'SHA256SUMS.asc')
        (d / 'SHA256SUMS').write_bytes(manifest); (d / 'SHA256SUMS.asc').write_bytes(signature)
        status = run(['gpgv', '--homedir', str(d / 'home'), '--status-fd', '1', '--keyring', str(keyring),
                      str(d / 'SHA256SUMS.asc'), str(d / 'SHA256SUMS')]).decode()
        if any(t in status for t in ('BADSIG', 'ERRSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'NO_PUBKEY')):
            raise ValueError('Invalid release signature')
        signers = [line.split()[-1] for line in status.splitlines() if ' VALIDSIG ' in line]
        if not signers or any(s not in [k['fingerprint'] for k in c['release_keys']] for s in signers):
            raise ValueError('Untrusted release signer')
        entries = parse_manifest(manifest, c['keygen_release'])
        raw = fetch(base + 'IMAGE_DIGEST')
        if hashlib.sha256(raw).hexdigest() != entries['IMAGE_DIGEST']: raise ValueError('Digest checksum mismatch')
        image = raw.decode().strip()
        if not re.fullmatch(r'ghcr.io/telcoin-association/tn-testnet@sha256:[a-f0-9]{64}', image):
            raise ValueError('Invalid offline tool image')
        run(['docker', 'pull', image])
        version = run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', '/usr/local/bin/telcoin', image, '--version']).decode()
        if 'Commit SHA: ' + c['keygen_commit'] not in version or 'Version: 0.18.0' not in version:
            raise ValueError('Signed tool version does not match the expected release commit')
        return image, {'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'signers': signers, 'version': version}


def main():
    os.umask(0o077)
    c = json.loads(CONFIG.read_text())
    if c['network'] != 'mainnet' or c['node_name'] not in ('mainnet-observer-1', 'mainnet-observer-2'):
        raise ValueError('Wrong preparation target')
    if not all(os.path.ismount(p) for p in ['/var/lib/telcoin/db', '/var/lib/telcoin/consensus-db', '/var/lib/telcoin/static_files']):
        raise ValueError('Data mounts are missing')
    enabled = subprocess.run(['systemctl', 'is-enabled', 'telcoin-observer.service'], capture_output=True)
    if enabled.stdout.strip() != b'masked':
        raise ValueError('Observer service must remain masked')
    # No persistent or concurrently running node containers are permitted here.
    if run(['docker', 'ps', '-aq']).strip(): raise ValueError('Unexpected containers on preparation VM')
    ROOT.mkdir(mode=0o700, exist_ok=True)
    state_path = ROOT / 'preparation.json'
    old = json.loads(state_path.read_text()) if state_path.exists() else None
    record = vault_request(c['vault'], c['passphrase_secret'], old['secret_version'] if old else '')
    password = record['value']
    if not re.fullmatch('[a-f0-9]{64}', password): raise ValueError('Unexpected observer passphrase format')
    image, provenance = verify_tool(c)
    common = ['docker', 'run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
              '--security-opt', 'no-new-privileges', '--read-only', '--user', '0', '--entrypoint', '/usr/local/bin/telcoin',
              '--mount', f'type=bind,src={ROOT},dst=/identity', image, '--log.file.max-files', '0']
    if old:
        if old['public_ip'] != c['public_ip'] or old['node_name'] != c['node_name']:
            raise ValueError('Existing identity belongs to a different address/node')
        if hashlib.sha256((ROOT / 'node-info.yaml').read_bytes()).hexdigest() != old['node_info_sha256']:
            raise ValueError('Existing public identity changed')
    else:
        if (ROOT / 'node-keys').exists() or (ROOT / 'node-info.yaml').exists():
            raise ValueError('Existing identity without state; recover rather than regenerate')
        # Observer addresses carry no stake. Zero is explicitly supported upstream.
        run(common + ['-q', '--datadir', '/identity', '--bls-passphrase-source', 'stdin',
                      'keytool', 'generate', 'observer', '--address', '0',
                      '--external-primary-addr', f"/ip4/{c['public_ip']}/udp/49590/quic-v1",
                      '--external-worker-addrs', f"/ip4/{c['public_ip']}/udp/49594/quic-v1"], (password + '\n').encode())
    keys = ROOT / 'node-keys'
    if (keys / 'bls.key').exists() or not (keys / 'bls.kw').is_file(): raise ValueError('Encrypted BLS key required')
    info = (ROOT / 'node-info.yaml').read_text()
    for port in (49590, 49594):
        if f"/ip4/{c['public_ip']}/udp/{port}/quic-v1/p2p/" not in info: raise ValueError('Wrong advertised public address')
    # Verify that the stored passphrase actually unlocks these keys, on a throwaway
    # RAM copy. This never runs `node` and never changes the real identity.
    with tempfile.TemporaryDirectory(dir='/dev/shm') as d:
        import shutil
        copy = Path(d) / 'identity'; shutil.copytree(ROOT, copy)
        test = [str(copy) if value == str(ROOT) else value for value in common]
        test = [value.replace(f'src={ROOT},', f'src={copy},') for value in test]
        run(test + ['-q', '--datadir', '/identity', '--bls-passphrase-source', 'stdin', 'keytool', 'generate', 'pop', '--address', '0'], (password + '\n').encode())
        if (copy / 'node-info.yaml').read_bytes() != (ROOT / 'node-info.yaml').read_bytes():
            raise ValueError('Key recovery did not reproduce public identity')
    files = {p.name: base64.b64encode(p.read_bytes()).decode() for p in keys.iterdir() if p.is_file()}
    if set(files) != {'bls.kw', 'primary.seed', 'worker.seed'}: raise ValueError('Unexpected private key files')
    backup = json.dumps({'network': 'mainnet', 'node': c['node_name'], 'public_ip': c['public_ip'],
                         'node_info': info, 'keys': files, 'passphrase_secret_id': record['id']}, sort_keys=True)
    try:
        previous = vault_request(c['backup_vault'], c['backup_secret'])
    except urllib.error.HTTPError as error:
        if error.code != 404: raise
        previous = None
    if previous:
        if previous['value'] != backup: raise ValueError('Existing key backup differs; refusing overwrite')
    else:
        vault_request(c['backup_vault'], c['backup_secret'], value=backup)
    if vault_request(c['backup_vault'], c['backup_secret'])['value'] != backup: raise ValueError('Backup readback failed')
    # Private keys reside on the retained consensus SSD; root datadir sees a bind mount.
    target = Path('/var/lib/telcoin/node-keys'); target.mkdir(mode=0o700, exist_ok=True)
    if not os.path.ismount(target):
        if any(target.iterdir()): raise ValueError('Refusing to hide existing node keys')
        with open('/etc/fstab', 'a') as f: f.write(f'{keys} {target} none bind,nofail,x-systemd.requires-mounts-for=/mnt/telcoin-consensus 0 0\n')
        run(['mount', str(target)])
    Path('/var/lib/telcoin/node-info.yaml').write_text(info)
    state = {'network': 'mainnet', 'node_name': c['node_name'], 'public_ip': c['public_ip'],
             'node_info_sha256': hashlib.sha256(info.encode()).hexdigest(), 'keygen_image': image,
             'secret_version': record['id'].rsplit('/', 1)[-1], 'provenance': provenance,
             'backup_verified': True, 'runtime_enabled': False}
    state_path.write_text(json.dumps(state)); state_path.chmod(0o600)
    for path in keys.iterdir(): path.chmod(0o600)
    keys.chmod(0o700)
    if run(['docker', 'ps', '-aq']).strip(): raise ValueError('Unexpected container remained after key generation')
    run(['docker', 'image', 'rm', image])
    print(json.dumps({'node_info': info, **state}))


if __name__ == '__main__':
    try:
        with open('/run/oprail-mainnet-identity.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except Exception as error:
        raise SystemExit('Mainnet preparation failed: ' + type(error).__name__ + '; private diagnostics withheld')
