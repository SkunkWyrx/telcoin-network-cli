#!/usr/bin/env python3
"""What-if by default. Creates only observer/API networking; never changes explorer apps."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config', default=str(ROOT / 'infra/pool/adiri.json'))
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
c = json.loads(Path(args.config).read_text())
# This implementation's topology is Adiri-specific; do not silently reuse its CIDRs on mainnet.
if c['network'] != 'adiri':
    raise SystemExit('Create a separate mainnet topology/parameter set before deployment')
parameters = {'observerVnetName': c['observer_vnet'], 'observerSubnetName': c['observer_subnet'],
              'privateIp': c['private_ip'], 'apiResourceGroup': c['api_resource_group'],
              'privateDnsName': c.get('private_dns_name', '')}
with tempfile.NamedTemporaryFile('w', suffix='.json') as file:
    json.dump({'parameters': {key: {'value': value} for key, value in parameters.items()}}, file)
    file.flush()
    subprocess.run(['az', 'deployment', 'group', 'create' if args.apply else 'what-if',
                    '--subscription', c['subscription'], '-g', c['resource_group'],
                    '-n', 'adiri-private-pool', '--template-file', str(ROOT / 'infra/pool/main.bicep'),
                    '--parameters', '@' + file.name], check=True)
