#!/usr/bin/env python3
"""Grow this observer's attached ext4 data disks using its Azure managed identity."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.request import Request, urlopen

CONFIG = Path('/etc/telcoin/disk-growth.json')
STATE = Path('/var/lib/telcoin-disk-growth/state.json')
ARM = 'https://management.azure.com'


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def next_size(size, used_percent, threshold, increment, maximum=65536):
    if not 0 < threshold < 100 or increment < 1 or maximum < 1:
        raise ValueError('Invalid growth policy')
    return min(maximum, size + increment) if used_percent >= threshold and size < maximum else size


def request(url, token=None, body=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {'Metadata': 'true'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(url, data=data, headers=headers, method='PATCH' if body is not None else 'GET'), timeout=30) as response:
        content = response.read()
        return json.loads(content) if content else {}


def disk_url(identifier):
    if not re.fullmatch(r'/subscriptions/[A-Za-z0-9-]+/resourceGroups/[A-Za-z0-9._()-]+/providers/Microsoft.Compute/disks/[A-Za-z0-9._-]+', identifier):
        raise ValueError('Invalid managed disk resource ID')
    return ARM + identifier + '?api-version=2024-03-02'


def verify_device(mount, lun):
    if not os.path.ismount(mount):
        raise ValueError('Data disk mount is missing: ' + mount)
    device = Path('/dev/disk/azure/data/by-lun/' + str(lun))
    if not device.exists():
        device = Path('/dev/disk/azure/scsi1/lun' + str(lun))
    device = device.resolve(strict=True)
    source = command('findmnt', '-n', '-o', 'SOURCE', '--mountpoint', mount)
    if not stat.S_ISBLK(device.stat().st_mode) or Path(source).stat().st_rdev != device.stat().st_rdev:
        raise ValueError('Mount does not match the expected Azure disk LUN')
    if command('findmnt', '-n', '-o', 'FSTYPE', '--mountpoint', mount) != 'ext4':
        raise ValueError('Only whole-disk ext4 growth is supported')
    if command('lsblk', '-n', '-o', 'TYPE', str(device)).splitlines() != ['disk']:
        raise ValueError('Expected an unpartitioned data disk')
    return device


def expand_filesystem(device, size):
    deadline = time.monotonic() + 660
    while time.monotonic() < deadline:
        if int(command('blockdev', '--getsize64', str(device))) >= size * 2**30:
            command('resize2fs', str(device))
            return
        nvme = re.fullmatch(r'(nvme[0-9]+)n[0-9]+', device.name)
        if nvme:
            command('nvme', 'ns-rescan', '/dev/' + nvme[1])
        else:
            Path('/sys/class/block/' + device.name + '/device/rescan').write_text('1')
        time.sleep(10)
    raise TimeoutError('Disk capacity has not appeared in Linux; retry on the next timer run')


def grow_disk(config, disk, token, applied):
    device = verify_device(disk['mount'], disk['lun'])
    url = disk_url(disk['id'])
    current = request(url, token)
    if (current.get('managedBy') or '').lower() != config['vm_id'].lower():
        raise ValueError('Disk is not attached to this observer')
    props = current['properties']
    if current['sku']['name'] != 'PremiumV2_LRS' or props.get('completionPercent', 100) < 100:
        raise ValueError('Disk is not Premium SSD v2 or has a background copy in progress')
    if props['provisioningState'] != 'Succeeded':
        raise ValueError('Disk update is still in progress')
    size = props['diskSizeGB']
    # Recover an Azure resize whose filesystem step was interrupted before checking utilization.
    if applied.get(disk['id']) != size:
        expand_filesystem(device, size)
        applied[disk['id']] = size
    usage = os.statvfs(disk['mount'])
    used = usage.f_blocks - usage.f_bfree
    percent = 100 * used / (used + usage.f_bavail)
    target = next_size(size, percent, config['threshold_percent'], config['increment_gb'], config.get('max_size_gb', 65536))
    print(json.dumps({'mount': disk['mount'], 'used_percent': round(percent, 2), 'size_gb': size, 'target_gb': target}), flush=True)
    if target == size:
        if percent >= config['threshold_percent']:
            raise RuntimeError('Disk reached the configured maximum size')
        return
    request(url, token, {'properties': {'diskSizeGB': target}})
    deadline = time.monotonic() + 660
    while time.monotonic() < deadline:
        props = request(url, token)['properties']
        if props.get('provisioningState') == 'Failed':
            raise RuntimeError('Azure disk expansion failed')
        if props['diskSizeGB'] >= target and props.get('provisioningState') == 'Succeeded':
            expand_filesystem(device, props['diskSizeGB'])
            applied[disk['id']] = props['diskSizeGB']
            print('Expanded and verified ' + disk['mount'], flush=True)
            return
        time.sleep(10)
    raise TimeoutError('Azure disk update timed out; retry on the next timer run')


def main():
    config = json.loads(CONFIG.read_text())
    applied = json.loads(STATE.read_text()) if STATE.exists() else {}
    token = request('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F')['access_token']
    errors = []
    with Path('/run/lock/telcoin-disk-growth.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for disk in config['disks']:
            try:
                grow_disk(config, disk, token, applied)
            except Exception as error:
                errors.append(type(error).__name__ + ': ' + str(error))
            temporary = STATE.with_suffix('.tmp')
            temporary.write_text(json.dumps(applied))
            temporary.replace(STATE)
    if errors:
        raise RuntimeError('; '.join(errors))


if __name__ == '__main__':
    main()
