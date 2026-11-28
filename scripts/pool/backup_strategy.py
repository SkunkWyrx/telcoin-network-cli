"""Select the configured network backup strategy without implicit provisioning."""


def capture(fleet, source=None):
    mode = fleet.c.get('backup_mode', 'existing-fleet')
    if mode == 'temporary-observer':
        from backup_helper import backup
        if source is None:
            backup(fleet)
        else:
            backup(fleet, source_name=source)
    elif mode == 'existing-fleet':
        if source is None:
            fleet.snapshot()
        else:
            fleet.snapshot(source)
    else:
        raise ValueError('Unsupported backup mode: ' + str(mode))
