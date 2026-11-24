import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rpc_keys', ROOT / 'infra/rpc-tls/api-key-sync.py')
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


def key(id='a', secret='b', tier='partner', **kwargs):
    return {'id': id * 32, 'key': secret * 64, 'tier': tier, **kwargs}


class RpcKeyPolicyTests(unittest.TestCase):
    def test_per_key_identity_and_tier_are_independent_of_source_ip(self):
        text = keys.render([key(), key('c', 'd', 'internal')], {}, now=100)
        self.assertIn('"' + 'b' * 64 + '" "' + 'a' * 32 + '";', text)
        self.assertIn('partner $rpc_key_id;', text)
        self.assertIn('internal $rpc_key_id;', text)
        self.assertIn('anonymous $binary_remote_addr;', text)
        self.assertIn('default invalid;', text)
        self.assertIn('"" anonymous;', text)
        self.assertIn('rate=300r/s;', text)

    def test_revoked_and_expired_keys_never_reach_nginx(self):
        text = keys.render([key(enabled=False), key('c', 'd', expires=100)], {}, now=100)
        self.assertNotIn('b' * 64, text)
        self.assertNotIn('d' * 64, text)

    def test_untrusted_values_cannot_inject_nginx_configuration(self):
        for item in [key(tier='partner; include /tmp/evil;'), key(secret='"'), {'id': 'bad', 'key': 'b' * 64, 'tier': 'partner'}]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                keys.render([item], {})
        with self.assertRaises(ValueError):
            keys.render([key(), key()], {})
        for rate in [True, '100;evil', 0, 10001]:
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                keys.policy({'limits': {'partner': {'rate': rate, 'burst': 200}}})

    def test_reload_failure_restores_previous_private_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'api-keys.conf'
            path.write_text('old policy')
            run = Mock(side_effect=[Mock(returncode=0), subprocess.CalledProcessError(1, ['nginx'])])
            with self.assertRaises(subprocess.CalledProcessError):
                keys.install('new policy', root, run)
            self.assertEqual(path.read_text(), 'old policy')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(run.call_count, 2)

    def test_unchanged_policy_does_not_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'api-keys.conf').write_text('same')
            run = Mock()
            keys.install('same', root, run)
            run.assert_not_called()

    def test_vault_failure_bounds_cached_credentials_and_still_applies_expiry(self):
        for age, expires, present in [(20, 500, True), (301, 500, False), (20, 90, False)]:
            with self.subTest(age=age, expires=expires), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'config.json').write_text(json.dumps({'vault': 'test-vault'}))
                (root / 'api-keys-cache.json').write_text(json.dumps({'fetched_at': 100-age, 'keys': [key(expires=expires)]}))
                original_read = Path.read_text
                def read(path, *args, **kwargs):
                    return original_read(root / 'config.json' if str(path) == '/etc/telcoin/api-keys.json' else path, *args, **kwargs)
                with patch.object(keys, 'ROOT', root), patch.object(keys, 'fetch', side_effect=TimeoutError), patch.object(
                        keys, 'install') as install, patch.object(keys.time, 'time', return_value=100), patch.object(Path, 'read_text', read):
                    with self.assertRaises(RuntimeError): keys.main()
                    self.assertEqual('b'*64 in install.call_args.args[0], present)

    def test_new_config_keeps_header_out_of_upstream(self):
        template = (ROOT / 'infra/rpc-tls/nginx-api-keys.conf.in').read_text()
        self.assertIn('proxy_set_header X-API-Key "";', template)
        self.assertIn('return 401;', template)
        self.assertIn('limit_req zone=rpc_backend', template)

class CacheTests(unittest.TestCase):
    def test_corrupt_or_future_cache_cannot_keep_credentials_active(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache'
            for text in ['broken json', '[]', '{}', '{"fetched_at":"invalid","keys":[]}', '{"fetched_at":999,"keys":[]}']:
                with self.subTest(text=text):
                    path.write_text(text)
                    self.assertEqual(keys.cached_keys(path, 100), [])


class ProxyDeploymentTests(unittest.TestCase):
    def test_updates_proxy_only_and_preserves_node_and_disks(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts/pool'))
        from fleet import Fleet
        f = object.__new__(Fleet)
        f.apply = True
        f.c = {'network': 'adiri', 'resource_group': 'rg', 'public_rpc': {
            'hostname': 'rpc.example.com', 'frontend': '192.0.2.1', 'api_keys': {'vault': 'key-vault'}}}
        f.vm = Mock(return_value={'identity': {'principalId': 'node-id'}, 'networkProfile': {'networkInterfaces': [{'id': '/nic'}]}})
        f.az = Mock(side_effect=[{'id': '/vault', 'tags': {'purpose': 'oprail-rpc-api-keys', 'network': 'adiri'}}, [], {},
                                 {'ipConfigurations': [{'name': 'primary', 'privateIPAddress': '10.0.0.2'}]}])
        f.ssh = Mock()
        f.configure_api_keys({'name': 'node'})
        script = f.ssh.call_args.args[1]
        self.assertIn('nginx.conf.before-api-keys', script)
        self.assertIn('nginx -t', script)
        self.assertIn('systemctl reload telcoin-rpc-tls', script)
        self.assertNotIn('systemctl restart telcoin-observer', script)
        self.assertNotIn('docker stop', script)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertFalse(any('delete' in call.args for call in f.az.call_args_list))
        scoped = next(call.args for call in f.az.call_args_list if call.args[:3] == ('role', 'assignment', 'list'))
        self.assertIn('--scope', scoped)
        self.assertNotIn('--all', scoped)  # Azure CLI rejects --all combined with --scope.
        self.assertEqual(scoped[-2:], ('--fill-principal-name', 'false'))

class VaultFetchTests(unittest.TestCase):
    def test_paging_skips_disabled_expired_and_unrelated_secrets(self):
        import io
        base = 'https://test-vault.vault.azure.net/'
        first = base + 'secrets?api-version=7.4'
        next_page = base + 'secrets?api-version=7.4&skip=1'
        identifier = base + 'secrets/rpc-key-' + 'a'*32
        replies = {
            first: {'value': [{'id': base+'secrets/rpc-key-'+'c'*32, 'attributes': {'enabled': False}},
                              {'id': base+'secrets/rpc-key-'+'d'*32, 'attributes': {'exp': 10}},
                              {'id': base+'secrets/unrelated', 'attributes': {}}], 'nextLink': next_page},
            next_page: {'value': [{'id': identifier, 'attributes': {'enabled': True, 'exp': 500}}]},
            identifier+'?api-version=7.4': {'value': json.dumps({'key': 'b'*64, 'tier': 'partner'}),
                                           'attributes': {'enabled': True, 'exp': 500}}}
        seen = []
        def open(request, **kwargs):
            seen.append(request.full_url)
            data = {'access_token':'test'} if request.full_url.startswith('http://169.254.') else replies[request.full_url]
            return io.StringIO(json.dumps(data))
        with patch.object(keys.urllib.request, 'urlopen', side_effect=open), patch.object(keys.time, 'time', return_value=100):
            result = keys.fetch({'vault': 'test-vault'})
        self.assertEqual(result, [key(expires=500, enabled=True)])
        self.assertEqual(len(seen), 4)

    def test_unexpected_next_link_never_receives_a_bearer_token(self):
        import io
        seen = []
        def open(request, **kwargs):
            seen.append(request.full_url)
            return io.StringIO(json.dumps({'access_token':'test'} if len(seen)==1 else
                                         {'value': [], 'nextLink':'https://other.example/secrets'}))
        with patch.object(keys.urllib.request, 'urlopen', side_effect=open), self.assertRaises(ValueError):
            keys.fetch({'vault': 'test-vault'})
        self.assertEqual(len(seen), 2)
