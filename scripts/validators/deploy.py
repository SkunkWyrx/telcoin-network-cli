#!/usr/bin/env python3
"""Independent validator deployment. Plan by default; --apply never targets observers."""
import argparse
import base64
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import uuid

from profile import ROOT, load_profile, plan, validate


def az(*args):
    return json.loads(subprocess.check_output(['az', *args, '-o', 'json'], text=True) or 'null')


def cloud_init(c):
    vm = c['node_name_prefix'] + '1'
    root = f"/subscriptions/{c['subscription']}/resourceGroups/{c['resource_group']}/providers/Microsoft.Compute/"
    files = [('/etc/modprobe.d/telcoin-algif-aead.conf', 'install algif_aead /bin/false\n', '0644'),
             ('/etc/oprail-validator/profile.json', json.dumps(c), '0600'),
             ('/usr/local/sbin/oprail-validator', (ROOT/'scripts/validators/guest.py').read_text(), '0700'),
             ('/usr/local/sbin/telcoin-prepare-disks', (ROOT/'infra/vm/prepare-disks.sh').read_text(), '0700')]
    growth = c['storage']['auto_grow']
    if growth['enabled']:
        policy = {'vm_id': root+'virtualMachines/'+vm, 'threshold_percent': growth['threshold_percent'],
                  'increment_gb': growth['increment_gb'], 'disks': [
                      {'id': root+'disks/'+vm+'-'+kind, 'lun': i, 'mount': mount}
                      for i, (kind, mount) in enumerate([('execution','/var/lib/telcoin/db'),('consensus','/mnt/telcoin-consensus')])]}
        files += [('/etc/telcoin/disk-growth.json', json.dumps(policy), '0600'),
                  ('/usr/local/sbin/telcoin-grow-disks',(ROOT/'infra/vm/grow-disks.py').read_text(),'0700'),
                  ('/etc/systemd/system/telcoin-disk-growth.service',(ROOT/'infra/vm/telcoin-disk-growth.service').read_text(),'0644'),
                  ('/etc/systemd/system/telcoin-disk-growth.timer',(ROOT/'infra/vm/telcoin-disk-growth.timer').read_text(),'0644')]
    result = '#cloud-config\npackage_update: true\npackages: [docker.io, e2fsprogs, python3, curl, chrony, tpm2-tools, azure-vm-utils, nvme-cli]\nwrite_files:\n'
    for target, content, permissions in files:
        result += f'  - path: {target}\n    permissions: "{permissions}"\n    encoding: b64\n    content: '+base64.b64encode(content.encode()).decode()+'\n'
    commands = [['udevadm','control','--reload-rules'],['udevadm','trigger','--subsystem-match=block'],
                ['udevadm','settle','--timeout=60'],['/usr/local/sbin/telcoin-prepare-disks'],
                ['swapoff','-a'],['bash','-c','modprobe -r algif_aead 2>/dev/null || true'],['bash','-c',"sed -i '/^[^#].*[[:space:]]swap[[:space:]]/s/^/# /' /etc/fstab"],
                ['systemctl','enable','--now','chrony','docker'],['systemctl','daemon-reload']]
    if growth['enabled']: commands.append(['systemctl','enable','--now','telcoin-disk-growth.timer'])
    return result+'runcmd:\n'+''.join('  - '+json.dumps(command)+'\n' for command in commands)


def parameters(c, ssh_key, operator):
    validate(c)
    if not re.fullmatch(r'ssh-(?:ed25519|rsa) [A-Za-z0-9+/=]+(?: .*)?', ssh_key.strip()):
        raise ValueError('Supply a valid SSH public key; inbound SSH remains closed by default')
    uuid.UUID(operator)
    n, storage = c['networking'], c['storage']
    values = {'location': c['location'], 'network': c['network'], 'vmName': c['node_name_prefix']+'1',
              'vmSize': c['vm']['size'], 'zone': c['vm']['zone'], 'sshPublicKey': ssh_key.strip(),
              'dnsLabel': c['dns_label'], 'vnetName': n['vnet_name'], 'vnetCidr': n['address_prefix'],
              'nodeSubnetCidr': n['node_subnet']['prefix'], 'managementSubnetCidr': n['management_subnet']['prefix'],
              'sshSources': n['ssh_source_cidrs'], 'p2pSources': n['p2p_source_cidrs'],
              'executionSize': storage['execution']['size_gb'], 'consensusSize': storage['consensus']['size_gb'],
              'executionIops': storage['execution']['iops'], 'consensusIops': storage['consensus']['iops'],
              'executionMbps': storage['execution']['throughput_mbps'], 'consensusMbps': storage['consensus']['throughput_mbps'],
              'customData': base64.b64encode(cloud_init(c).encode()).decode(),
              'vaultName': c['identity']['passphrase_vault'], 'operatorPrincipalId': operator,
              'autoGrow': storage['auto_grow']['enabled']}
    return {'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
            'contentVersion':'1.0.0.0','parameters':{k:{'value':v} for k,v in values.items()}}


def validate_phase(c, phase, backup=False):
    validate(c)
    if phase in ('prepare','finalize') and (not c['release']['tag'] or not c['identity']['execution_address']):
        raise ValueError('Supply this network\'s signed release tag and confirmed execution/staking address')
    if phase == 'finalize' and (not backup or not c['networking']['p2p_source_cidrs']):
        raise ValueError('Finalization requires --acknowledge-key-backup and approved P2P source CIDRs')


def configure_network(c, vm):
    if not c['networking']['p2p_source_cidrs']:
        raise ValueError('Supply approved P2P sources before opening validator ports')
    name = vm + '-nsg'
    nsg = az('network', 'nsg', 'show', '-g', c['resource_group'], '-n', name)
    if nsg.get('tags', {}).get('purpose') != 'oprail-validator' or nsg['tags'].get('network') != c['network']:
        raise ValueError('Refusing an unrelated network security group')
    az('network', 'nsg', 'rule', 'create', '-g', c['resource_group'], '--nsg-name', name,
       '-n', 'Approved-P2P', '--priority', '110', '--direction', 'Inbound', '--access', 'Allow',
       '--protocol', 'Udp', '--source-address-prefixes', *c['networking']['p2p_source_cidrs'],
       '--source-port-ranges', '*', '--destination-address-prefixes', '*',
       '--destination-port-ranges', '49590', '49594')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--network', choices=['adiri','mainnet'], required=True)
    p.add_argument('--config', type=Path)
    p.add_argument('--phase', choices=['plan','provision','set-passphrase','prepare','export','finalize','network','status'], default='plan')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--ssh-public-key-file', type=Path)
    p.add_argument('--operator-principal-id')
    p.add_argument('--release-tag')
    p.add_argument('--execution-address')
    p.add_argument('--acknowledge-key-backup', action='store_true')
    args = p.parse_args()
    c = load_profile(args.config or ROOT/'infra/validators'/(args.network+'.json'))
    if c['network'] != args.network: raise ValueError('The selected network does not match the profile')
    if args.release_tag is not None: c['release']['tag'] = args.release_tag
    if args.execution_address is not None: c['identity']['execution_address'] = args.execution_address
    validate_phase(c, args.phase, args.acknowledge_key_backup)
    if not args.apply or args.phase == 'plan':
        print(json.dumps({'phase': args.phase, 'apply': False, **plan(c)},indent=2))
        return
    if az('account','show')['id'] != c['subscription']: raise ValueError('Select the configured subscription first')
    rg, vm = c['resource_group'], c['node_name_prefix']+'1'
    exists = az('group','exists','-n',rg)
    if exists:
        tags = az('group','show','-n',rg).get('tags') or {}
        if tags.get('purpose')!='oprail-validator' or tags.get('network')!=c['network']:
            raise ValueError('Refusing an unowned resource group')
    if args.phase == 'provision':
        if exists:
            vms = az('vm','list','-g',rg)
            if vms:
                if len(vms)!=1 or vms[0]['name']!=vm or vms[0].get('tags',{}).get('telcoin-chain')!=c['network'] or vms[0].get('tags',{}).get('telcoin-role')!='validator':
                    raise ValueError('Unexpected existing validators; refusing overwrite')
                print('Existing validator retained; provision will not redeploy or replace it:',vm)
                return
            if az('resource','list','-g',rg): raise ValueError('Partial provisioning requires inspection before retry')
        if not args.ssh_public_key_file: raise ValueError('Supply --ssh-public-key-file')
        operator = args.operator_principal_id
        if not operator:
            access = az('account','get-access-token','--resource','https://management.azure.com/')['accessToken']
            payload = access.split('.')[1]
            operator = json.loads(base64.urlsafe_b64decode(payload+'='*((-len(payload))%4)))['oid']
        params = parameters(c,args.ssh_public_key_file.read_text(),operator)
        if not exists: az('group','create','-n',rg,'-l',c['location'],'--tags','purpose=oprail-validator','network='+c['network'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'parameters.json'; path.write_text(json.dumps(params))
            az('deployment','group','create','-g',rg,'-n','validator-'+c['network'],
               '--template-file',str(ROOT/'infra/validators/main.bicep'),'--parameters','@'+str(path))
        print('Infrastructure prepared; no Telcoin node service has been started.')
        return
    if not exists: raise ValueError('Provision this network first')
    node = az('vm','show','-g',rg,'-n',vm)
    if node.get('tags',{}).get('telcoin-role')!='validator' or node['tags'].get('telcoin-chain')!=c['network']:
        raise ValueError('Refusing a non-validator or different-network VM')
    if args.phase == 'network':
        configure_network(c,vm)
        print('Validator P2P source rules updated; no node restarted.')
        return
    if args.phase == 'set-passphrase':
        vault, name = c['identity']['passphrase_vault'], c['identity']['passphrase_secret']
        if any(item['name']==name for item in az('keyvault','secret','list','--vault-name',vault)):
            raise ValueError('A passphrase already exists; rotation is a separate identity-preserving operation')
        password = getpass.getpass('Validator BLS passphrase (at least 16 bytes): ')
        if password!=getpass.getpass('Repeat passphrase: ') or len(password.encode())<16 or password!=password.strip() or '\n' in password or '\r' in password:
            raise ValueError('Passphrases must match and be at least 16 bytes with no surrounding whitespace')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'value';path.touch(mode=0o600);path.write_text(password)
            subprocess.run(['az','keyvault','secret','set','--vault-name',vault,'-n',name,'--file',str(path),'--output','none'],check=True)
        print('Passphrase stored in this validator network\'s vault; its value was not printed.')
        return
    if args.phase == 'finalize':
        req=urllib.request.Request(c['reference_rpc'],data=json.dumps({'jsonrpc':'2.0','id':1,'method':'eth_chainId','params':[]}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=15) as response: actual=int(json.load(response)['result'],16)
        if actual!=int(c['release']['chain_id'],0): raise ValueError('Reference RPC is on another chain; obtain the internal mainnet endpoint before finalizing')
        configure_network(c,vm)
    ip=str(ipaddress.IPv4Address(az('network','public-ip','show','-g',rg,'-n',vm+'-ip')['ipAddress']))
    # Only public configuration crosses ARM. The VM retrieves its passphrase itself.
    encoded=base64.b64encode(json.dumps(c).encode()).decode()
    script="set -eu\npython3 - <<'PYREMOTE'\nimport base64,json,pathlib\nc=json.loads(base64.b64decode('"+encoded+"'))\np=pathlib.Path('/etc/oprail-validator/profile.json')\nold=json.loads(p.read_text())\nif old['network']!=c['network']: raise RuntimeError('Network mismatch')\ns=pathlib.Path('/etc/oprail-validator/state.json')\nif s.exists():\n state=json.loads(s.read_text())\n if c['identity']['execution_address'] and state['address']!=c['identity']['execution_address']: raise RuntimeError('Identity mismatch')\n if c['release']['tag'] and state['tag']!=c['release']['tag']: raise RuntimeError('Release mismatch')\n if not c['identity']['execution_address']: c['identity']['execution_address']=state['address']\n if not c['release']['tag']: c['release']['tag']=state['tag']\np.write_text(json.dumps(c))\nPYREMOTE\n"
    script+='python3 /usr/local/sbin/oprail-validator --expected-network '+c['network']+' --phase '+args.phase+' --public-ip '+ip
    if args.acknowledge_key_backup: script+=' --acknowledge-key-backup'
    result=az('vm','run-command','invoke','-g',rg,'-n',vm,'--command-id','RunShellScript','--scripts',script+'\n')
    messages='\n'.join(v.get('message','') for v in result.get('value',[]))
    if '[stderr]' in messages and messages.split('[stderr]',1)[1].strip():
        raise RuntimeError('Validator operation failed; no private output was forwarded')
    output=messages.split('[stdout]',1)[-1].split('[stderr]',1)[0].strip()
    data=json.loads(output)
    if args.phase == 'export':
        directory=ROOT/'.validator-state'/c['network'];directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=directory/'onboarding.json';path.write_text(json.dumps(data,indent=2)+'\n');path.chmod(0o600)
        print('Public-only onboarding file:',path)
    print(json.dumps(data,indent=2))


if __name__ == '__main__':
    try: main()
    except (ValueError,KeyError,OSError,RuntimeError,subprocess.CalledProcessError) as error:
        raise SystemExit(str(error) if isinstance(error,(ValueError,RuntimeError)) else 'Validator deployment failed: '+type(error).__name__)
