#!/usr/bin/env python3
"""Trusted runner entry point. Never accept an env path or shell command from the web."""
import argparse
import fcntl
import json
import os
import secrets
import shlex
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone
from fleet import Fleet
from policy import pinned_image


def validate(operation, argument):
    patterns = {'add': r'[1-9][0-9]?', 'remove': r'[A-Za-z0-9][A-Za-z0-9-]{0,63}',
                'snapshot': r'[A-Za-z0-9][A-Za-z0-9-]{0,63}',
                'build': r'refs/(tags|heads)/[A-Za-z0-9._/-]+',
                'upgrade-tag': r'v[0-9]+\.[0-9]+\.[0-9]+(?:[.-][A-Za-z0-9._-]+)?',
                'upgrade': r'[a-z0-9./-]+@sha256:[a-f0-9]{64}'}
    if operation not in patterns or len(argument) > 300 or not re.fullmatch(patterns[operation], argument):
        raise ValueError('Invalid management operation')


def reconcile(fleet):
    """Refuse stale inventory before allowing destructive or snapshot-based operations."""
    pool = fleet.az('network', 'lb', 'address-pool', 'show', '-g', fleet.c['resource_group'],
                    '--lb-name', fleet.c.get('load_balancer', fleet.c['network'] + '-observers'), '-n', 'observers')
    if pool.get('loadBalancerBackendAddresses'):
        raise ValueError('IP-based backend inventory is not supported')
    actual = set()
    root = f"/subscriptions/{fleet.c['subscription']}/resourceGroups/{fleet.c['resource_group']}/providers/"
    for item in pool.get('backendIPConfigurations', []):
        nic_id = item['id'].split('/ipConfigurations/')[0]
        if not nic_id.lower().startswith((root + 'Microsoft.Network/networkInterfaces/').lower()):
            raise ValueError('Backend outside managed resource group')
        nic = fleet.az('network', 'nic', 'show', '--ids', nic_id)
        vm_id = nic.get('virtualMachine', {}).get('id', '')
        if not vm_id.lower().startswith((root + 'Microsoft.Compute/virtualMachines/').lower()):
            raise ValueError('Unexpected VM scope')
        actual.add(vm_id.rsplit('/', 1)[-1])
    if actual != {node['name'] for node in fleet.c['nodes']}:
        raise ValueError('Load-balancer membership differs from controller inventory; reconcile before changing nodes')
    if not actual:
        raise ValueError('No existing pool members')
    return actual


def run(fleet, operation, argument, env_root=Path('/etc/oprail-observers')):
    validate(operation, argument)
    if operation == 'build':
        fleet.build(argument)
        return
    actual = reconcile(fleet)
    if operation == 'snapshot':
        if argument not in actual:
            raise ValueError('Snapshot source is not in the managed pool')
        fleet.snapshot(argument)
    elif operation == 'remove':
        if argument == (fleet.c.get('public_rpc') or {}).get('issuer'):
            raise ValueError('Move the certificate issuer before removing this node')
        if len(actual) < 2 or argument not in actual:
            raise ValueError('Cannot remove final or unknown observer')
        fleet.remove(argument)
    elif operation == 'upgrade-tag':
        if fleet.c['network'] == 'adiri' and not argument.endswith('-adiri'):
            raise ValueError('Adiri upgrades require an Adiri release tag')
        if fleet.c['network'] == 'mainnet' and 'adiri' in argument:
            raise ValueError('An Adiri tag cannot be deployed to mainnet')
        print('Stage: resolve tag and build pinned candidate', flush=True)
        fleet.build('refs/tags/' + argument)
        release = json.loads((fleet.state / 'candidate.json').read_text())
        if release.get('network') != fleet.c['network'] or release.get('ref') != 'refs/tags/' + argument:
            raise ValueError('Build candidate does not match requested network and tag')
        image = pinned_image(release['image'])
        print('Stage: roll out one node at a time with sync gates', flush=True)
        fleet.upgrade(image)
        print('Stage: promote only after all nodes pass rollout', flush=True)
        fleet.promote(image)
    elif operation == 'upgrade':
        image = pinned_image(argument)
        release = json.loads((fleet.state / 'latest-release.json').read_text())
        if release.get('network') != fleet.c['network'] or not release.get('validated') or release['image'] != image:
            raise ValueError('Only the validated promoted release for this network may be deployed')
        fleet.upgrade(image)
    elif operation == 'add':
        # Slot files are operator-provisioned secrets, never browser-supplied paths.
        path = env_root / fleet.c['network'] / f'node-{argument}.env'
        if not path.is_file():
            template = env_root / fleet.c['network'] / 'template.env'
            if not template.is_file():
                raise ValueError('Configure the network node template on the runner first')
            prefix = fleet.c.get('node_name_prefix', 'telcoin-observer-vm-')
            dns = fleet.c.get('dns_label_prefix', 'adiri-oprail-observer-' if fleet.c['network'] == 'adiri' else '')
            if not dns or not re.fullmatch(r'[A-Za-z0-9-]+', prefix) or not re.fullmatch(r'[a-z0-9-]+', dns):
                raise ValueError('Configure safe network-specific node name prefixes')
            slots = fleet.state / 'node-slots'
            slots.mkdir(mode=0o700, exist_ok=True)
            path = slots / f'node-{argument}.env'
            if not path.exists():
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as output:
                    output.write('source ' + shlex.quote(str(template)) + '\n')
                    output.write('VM_NAME=' + prefix + argument + '\n')
                    output.write('VM_DNS_LABEL=' + dns + argument + '\n')
                    output.write('TN_BLS_PASSPHRASE=' + secrets.token_hex(32) + '\n')
        fleet.add(str(path))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    p.add_argument('--operation', required=True)
    p.add_argument('--argument', required=True)
    p.add_argument('--request-id', required=True)
    args = p.parse_args()
    request_id = str(uuid.UUID(args.request_id))
    fleet = Fleet(json.loads(Path(args.config).read_text()), apply=True)
    fleet.config_path = Path(args.config).resolve()
    with (fleet.state / 'operation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        name = 'request-' + request_id + '.json'
        if (fleet.state / name).exists():
            raise ValueError('This request was already started; inspect its result instead of re-running')
        record = {'request_id': request_id, 'operation': args.operation, 'argument': args.argument,
                  'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running'}
        fleet.save(name, record)
        try:
            run(fleet, args.operation, args.argument)
        except Exception:
            fleet.save(name, {**record, 'status': 'failed', 'finished_at': datetime.now(timezone.utc).isoformat()})
            raise
        fleet.save(name, {**record, 'status': 'completed', 'finished_at': datetime.now(timezone.utc).isoformat()})


if __name__ == '__main__':
    main()
