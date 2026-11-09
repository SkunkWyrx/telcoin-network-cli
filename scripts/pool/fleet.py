#!/usr/bin/env python3
"""Explicit, serialized observer operations. Defaults to plan-only; mutations require --apply."""
import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

from policy import complete_pairs, pinned_image, retention, upgrade_order

ROOT = Path(__file__).resolve().parents[2]


def execute(argv, capture=True, stdin=None, env=None):
    return subprocess.run(argv, input=stdin, text=True, check=True,
                          stdout=subprocess.PIPE if capture else None, env=env).stdout


class Fleet:
    def __init__(self, config, apply=False):
        self.c, self.apply = config, apply
        if config['network'] not in ('adiri', 'mainnet'):
            raise ValueError('Unsupported network')
        int(str(config['chain_id']), 0)
        upgrade_order(config['nodes'])
        self.config_path = None
        self.state = Path(os.getenv('OBSERVER_STATE_DIR', str(ROOT / '.observer-state'))) / config['network'] / config['pool']
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        inventory = self.state / 'nodes.json'
        if inventory.exists():
            self.c['nodes'] = json.loads(inventory.read_text())['nodes']
            upgrade_order(self.c['nodes'])

    def az(self, *args):
        return json.loads(execute(['az', *args, '--subscription', self.c['subscription'], '-o', 'json']) or 'null')

    def ssh(self, node, script):
        if not re.fullmatch(r'[A-Za-z0-9.-]+', node['host']):
            raise ValueError('Invalid SSH hostname')
        return execute(['ssh', '-i', str(Path(self.c['ssh_key']).expanduser()),
                        '-o', 'IgnoreUnknown=UseKeychain', '-o', 'UseKeychain=yes',
                        '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                        self.c.get('ssh_user', 'azureuser') + '@' + node['host'],
                        'sudo bash -se'], stdin=script)

    def save(self, name, data):
        path = self.state / name
        with tempfile.NamedTemporaryFile('w', dir=self.state, delete=False) as output:
            os.chmod(output.name, 0o600)
            json.dump(data, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        Path(output.name).replace(path)

    def vm(self, node):
        vm = self.az('vm', 'show', '-g', self.c['resource_group'], '-n', node['name'])
        if (vm.get('tags') or {}).get('telcoin-chain') != self.c['network']:
            raise ValueError('Refusing VM with wrong/missing network tag')
        return vm

    def synced(self, node):
        try:
            body = self.ssh(node, "curl -s --max-time 10 http://127.0.0.1:9090/status\n")
            return json.loads(body).get('ready') is True
        except (subprocess.CalledProcessError, ValueError):
            return False

    def wait_synced(self, node):
        deadline = time.monotonic() + self.c.get('upgrade_timeout_seconds', 7200)
        successes = 0
        while time.monotonic() < deadline:
            successes = successes + 1 if self.synced(node) else 0
            if successes >= self.c.get('ready_samples', 3):
                return
            time.sleep(self.c.get('poll_seconds', 10))
        raise RuntimeError('Sync gate timed out for ' + node['name'] + '; no later node will be changed')

    def install_health(self, node):
        print('Install readiness (no VM start):', node['name'])
        if not self.apply:
            return
        self.vm(node)
        config = {key: self.c[key] for key in
                  ('reference_rpc', 'chain_id', 'max_lag', 'poll_seconds', 'health_ttl_seconds')}
        self.ssh(node, 'install -d -m 755 /usr/local/lib/telcoin\ninstall -d -m 700 /etc/telcoin\n')
        for path, content, mode in [
            ('/usr/local/lib/telcoin/health.py', (ROOT / 'scripts/pool/health.py').read_text(), '755'),
            ('/etc/telcoin/readiness.json', json.dumps(config), '600'),
            ('/etc/systemd/system/telcoin-readiness.service', (ROOT / 'infra/pool/readiness.service').read_text(), '644'),
        ]:
            # Shell-quote the contents; never interpolate settings as executable shell text.
            self.ssh(node, 'printf %s ' + shlex.quote(content) + ' > ' + shlex.quote(path) +
                     '\nchmod ' + mode + ' ' + shlex.quote(path) + '\n')
        self.ssh(node, 'systemctl daemon-reload\nsystemctl enable --now telcoin-readiness\n')

    def build(self, ref):
        if not re.fullmatch(r'[A-Za-z0-9._/-]+', ref):
            raise ValueError('Invalid release ref')
        # A separate network repository and immutable unique tag prevent cross-network latest.
        if self.c['network'] == 'mainnet' and 'adiri' in self.c['features'].split(','):
            raise ValueError('Mainnet must not compile the adiri feature')
        if self.c['network'] == 'adiri' and 'adiri' not in self.c['features'].split(','):
            raise ValueError('Adiri requires its build feature')
        tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        print('Build', self.c['network'], ref, 'as', self.c['repository'] + ':' + tag)
        if not self.apply:
            return
        sha = execute(['git', 'ls-remote', self.c['git_repository'], ref, ref + '^{}']).splitlines()
        if not sha:
            raise ValueError('Release ref not found; use a full refs/tags/... or refs/heads/... ref')
        commit = next((s.split()[0] for s in sha if s.endswith('^{}')), sha[0].split()[0])
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            (context / 'src/docker').mkdir(parents=True)
            for name in ('Dockerfile', 'entrypoint.sh'):
                (context / 'src/docker' / name).write_bytes((ROOT / 'src/docker' / name).read_bytes())
            self.az('acr', 'build', '--registry', self.c['registry'], '-g', self.c['registry_resource_group'],
                    '--no-logs', '--platform', 'linux/amd64', '--image', self.c['repository'] + ':' + tag,
                    '--build-arg', 'GIT_TAG=', '--build-arg', 'GIT_BRANCH=' + commit,
                    '--build-arg', 'GIT_REPO_URL=' + self.c['git_repository'],
                    '--build-arg', 'NETWORK_FEATURES=' + self.c['features'],
                    '--file', 'src/docker/Dockerfile', str(context))
        manifest = self.az('acr', 'repository', 'show', '--name', self.c['registry'],
                           '--image', self.c['repository'] + ':' + tag)
        image = pinned_image(self.c['registry'] + '.azurecr.io/' + self.c['repository'] + '@' + manifest['digest'])
        # Build success is not promotion: an operator must validate before latest.json changes.
        release = {'network': self.c['network'], 'ref': ref, 'commit': commit, 'image': image,
                   'tag': tag, 'built_at': datetime.now(timezone.utc).isoformat(), 'validated': False}
        self.save('candidate.json', release)
        print('Candidate saved. Validate it, then explicitly promote its digest; no running nodes changed.')

    def promote(self, image):
        image = pinned_image(image)
        candidate = json.loads((self.state / 'candidate.json').read_text())
        if candidate['image'] != image or candidate['network'] != self.c['network']:
            raise ValueError('Digest does not match this network candidate')
        print('Explicit validation/promotion of', image)
        if self.apply:
            candidate['validated'] = True
            self.save('latest-release.json', candidate)

    def upgrade(self, image):
        image = pinned_image(image)
        expected = self.c['registry'] + '.azurecr.io/' + self.c['repository'] + '@'
        if not image.startswith(expected):
            raise ValueError('Image is outside this network release repository')
        nodes = upgrade_order(self.c['nodes'])
        if len(nodes) < 2 and not self.c.get('allow_single_node_maintenance', False):
            raise ValueError('At least two nodes required; opt into single-node downtime explicitly')
        print('Sequential upgrade order:', ', '.join(n['name'] for n in nodes))
        if not self.apply:
            return
        if not all(self.synced(node) for node in nodes):
            raise RuntimeError('All nodes must pass readiness before rolling upgrade begins')
        for node in nodes:
            self.vm(node)
            self.ssh(node, 'docker pull ' + shlex.quote(image) + '\n')
            self.ssh(node, 'install -d /run/telcoin\ntouch /run/telcoin/draining\n')
            time.sleep(self.c.get('drain_seconds', 30))
            self.save('upgrade-progress.json', {'node': node['name'], 'image': image, 'state': 'drained'})
            script = ('cp -p /etc/telcoin/image.conf /etc/telcoin/image.conf.before-upgrade\n'
                      'systemctl stop telcoin-observer\n'
                      'python3 - <<\'PY\'\nfrom pathlib import Path\np=Path("/etc/telcoin/image.conf")\n'
                      'lines=[s for s in p.read_text().splitlines() if not s.startswith("IMAGE=")]\n'
                      'p.write_text("\\n".join(lines)+"\\nIMAGE="+' + repr(image) + '+"\\n")\nPY\n'
                      'systemctl start telcoin-observer\n')
            self.ssh(node, script)
            # Exceptions leave this node drained and stop the entire sequence. Never blindly
            # downgrade a binary against a database whose schema may have changed.
            self.wait_synced(node)
            running = self.ssh(node, 'docker inspect telcoin-observer --format "{{.Config.Image}}"\n').strip()
            if running != image:
                raise RuntimeError('Unexpected running image; node remains drained')
            self.ssh(node, 'rm /run/telcoin/draining\n')
            self.save('upgrade-progress.json', {'node': node['name'], 'image': image, 'state': 'ready'})
            print(node['name'], 'synced and returned to service')

    def pairs(self):
        snapshots = self.az('snapshot', 'list', '-g', self.c['resource_group'])
        return complete_pairs(snapshots, self.c['network'], self.c['pool'])

    def snapshot(self, source=None):
        nodes = upgrade_order(self.c['nodes'])
        if not nodes:
            raise ValueError('No nodes configured')
        node = next((n for n in nodes if n['name'] == source), None) if source else nodes[0]
        if node is None:
            raise ValueError('Unknown snapshot source: ' + source)
        print('Capture paired data disks from:', node['name'])
        if not self.apply:
            return
        vm = self.vm(node)
        instance = self.az('vm', 'get-instance-view', '-g', self.c['resource_group'], '-n', node['name'])
        running = any(s['code'] == 'PowerState/running' for s in instance['instanceView']['statuses'])
        if not running:
            raise ValueError('Scheduled capture requires a running, healthy source; never starts a stopped node')
        if not self.synced(node):
            raise RuntimeError('Snapshot source is not synced')
        others = [n for n in nodes if n['name'] != node['name']]
        if not any(self.synced(n) for n in others) and not self.c.get('allow_single_node_maintenance', False):
            raise RuntimeError('No second ready observer; snapshot downtime needs explicit opt-in')
        disks = {d['lun']: d['managedDisk']['id'] for d in vm['storageProfile']['dataDisks']}
        if set(disks) != {0, 1}:
            raise ValueError('Expected exactly the execution and consensus data disks')
        stamp = datetime.now(timezone.utc)
        capture = self.c['pool'] + '-' + stamp.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
        created = []
        self.ssh(node, 'install -d /run/telcoin\n(set -o noclobber; printf %s snapshot > /run/telcoin/draining)\n')
        try:
            time.sleep(self.c.get('drain_seconds', 30))
            self.ssh(node, '''since=$(date -u +%FT%T.%NZ)
id=$(docker inspect telcoin-observer --format '{{.Id}}')
systemctl stop telcoin-observer
test -z "$(docker ps -q --filter "id=$id")"
events=$(docker events --since "$since" --until "$(date -u +%FT%T.%NZ)" --filter "container=$id" --filter event=die --format '{{.Actor.Attributes.exitCode}}')
case "$events" in
  0|143) ;;
  *) echo "Could not verify graceful observer exit (Docker exit: $events)" >&2; exit 1 ;;
esac
sync
''')
            self.az('vm', 'deallocate', '-g', self.c['resource_group'], '-n', node['name'])
            for lun, kind in [(0, 'execution'), (1, 'consensus')]:
                tags = ['managed-by=oprail-observer-fleet', 'network=' + self.c['network'],
                        'pool=' + self.c['pool'], 'capture=' + capture, 'disk=' + kind,
                        'capture-status=pending', 'captured-at=' + stamp.isoformat()]
                snapshot = self.az('snapshot', 'create', '-g', self.c['resource_group'],
                                   '-n', capture + '-' + kind, '--location', self.c['location'],
                                   '--source', disks[lun], '--incremental', 'true', '--tags', *tags)
                created.append(snapshot['id'])
        finally:
            self.az('vm', 'start', '-g', self.c['resource_group'], '-n', node['name'])
            # Boot/SSH can take minutes. Retry without allowing a failed capture to skip recovery.
            for attempt in range(60):
                try:
                    self.ssh(node, 'systemctl start telcoin-observer\n')
                    break
                except subprocess.CalledProcessError:
                    if attempt == 59:
                        raise
                    time.sleep(5)
            self.wait_synced(node)
            self.ssh(node, 'rm -f /run/telcoin/draining\n')
        deadline = time.monotonic() + self.c.get('snapshot_copy_timeout_seconds', 14400)
        while time.monotonic() < deadline:
            copies = [self.az('snapshot', 'show', '--ids', resource) for resource in created]
            if all(s.get('completionPercent') == 100 and s.get('provisioningState') == 'Succeeded' for s in copies):
                break
            time.sleep(30)
        else:
            raise RuntimeError('Copies not complete; partial capture retained, latest unchanged')
        for resource in created:
            self.az('tag', 'update', '--resource-id', resource, '--operation', 'Merge',
                    '--tags', 'capture-status=complete')
        pairs = self.pairs()
        self.save('latest-snapshot.json', self.serial_pair(pairs[-1]))
        print('Latest validated pair:', capture)

    @staticmethod
    def serial_pair(pair):
        return {**pair, 'created': pair['created'].isoformat()}

    def prune(self):
        keep, delete = retention(self.pairs(), datetime.now(timezone.utc), self.c.get('snapshot_retention_days', 7))
        print(json.dumps({'keep': [p['capture'] for p in keep], 'delete': [p['capture'] for p in delete]}, indent=2))
        if self.apply:
            for pair in delete:
                for kind in ('execution', 'consensus'):
                    self.az('snapshot', 'delete', '--ids', pair[kind])

    def add(self, env_file, candidate_image=None):
        if candidate_image:
            image = pinned_image(candidate_image)
            expected = self.c['registry'] + '.azurecr.io/' + self.c['repository'] + '@'
            if not image.startswith(expected):
                raise ValueError('Candidate image is outside the network release repository')
            print('Use an explicit candidate for this new node only:', image)
        print('Provision from latest completed snapshot pair using', env_file)
        if self.c.get('public_rpc'):
            print('Public HTTPS membership requires private sync and a verified TLS proxy.')
        if not self.apply:
            return
        pairs = self.pairs()
        if not pairs:
            raise ValueError('No completed owned snapshot pair; refuse genesis fallback')
        if not candidate_image:
            release = json.loads((self.state / 'latest-release.json').read_text())
            if release.get('network') != self.c['network'] or not release.get('validated'):
                raise ValueError('No promoted release for this network')
            image = pinned_image(release['image'])
        if not image.startswith(self.c['registry'] + '.azurecr.io/'):
            raise ValueError('Promoted release uses a different registry')
        pair = pairs[-1]
        env = dict(os.environ, ENV_FILE=str(Path(env_file).resolve()),
                   AZURE_SUBSCRIPTION_ID=self.c['subscription'], AZURE_RESOURCE_GROUP=self.c['resource_group'],
                   ACR_NAME=self.c['registry'], ACR_RESOURCE_GROUP=self.c['registry_resource_group'],
                   CHAIN=self.c['network'], EXPECTED_CHAIN_ID=self.c['chain_id'], NETWORK_RPC_URL=self.c['reference_rpc'],
                   SKIP_IMAGE_BUILD='true', IMAGE_REPO=image.split('.azurecr.io/', 1)[1].split('@')[0], IMAGE_DIGEST=image.split('@')[1],
                   EXECUTION_SNAPSHOT_ID=pair['execution'], CONSENSUS_SNAPSHOT_ID=pair['consensus'],
                   EXISTING_SUBNET_ID=self.subnet_id(), BACKEND_POOL_ID=self.backend_id(),
                   PRIVATE_RPC_SOURCE_CIDRS=self.c.get('private_rpc_source_cidrs', self.c.get('api_cidr', '10.51.0.0/16')), PUBLIC_RPC_ENABLED='false')
        env['AZURE_LOCATION'] = self.c['location']
        settings = json.loads(execute(['bash', '-c',
            'set -a; source "$1"; python3 -c ' + shlex.quote(
                'import json,os; print(json.dumps({k:os.environ.get(k) for k in ["VM_NAME","VM_DNS_LABEL"]}))'),
            'node-settings', str(Path(env_file).resolve())], env=env))
        if not settings.get('VM_NAME') or not settings.get('VM_DNS_LABEL'):
            raise ValueError('Node settings require VM_NAME and VM_DNS_LABEL')
        if any(n['name'] == settings['VM_NAME'] for n in self.c['nodes']):
            raise ValueError('Node name already belongs to this pool')
        env.update(settings)
        execute(['bash', str(ROOT / 'scripts/deploy-azure-vm.sh')], capture=False, env=env)
        node = {'name': settings['VM_NAME'],
                'host': settings['VM_DNS_LABEL'] + '.' + self.c['location'] + '.cloudapp.azure.com',
                'ordinal': max([n['ordinal'] for n in self.c['nodes']] or [0]) + 1}
        self.wait_synced(node)
        self.c['nodes'].append(node)
        self.save_profile()
        print('Node synced and added to the pool profile:', node['name'])
        if self.c.get('public_rpc'):
            self.install_public_rpc(node)

    def install_public_rpc(self, node):
        config = self.c.get('public_rpc')
        if not config:
            raise ValueError('No public RPC configuration for this pool')
        if node['name'] == config['issuer']:
            raise ValueError('Refusing to reconfigure the certificate issuer as a follower')
        if not re.fullmatch(r'[a-z0-9.-]+', config['hostname']):
            raise ValueError('Invalid public hostname')
        print('Set up public HTTPS after private sync:', node['name'])
        if not self.apply:
            return
        vm = self.vm(node)
        if not self.synced(node):
            raise ValueError('Observer must be synced before public HTTPS setup')
        rg = self.c['resource_group']
        if len(vm['networkProfile']['networkInterfaces']) != 1:
            raise ValueError('Expected exactly one observer NIC')
        nic = self.az('network', 'nic', 'show', '--ids', vm['networkProfile']['networkInterfaces'][0]['id'])
        nsg = nic['networkSecurityGroup']['id'].rsplit('/', 1)[-1]
        if nsg != node['name'] + '-nsg':
            raise ValueError('Refusing to edit a shared or unexpected NSG')
        ipconfig = next(ip for ip in nic['ipConfigurations'] if ip['name'] == 'primary')
        if self.backend_id().lower() not in [p['id'].lower() for p in ipconfig.get('loadBalancerBackendAddressPools', [])]:
            raise ValueError('Observer is missing its private pool membership')
        backend = str(ipaddress.IPv4Address(ipconfig['privateIPAddress']))
        frontend = str(ipaddress.IPv4Address(config['frontend']))
        public_ip = self.az('network', 'public-ip', 'show', '-g', rg, '-n', config['public_ip_name'])
        if public_ip['ipAddress'] != frontend:
            raise ValueError('Public IP does not match pool configuration')
        lb = self.az('network', 'lb', 'show', '-g', rg, '-n', config['load_balancer'])
        if not any((f.get('publicIPAddress') or {}).get('id', '').lower() == public_ip['id'].lower()
                   for f in lb['frontendIPConfigurations']):
            raise ValueError('Public IP is not attached to the configured load balancer')
        image = pinned_image(config['proxy_image'])
        vault = self.az('keyvault', 'show', '-g', rg, '-n', config['vault'])
        self.az('role', 'assignment', 'create', '--assignee-object-id', vm['identity']['principalId'],
                '--assignee-principal-type', 'ServicePrincipal', '--role', 'Key Vault Secrets User',
                '--scope', vault['id'] + '/secrets/' + config['secret'])
        files = ROOT / 'infra/rpc-tls'
        replacements = {'@FRONTEND_IP@': frontend, '@BACKEND_IP@': backend,
                        '@HOSTNAME@': config['hostname'], '@IMAGE@': image}
        commands = ['install -d -m 0700 /etc/telcoin/rpc-tls']
        for source, destination in (
            ('nginx-public.conf.in', '/etc/telcoin/rpc-tls/nginx.conf'),
            ('telcoin-rpc-tls.service.in', '/etc/systemd/system/telcoin-rpc-tls.service'),
            ('cert-sync.py', '/etc/telcoin/cert-sync.py'),
            ('oprail-rpc-cert-sync.service', '/etc/systemd/system/oprail-rpc-cert-sync.service'),
            ('oprail-rpc-cert-sync.timer', '/etc/systemd/system/oprail-rpc-cert-sync.timer'),
        ):
            content = (files / source).read_text()
            for tag, value in replacements.items():
                content = content.replace(tag, value)
            commands.append(f'printf %s {shlex.quote(content)} > {shlex.quote(destination)}')
        bundle = json.dumps({k: config[k] for k in ('vault', 'secret', 'hostname', 'frontend')})
        commands.extend([
            'umask 077; printf %s ' + shlex.quote(bundle) + ' > /etc/telcoin/cert-sync.json',
            'docker pull ' + shlex.quote(image),
            'for attempt in $(seq 1 24); do if python3 /etc/telcoin/cert-sync.py pull; then break; fi; '
            'if [ "$attempt" = 24 ]; then exit 1; fi; sleep 10; done',
            'systemctl daemon-reload',
            'systemctl enable --now telcoin-rpc-tls oprail-rpc-cert-sync.timer',
            'for attempt in $(seq 1 10); do if curl -fsS --max-time 4 http://' + backend + ':19091/readyz; '
            'then break; fi; if [ "$attempt" = 10 ]; then exit 1; fi; sleep 2; done',
            'timeout 8 openssl s_client -connect ' + frontend + ':443 -servername ' + config['hostname']
            + ' -verify_hostname ' + config['hostname'] + ' -verify_return_error </dev/null >/dev/null',
        ])
        self.ssh(node, '\n'.join(commands) + '\n')
        for name, priority, source, address, port in (
            ('Observer-Public-HTTPS', '118', 'Internet', frontend + '/32', '443'),
            ('Observer-Public-Proxy-Probe', '119', 'AzureLoadBalancer', backend + '/32', '19091'),
        ):
            self.az('network', 'nsg', 'rule', 'create', '-g', rg, '--nsg-name', nsg, '-n', name,
                    '--priority', priority, '--direction', 'Inbound', '--access', 'Allow', '--protocol', 'Tcp',
                    '--source-address-prefixes', source, '--source-port-ranges', '*',
                    '--destination-address-prefixes', address, '--destination-port-ranges', port)
        self.az('network', 'nic', 'ip-config', 'address-pool', 'add', '-g', rg, '--nic-name', nic['name'],
                '--ip-config-name', ipconfig['name'], '--lb-name', config['load_balancer'], '--address-pool', 'observers')
        print('Added verified HTTPS proxy to public backend pool:', node['name'])

    def save_profile(self):
        # Runtime inventory survives runner checkout cleanup, unlike edits to a tracked profile.
        self.save('nodes.json', {'nodes': self.c['nodes']})

    def subnet_id(self):
        return (f"/subscriptions/{self.c['subscription']}/resourceGroups/{self.c['resource_group']}/providers/"
                f"Microsoft.Network/virtualNetworks/{self.c['observer_vnet']}/subnets/{self.c['observer_subnet']}")

    def backend_id(self):
        return (f"/subscriptions/{self.c['subscription']}/resourceGroups/{self.c['resource_group']}/providers/"
                f"Microsoft.Network/loadBalancers/{self.c.get('load_balancer', 'adiri-observers')}/backendAddressPools/observers")

    def remove(self, name):
        node = next(n for n in self.c['nodes'] if n['name'] == name)
        print('Delete node VM and its exact attached disks/NIC/public IP/NSG; preserve shared VNet and LB:', name)
        if not self.apply:
            return
        others = [n for n in self.c['nodes'] if n['name'] != name]
        if not any(self.synced(n) for n in others):
            raise ValueError('Refuse removal without another synced observer')
        vm = self.vm(node)
        self.ssh(node, 'install -d /run/telcoin\ntouch /run/telcoin/draining\n')
        time.sleep(self.c.get('drain_seconds', 30))
        disks = [vm['storageProfile']['osDisk']['managedDisk']['id']]
        disks += [d['managedDisk']['id'] for d in vm['storageProfile']['dataDisks']]
        nics = [self.az('network', 'nic', 'show', '--ids', n['id']) for n in vm['networkProfile']['networkInterfaces']]
        self.az('vm', 'delete', '-g', self.c['resource_group'], '-n', name, '--yes')
        for nic in nics:
            ips = [i['publicIPAddress']['id'] for i in nic['ipConfigurations'] if i.get('publicIPAddress')]
            nsg = nic.get('networkSecurityGroup', {}).get('id')
            self.az('network', 'nic', 'delete', '--ids', nic['id'])
            for ip in ips:
                self.az('network', 'public-ip', 'delete', '--ids', ip)
            if nsg and nsg.rsplit('/', 1)[-1] == name + '-nsg':
                self.az('network', 'nsg', 'delete', '--ids', nsg)
        for disk in disks:
            self.az('disk', 'delete', '--ids', disk, '--yes')
        principal = (vm.get('identity') or {}).get('principalId')
        if principal:
            assignments = self.az('role', 'assignment', 'list', '--all', '--query', "[?principalId=='" + principal + "']")
            for assignment in assignments:
                self.az('role', 'assignment', 'delete', '--ids', assignment['id'])
        self.c['nodes'] = [n for n in self.c['nodes'] if n['name'] != name]
        self.save_profile()
        print('Node removed from profile; shared pool resources retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(ROOT / 'infra/pool/adiri.json'))
    parser.add_argument('--apply', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('plan')
    sub.add_parser('snapshot').add_argument('--node', help='Snapshot this node instead of the highest ordinal')
    sub.add_parser('prune')
    for name, argument in [('build', 'ref'), ('promote', 'image'), ('upgrade', 'image'),
                           ('install-health', 'node'), ('install-public-rpc', 'node'), ('add', 'env_file'), ('remove', 'node')]:
        sub.add_parser(name).add_argument(argument)
    sub.choices['add'].add_argument('--candidate-image', help='Explicit digest for a new node trial; does not promote')
    args = parser.parse_args()
    fleet = Fleet(json.loads(Path(args.config).read_text()), args.apply)
    fleet.config_path = Path(args.config).resolve()
    with (fleet.state / 'operation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == 'plan':
            print(json.dumps({'network': fleet.c['network'], 'resource_group': fleet.c['resource_group'],
                              'upgrade_order': [n['name'] for n in upgrade_order(fleet.c['nodes'])],
                              'snapshot_interval_hours': fleet.c['snapshot_interval_hours'],
                              'retention_days': fleet.c['snapshot_retention_days'],
                              'automation_enabled': fleet.c['automation_enabled']}, indent=2))
        elif args.command in ('install-health', 'install-public-rpc'):
            getattr(fleet, args.command.replace('-', '_'))(next(n for n in fleet.c['nodes'] if n['name'] == args.node))
        elif args.command == 'snapshot':
            fleet.snapshot(args.node)
        elif args.command == 'add':
            fleet.add(args.env_file, args.candidate_image)
        else:
            argument = getattr(args, {'build': 'ref', 'promote': 'image', 'upgrade': 'image',
                                      'add': 'env_file', 'remove': 'node'}.get(args.command, ''), None)
            getattr(fleet, args.command)(*([argument] if argument is not None else []))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError, StopIteration) as error:
        print('Stopped safely:', str(error), file=sys.stderr)
        raise SystemExit(1)
