#!/usr/bin/env python3
"""Create an isolated one-observer pool from an explicit network release."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import subprocess

from fleet import Fleet, ROOT
from health import rpc


def validate_release(network, tag):
    pattern = r'v[0-9]+\.[0-9]+\.[0-9]+' + ('-adiri' if network == 'adiri' else '')
    if not re.fullmatch(pattern, tag):
        raise ValueError('Choose a stable release tag for the configured network')


def bootstrap(config, env_file, tag, reference, apply=False):
    validate_release(config['network'], tag)
    if not reference.startswith('https://'):
        raise ValueError('Provide an independent HTTPS RPC for this network')
    config = dict(config, reference_rpc=reference)
    fleet = Fleet(config, apply=apply)
    with (fleet.state / 'operation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inventory = fleet.state / 'nodes.json'
        if inventory.exists():
            fleet.c['nodes'] = json.loads(inventory.read_text())['nodes']
        if fleet.c['nodes']:
            raise ValueError('Bootstrap requires an empty inventory; use add or upgrade for an existing pool')
        print('Bootstrap one', config['network'], 'observer in', config['resource_group'])
        if not apply:
            print('Release:', tag, 'Reference:', reference, 'Chain:', config['chain_id'])
            return
        if int(rpc(reference, 'eth_chainId'), 16) != int(str(config['chain_id']), 0):
            raise ValueError('Reference RPC belongs to a different network; no resources created')
        for group in (config['resource_group'], config['api_resource_group']):
            if fleet.az('group', 'exists', '-n', group):
                if fleet.az('resource', 'list', '-g', group):
                    raise ValueError('Bootstrap requires empty target resource groups; existing resources found in ' + group)
        # Resolve the release before incurring infrastructure or image-build costs.
        from fleet import execute
        if not execute(['git', 'ls-remote', '--exit-code', config['git_repository'], 'refs/tags/' + tag]).strip():
            raise ValueError('Release tag does not exist')
        for group, workload in [(config['resource_group'], 'observers'), (config['api_resource_group'], 'api')]:
            fleet.az('group', 'create', '-n', group, '-l', config['location'],
                     '--tags', 'network=' + config['network'], 'workload=' + workload)
        fleet.az('acr', 'create', '-g', config['registry_resource_group'], '-n', config['registry'],
                 '-l', config['location'], '--sku', 'Basic', '--admin-enabled', 'false')
        fleet.build('refs/tags/' + tag)
        # Save the verified endpoint in a durable network profile for later operations.
        fleet.save('bootstrap-profile.json', config)
        subprocess.run(['python3', str(ROOT / 'scripts/pool/deploy-pool.py'), '--config',
                        str(fleet.state / 'bootstrap-profile.json'), '--apply'], check=True)
        release = json.loads((fleet.state / 'candidate.json').read_text())
        fleet.add(env_file, release['image'], fresh=True)
        fleet.promote(release['image'])
        fleet.save('bootstrap-profile.json', fleet.c)
        print('One observer is synced. Use bootstrap-profile.json as the network controller profile.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--release-tag', required=True)
    parser.add_argument('--reference-rpc', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    bootstrap(json.loads(Path(args.config).read_text()), args.env_file,
              args.release_tag, args.reference_rpc, args.apply)


if __name__ == '__main__':
    main()
