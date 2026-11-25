import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/validators'))
import recovery
from profile import load_profile
from backup import validate_index


class ValidatorRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.certificates = []; cls.keys = []
        for i in range(3):
            key = cls.root / f'{i}.key'; cert = cls.root / f'{i}.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-days', '2',
                            '-keyout', str(key), '-out', str(cert), '-subj', f'/CN=Recovery-test-{i}',
                            '-passout', 'stdin'], input=b'fixture-password\n', check=True, capture_output=True)
            key.chmod(0o600)
            cls.certificates.append(cert); cls.keys.append(key)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def profile(self, network='adiri'):
        return load_profile(ROOT / 'infra/validators' / (network + '.json'))

    def fixture(self, directory):
        directory = Path(directory); data = directory / 'data'; (data / 'node-keys').mkdir(parents=True)
        for name in recovery.KEY_FILES:
            (data / name).write_bytes(('test-' + name).encode())
        c = self.profile(); c['identity']['execution_address'] = '0x' + '1' * 40
        state = {'network': 'adiri', 'address': c['identity']['execution_address'], 'phase': 'prepared',
                 'node_info_sha256': recovery.digest((data / 'node-info.yaml').read_bytes()),
                 'passphrase_secret_id': 'https://oprail-val-adiri-keys.vault.azure.net/secrets/validator-1-bls-passphrase/' + 'a' * 32}
        config = directory / 'config'; config.mkdir()
        image = c['release']['image_repository'] + '@sha256:' + 'b' * 64
        (config / '.node-meta').write_text('DOCKER_IMAGE=' + image + '\n')
        saved = directory / 'state.json'; saved.write_text(json.dumps(state))
        profile = directory / 'profile.json'; profile.write_text(json.dumps(c))
        return c, state, data, config, saved, profile

    def create(self, fixture, directory, store):
        c, state, data, config, saved, profile = fixture
        def blob(c, container, name, data=None):
            if data is None: return store[container, name]
            if (container, name) in store: raise ValueError('Overwrite')
            store[container, name] = data
            return b''
        with patch.object(recovery, 'blob', side_effect=blob):
            index = recovery.create(c, state, b'BLS-passphrase-test\n', state['passphrase_secret_id'], datadir=data,
                                    config_dir=config, state_path=saved, profile_path=profile, directory=directory,
                                    certificates=self.certificates[:2])
        return index, blob

    def test_real_crypto_roundtrip_and_signed_gate_reject_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = self.fixture(d); store = {}
            index, blob = self.create(fixture, d, store)
            validate_index(fixture[0], index, self.certificates[:2])
            files = None
            for i, kind in enumerate(('identity', 'passphrase')):
                cipher = store[kind, index['objects'][kind]['name']]
                self.assertNotIn(b'BLS-passphrase-test', cipher)
                plain = recovery.decrypt(cipher, self.certificates[i], self.keys[i], b'fixture-password\n', d, kind)
                if kind == 'identity':
                    files = recovery.unpack(plain)
                    self.assertEqual({name: recovery.digest(v) for name, v in files.items()}, index['files'])
                    self.assertNotIn(b'BLS-passphrase-test', plain)
                    self.assertNotIn('bls-passphrase.cred', files)
                else: self.assertEqual(json.loads(plain)['passphrase'], 'BLS-passphrase-test')
            receipt = {'schema': 1, 'index_sha256': recovery.digest(recovery.canonical(index)), 'offline_key_check': True}
            signature = recovery.sign_receipt(receipt, self.certificates[0], self.keys[0], b'fixture-password\n', d)
            store['identity', index['backup_id'] + '/verified.cms'] = signature
            with patch.object(recovery, 'blob', side_effect=blob):
                recovery.check_gate(fixture[0], fixture[1], fixture[2], index, self.certificates[:2], d)
                obj = index['objects']['identity']['name']; store['identity', obj] += b'tamper'
                with self.assertRaisesRegex(ValueError, 'encrypted backup changed'):
                    recovery.check_gate(fixture[0], fixture[1], fixture[2], index, self.certificates[:2], d)

    def test_wrong_private_key_password_and_receipt_signer_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            cipher = recovery.encrypt(b'secret fixture', self.certificates[0], d, 'test')
            for key, password in [(self.keys[1], b'fixture-password\n'), (self.keys[0], b'wrong\n')]:
                with self.subTest(key=key), self.assertRaises(ValueError) as error:
                    recovery.decrypt(cipher, self.certificates[0], key, password, d, 'test')
                self.assertNotIn('fixture-password', str(error.exception))
            signature = recovery.sign_receipt({'offline_key_check': True}, self.certificates[1], self.keys[1], b'fixture-password\n', d)
            with self.assertRaises(ValueError): recovery.verify_receipt(signature, self.certificates[0], d)
            broken = bytearray(signature); broken[-1] ^= 1
            with self.assertRaises(ValueError): recovery.verify_receipt(bytes(broken), self.certificates[1], d)

    def test_archive_rejects_traversal_symlink_duplicate_missing_and_oversize(self):
        for name, kind, size in [('../escape', tarfile.REGTYPE, 0), ('node-info.yaml', tarfile.SYMTYPE, 0),
                                 ('node-info.yaml', tarfile.REGTYPE, recovery.MAX_BYTES + 1)]:
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as tar:
                member = tarfile.TarInfo(name); member.type = kind; member.size = size
                if size == 0: tar.addfile(member)
                else:
                    # Size rejection is checked before extraction; no large fixture needed.
                    tar.fileobj.write(member.tobuf()); tar.offset += 512
            with self.subTest(name=name, kind=kind), self.assertRaises((ValueError, tarfile.TarError)):
                recovery.unpack(output.getvalue())
        with self.assertRaises(ValueError): recovery.archive({'node-info.yaml': b'partial'})
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as tar:
            for _ in range(2): tar.addfile(tarfile.TarInfo('node-info.yaml'))
        with self.assertRaises(ValueError): recovery.unpack(output.getvalue())

    def test_symlink_and_cleartext_keys_are_not_backed_up(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = self.fixture(d); data = fixture[2]
            (data / 'node-keys/bls.key').write_text('cleartext')
            with self.assertRaises(ValueError): recovery.identity_files(data)
            (data / 'node-keys/bls.key').unlink()
            key = data / 'node-keys/bls.kw'; key.unlink(); key.symlink_to(data / 'node-info.yaml')
            with self.assertRaises(OSError): recovery.identity_files(data)

    def test_different_recovery_keys_required_before_upload(self):
        with tempfile.TemporaryDirectory() as d:
            c, state, data, config, saved, profile = self.fixture(d)
            with patch.object(recovery, 'blob') as blob, self.assertRaisesRegex(ValueError, 'different recovery keys'):
                recovery.create(c, state, b'BLS-passphrase-test\n', state['passphrase_secret_id'], datadir=data,
                                config_dir=config, state_path=saved, profile_path=profile, directory=d,
                                certificates=[self.certificates[0]] * 2)
            blob.assert_not_called()

    def test_backup_cannot_cross_network_or_accept_untrusted_image(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = self.fixture(d); index, _ = self.create(fixture, d, {})
            bad = copy.deepcopy(index); bad['image'] = 'untrusted/image@sha256:' + 'a' * 64
            with self.assertRaises(ValueError): validate_index(fixture[0], bad, self.certificates[:2])
            with self.assertRaises(ValueError): validate_index(self.profile('mainnet'), index, self.certificates[:2])
            bad = copy.deepcopy(index); bad['secret_id'] = 'https://attacker.example/secret'
            with self.assertRaises(ValueError): validate_index(fixture[0], bad, self.certificates[:2])

    def test_no_receipt_or_changed_identity_blocks_activation(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = self.fixture(d); index, blob = self.create(fixture, d, {})
            with patch.object(recovery, 'blob', side_effect=blob), self.assertRaises(KeyError):
                recovery.check_gate(fixture[0], fixture[1], fixture[2], index, self.certificates[:2], d)
            (fixture[2] / 'node-keys/worker.seed').write_text('changed')
            with patch.object(recovery, 'blob') as remote, self.assertRaisesRegex(ValueError, 'changed after backup'):
                recovery.check_gate(fixture[0], fixture[1], fixture[2], index, self.certificates[:2], d)
            remote.assert_not_called()

    def test_key_drill_is_offline_no_node_no_secrets_in_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            fixture = self.fixture(d); index, _ = self.create(fixture, d, {})
            files = recovery.identity_files(fixture[2]); result = b'{"blsPubkey":"public","signature":"proof"}'
            with patch.object(recovery, 'run', side_effect=[result, b'', result]) as run:
                recovery.offline_key_check(files, index, b'BLS-passphrase-test\n', d)
            for call in run.call_args_list:
                command = call.args[0]
                self.assertIn('none', command); self.assertIn('--read-only', command)
                self.assertNotIn('node', command); self.assertNotIn('BLS-passphrase-test', ' '.join(command))
            self.assertIn('-i', run.call_args_list[1].args[0])
            self.assertEqual(run.call_args_list[1].args[1], b'BLS-passphrase-test\n')

    def test_storage_has_retention_no_shared_keys_and_no_delete_role(self):
        template = (ROOT / 'infra/validators/main.bicep').read_text()
        self.assertIn('allowSharedKeyAccess: false', template)
        self.assertIn('allowBlobPublicAccess: false', template)
        self.assertIn('enablePurgeProtection: true', template)
        self.assertIn('isVersioningEnabled: true', template)
        self.assertNotIn('containers/blobs/delete', template)

    def test_private_key_is_rejected_as_public_certificate(self):
        with self.assertRaises(ValueError): recovery.certificate_id(self.keys[0])

    def test_guest_finalize_cannot_bypass_drill_with_acknowledgment(self):
        spec = importlib.util.spec_from_file_location('recovery_guest_test', ROOT / 'scripts/validators/guest.py')
        guest = importlib.util.module_from_spec(spec); spec.loader.exec_module(guest)
        with tempfile.TemporaryDirectory() as d:
            fixture = self.fixture(d); c, state, data, config, saved, profile = fixture
            state.update({'ip': '192.0.2.1', 'tag': 'v0.17.0-adiri'})
            c['release']['tag'] = state['tag']; saved.write_text(json.dumps(state)); profile.write_text(json.dumps(c))
            (saved.parent / 'backup-index.json').write_text('{}')
            temporary = Mock(); temporary.__enter__ = Mock(return_value=d); temporary.__exit__ = Mock(return_value=False)
            with patch.object(guest, 'STATE', saved), patch.object(guest, 'CONFIG', profile), patch.object(guest, 'mounts'), \
                 patch.object(Path, 'read_bytes', return_value=(data / 'node-info.yaml').read_bytes()), \
                 patch.object(guest.tempfile, 'TemporaryDirectory', return_value=temporary), \
                 patch.object(recovery, 'check_gate', side_effect=ValueError('missing verified receipt')) as gate, \
                 patch.object(guest, 'private_run') as setup, patch.object(guest, 'secret') as secret, \
                 patch('sys.argv', ['guest', '--phase', 'finalize', '--expected-network', 'adiri',
                                    '--public-ip', '192.0.2.1', '--acknowledge-key-backup']):
                with self.assertRaisesRegex(ValueError, 'missing verified receipt'): guest.main()
                gate.assert_called_once(); setup.assert_not_called(); secret.assert_not_called()

    def test_guest_refuses_identity_only_backup_after_installation(self):
        spec = importlib.util.spec_from_file_location('recovery_guest_running_test', ROOT / 'scripts/validators/guest.py')
        guest = importlib.util.module_from_spec(spec); spec.loader.exec_module(guest)
        with tempfile.TemporaryDirectory() as d:
            c, state, data, config, saved, profile = self.fixture(d)
            state['phase'] = 'running'; saved.write_text(json.dumps(state))
            with patch.object(guest, 'STATE', saved), patch.object(guest, 'CONFIG', profile), patch.object(guest, 'mounts'), \
                 patch.object(guest, 'secret') as secret, patch.object(recovery, 'create') as create, \
                 patch('sys.argv', ['guest', '--phase', 'backup', '--expected-network', 'adiri', '--public-ip', '192.0.2.1']):
                with self.assertRaisesRegex(ValueError, 'coordinated cold recovery'): guest.main()
                create.assert_not_called(); secret.assert_not_called()

    def test_passphrase_reader_pins_version_and_rejects_different_returned_version(self):
        spec = importlib.util.spec_from_file_location('recovery_guest_secret_test', ROOT / 'scripts/validators/guest.py')
        guest = importlib.util.module_from_spec(spec); spec.loader.exec_module(guest)
        c = self.profile(); version = f"https://{c['identity']['passphrase_vault']}.vault.azure.net/secrets/{c['identity']['passphrase_secret']}/" + 'a' * 32
        for returned in [version, version[:-1] + 'b']:
            imds = Mock(); imds.__enter__ = Mock(return_value=io.StringIO('{"access_token":"fixture-token"}')); imds.__exit__ = Mock(return_value=False)
            vault = Mock(); vault.__enter__ = Mock(return_value=io.StringIO(json.dumps({'id': returned, 'value': 'fixture-password-value'}))); vault.__exit__ = Mock(return_value=False)
            with patch.object(guest.urllib.request, 'urlopen', side_effect=[imds, vault]) as remote:
                if returned == version:
                    self.assertEqual(guest.secret(c, version, with_id=True), (b'fixture-password-value\n', version))
                else:
                    with self.assertRaisesRegex(ValueError, 'Unexpected passphrase secret version'): guest.secret(c, version)
                self.assertEqual(remote.call_args.args[0].full_url, version + '?api-version=7.4')

    def test_corrupt_upload_is_not_recorded_as_completed_backup(self):
        with tempfile.TemporaryDirectory() as d:
            c, state, data, config, saved, profile = self.fixture(d)
            writes = []
            def blob(c, container, name, data=None):
                if data is not None: writes.append(name); return b''
                return b'corrupt'
            with patch.object(recovery, 'blob', side_effect=blob), self.assertRaisesRegex(ValueError, 'does not match'):
                recovery.create(c, state, b'BLS-passphrase-test\n', state['passphrase_secret_id'], datadir=data,
                                config_dir=config, state_path=saved, profile_path=profile, directory=d,
                                certificates=self.certificates[:2])
            self.assertFalse(any(name.endswith('/index.json') for name in writes))

    def test_operator_drill_downloads_only_ciphertext_and_uploads_signed_receipt(self):
        import backup
        with tempfile.TemporaryDirectory() as d:
            fixture = self.fixture(d); store = {}; index, _ = self.create(fixture, d, store)
            uploaded = []
            def azure(*args):
                if args[:2] == ('account', 'show'): return json.dumps({'id': fixture[0]['subscription']}).encode()
                if args[:3] == ('storage', 'blob', 'download'):
                    kind = args[args.index('--container-name') + 1]; name = args[args.index('--name') + 1]
                    Path(args[args.index('--file') + 1]).write_bytes(store[kind, name]); return b''
                if args[:3] == ('storage', 'blob', 'upload'):
                    uploaded.append(Path(args[args.index('--file') + 1]).read_bytes()); return b''
                raise AssertionError(args)
            args = ['backup', '--network', 'adiri', '--backup-id', index['backup_id'],
                    '--identity-certificate', str(self.certificates[0]), '--passphrase-certificate', str(self.certificates[1]),
                    '--identity-private-key', str(self.keys[0]), '--passphrase-private-key', str(self.keys[1])]
            # Docker invocation is separately constrained/tested; use a fake inspect
            # and keytool here, while encryption, decryption and signing stay real.
            original_run = subprocess.run
            def subprocess_run(command, **kwargs):
                if command[:3] == ['docker', 'image', 'inspect']: return Mock(returncode=0)
                return original_run(command, **kwargs)
            with patch('sys.argv', args), patch.object(backup, 'load_profile', return_value=fixture[0]), \
                 patch.object(backup, 'azure', side_effect=azure), patch.object(backup, 'scratch', return_value=Path(d)), \
                 patch.object(backup.os, 'getuid', return_value=1000), patch.object(backup.getpass, 'getpass', return_value='fixture-password'), \
                 patch.object(backup.subprocess, 'run', side_effect=subprocess_run), patch.object(recovery, 'offline_key_check') as drill, \
                 patch('builtins.print') as output:
                backup.main()
                self.assertEqual(drill.call_args.args[2], b'BLS-passphrase-test\n')
                self.assertNotIn('BLS-passphrase-test', str(output.call_args_list))
            self.assertEqual(len(uploaded), 1)
            self.assertEqual(recovery.verify_receipt(uploaded[0], self.certificates[0], d),
                             {'schema': 1, 'index_sha256': recovery.digest(recovery.canonical(index)), 'offline_key_check': True})
            self.assertFalse(any(Path(d).glob('validator-recovery-*')))

    def test_blob_transport_uses_oauth_https_and_refuses_overwrites(self):
        response = Mock(); response.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b''))); response.__exit__ = Mock(return_value=False)
        with patch.object(recovery, 'token', return_value='fixture-bearer'), patch.object(recovery.urllib.request, 'urlopen', return_value=response) as remote:
            c = self.profile(); recovery.blob(c, 'identity', 'a' * 32 + '/identity.cms', b'encrypted-data')
            request = remote.call_args.args[0]
            self.assertTrue(request.full_url.startswith('https://' + c['backup']['storage_account'] + '.blob.core.windows.net/identity/'))
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-bearer')
            self.assertEqual(request.headers['If-none-match'], '*')
            self.assertIn('X-ms-date', request.headers)
        c['backup']['storage_account'] = self.profile('mainnet')['backup']['storage_account']
        with patch.object(recovery, 'token') as auth, self.assertRaises(ValueError):
            recovery.blob(c, 'identity', 'a' * 32 + '/identity.cms', b'encrypted-data')
        auth.assert_not_called()
