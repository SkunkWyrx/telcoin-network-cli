#!/usr/bin/env python3
"""Prepare a dedicated API-key vault and managed identities; never restart observers."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def az(*args):
    return json.loads(subprocess.check_output(['az', *args, '-o', 'json'], text=True) or 'null')


def runtime_policy_script(network, keys):
    if network not in ('adiri', 'mainnet'):
        raise ValueError('Unexpected network')
    return "python3 - <<'PYREMOTE'\n" + r'''import json,os,pathlib,tempfile
path=pathlib.Path('/etc/oprail-observers/NETWORK/pool.json')
st=path.stat()
c=json.loads(path.read_text())
if c['network']!='NETWORK' or not c.get('public_rpc'): raise RuntimeError('Unexpected observer profile')
c['public_rpc']['api_keys']=json.loads(KEY_POLICY)
with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False) as output:
 os.chmod(output.name,st.st_mode & 0o777)
 os.chown(output.name,st.st_uid,st.st_gid)
 json.dump(c,output,indent=2)
 output.flush();os.fsync(output.fileno())
pathlib.Path(output.name).replace(path)
print('Runtime key policy recorded; proxy reload not requested.')
'''.replace('NETWORK', network).replace('KEY_POLICY', repr(json.dumps(keys))) + "PYREMOTE\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--network', choices=['adiri', 'mainnet'], default='adiri')
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    c = json.loads((ROOT / 'infra/pool' / (args.network + '.json')).read_text())
    keys = c['public_rpc']['api_keys']
    rg = c['resource_group']
    app = 'oprail-observer-mgmt-' + args.network
    print('Prepare API-key vault', keys['vault'], 'for', app, 'in', rg)
    if not args.apply:
        print('Plan only. Use --apply to configure vault permissions and website settings.')
        return
    account = az('account', 'show')
    if account['id'] != c['subscription']:
        raise ValueError('Select the configured subscription first')
    current = az('containerapp', 'show', '-g', rg, '-n', app)
    if current.get('tags', {}).get('purpose') != 'oprail-observer-management' or current['tags'].get('network') != args.network:
        raise ValueError('Unexpected management application')
    before = set(current.get('identity', {}).get('userAssignedIdentities', {}))
    if not current.get('identity', {}).get('principalId'):
        az('containerapp', 'identity', 'assign', '-g', rg, '-n', app, '--system-assigned')
    after = az('containerapp', 'show', '-g', rg, '-n', app)['identity']
    if before != set(after.get('userAssignedIdentities', {})):
        raise ValueError('Image-pull identities changed unexpectedly; inspect before continuing')
    runner = az('vm', 'show', '-g', rg, '-n', args.network + '-observer-runner')['identity']['principalId']
    observers = [vm['identity']['principalId'] for vm in az('vm', 'list', '-g', rg)
                 if vm.get('tags', {}).get('telcoin-role') == 'observer' and
                 vm.get('tags', {}).get('telcoin-chain') == args.network]
    if not observers:
        raise ValueError('No managed observers found')
    params = {'vaultName': {'value': keys['vault']}, 'network': {'value': args.network},
              'location': {'value': c['location']}, 'managementPrincipalId': {'value': after['principalId']},
              'runnerPrincipalId': {'value': runner}, 'observerPrincipalIds': {'value': observers}}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'parameters.json'
        path.write_text(json.dumps({'$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
                                    'contentVersion': '1.0.0.0', 'parameters': params}))
        az('deployment', 'group', 'create', '-g', rg, '-n', 'rpc-api-keys-' + args.network,
           '--template-file', str(ROOT / 'infra/rpc-tls/api-keys.bicep'), '--parameters', '@' + str(path))
    runtime_script = runtime_policy_script(args.network, keys)
    az('vm', 'run-command', 'invoke', '-g', rg, '-n', args.network + '-observer-runner',
       '--command-id', 'RunShellScript', '--scripts', runtime_script)
    # Existing app versions ignore this setting; the next approved website release enables the UI.
    settings = ['RpcKeys__Vault=' + keys['vault']]
    for tier, limits in keys['limits'].items():
        settings.extend('RpcKeys__Limits__'+tier+'__'+key+'='+str(value) for key, value in limits.items())
    az('containerapp', 'update', '-g', rg, '-n', app, '--set-env-vars', *settings)
    subprocess.run(['gh', 'variable', 'set', 'OBSERVER_RPC_KEY_VAULT', '--repo', 'SkunkWyrx/telcoin-network-cli',
                    '--env', 'observer-management-' + args.network, '--body', keys['vault']], check=True)
    print('Dedicated key vault, scoped identities, and website settings prepared. No observer restarted.')


if __name__ == '__main__':
    main()
