#!/usr/bin/env python3
"""Validate and preview validator profiles locally without contacting Azure."""
import argparse
import copy
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]


def load_profile(path):
    profile = json.loads(Path(path).read_text())
    validate(profile)
    return profile


def validate(profile):
    network = profile['network']
    if profile['role'] != 'validator' or network not in ('adiri', 'mainnet'):
        raise ValueError('Choose an Adiri or mainnet validator profile')
    if profile['resource_group'] != 'oprail-validator-' + network:
        raise ValueError('Validator resources must use their own network-specific resource group')
    if profile['operations']['approval_environment'] != 'validator-operations-' + network:
        raise ValueError('Validator approvals must be isolated by network')
    if profile['identity']['passphrase_vault'] != 'oprail-val-' + network + '-keys':
        raise ValueError('Validator passphrase vault must match the network')
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,62}', profile['dns_label']):
        raise ValueError('Invalid validator DNS label')
    if profile['vm']['security_type'] != 'TrustedLaunch' or not profile['vm']['vtpm_enabled'] or not profile['vm']['secure_boot_enabled']:
        raise ValueError('Validators require Trusted Launch, vTPM and Secure Boot')
    release = profile['release']
    upstream = 'testnet' if network == 'adiri' else 'mainnet'
    expected = ('0x7e1', 'adiri') if network == 'adiri' else ('0x2db', '')
    if (release['chain_id'], release['features']) != expected or release['chain'] != network:
        raise ValueError('Build features and chain ID must match the validator network')
    if release['scripts_network'] != upstream or release['image_repository'] != 'ghcr.io/telcoin-association/tn-' + upstream:
        raise ValueError('Use the upstream release channel for this network')
    if release['tag'] and not re.fullmatch(
            r'v[0-9]+\.[0-9]+\.[0-9]+' + ('-adiri' if network == 'adiri' else ''), release['tag']):
        raise ValueError('Use a stable release tag for this network')
    if profile['initial_node_count'] != 1:
        raise ValueError('Initial validator deployments contain one node')
    networking = profile['networking']
    if (networking['public_rpc'] or networking['public_tcp_ports'] or
            networking['rpc_bind_address'] != '127.0.0.1' or networking['metrics_bind_address'] != '127.0.0.1'):
        raise ValueError('Validator RPC and metrics must remain local with private management')
    url = networking['advertised_transaction_rpc_url']
    if url:
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Advertise an HTTPS gateway URL without embedded credentials')
    vnet = ipaddress.ip_network(networking['address_prefix'])
    subnets = [ipaddress.ip_network(networking[key]['prefix']) for key in ('node_subnet', 'management_subnet')]
    if any(not subnet.subnet_of(vnet) for subnet in subnets) or subnets[0].overlaps(subnets[1]):
        raise ValueError('Validator and management subnets must be distinct and inside the VNet')
    for kind in ('ssh_source_cidrs', 'p2p_source_cidrs'):
        for cidr in networking[kind]:
            parsed = ipaddress.ip_network(cidr)
            if parsed.version != 4 or (parsed.prefixlen < 8 and (network == 'mainnet' or kind == 'ssh_source_cidrs')):
                raise ValueError('Use explicit IPv4 source CIDRs; mainnet and SSH cannot allow the entire Internet')
    if networking['p2p_udp_ports'] != [49590, 49594]:
        raise ValueError('Current validator transport uses UDP 49590 and 49594')
    if profile['storage']['datadir'] != '/var/lib/telcoin':
        raise ValueError('Use the isolated validator data directory')
    growth = profile['storage']['auto_grow']
    if type(growth['enabled']) is not bool or not 0 < growth['threshold_percent'] < 100 or growth['increment_gb'] < 1:
        raise ValueError('Invalid disk growth settings')
    for name, lun in [('execution', 0), ('consensus', 1)]:
        disk = profile['storage'][name]
        size, iops, throughput = disk['size_gb'], disk['iops'], disk['throughput_mbps']
        if disk['lun'] != lun or not 6 <= size <= 65536:
            raise ValueError('Invalid ' + name + ' disk capacity or LUN')
        if not 3000 <= iops <= min(80000, size * 500) or not 125 <= throughput <= min(1200, iops // 4):
            raise ValueError('Invalid Premium SSD v2 performance for ' + name)
    if not re.fullmatch(r'[A-Za-z0-9-]+', profile['identity']['passphrase_secret']):
        raise ValueError('Invalid passphrase secret name')
    address = profile['identity']['execution_address']
    if address and (not re.fullmatch(r'0x[0-9a-fA-F]{40}', address) or int(address, 16) == 0):
        raise ValueError('Execution address must be a nonzero Ethereum address')


def plan(profile):
    result = copy.deepcopy(profile)
    result['node_names'] = [profile['node_name_prefix'] + '1']
    result['inputs_to_supply'] = [name for name, value in {
        'release.tag': profile['release']['tag'],
        'identity.execution_address': profile['identity']['execution_address'],
        'networking.advertised_transaction_rpc_url': profile['networking']['advertised_transaction_rpc_url'],
    }.items() if not value]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--network', choices=['adiri', 'mainnet'])
    source.add_argument('--config', type=Path)
    parser.add_argument('--execution-disk-gb', type=int)
    parser.add_argument('--consensus-disk-gb', type=int)
    parser.add_argument('--release-tag')
    parser.add_argument('--execution-address')
    args = parser.parse_args()
    path = args.config or ROOT / 'infra/validators' / (args.network + '.json')
    profile = load_profile(path)
    for name, size in [('execution', args.execution_disk_gb), ('consensus', args.consensus_disk_gb)]:
        if size is not None:
            profile['storage'][name]['size_gb'] = size
    if args.release_tag is not None:
        profile['release']['tag'] = args.release_tag
    if args.execution_address is not None:
        profile['identity']['execution_address'] = args.execution_address
    validate(profile)
    print(json.dumps(plan(profile), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError) as error:
        raise SystemExit(str(error))
