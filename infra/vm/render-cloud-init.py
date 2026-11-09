#!/usr/bin/env python3
"""Render cloud-init without secrets; also supports local preview/validation."""
import base64
import json
import os
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
registry, image, fqdn = sys.argv[1:4]
for value in (registry, image, fqdn):
    if not re.fullmatch(r'[A-Za-z0-9._:/@-]+', value):
        raise SystemExit('Invalid registry, image, or DNS name')
files = [
    ('../../src/docker/entrypoint.sh', '/etc/telcoin/entrypoint.sh', '0700'),
    ('prepare-disks.sh', '/usr/local/sbin/telcoin-prepare-disks', '0700'),
    ('registry-login.py', '/usr/local/sbin/telcoin-registry-login', '0700'),
    ('start-observer.sh', '/usr/local/sbin/telcoin-start-observer', '0700'),
    ('telcoin-observer.service', '/etc/systemd/system/telcoin-observer.service', '0644'),
]
if os.getenv('EXPECTED_CHAIN_ID'):
    files += [('../../scripts/pool/health.py', '/usr/local/lib/telcoin/health.py', '0755'), ('../pool/readiness.service', '/etc/systemd/system/telcoin-readiness.service', '0644')]
print('#cloud-config\npackage_update: true\npackages: [docker.io, e2fsprogs, python3, curl, azure-vm-utils]\nwrite_files:')
for source, target, mode in files:
    content = base64.b64encode((root / source).read_bytes()).decode()
    print(f'  - path: {target}\n    permissions: "{mode}"\n    encoding: b64\n    content: {content}')
if os.getenv('EXPECTED_CHAIN_ID'):
    int(os.environ['EXPECTED_CHAIN_ID'], 0)
    readiness = json.dumps({'reference_rpc': os.environ['NETWORK_RPC_URL'], 'chain_id': os.environ['EXPECTED_CHAIN_ID'], 'max_lag': 3, 'poll_seconds': 10, 'health_ttl_seconds': 30})
    print('  - path: /etc/telcoin/readiness.json\n    permissions: "0600"\n    encoding: b64\n    content: ' + base64.b64encode(readiness.encode()).decode())
config = f'REGISTRY={registry}\nIMAGE={image}\n'
print('  - path: /etc/telcoin/image.conf\n    permissions: "0600"\n    encoding: b64\n    content: ' + base64.b64encode(config.encode()).decode())
print('runcmd:')
for command in [
    ['chmod', '700', '/etc/telcoin'],
    ['udevadm', 'control', '--reload-rules'],
    ['udevadm', 'trigger', '--subsystem-match=block'],
    ['udevadm', 'settle', '--timeout=60'],
    ['/usr/local/sbin/telcoin-prepare-disks'],
    ['systemctl', 'enable', '--now', 'docker'],
    ['systemctl', 'daemon-reload'],
    ['systemctl', 'enable', 'telcoin-observer'],
]:
    print('  - ' + json.dumps(command))

if os.getenv('EXPECTED_CHAIN_ID'):
    print('  - ' + json.dumps(['systemctl', 'enable', '--now', 'telcoin-readiness']))
