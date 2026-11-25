#!/usr/bin/env python3
"""Validator recovery primitives. No node process is ever started by this module."""
import hashlib
from email.utils import formatdate
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
import uuid

MAX_BYTES = 8 * 1024 * 1024
KEY_FILES = ('node-keys/bls.kw', 'node-keys/primary.seed', 'node-keys/worker.seed', 'node-info.yaml')
EXTRA_FILES = ('installer-meta', 'deployment-state.json', 'profile.json')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def run(command, password=None):
    # Never forward subprocess output: it can contain passphrases or private keys.
    result = subprocess.run(command, input=password, capture_output=True)
    if result.returncode:
        raise ValueError('Recovery cryptographic operation failed; no private output was forwarded')
    return result.stdout


def certificate_id(certificate):
    pem = read_regular(certificate)
    if pem.count(b'-----BEGIN CERTIFICATE-----') != 1 or b'PRIVATE KEY' in pem or len(pem) > 16384:
        raise ValueError('Supply exactly one public recovery certificate, never a private key')
    der = run(['openssl', 'x509', '-in', str(certificate), '-outform', 'DER'])
    public = run(['openssl', 'x509', '-in', str(certificate), '-pubkey', '-noout'])
    # Require a conventional RSA recipient compatible with CMS OAEP and signing.
    description = run(['openssl', 'pkey', '-pubin', '-text', '-noout'], public).decode()
    match = re.search(r'Public-Key: \((\d+) bit\)', description)
    if not match or int(match[1]) < 3072 or 'Modulus:' not in description:
        raise ValueError('Use separate RSA certificates with keys of at least 3072 bits')
    return digest(der), digest(public)


def encrypt(data, certificate, directory, label):
    source = Path(directory) / (label + '.plain')
    source.write_bytes(data); source.chmod(0o600)
    try:
        return run(['openssl', 'cms', '-encrypt', '-binary', '-aes-256-gcm', '-outform', 'DER',
                    '-in', str(source), '-recip', str(certificate), '-keyopt', 'rsa_padding_mode:oaep', '-keyopt', 'rsa_oaep_md:sha256',
                    '-keyopt', 'rsa_mgf1_md:sha256'])
    finally:
        source.unlink(missing_ok=True)


def decrypt(ciphertext, certificate, private_key, password, directory, label):
    source = Path(directory) / (label + '.cms')
    source.write_bytes(ciphertext); source.chmod(0o600)
    try:
        return run(['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER', '-in', str(source),
                    '-recip', str(certificate), '-inkey', str(private_key), '-passin', 'stdin'], password)
    finally:
        source.unlink(missing_ok=True)


def sign_receipt(receipt, certificate, private_key, password, directory):
    source = Path(directory) / 'receipt.json'
    source.write_bytes(canonical(receipt)); source.chmod(0o600)
    try:
        return run(['openssl', 'cms', '-sign', '-binary', '-nodetach', '-nosmimecap', '-md', 'sha256',
                    '-outform', 'DER', '-in', str(source), '-signer', str(certificate),
                    '-inkey', str(private_key), '-passin', 'stdin'], password)
    finally:
        source.unlink(missing_ok=True)


def verify_receipt(signature, certificate, directory):
    source = Path(directory) / 'receipt.cms'
    source.write_bytes(signature)
    try:
        # Ignore certificates embedded by the sender. Only the pre-pinned recipient
        # certificate may verify the signature. No network trust lookup is involved.
        result = run(['openssl', 'cms', '-verify', '-binary', '-inform', 'DER', '-in', str(source),
                      '-nointern', '-certfile', str(certificate), '-noverify'])
        return json.loads(result)
    finally:
        source.unlink(missing_ok=True)


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('Backup input must be a small regular file')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            value = source.read(MAX_BYTES + 1)
        if len(value) > MAX_BYTES:
            raise ValueError('Backup input is too large')
        return value
    finally:
        os.close(fd)


def identity_files(datadir):
    root = Path(datadir)
    if root.is_symlink() or (root / 'node-keys').is_symlink() or (root / 'node-keys/bls.key').exists():
        raise ValueError('Refusing symlinks or an unencrypted BLS key')
    if {p.name for p in (root / 'node-keys').iterdir()} != {'bls.kw', 'primary.seed', 'worker.seed'}:
        raise ValueError('Unexpected identity files; inspect before backing up')
    return {name: read_regular(root / name) for name in KEY_FILES}


def identity_digest(files):
    return digest(canonical({name: digest(files[name]) for name in KEY_FILES}))


def archive(files):
    if set(files) != set(KEY_FILES + EXTRA_FILES):
        raise ValueError('Backup archive must contain only the approved identity/configuration files')
    if sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError('Identity backup exceeds its size limit')
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name, value in sorted(files.items()):
            info = tarfile.TarInfo(name); info.size = len(value); info.mode = 0o600
            tar.addfile(info, io.BytesIO(value))
    return output.getvalue()


def unpack(value):
    if len(value) > MAX_BYTES + 65536:
        raise ValueError('Backup exceeds its size limit')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(value), mode='r:') as tar:
        for member in tar:
            if (member.name not in KEY_FILES + EXTRA_FILES or member.name in files or
                    not member.isfile() or member.size < 0 or member.size > MAX_BYTES):
                raise ValueError('Unexpected/unsafe backup member')
            files[member.name] = tar.extractfile(member).read(MAX_BYTES + 1)
    # Validate the complete allowlist and combined limit without extracting paths.
    archive(files)
    return files


def token(resource):
    uri = ('http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=' +
           urllib.parse.quote(resource, safe=''))
    with urllib.request.urlopen(urllib.request.Request(uri, headers={'Metadata': 'true'}), timeout=15) as response:
        return json.load(response)['access_token']


def blob(c, container, name, data=None):
    account = c['backup']['storage_account']
    expected = 'ov' + c['network'] + hashlib.sha256((c['subscription'] + c['resource_group']).encode()).hexdigest()[:12]
    if account != expected or container not in ('identity', 'passphrase') or not re.fullmatch(r'[a-f0-9]{32}/[a-z.-]+', name):
        raise ValueError('Invalid network-scoped backup location')
    uri = f'https://{account}.blob.core.windows.net/{container}/{name}'
    headers = {'Authorization': 'Bearer ' + token('https://storage.azure.com/'), 'x-ms-version': '2023-11-03', 'x-ms-date': formatdate(usegmt=True)}
    if data is not None:
        if len(data) > MAX_BYTES + 65536:
            raise ValueError('Backup object exceeds size limit')
        headers.update({'x-ms-blob-type': 'BlockBlob', 'If-None-Match': '*', 'Content-Type': 'application/octet-stream'})
    req = urllib.request.Request(uri, headers=headers, data=data, method='GET' if data is None else 'PUT')
    with urllib.request.urlopen(req, timeout=60) as response:
        result = response.read(MAX_BYTES + 65537)
    if len(result) > MAX_BYTES + 65536:
        raise ValueError('Backup response exceeds size limit')
    return result


def image_from_metadata(c, metadata):
    values = dict(line.split('=', 1) for line in metadata.decode().splitlines() if '=' in line)
    image = values.get('DOCKER_IMAGE', '').strip().strip('"').strip("'")
    if not re.fullmatch(re.escape(c['release']['image_repository']) + r'@sha256:[a-f0-9]{64}', image):
        raise ValueError('Backup must name a network-specific pinned image')
    return image


def create(c, state, password, secret_id, *, datadir, config_dir, state_path, profile_path, directory, certificates):
    files = identity_files(datadir)
    if digest(files['node-info.yaml']) != state['node_info_sha256']:
        raise ValueError('Public identity changed; inspect before backup')
    files.update({'installer-meta': read_regular(Path(config_dir) / '.node-meta'),
                  'deployment-state.json': read_regular(state_path), 'profile.json': read_regular(profile_path)})
    image = image_from_metadata(c, files['installer-meta'])
    cert_ids = [certificate_id(p) for p in certificates]
    if cert_ids[0][1] == cert_ids[1][1]:
        raise ValueError('Identity and passphrase backups require different recovery keys')
    backup_id = uuid.uuid4().hex
    index = {'schema': 1, 'backup_id': backup_id, 'network': c['network'], 'address': state['address'],
             'source_vm_id': c['backup']['source_vm_id'], 'phase': state['phase'], 'image': image,
             'identity_digest': identity_digest(files), 'secret_id': secret_id,
             'files': {name: digest(data) for name, data in files.items()},
             'certificates': [entry[0] for entry in cert_ids], 'objects': {}}
    bundles = [('identity', 'identity.cms', archive(files)),
               ('passphrase', 'passphrase.cms', canonical({'schema': 1, 'network': c['network'],
                 'identity_digest': index['identity_digest'], 'secret_id': secret_id, 'passphrase': password.decode().rstrip('\n')}))]
    for i, (container, name, plain) in enumerate(bundles):
        cipher = encrypt(plain, certificates[i], directory, container)
        path = backup_id + '/' + name
        blob(c, container, path, cipher)
        # Check bytes persisted off-host before recording the backup as created.
        if digest(blob(c, container, path)) != digest(cipher):
            raise ValueError('Uploaded backup does not match its local ciphertext')
        index['objects'][container] = {'name': path, 'sha256': digest(cipher)}
    blob(c, 'identity', backup_id + '/index.json', canonical(index))
    return index


def check_gate(c, state, datadir, index, certificates, directory):
    if index['network'] != c['network'] or index['source_vm_id'] != c['backup']['source_vm_id'] or index['address'] != state['address']:
        raise ValueError('Backup belongs to another validator')
    if index['identity_digest'] != identity_digest(identity_files(datadir)) or index['secret_id'] != state['passphrase_secret_id']:
        raise ValueError('Identity/passphrase changed after backup')
    if index['certificates'] != [certificate_id(p)[0] for p in certificates]:
        raise ValueError('Recovery certificate changed after backup')
    if json.loads(blob(c, 'identity', index['backup_id'] + '/index.json')) != index:
        raise ValueError('Remote backup index changed')
    for container, obj in index['objects'].items():
        if digest(blob(c, container, obj['name'])) != obj['sha256']:
            raise ValueError('Remote encrypted backup changed')
    receipt = verify_receipt(blob(c, 'identity', index['backup_id'] + '/verified.cms'), certificates[0], directory)
    if receipt != {'schema': 1, 'index_sha256': digest(canonical(index)), 'offline_key_check': True}:
        raise ValueError('A verified offline recovery drill is required before activation')


def offline_key_check(files, index, password, directory):
    root = Path(directory) / 'drill'; root.mkdir(mode=0o700)
    for name in KEY_FILES:
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(files[name]); target.chmod(0o600)
    # Execute ONLY keytool in a network-isolated, unprivileged sandbox. No node,
    # RPC port, Docker socket, or host path beyond the throwaway identity is exposed.
    common = ['docker', 'run', '--rm', '-i', '--cpus', '1', '--network', 'none', '--cap-drop', 'ALL',
              '--security-opt', 'no-new-privileges', '--read-only', '--pids-limit', '64', '--memory', '512m',
              '--user', str(os.getuid()) + ':' + str(os.getgid()), '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
              '--mount', f'type=bind,src={root},dst=/data', '--entrypoint', '/usr/local/bin/telcoin', index['image'], '-q']
    def export():
        return json.loads(run(common + ['--bls-passphrase-source', 'no-passphrase', 'keytool', 'export-staking-args',
                                      '--node-info', '/data/node-info.yaml', '--json']))
    original = export()
    run(common + ['--datadir', '/data', '--bls-passphrase-source', 'stdin', 'keytool', 'generate', 'pop',
                  '--address', index['address']], password)
    if export() != original:
        raise ValueError('Restored BLS key/passphrase does not match the backed-up public identity')
    if {name: digest(read_regular(root / name)) for name in KEY_FILES if name != 'node-info.yaml'} != {
            name: index['files'][name] for name in KEY_FILES if name != 'node-info.yaml'}:
        raise ValueError('Offline verification changed private key material')
