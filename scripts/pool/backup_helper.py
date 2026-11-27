#!/usr/bin/env python3
"""Cold backups from a disposable observer; never stop a public source node."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import time
import uuid

from fleet import pinned_image
from health import rpc


def wait_snapshots(fleet, resources):
    deadline=time.monotonic()+fleet.c.get('snapshot_copy_timeout_seconds',14400)
    while time.monotonic()<deadline:
        copies=[fleet.az('snapshot','show','--ids',value) for value in resources]
        if all(s.get('completionPercent')==100 and s.get('provisioningState')=='Succeeded' for s in copies):return
        time.sleep(30)
    raise RuntimeError('Snapshot copy did not complete; existing backups retained')


def seed(fleet, source, state):
    """A live disk copy is an UNTRUSTED seed, never a backup or retention candidate."""
    vm=fleet.vm(source)
    disks={d['lun']:d['managedDisk']['id'] for d in vm['storageProfile']['dataDisks']}
    if set(disks)!={0,1}:raise ValueError('Expected exactly two source data disks')
    state['seed']={};fleet.save('scheduled-backup.json',state)
    # Execution first, consensus second: the disposable candidate must still pass
    # startup, canonical hash, sync and a clean stop before it can produce a backup.
    for lun,kind in [(0,'execution'),(1,'consensus')]:
        name=state['name']+'-seed-'+kind
        identifier=f"/subscriptions/{fleet.c['subscription']}/resourceGroups/{fleet.c['resource_group']}/providers/Microsoft.Compute/snapshots/{name}"
        state['seed'][kind]=identifier;fleet.save('scheduled-backup.json',state)
        fleet.az('snapshot','create','-g',fleet.c['resource_group'],'-n',name,'--source',disks[lun],
                 '--incremental','true','--location',fleet.c['location'],'--tags',
                 'managed-by=oprail-observer-seed','network='+fleet.c['network'],'capture-status=seed-only')
    wait_snapshots(fleet,state['seed'].values())
    return state['seed']


def _backup(fleet, source_name=None):
    if fleet.c['network']!='adiri':raise ValueError('Temporary observer backups are enabled only for Adiri')
    if not fleet.apply:
        print('Create/resume a temporary observer, verify canonical sync, cold-snapshot it, prune, and remove it');return
    file=fleet.state/'scheduled-backup.json'
    state=json.loads(file.read_text()) if file.exists() else None
    if not state or state['stage']=='completed':
        candidates=[n for n in fleet.c['nodes'] if fleet.synced(n)]
        source=next((n for n in candidates if n['name']==source_name),None) if source_name else next(iter(candidates),None)
        if not source:raise ValueError('No healthy public source; leave RPC and existing backups untouched')
        image=pinned_image(fleet.ssh(source,"python3 - <<'READ'\nfrom pathlib import Path\nprint(next(v.split('=',1)[1].strip().strip(chr(34)) for v in Path('/etc/telcoin/image.conf').read_text().splitlines() if v.startswith('IMAGE=')))\nREAD\n").strip())
        expected=fleet.c['registry']+'.azurecr.io/'+fleet.c['repository']+'@'
        if not image.startswith(expected):raise ValueError('Source image is outside Adiri release repository')
        name='adiri-observer-backup-'+uuid.uuid4().hex[:8]
        template=Path(fleet.c.get('node_template','/etc/oprail-observers/adiri/template.env'))
        if not template.is_file():raise ValueError('Configure the private Adiri node template')
        env=fleet.state/(name+'.env')
        fd=os.open(env,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:
            f.write('source '+shlex.quote(str(template))+'\nVM_NAME='+name+'\nVM_DNS_LABEL=oprail-'+name+'\nTN_BLS_PASSPHRASE='+secrets.token_hex(32)+'\n')
        state={'name':name,'source':source,'image':image,'env_file':str(env),'stage':'planned'}
        fleet.save('scheduled-backup.json',state)
    if not re.fullmatch(r'adiri-observer-backup-[a-f0-9]{8}',state['name']):raise ValueError('Unexpected helper identity')
    source=state['source']
    if not any(n['name']==source['name'] and fleet.synced(n) for n in fleet.c['nodes']):
        raise ValueError('Public source must remain healthy; no source service will be changed')
    helper=next((n for n in fleet.c['nodes'] if n['name']==state['name']),None)
    if not helper and state['stage']=='retiring' and (fleet.state/('remove-'+state['name']+'.json')).exists():
        fleet.remove(state['name'],disposable=True)
        helper={'name':state['name']}
    if not helper:
        if state['stage'] not in ('planned','seeded'):
            raise ValueError('Interrupted helper provisioning needs inventory reconciliation; never blindly create another helper')
        if state['stage']=='planned':
            # Latest compatible cold backup is preferred. Older migration backups
            # must never be silently fed to a database-incompatible binary.
            snapshots=fleet.az('snapshot','list','-g',fleet.c['resource_group'])
            usable=[s for s in snapshots if (s.get('tags') or {}).get('source-image')==state['image']]
            from policy import complete_pairs
            pairs=complete_pairs(usable,fleet.c['network'],fleet.c['pool'])
            if pairs:state['seed']={k:pairs[-1][k] for k in ('execution','consensus')}
            else:
                marker=fleet.ssh(source,'if test -e /var/lib/telcoin/restored-state-floor; then printf present; fi\n').strip()
                if marker:state['seed']={}
                else:seed(fleet,source,state)
            state['stage']='seeded';fleet.save('scheduled-backup.json',state)
        state['stage']='provisioning';fleet.save('scheduled-backup.json',state)
        old_timeout=fleet.c.get('upgrade_timeout_seconds',7200)
        fleet.c['upgrade_timeout_seconds']=fleet.c.get('backup_helper_timeout_seconds',7200)
        try:fleet.add(state['env_file'],state['image'],fresh=not bool(state['seed']),seed_pair=state['seed'] or None,serve_public=False,temporary_backup=True)
        finally:fleet.c['upgrade_timeout_seconds']=old_timeout
        helper=next(n for n in fleet.c['nodes'] if n['name']==state['name'])
        fleet.az('tag','update','--resource-id',fleet.vm(helper)['id'],'--operation','Merge','--tags','purpose=temporary-backup-observer')
    if state['stage']=='capturing':
        vm=fleet.vm(helper)
        if (vm.get('tags') or {}).get('purpose')!='temporary-backup-observer':raise ValueError('Unexpected recovery target')
        view=fleet.az('vm','get-instance-view','-g',fleet.c['resource_group'],'-n',helper['name'])
        if not any(s['code']=='PowerState/running' for s in view['instanceView']['statuses']):
            fleet.az('vm','start','-g',fleet.c['resource_group'],'-n',helper['name'])
    if state['stage'] not in ('captured','pruned','retiring'):
        fleet.wait_synced(helper)
        if not fleet.synced(source):raise ValueError('Public source unhealthy; do not take helper offline')
        before={p['capture'] for p in fleet.pairs()}
        state['stage']='capturing';fleet.save('scheduled-backup.json',state)
        fleet.snapshot(helper['name'])
        latest=fleet.pairs()[-1]
        if latest['capture'] in before:raise RuntimeError('A new cold backup was not recorded')
        for kind in ('execution','consensus'):
            fleet.az('tag','update','--resource-id',latest[kind],'--operation','Merge','--tags','source-image='+state['image'])
        state['capture']=latest['capture'];state['stage']='captured';fleet.save('scheduled-backup.json',state)
    if state['stage']=='captured':
        fleet.prune();state['stage']='pruned';fleet.save('scheduled-backup.json',state)
    state['stage']='retiring';fleet.save('scheduled-backup.json',state)
    fleet.remove(helper['name'],disposable=True)
    for identifier in state.get('seed',{}).values():
        # Do not delete a normal retained backup used as a seed.
        snapshots=fleet.az('snapshot','list','-g',fleet.c['resource_group'])
        found=next((s for s in snapshots if s['id'].lower()==identifier.lower()),None)
        if found and (found.get('tags') or {}).get('managed-by')=='oprail-observer-seed':
            fleet.az('snapshot','delete','--ids',identifier)
    Path(state['env_file']).unlink(missing_ok=True)
    state['stage']='completed';fleet.save('scheduled-backup.json',state)
    print('Current cold backup retained; temporary observer and seed copies removed; public source was never stopped')


def backup(fleet, source_name=None):
    try:
        return _backup(fleet, source_name)
    except BaseException:
        path=fleet.state/'scheduled-backup.json'
        if fleet.apply and fleet.c['network']=='adiri' and path.exists():
            state=json.loads(path.read_text())
            name=state.get('name','')
            if re.fullmatch(r'adiri-observer-backup-[a-f0-9]{8}',name):
                helper=next((n for n in fleet.c['nodes'] if n['name']==name),None)
                if helper and any(n['name']!=name and fleet.synced(n) for n in fleet.c['nodes']):
                    fleet.remove(name,disposable=True)
                    snapshots=fleet.az('snapshot','list','-g',fleet.c['resource_group'])
                    for item in snapshots:
                        if item['name'].startswith(name+'-seed-') and (item.get('tags') or {}).get('managed-by')=='oprail-observer-seed':
                            fleet.az('snapshot','delete','--ids',item['id'])
                    Path(state['env_file']).unlink(missing_ok=True)
                    state['stage']='failed';fleet.save('scheduled-backup.json',state)
                    print('Failed helper cleaned up; snapshot retention halted and automatic retries paused for operator review.')
        raise
