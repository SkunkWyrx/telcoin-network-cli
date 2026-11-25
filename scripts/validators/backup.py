#!/usr/bin/env python3
"""Offline recovery drill for validator backups. Never starts or restores a live node."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from profile import ROOT, load_profile
import recovery


def azure(*args):
    result = subprocess.run(['az', *args], capture_output=True)
    if result.returncode:
        raise ValueError('Azure backup operation failed; no private output was forwarded')
    return result.stdout


def scratch(path):
    path = Path(path).resolve()
    # Plaintext recovery material may be written only to a Linux RAM filesystem.
    result = subprocess.run(['findmnt', '-n', '-o', 'FSTYPE', '--target', str(path)], capture_output=True, text=True)
    if result.returncode or result.stdout.strip() != 'tmpfs':
        raise ValueError('Use an isolated Linux recovery host and a tmpfs scratch directory, e.g. /dev/shm')
    return path


def validate_index(c, index, certificates):
    expected_vm = (f"/subscriptions/{c['subscription']}/resourceGroups/{c['resource_group']}/providers/"
                   f"Microsoft.Compute/virtualMachines/{c['node_name_prefix']}1")
    if (index['schema'] != 1 or index['network'] != c['network'] or index['source_vm_id'] != expected_vm or index['phase'] != 'prepared' or
            not re.fullmatch(r'[a-f0-9]{32}', index['backup_id']) or
            not re.fullmatch(r'0x[0-9a-fA-F]{40}', index['address']) or int(index['address'], 16) == 0):
        raise ValueError('Backup does not belong to this network and validator')
    recovery.image_from_metadata(c, ('DOCKER_IMAGE=' + index['image']).encode())
    vault = c['identity']['passphrase_vault']; secret = c['identity']['passphrase_secret']
    if not re.fullmatch(re.escape(f'https://{vault}.vault.azure.net/secrets/{secret}/') + r'[a-f0-9]{32}', index['secret_id']):
        raise ValueError('Unexpected passphrase secret version')
    if index['certificates'] != [recovery.certificate_id(p)[0] for p in certificates]:
        raise ValueError('Backup recovery certificates do not match')
    if recovery.certificate_id(certificates[0])[1] == recovery.certificate_id(certificates[1])[1]:
        raise ValueError('Use distinct recovery keys')
    if set(index['objects']) != {'identity', 'passphrase'} or set(index['files']) != set(recovery.KEY_FILES + recovery.EXTRA_FILES):
        raise ValueError('Incomplete backup index')
    for container, obj in index['objects'].items():
        if obj['name'] != index['backup_id'] + '/' + container + '.cms' or not re.fullmatch('[a-f0-9]{64}', obj['sha256']):
            raise ValueError('Unexpected backup object')
    if any(not re.fullmatch('[a-f0-9]{64}', value) for value in index['files'].values()):
        raise ValueError('Invalid backup file digest')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--network', choices=['adiri', 'mainnet'], required=True)
    p.add_argument('--config', type=Path)
    p.add_argument('--backup-id', required=True)
    p.add_argument('--identity-certificate', type=Path, required=True)
    p.add_argument('--passphrase-certificate', type=Path, required=True)
    p.add_argument('--identity-private-key', type=Path, required=True)
    p.add_argument('--passphrase-private-key', type=Path, required=True)
    p.add_argument('--scratch-dir', type=Path, default=Path('/dev/shm'))
    p.add_argument('--stage-only', action='store_true', help='Stage a verified identity for MANUAL recovery; never start a node')
    p.add_argument('--recover-passphrase-to-vault', action='store_true',
                   help='With --stage-only, recover the original passphrase ONLY if its configured vault secret is absent')
    p.add_argument('--output-dir', type=Path, help='New private directory on tmpfs; valid only with --stage-only')
    args = p.parse_args()
    c = load_profile(args.config or ROOT / 'infra/validators' / (args.network + '.json'))
    if c['network'] != args.network or not re.fullmatch('[a-f0-9]{32}', args.backup_id):
        raise ValueError('Invalid network or backup ID')
    certificates = [args.identity_certificate.resolve(), args.passphrase_certificate.resolve()]
    private_keys = [args.identity_private_key.absolute(), args.passphrase_private_key.absolute()]
    for path in private_keys:
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError('Recovery private keys must be regular owner-only files')
    for certificate in certificates:
        recovery.certificate_id(certificate)
    if os.getuid() == 0:
        raise ValueError('Run the drill as a dedicated non-root recovery operator')
    root = scratch(args.scratch_dir)
    if args.recover_passphrase_to_vault and not args.stage_only:
        raise ValueError('Passphrase recovery is available only while staging a manual restore')
    if bool(args.output_dir) != args.stage_only:
        raise ValueError('--stage-only requires a new --output-dir on tmpfs')
    if args.output_dir:
        scratch(args.output_dir.parent)
        if args.output_dir.exists():
            raise ValueError('Use a new recovery output directory on tmpfs')
    if json.loads(azure('account', 'show', '-o', 'json'))['id'] != c['subscription']:
        raise ValueError('Select the validator subscription first')
    account = c['backup']['storage_account']
    # Download only ciphertext; Azure CLI never handles the plaintext key/passphrase.
    def download(container, name, target):
        azure('storage', 'blob', 'download', '--account-name', account, '--auth-mode', 'login',
              '--container-name', container, '--name', name, '--file', str(target), '--only-show-errors', '-o', 'none')
        return recovery.read_regular(target)
    os.umask(0o077)
    with tempfile.TemporaryDirectory(dir=root, prefix='validator-recovery-') as temporary:
        directory = Path(temporary)
        index = json.loads(download('identity', args.backup_id + '/index.json', directory / 'index.json'))
        validate_index(c, index, certificates)
        files = None; secret = None
        passwords = []
        for kind in ('identity', 'passphrase'):
            passwords.append((getpass.getpass(f'{kind.title()} recovery private-key password (blank if unencrypted): ') + '\n').encode())
        for i, container in enumerate(('identity', 'passphrase')):
            obj = index['objects'][container]
            cipher = download(container, obj['name'], directory / (container + '.download'))
            if recovery.digest(cipher) != obj['sha256']:
                raise ValueError('Encrypted backup checksum failed')
            plain = recovery.decrypt(cipher, certificates[i], private_keys[i], passwords[i], directory, container)
            if container == 'identity':
                files = recovery.unpack(plain)
                if {name: recovery.digest(value) for name, value in files.items()} != index['files'] or recovery.identity_digest(files) != index['identity_digest']:
                    raise ValueError('Identity backup checksum failed')
            else:
                secret = json.loads(plain)
        if (secret['schema'] != 1 or secret['network'] != c['network'] or secret['identity_digest'] != index['identity_digest'] or
                secret['secret_id'] != index['secret_id'] or not isinstance(secret['passphrase'], str) or
                len(secret['passphrase'].encode()) < 16 or secret['passphrase'] != secret['passphrase'].strip() or
                '\n' in secret['passphrase'] or '\r' in secret['passphrase']):
            raise ValueError('Passphrase backup does not match this validator')
        # Exact image must already be present: a drill does not fetch network code.
        azure_image = subprocess.run(['docker', 'image', 'inspect', index['image']], capture_output=True)
        if azure_image.returncode:
            raise ValueError('Preload the trusted pinned image on the isolated recovery host before the drill')
        recovery.offline_key_check(files, index, (secret['passphrase'] + '\n').encode(), directory)
        receipt = {'schema': 1, 'index_sha256': recovery.digest(recovery.canonical(index)), 'offline_key_check': True}
        signature = recovery.sign_receipt(receipt, certificates[0], private_keys[0], passwords[0], directory)
        if recovery.verify_receipt(signature, certificates[0], directory) != receipt:
            raise ValueError('Recovery receipt failed verification')
        signature_file = directory / 'verified.cms'; signature_file.write_bytes(signature)
        if args.stage_only:
            # No network/node/TPM writes. Operator must fence the old signer and
            # restore CURRENT consensus state before copying to any replacement VM.
            args.output_dir.mkdir(mode=0o700)
            for name, value in files.items():
                target = args.output_dir / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.write_bytes(value); target.chmod(0o600)
            # Passphrase deliberately not written to the staged directory.
            if args.recover_passphrase_to_vault:
                vault = c['identity']['passphrase_vault']; name = c['identity']['passphrase_secret']
                existing = json.loads(azure('keyvault', 'secret', 'list', '--vault-name', vault, '-o', 'json'))
                if any(item['name'] == name for item in existing):
                    raise ValueError('Existing passphrase secret retained; recovery never rotates it')
                result = subprocess.run(['az', 'keyvault', 'secret', 'set', '--vault-name', vault, '-n', name,
                                         '--file', '/dev/stdin', '--query', 'id', '-o', 'tsv', '--only-show-errors'],
                                        input=secret['passphrase'].encode(), capture_output=True)
                if result.returncode:
                    raise ValueError('Passphrase recovery to Key Vault failed; no private output was forwarded')
                reference = result.stdout.decode().strip()
                if not re.fullmatch(re.escape(f'https://{vault}.vault.azure.net/secrets/{name}/') + r'[a-f0-9]{32}', reference):
                    raise ValueError('Unexpected recovered passphrase reference; inspect the vault manually')
                (args.output_dir / 'recovered-secret-id.txt').write_text(reference + '\n')
                print('Original passphrase recovered to the configured vault; no existing secret was replaced.')
            print('Verified identity staged on tmpfs. No node started; passphrase not written to disk.')
            print('Keep old signer fenced; restore consistent consensus state and reseal credentials before activation.')
        else:
            azure('storage', 'blob', 'upload', '--account-name', account, '--auth-mode', 'login',
                  '--container-name', 'identity', '--name', args.backup_id + '/verified.cms', '--file', str(signature_file),
                  '--overwrite', 'false', '--only-show-errors', '-o', 'none')
            print('Offline recovery verified; signed public receipt uploaded. No validator was started.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Includes malformed/untrusted archives: their contents must not enter logs.
        raise SystemExit('Validator recovery failed; private data and subprocess output were withheld. Check inputs and recovery-host prerequisites.')
