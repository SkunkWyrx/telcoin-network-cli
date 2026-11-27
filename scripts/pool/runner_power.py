#!/usr/bin/env python3
"""Power only the Adiri management runner. Never powers an observer VM."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time

SUBSCRIPTION='8a149d4d-c52a-4432-929e-de754584e269'
GROUP='oprail-observer-adiri'
VM='adiri-observer-runner'
STATE=Path('/var/lib/oprail-runner-power/idle.json')
LOCK=Path('/var/lib/oprail-observers/adiri/adiri/operation.lock')


def az(*args):
    return json.loads(subprocess.check_output(['az',*args,'--subscription',SUBSCRIPTION,'-o','json'],text=True) or 'null')


def target():
    node=az('vm','show','-g',GROUP,'-n',VM)
    if node['name']!=VM or node.get('tags',{}).get('purpose')!='oprail-observer-runner':
        raise ValueError('Refusing a non-runner VM')
    return node


def stop(node):
    tags=node.get('tags') or {}
    if tags.get('runner-power-mode')!='on-demand' or tags.get('rpc-monitor-host')!='management-app-verified':
        raise ValueError('Independent monitoring must be verified before runner shutdown')
    if tags.get('runner-keepalive')=='true':
        print('Operator keepalive is enabled; retaining runner');return
    az('vm','deallocate','-g',GROUP,'-n',VM)
    print('Management runner deallocated; no observer power state changed')


def idle(node):
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    now=time.time();old=json.loads(STATE.read_text()) if STATE.exists() else {}
    busy=False
    for path in Path('/proc').glob('[0-9]*/comm'):
        try: busy=busy or path.read_text().strip()=='Runner.Worker'
        except (FileNotFoundError,PermissionError,ProcessLookupError):pass
    if old.get('boot')!=boot or busy:
        STATE.parent.mkdir(parents=True,exist_ok=True)
        STATE.write_text(json.dumps({'boot':boot,'last_busy':now}));return
    if now-old.get('last_busy',now)<900:return
    if not LOCK.exists():return  # Do not create a root-owned lock over runner state.
    with LOCK.open('r+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        # Holds the same lock as fleet operations until deallocation is requested.
        stop(node)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['start','stop','idle'])
    args=parser.parse_args()
    node=target()
    if args.operation=='start':
        az('vm','start','-g',GROUP,'-n',VM);print('Management runner started; waiting jobs may connect')
    elif args.operation=='stop':stop(node)
    else:idle(node)


if __name__=='__main__':main()
