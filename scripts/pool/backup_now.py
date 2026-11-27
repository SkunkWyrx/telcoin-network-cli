#!/usr/bin/env python3
"""Explicit one-off backup with the same operation lock used by management jobs."""
import argparse
import fcntl
import json
from pathlib import Path
from fleet import Fleet
from backup_helper import backup

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config',required=True)
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
fleet=Fleet(json.loads(Path(args.config).read_text()),apply=args.apply)
with (fleet.state/'operation.lock').open('w') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    backup(fleet)
