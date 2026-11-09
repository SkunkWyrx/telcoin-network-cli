"""Pure policies shared by operators and automation; Azure mutations live in fleet.py."""
from datetime import datetime, timedelta, timezone
import re


def pinned_image(value):
    if not re.fullmatch(r'[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}', value):
        raise ValueError('Use a digest-pinned image, not latest or a mutable release tag')
    return value


def complete_pairs(snapshots, network, pool):
    groups = {}
    for snapshot in snapshots:
        tags = snapshot.get('tags') or {}
        if (tags.get('managed-by'), tags.get('network'), tags.get('pool')) != (
                'oprail-observer-fleet', network, pool):
            continue
        capture, disk = tags.get('capture'), tags.get('disk')
        if not capture or disk not in ('execution', 'consensus'):
            continue
        groups.setdefault(capture, {}).setdefault(disk, []).append(snapshot)
    result = []
    for capture, disks in groups.items():
        if set(disks) != {'execution', 'consensus'} or any(len(v) != 1 for v in disks.values()):
            continue
        pair = {disk: values[0] for disk, values in disks.items()}
        if not all(s.get('completionPercent') == 100 and s.get('provisioningState') == 'Succeeded'
                   and s['tags'].get('capture-status') == 'complete' for s in pair.values()):
            continue
        times = {s['tags'].get('captured-at') for s in pair.values()}
        if len(times) != 1 or None in times:
            continue
        created = datetime.fromisoformat(times.pop().replace('Z', '+00:00'))
        if created.tzinfo is None:
            continue
        result.append({'capture': capture, 'created': created.astimezone(timezone.utc),
                       'execution': pair['execution']['id'], 'consensus': pair['consensus']['id']})
    return sorted(result, key=lambda p: p['created'])


def retention(pairs, now, days=7):
    """Keep today's captures; earliest pair/day for the previous days; always keep latest."""
    if days < 1:
        raise ValueError('retention_days must be positive')
    if not pairs:
        return [], []
    pairs = sorted(pairs, key=lambda p: p['created'])
    today = now.astimezone(timezone.utc).date()
    cutoff = today - timedelta(days=days - 1)
    keep = {pairs[-1]['capture']}
    oldest = {}
    for pair in pairs:
        day = pair['created'].astimezone(timezone.utc).date()
        if day >= today:
            keep.add(pair['capture'])
        elif day >= cutoff:
            oldest.setdefault(day, pair['capture'])
    keep.update(oldest.values())
    return ([p for p in pairs if p['capture'] in keep],
            [p for p in pairs if p['capture'] not in keep])


def upgrade_order(nodes):
    names = [n['name'] for n in nodes]
    ordinals = [n['ordinal'] for n in nodes]
    if len(set(names)) != len(names) or len(set(ordinals)) != len(ordinals):
        raise ValueError('Node names and ordinals must be unique')
    return sorted(nodes, key=lambda n: n['ordinal'], reverse=True)
