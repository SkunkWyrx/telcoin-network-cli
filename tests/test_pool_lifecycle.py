import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

POOL = Path(__file__).resolve().parents[1] / 'scripts/pool'
sys.path.insert(0, str(POOL))
from health import effective, evaluate
from policy import complete_pairs, pinned_image, retention, upgrade_order
from fleet import Fleet


class HealthTests(unittest.TestCase):
    def query(self, url, method, params=None):
        if method == 'eth_chainId':
            return '0x7e1'
        if method == 'eth_blockNumber':
            return hex(100 if url == 'local' else 102)
        return {'number': params[0], 'hash': '0xsame'}

    def test_synced_hash_and_chain(self):
        self.assertTrue(evaluate('local', 'reference', '0x7e1', query=self.query)['ready'])

    def test_wrong_chain_is_not_ready(self):
        self.assertFalse(evaluate('local', 'reference', '0x1', query=self.query)['ready'])

    def test_lagging_or_ahead_is_not_ready(self):
        self.assertFalse(evaluate('local', 'reference', '0x7e1', max_lag=1, query=self.query)['ready'])
        self.assertFalse(evaluate('reference', 'local', '0x7e1', max_lag=1, query=self.query)['ready'])

    def test_same_height_different_hash_is_not_ready(self):
        def query(url, method, params=None):
            if method == 'eth_getBlockByNumber':
                return {'number': params[0], 'hash': url}
            return self.query(url, method, params)
        self.assertFalse(evaluate('local', 'reference', '0x7e1', query=query)['ready'])

    def test_failed_reference_does_not_grant_readiness(self):
        def query(*_):
            raise TimeoutError()
        with self.assertRaises(TimeoutError):
            evaluate('local', 'reference', '0x7e1', query=query)

    def test_stale_cache_and_maintenance_fail_closed(self):
        good = {'ready': True}
        self.assertFalse(effective(good, None, 50, 30)['ready'])
        self.assertFalse(effective(good, 1, 50, 30)['ready'])
        self.assertFalse(effective(good, 49, 50, 30, drained=True)['ready'])
        self.assertTrue(effective(good, 49, 50, 30)['ready'])


class PublicRpcTests(unittest.TestCase):
    def test_unsynced_node_never_gets_public_membership_or_secret_access(self):
        fleet = object.__new__(Fleet)
        fleet.apply = True
        fleet.c = {'public_rpc': {'issuer': 'observer-1', 'hostname': 'rpc.example.com'}}
        fleet.vm = Mock()
        fleet.synced = Mock(return_value=False)
        fleet.az = Mock()
        fleet.ssh = Mock()
        with self.assertRaisesRegex(ValueError, 'must be synced'):
            fleet.install_public_rpc({'name': 'observer-2'})
        fleet.az.assert_not_called()
        fleet.ssh.assert_not_called()

    def test_existing_issuer_is_never_reconfigured_as_follower(self):
        fleet = object.__new__(Fleet)
        fleet.apply = True
        fleet.c = {'public_rpc': {'issuer': 'observer-1'}}
        fleet.vm = Mock()
        with self.assertRaisesRegex(ValueError, 'issuer'):
            fleet.install_public_rpc({'name': 'observer-1'})
        fleet.vm.assert_not_called()


class SnapshotTests(unittest.TestCase):
    def snapshots(self, capture='capture-1', when='2026-10-24T04:00:00+00:00'):
        return [{'id': '/' + capture + '/' + kind, 'completionPercent': 100,
                 'provisioningState': 'Succeeded', 'tags': {
                     'managed-by': 'oprail-observer-fleet', 'network': 'adiri', 'pool': 'adiri',
                     'capture': capture, 'disk': kind, 'capture-status': 'complete', 'captured-at': when}}
                for kind in ('execution', 'consensus')]

    def test_selects_only_complete_matching_pairs(self):
        valid = self.snapshots()
        incomplete = self.snapshots('partial')
        incomplete[0]['completionPercent'] = 99
        other = self.snapshots('mainnet')
        for s in other:
            s['tags']['network'] = 'mainnet'
        self.assertEqual([p['capture'] for p in complete_pairs(valid + incomplete + other, 'adiri', 'adiri')], ['capture-1'])

    def test_missing_duplicate_and_pending_pairs_are_not_usable(self):
        valid = self.snapshots()
        self.assertEqual(complete_pairs(valid[:1], 'adiri', 'adiri'), [])
        self.assertEqual(complete_pairs(valid + [copy.deepcopy(valid[0])], 'adiri', 'adiri'), [])
        valid[1]['tags']['capture-status'] = 'pending'
        self.assertEqual(complete_pairs(valid, 'adiri', 'adiri'), [])

    def test_retention_keeps_today_and_oldest_per_day_for_seven_days(self):
        now = datetime(2026, 10, 24, 23, tzinfo=timezone.utc)
        snapshots = []
        for days in range(9):
            for hour in (0, 4, 8, 12, 16, 20):
                when = now.replace(hour=hour) - timedelta(days=days)
                snapshots.extend(self.snapshots(f'{days}-{hour}', when.isoformat()))
        pairs = complete_pairs(snapshots, 'adiri', 'adiri')
        keep, delete = retention(pairs, now, 7)
        self.assertEqual(len(keep), 12)  # six today plus the first of each previous six days
        self.assertEqual(len(delete), 42)
        self.assertIn('0-20', {p['capture'] for p in keep})
        self.assertIn('6-0', {p['capture'] for p in keep})
        self.assertNotIn('7-0', {p['capture'] for p in keep})

    def test_latest_backup_is_not_deleted_if_all_captures_are_old(self):
        pairs = complete_pairs(self.snapshots(), 'adiri', 'adiri')
        keep, delete = retention(pairs, datetime(2026, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(len(keep), 1)
        self.assertEqual(delete, [])

    def test_cross_disk_timestamp_mismatch_is_rejected(self):
        pair = self.snapshots()
        pair[1]['tags']['captured-at'] = '2026-10-24T08:00:00Z'
        self.assertEqual(complete_pairs(pair, 'adiri', 'adiri'), [])


class UpgradeTests(unittest.TestCase):
    image = 'registry.azurecr.io/observer/adiri@sha256:' + 'a' * 64

    def test_mutable_images_rejected(self):
        with self.assertRaises(ValueError):
            pinned_image('registry.azurecr.io/observer/adiri:latest')
        self.assertEqual(pinned_image(self.image), self.image)

    def test_last_node_first_and_no_duplicate_ordinals(self):
        self.assertEqual([n['name'] for n in upgrade_order([{'name': 'a', 'ordinal': 1}, {'name': 'b', 'ordinal': 2}])], ['b', 'a'])
        with self.assertRaises(ValueError):
            upgrade_order([{'name': 'a', 'ordinal': 1}, {'name': 'b', 'ordinal': 1}])

    def fleet(self, directory):
        config = {'network': 'adiri', 'pool': 'test', 'chain_id': '0x7e1',
                  'registry': 'registry', 'repository': 'observer/adiri',
                  'nodes': [{'name': 'a', 'ordinal': 1}, {'name': 'b', 'ordinal': 2}],
                  'drain_seconds': 0}
        with patch('fleet.ROOT', Path(directory)):
            fleet = Fleet(config, apply=True)
        fleet.vm = lambda _: {}
        fleet.synced = lambda _: True
        return fleet

    @patch('fleet.time.sleep')
    def test_failed_first_upgrade_never_starts_next_node(self, _sleep):
        with tempfile.TemporaryDirectory() as directory:
            fleet = self.fleet(directory)
            calls = []
            fleet.ssh = lambda n, s: calls.append((n['name'], s)) or ''
            fleet.wait_synced = lambda _: (_ for _ in ()).throw(RuntimeError('not synced'))
            with self.assertRaisesRegex(RuntimeError, 'not synced'):
                fleet.upgrade(self.image)
            self.assertEqual({name for name, _ in calls}, {'b'})
            self.assertFalse(any('rm /run/telcoin/draining' in s for _, s in calls))

    @patch('fleet.time.sleep')
    def test_next_upgrade_begins_only_after_sync_gate(self, _sleep):
        with tempfile.TemporaryDirectory() as directory:
            fleet = self.fleet(directory)
            events = []
            fleet.ssh = lambda n, s: events.append((n['name'], s)) or self.image + '\n'
            fleet.wait_synced = lambda n: events.append((n['name'], 'SYNC-GATE'))
            fleet.upgrade(self.image)
            first_a = next(i for i, (n, _) in enumerate(events) if n == 'a')
            gate_b = next(i for i, event in enumerate(events) if event == ('b', 'SYNC-GATE'))
            self.assertLess(gate_b, first_a)

    def test_single_node_upgrade_refused_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            fleet = self.fleet(directory)
            fleet.c['nodes'] = fleet.c['nodes'][:1]
            with self.assertRaisesRegex(ValueError, 'two nodes'):
                fleet.upgrade(self.image)

    def test_runtime_inventory_survives_unchanged_checkout_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            fleet = self.fleet(directory)
            original_config = copy.deepcopy(fleet.c)
            fleet.c['nodes'].append({'name': 'c', 'ordinal': 3})
            fleet.save_profile()
            with patch('fleet.ROOT', Path(directory)):
                reloaded = Fleet(original_config)
            self.assertEqual([n['name'] for n in reloaded.c['nodes']], ['a', 'b', 'c'])


if __name__ == '__main__':
    unittest.main()
