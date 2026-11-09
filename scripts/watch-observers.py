#!/usr/bin/env python3
"""Discover tagged Azure observers and compare public RPC heights without SSH."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import subprocess

spec = importlib.util.spec_from_file_location('observer_sync', Path(__file__).with_name('check-observer-sync.py'))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def check(resource, reference, threshold):
    name = resource['name']
    if (resource.get('tags') or {}).get('telcoin-rpc-enabled') == 'false':
        return (name, '-', '-', 'RPC DISABLED')
    url = (resource.get('tags') or {}).get('telcoin-rpc-url', '').strip()
    if not url:
        return (name, '-', '-', 'NO RPC URL')
    if not url.startswith(('https://', 'http://')):
        return (name, '-', '-', 'INVALID RPC URL')
    try:
        local = sync.height(url)
    except Exception:
        return (name, '-', '-', 'RPC UNAVAILABLE')
    if reference is None:
        return (name, str(local), '?', 'REFERENCE UNAVAILABLE')
    gap = reference - local
    status = 'AHEAD OF REFERENCE' if gap < -threshold else ('NEAR HEAD' if gap <= threshold else 'BEHIND')
    return (name, str(local), str(max(0, gap)), status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subscription', default=os.getenv('AZURE_SUBSCRIPTION_ID'))
    parser.add_argument('--resource-group', default=os.getenv('AZURE_RESOURCE_GROUP'))
    parser.add_argument('--chain', default=os.getenv('CHAIN', 'adiri'))
    parser.add_argument('--network-rpc', default=os.getenv('NETWORK_RPC_URL', 'https://rpc.adiri.tel'))
    parser.add_argument('--threshold', type=int, default=3)
    args = parser.parse_args()
    if args.threshold < 0:
        parser.error('threshold must be nonnegative')
    command = ['az', 'resource', 'list', '-o', 'json']
    if args.subscription:
        command += ['--subscription', args.subscription]
    if args.resource_group:
        command += ['--resource-group', args.resource_group]
    try:
        resources = json.loads(subprocess.check_output(command, text=True))
    except (subprocess.CalledProcessError, OSError, ValueError):
        print('Azure discovery failed. Check az login and subscription access.')
        return 1
    resources = [r for r in resources
                 if r.get('type', '').lower() in ('microsoft.compute/virtualmachines', 'microsoft.containerinstance/containergroups')
                 and (r.get('tags') or {}).get('telcoin-role') == 'observer'
                 and (r.get('tags') or {}).get('telcoin-chain') == args.chain]
    if not resources:
        print(f'No tagged observers found for chain {args.chain}.')
        return 1
    try:
        reference = sync.height(args.network_rpc)
    except Exception:
        reference = None
    print(f'Chain: {args.chain} | Reference height: {reference if reference is not None else "unavailable"}')
    print(f'{"OBSERVER":40} {"BLOCK":>12} {"BEHIND":>12}  STATUS')
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda r: check(r, reference, args.threshold), sorted(resources, key=lambda r: r['name'])))
    for name, local, gap, status in rows:
        print(f'{name:40} {local:>12} {gap:>12}  {status}')
    print(f'NEAR HEAD means within {args.threshold} blocks of this reference; hashes are not verified.')
    return 1 if any(row[3] != 'NEAR HEAD' for row in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
