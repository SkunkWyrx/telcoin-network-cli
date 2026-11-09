#!/usr/bin/env python3
"""Compare observer execution height with a reference RPC. No third-party packages."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time
import urllib.request


def height(url):
    request = urllib.request.Request(url, data=json.dumps({
        'jsonrpc': '2.0', 'method': 'eth_blockNumber', 'params': [], 'id': 1
    }).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    value = result.get('result')
    if result.get('error') or not isinstance(value, str) or not value.startswith('0x'):
        raise ValueError('RPC did not return a hexadecimal block height')
    return int(value, 16)


def report(local, network, previous=None, now=None, threshold=3):
    now = time.time() if now is None else now
    gap = network - local
    print(f'Observer: {local:,} | Network: {network:,}')
    print(f'Blocks behind: {max(gap, 0):,}')
    if gap < -threshold:
        print('Status: observer ahead of reference; check reference health')
    elif gap <= threshold:
        print(f'Status: near reference head (within {threshold} blocks)')
    else:
        print('Status: behind reference head')
    if previous and now > previous['time']:
        delta = local - previous['local']
        change = (previous['network'] - previous['local']) - gap
        print(f"Since last check ({now - previous['time']:.0f}s): observer {delta:+,} blocks; gap closed {change:+,} blocks")
        if delta == 0 and gap > threshold:
            print('Warning: observer has not advanced since the last check')
    else:
        print('Run again to measure advancement and change in gap.')
    print('Height comparison does not verify chain hashes; reference RPC can also lag.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc', default='http://127.0.0.1:8545')
    parser.add_argument('--network-rpc', default='https://rpc.adiri.tel')
    parser.add_argument('--threshold', type=int, default=3)
    args = parser.parse_args()
    if args.threshold < 0:
        parser.error('threshold must be nonnegative')
    try:
        local = height(args.rpc)
        network = height(args.network_rpc)
    except Exception as error:
        print(f'Sync status: unknown; RPC query failed ({type(error).__name__}).')
        return 1
    key = hashlib.sha256((args.rpc + '\n' + args.network_rpc).encode()).hexdigest()[:20]
    state = Path(tempfile.gettempdir()) / f'telcoin-reference-sync-{key}.json'
    previous = None
    try:
        previous = json.loads(state.read_text())
        if not all(isinstance(previous[k], (int, float)) for k in ('time', 'local', 'network')):
            previous = None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    now = time.time()
    report(local, network, previous, now, args.threshold)
    try:
        # Atomic replacement; state is advisory and contains no secrets.
        with tempfile.NamedTemporaryFile(mode='w', dir=state.parent, delete=False) as output:
            json.dump({'time': now, 'local': local, 'network': network}, output)
            temporary = Path(output.name)
        temporary.replace(state)
    except OSError:
        print('Progress history unavailable; height comparison succeeded.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
