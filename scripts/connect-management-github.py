#!/usr/bin/env python3
"""Prompt locally for a repository-scoped token; connect history, leaving mutations disabled."""
import getpass
import json
import subprocess
import urllib.request


def main():
    token = getpass.getpass('Fine-grained GitHub token (hidden; never paste into chat): ')
    if not token:
        raise SystemExit('No token supplied; nothing changed.')
    request = urllib.request.Request(
        'https://api.github.com/repos/SkunkWyrx/telcoin-network-cli/actions/workflows/observer-management-operations.yml',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        if json.load(response)['path'] != '.github/workflows/observer-management-operations.yml':
            raise SystemExit('Unexpected repository workflow; nothing changed.')
    common = ['-g', 'oprail-observer-adiri', '-n', 'oprail-observer-mgmt-adiri',
              '--subscription', '8a149d4d-c52a-4432-929e-de754584e269', '--output', 'none']
    subprocess.run(['az', 'containerapp', 'secret', 'set', *common,
                    '--secrets', 'github-dispatch-token=' + token], check=True, capture_output=True)
    subprocess.run(['az', 'containerapp', 'update', *common, '--set-env-vars',
                    'Operations__GitHubToken=secretref:github-dispatch-token',
                    'Operations__Enabled=false'], check=True, capture_output=True)
    print('Adiri GitHub history connected. Node-changing controls remain disabled.')


if __name__ == '__main__':
    main()
