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
if c['network'] not in ('adiri', 'mainnet'):
    raise SystemExit('Unsupported network')
keys = {
    'observer_vnet': 'observerVnetName', 'observer_subnet': 'observerSubnetName',
    'private_ip': 'privateIp', 'api_resource_group': 'apiResourceGroup',
    'load_balancer': 'loadBalancerName', 'observer_cidr': 'observerAddressPrefix',
    'observer_subnet_cidr': 'observerSubnetPrefix', 'api_vnet': 'apiVnetName',
    'api_cidr': 'apiAddressPrefix', 'api_subnet_cidr': 'apiSubnetPrefix',
}
if c['network'] == 'mainnet' and any(key not in c for key in keys):
    raise SystemExit('Mainnet requires an explicit topology for every network parameter')
parameters = {target: c[key] for key, target in keys.items() if key in c}
parameters['privateDnsName'] = c.get('private_dns_name', '')
with tempfile.NamedTemporaryFile('w', suffix='.json') as file:
    json.dump({'parameters': {key: {'value': value} for key, value in parameters.items()}}, file)
    file.flush()
    subprocess.run(['az', 'deployment', 'group', 'create' if args.apply else 'what-if',
                    '--subscription', c['subscription'], '-g', c['resource_group'],
                    '-n', c['network'] + '-private-pool', '--template-file', str(ROOT / 'infra/pool/main.bicep'),
                    '--parameters', '@' + file.name], check=True)
