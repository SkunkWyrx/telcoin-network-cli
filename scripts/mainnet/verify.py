#!/usr/bin/env python3
"""Verify/export pre-genesis public identities. Never starts a node."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

spec = importlib.util.spec_from_file_location('preparation', Path(__file__).with_name('prepare.py'))
preparation = importlib.util.module_from_spec(spec); spec.loader.exec_module(preparation)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--remove-keygen-image', action='store_true')
    args = p.parse_args()
    if preparation.az('account', 'show')['id'] != preparation.SUBSCRIPTION:
        raise ValueError('Wrong subscription')
    reports = []
    preparation.ROOT.joinpath('.mainnet-preparation').mkdir(mode=0o700, exist_ok=True)
    for i, name in enumerate(preparation.NODES, 1):
        command = '''set -eu
python3 - <<'CHECK'
import json,subprocess,hashlib,os,re
from pathlib import Path
state=json.loads(Path('/mnt/telcoin-consensus/identity/preparation.json').read_text())
if state['network']!='mainnet' or state['runtime_enabled'] or not state['backup_verified']: raise RuntimeError('Wrong preparation state')
def output(args):return subprocess.run(args,capture_output=True,text=True).stdout.strip()
if output(['systemctl','is-enabled','telcoin-observer.service'])!='masked': raise RuntimeError('Service is not masked')
if output(['systemctl','is-active','telcoin-observer.service'])=='active': raise RuntimeError('Service unexpectedly active')
if output(['docker','ps','-aq']): raise RuntimeError('Unexpected containers')
mounts=['/var/lib/telcoin/db','/var/lib/telcoin/consensus-db','/var/lib/telcoin/static_files','/var/lib/telcoin/node-keys']
if not all(os.path.ismount(p) for p in mounts): raise RuntimeError('Missing data/identity mount')
if Path('/etc/telcoin/observer.env').exists() or Path('/etc/telcoin/image.conf').exists(): raise RuntimeError('Unexpected runtime configuration')
info=Path('/var/lib/telcoin/node-info.yaml').read_text()
if hashlib.sha256(info.encode()).hexdigest()!=state['node_info_sha256']: raise RuntimeError('Identity changed')
if not re.fullmatch(r'ghcr.io/telcoin-association/tn-testnet@sha256:[a-f0-9]{64}',state['keygen_image']): raise RuntimeError('Unexpected tool image')
if 'Commit SHA: 55e0c10ad2147d3e7da40f58e092b47bebfc5485' not in state['provenance']['version']: raise RuntimeError('Wrong tool commit')
CLEANUP
print(json.dumps({**state,'node_info':info,'service_masked':True,'containers':[], 'identity_on_data_disk':True}))
CHECK
'''
        cleanup = "subprocess.run(['docker','image','rm',state['keygen_image']],capture_output=True)" if args.remove_keygen_image else ''
        result = json.loads(preparation.run_vm(name, command.replace('CLEANUP', cleanup)))
        public_ip = preparation.az('network', 'public-ip', 'show', '-g', preparation.GROUP, '-n', name + '-ip')['ipAddress']
        if result['node_name'] != name or result['public_ip'] != public_ip: raise ValueError('Unexpected identity address')
        info = result.pop('node_info')
        path = preparation.ROOT / '.mainnet-preparation' / f'oprail-prod-observer-{i:02d}-node-info.yaml'
        if path.exists() and path.read_text() != info: raise ValueError('Existing export differs; refusing overwrite')
        path.write_text(info); path.chmod(0o600)
        public = re.findall(r'^\s*(bls_public_key|network_key):\s*(\S+)\s*$', info, re.MULTILINE)
        result['bls_public_key'] = [value for key, value in public if key == 'bls_public_key'][0]
        result['network_keys'] = [value for key, value in public if key == 'network_key']
        reports.append(result)
        print('Verified stopped mainnet identity:', name, public_ip, flush=True)
    if len({r['bls_public_key'] for r in reports}) != 2 or len({k for r in reports for k in r['network_keys']}) != 4:
        raise ValueError('Production keys are not distinct')
    # Verify uniqueness without printing, hashing or persisting passphrase values.
    values = []
    for i in (1, 2):
        record = preparation.az('keyvault', 'secret', 'show', '--vault-name', preparation.VAULT, '-n', f'observer-{i:02d}-bls-passphrase')
        values.append(record['value'])
    if values[0] == values[1] or any(not re.fullmatch('[a-f0-9]{64}', value) for value in values):
        raise ValueError('Passphrases must be independent random values')
    values.clear()
    target = preparation.ROOT / '.mainnet-preparation/preparation.json'
    target.write_text(json.dumps(reports, indent=2)); target.chmod(0o600)
    print('Distinct BLS keys, four distinct network keys, unique passphrases, persistent identities, verified backups and masked services confirmed.')


if __name__ == '__main__':
    try: main()
    except Exception:
        raise SystemExit('Mainnet readiness verification failed; private diagnostics withheld.')
