import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('acme_dns', Path(__file__).resolve().parents[1] / 'infra/rpc-tls/acme-azure-dns.py')
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


def record(*values, etag='version1'):
    return {'etag': etag, 'properties': {'TTL': 60, 'metadata': {'owner': 'rpc'},
                                       'TXTRecords': [{'value': [v]} for v in values]}}


class ChallengeTests(unittest.TestCase):
    def test_auth_preserves_existing_challenge(self):
        with patch.object(dns, 'request', side_effect=[record('stale'), {}]) as arm:
            dns.update_challenge('url', 'token', 'new')
        write = arm.call_args_list[1]
        self.assertEqual(write.args[3]['properties'], record('stale', 'new')['properties'])
        self.assertEqual(write.kwargs, {'If-Match': 'version1'})

    def test_absent_record_uses_conditional_create(self):
        with patch.object(dns, 'request', side_effect=[HTTPError('url', 404, '', {}, None), {}]) as arm:
            dns.update_challenge('url', 'token', 'new')
        self.assertEqual(arm.call_args_list[1].kwargs, {'If-None-Match': '*'})

    def test_cleanup_preserves_other_values(self):
        with patch.object(dns, 'request', side_effect=[record('stale', 'new'), {}]) as arm:
            dns.cleanup('url', 'token', 'new')
        self.assertEqual(arm.call_args_list[1].args[3]['properties'], record('stale')['properties'])

    def test_cleanup_deletes_only_empty_set(self):
        with patch.object(dns, 'request', side_effect=[record('new'), None]) as arm:
            dns.cleanup('url', 'token', 'new')
        self.assertEqual(arm.call_args_list[1].args, ('url', 'token', 'DELETE'))
        self.assertEqual(arm.call_args_list[1].kwargs, {'If-Match': 'version1'})

    def test_idempotent_operations_do_not_write(self):
        for current, remove in [(record('new'), False), (record('other'), True),
                                (HTTPError('url', 404, '', {}, None), True)]:
            with self.subTest(remove=remove), patch.object(dns, 'request', side_effect=[current]) as arm:
                dns.update_challenge('url', 'token', 'new', remove=remove)
                self.assertEqual(arm.call_count, 1)

    def test_concurrent_update_reloads_before_retry(self):
        with patch.object(dns, 'request', side_effect=[record('stale'), HTTPError('url', 412, '', {}, None),
                                                      record('stale', 'concurrent', etag='version2'), {}]) as arm:
            dns.update_challenge('url', 'token', 'new')
        self.assertEqual(arm.call_args_list[3].args[3]['properties'], record('stale', 'concurrent', 'new')['properties'])
        self.assertEqual(arm.call_args_list[3].kwargs, {'If-Match': 'version2'})

    def test_permission_errors_are_not_hidden(self):
        with patch.object(dns, 'request', side_effect=HTTPError('url', 403, '', {}, None)):
            with self.assertRaises(HTTPError):
                dns.update_challenge('url', 'token', 'new')
