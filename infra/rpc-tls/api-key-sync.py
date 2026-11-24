#!/usr/bin/env python3
"""Read RPC keys from a dedicated vault and atomically reload NGINX maps."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path('/etc/telcoin/rpc-tls')
TIERS = ('anonymous', 'partner', 'internal')
DEFAULTS = {'anonymous': {'rate': 30, 'burst': 60}, 'partner': {'rate': 100, 'burst': 200},
            'internal': {'rate': 300, 'burst': 600}, 'backend': {'rate': 500, 'burst': 400}}


def policy(config):
    result = {tier: dict(config.get('limits', {}).get(tier, values)) for tier, values in DEFAULTS.items()}
    for limits in result.values():
        if set(limits) != {'rate', 'burst'} or any(type(v) is not int or not 1 <= v <= 10000 for v in limits.values()):
            raise ValueError('Invalid rate limit configuration')
    return result


def render(keys, config, now=None):
    now = time.time() if now is None else now
    limits = policy(config)
    active = []
    seen = set()
    for item in keys:
        if not item.get('enabled', True) or (item.get('expires') is not None and item['expires'] <= now):
            continue
        if (not re.fullmatch(r'[a-f0-9]{64}', item['key']) or
                not re.fullmatch(r'[a-f0-9]{32}', item['id']) or item['tier'] not in ('partner', 'internal')):
            raise ValueError('Invalid RPC key record')
        if item['key'] in seen:
            raise ValueError('Duplicate RPC key')
        seen.add(item['key'])
        active.append(item)
    if len(active) > 1000:
        raise ValueError('Too many RPC keys')
    lines = ['# Generated privately; do not print or commit this file.',
             'map $http_x_api_key $rpc_tier {', '  default invalid;', '  "" anonymous;']
    lines += [f'  "{k["key"]}" {k["tier"]};' for k in active]
    lines += ['}', 'map $http_x_api_key $rpc_key_id {', '  default "";']
    lines += [f'  "{k["key"]}" "{k["id"]}";' for k in active]
    lines += ['}']
    for tier in TIERS:
        key = '$binary_remote_addr' if tier == 'anonymous' else '$rpc_key_id'
        lines += [f'map $rpc_tier $rpc_{tier}_key {{', '  default "";', f'  {tier} {key};', '}',
                  f'limit_req_zone $rpc_{tier}_key zone=rpc_{tier}:10m rate={limits[tier]["rate"]}r/s;']
    lines += [f'limit_req_zone $server_addr zone=rpc_backend:1m rate={limits["backend"]["rate"]}r/s;']
    return '\n'.join(lines) + '\n'


def fetch(config):
    deadline = time.monotonic() + 50
    vault = config['vault']
    if not re.fullmatch(r'[a-z0-9-]{3,24}', vault):
        raise ValueError('Invalid API key vault')
    token_url = ('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01'
                 '&resource=https%3A%2F%2Fvault.azure.net')
    with urllib.request.urlopen(urllib.request.Request(token_url, headers={'Metadata': 'true'}), timeout=10) as r:
        token = json.load(r)['access_token']
    base = f'https://{vault}.vault.azure.net/'
    def get(url):
        if not url.startswith(base):
            raise ValueError('Unexpected key vault pagination URL')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Key vault refresh deadline exceeded')
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token}), timeout=min(15, remaining)) as r:
            return json.load(r)
    url = base + 'secrets?api-version=7.4'
    result = []
    count = 0
    while url:
        page = get(url)
        for item in page['value']:
            name = item['id'].split('/')[-1]
            if not re.fullmatch(r'rpc-key-[a-f0-9]{32}', name):
                continue
            count += 1
            if count > 10000:
                raise ValueError('Too many key records')
            a = item['attributes']
            if not a.get('enabled', True) or a.get('exp', float('inf')) <= time.time() or a.get('nbf', 0) > time.time():
                continue
            data = get(base + 'secrets/' + name + '?api-version=7.4')
            value = json.loads(data['value'])
            # Attributes are checked again after fetching to handle concurrent revocation.
            a = data['attributes']
            result.append({'id': name[len('rpc-key-'):], 'key': value['key'], 'tier': value['tier'],
                           'enabled': a.get('enabled', True), 'expires': a.get('exp')})
        url = page.get('nextLink')
    return result


def write_private(path, content):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as f:
        os.chmod(f.name, 0o600)
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    Path(f.name).replace(path)


def install(content, root=ROOT, run=subprocess.run):
    path = root / 'api-keys.conf'
    old = path.read_text() if path.exists() else None
    if content == old:
        return
    write_private(path, content)
    active = run(['systemctl', 'is-active', '--quiet', 'telcoin-rpc-tls'], capture_output=True).returncode == 0
    if not active:
        return
    try:
        run(['docker', 'exec', 'telcoin-rpc-tls', 'nginx', '-t', '-c', '/rpc-tls/nginx.conf'],
            check=True, capture_output=True)
        run(['systemctl', 'reload', 'telcoin-rpc-tls'], check=True, capture_output=True)
    except Exception:
        if old is not None:
            write_private(path, old)
        else:
            path.unlink(missing_ok=True)
        raise


def cached_keys(cache, now):
    try:
        saved = json.loads(cache.read_text())
        stamp = saved['fetched_at']
        if type(stamp) not in (int, float) or not 0 <= now - stamp <= 300:
            return []
        return saved['keys']
    except (OSError, ValueError, KeyError, TypeError):
        return []


def main():
    os.umask(0o077)
    config = json.loads(Path('/etc/telcoin/api-keys.json').read_text())
    cache = ROOT / 'api-keys-cache.json'
    failed = False
    now = time.time()
    try:
        keys = fetch(config)
        now = time.time()
        render(keys, config, now)  # Reject invalid data before replacing a usable cache.
        write_private(cache, json.dumps({'fetched_at': now, 'keys': keys}))
    except Exception:
        failed = True
        now = time.time()
        # An outage never keeps revoked or expired credentials usable indefinitely.
        keys = cached_keys(cache, now)
        try:
            render(keys, config, now)
        except (ValueError, KeyError, TypeError):
            keys = []
    install(render(keys, config, now))
    if failed:
        raise RuntimeError('Vault sync unavailable; bounded cached-key policy applied')
    print('RPC key policy synchronized.')


if __name__ == '__main__':
    try:
        with open('/run/oprail-rpc-key-sync.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            main()
    except Exception as error:
        # NGINX configuration and vault values contain credentials. Never print them.
        raise SystemExit('RPC key sync failed: ' + type(error).__name__)
