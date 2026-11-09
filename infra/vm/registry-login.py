#!/usr/bin/env python3
"""Exchange the VM's managed identity for an ACR credential; never print tokens."""
import json
import subprocess
import sys
import urllib.parse
import urllib.request

registry = sys.argv[1]
request = urllib.request.Request(
    'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F',
    headers={'Metadata': 'true'},
)
# IMDS must bypass proxy settings.
client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with client.open(request, timeout=30) as response:
    identity = json.load(response)
data = urllib.parse.urlencode({
    'grant_type': 'access_token', 'service': registry,
    'access_token': identity['access_token'],
}).encode()
with urllib.request.urlopen(f'https://{registry}/oauth2/exchange', data=data, timeout=30) as response:
    credential = json.load(response)['refresh_token']
subprocess.run(
    ['docker', 'login', registry, '--username', '00000000-0000-0000-0000-000000000000', '--password-stdin'],
    input=credential, text=True, check=True, stdout=subprocess.DEVNULL,
)
