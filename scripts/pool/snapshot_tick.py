#!/usr/bin/env python3
"""Opt-in scheduler tick. A stopped/unsynced pool is never started automatically."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
from fleet import Fleet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if not config.get('automation_enabled', False):
        print('Snapshot automation disabled; no Azure calls or node changes.')
        return
    hours = config.get('snapshot_interval_hours', 4)
    if hours <= 0:
        raise ValueError('Snapshot interval must be positive')
    fleet = Fleet(config, apply=True)
    with (fleet.state / 'operation.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another pool operation is running; skipping tick.')
            return
        pairs = fleet.pairs()
        if pairs and (datetime.now(timezone.utc) - pairs[-1]['created']).total_seconds() < hours * 3600:
            print('Latest snapshot is inside the configured interval; no new capture.')
            return
        fleet.snapshot()
        # Never prune if capture or source recovery fails.
        fleet.prune()


if __name__ == '__main__':
    main()
