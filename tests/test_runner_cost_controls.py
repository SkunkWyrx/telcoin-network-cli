import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/pool'))
import backup_helper
import runner_power
from fleet import Fleet
from policy import complete_pairs

class RunnerCostTests(unittest.TestCase):
    def test_power_controller_refuses_shutdown_before_independent_monitoring(self):
        with patch.object(runner_power,'az') as azure:
            with self.assertRaises(ValueError):runner_power.stop({'tags':{'runner-power-mode':'on-demand'}})
            azure.assert_not_called()
            runner_power.stop({'tags':{'runner-power-mode':'on-demand','rpc-monitor-host':'management-app-verified','runner-keepalive':'true'}})
            azure.assert_not_called()
            runner_power.stop({'tags':{'runner-power-mode':'on-demand','rpc-monitor-host':'management-app-verified'}})
            self.assertEqual(azure.call_args.args,('vm','deallocate','-g','oprail-observer-adiri','-n','adiri-observer-runner'))

    def test_power_controller_cannot_target_an_observer(self):
        with patch.object(runner_power,'az',return_value={'name':'observer','tags':{'purpose':'observer'}}):
            with self.assertRaises(ValueError):runner_power.target()

    def test_recovery_pairs_require_explicit_opt_in(self):
        rows=[]
        for disk in ('execution','consensus'):
            rows.append({'id':disk,'tags':{'managed-by':'oprail-observer-recovery','network':'adiri','node':'old-node',
                         'capture':'recovery-1','disk':disk,'capture-status':'complete','captured-at':'2026-11-01T00:00:00+00:00'},
                         'completionPercent':100,'provisioningState':'Succeeded'})
        self.assertEqual(complete_pairs(rows,'adiri','adiri'),[])
        self.assertEqual(len(complete_pairs(rows,'adiri','adiri',include_recovery=True)),1)
        self.assertEqual(complete_pairs(rows,'mainnet','mainnet',include_recovery=True),[])
        rows[1]['tags']['capture-status']='pending'
        self.assertEqual(complete_pairs(rows,'adiri','adiri',include_recovery=True),[])

    def test_prune_keeps_everything_until_a_current_pair_exists(self):
        fleet=object.__new__(Fleet);fleet.c={'backup_max_age_hours':24};fleet.apply=True;fleet.pairs=Mock(return_value=[]);fleet.az=Mock()
        with self.assertRaises(ValueError):fleet.prune()
        fleet.az.assert_not_called()
        fleet.pairs.return_value=[{'created':datetime.now(timezone.utc)-timedelta(days=3)}]
        with self.assertRaises(ValueError):fleet.prune()
        fleet.az.assert_not_called()

    def test_partial_prune_records_remaining_disk_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            fleet=object.__new__(Fleet);fleet.state=Path(directory);fleet.apply=True
            fleet.c={'network':'adiri','pool':'adiri','resource_group':'rg','prune_recovery_snapshots':True}
            old={'capture':'old','created':datetime.now(timezone.utc)-timedelta(days=20),'execution':'old-e','consensus':'old-c'}
            new={'capture':'new','created':datetime.now(timezone.utc),'execution':'new-e','consensus':'new-c'}
            fleet.pairs=lambda include_recovery=False:[old,new]
            remaining={'old-e','old-c'};deleted=[];fail=True
            def az(*args):
                nonlocal fail
                if args[:2]==('snapshot','list'):
                    return [{'id':name,'tags':{'network':'adiri','pool':'adiri','managed-by':'oprail-observer-fleet'}} for name in remaining]
                name=args[-1]
                if name=='old-c' and fail:raise RuntimeError('fixture deletion failure')
                remaining.remove(name);deleted.append(name)
            fleet.az=az
            with self.assertRaises(RuntimeError):fleet.prune()
            self.assertEqual(json.loads((fleet.state/'prune.json').read_text())['remaining'],['old-c'])
            # A partly deleted pair disappears from Azure's complete-pair inventory.
            fleet.pairs=lambda include_recovery=False:[new];fail=False;fleet.prune()
            self.assertEqual(deleted,['old-e','old-c'])
            self.assertEqual(remaining,set())

    def helper_fleet(self,directory):
        f=Mock();f.apply=True;f.state=Path(directory)
        source={'name':'primary','host':'source','ordinal':1};helper={'name':'adiri-observer-backup-1234abcd','host':'helper','ordinal':2}
        f.c={'network':'adiri','resource_group':'rg','nodes':[source,helper]};f.synced.return_value=True
        env=f.state/'helper.env';env.write_text('fixture')
        (f.state/'scheduled-backup.json').write_text(json.dumps({'name':helper['name'],'source':source,'image':'repo@sha256:'+'a'*64,
                                                                'env_file':str(env),'stage':'provisioning','seed':{}}))
        f.save.side_effect=lambda name,data:(f.state/name).write_text(json.dumps(data))
        f.az.return_value=[]
        f.pairs.side_effect=[[{'capture':'old'}],[{'capture':'new','execution':'e','consensus':'c'}]]
        return f,source,helper

    def test_backup_stops_and_removes_only_helper_after_completed_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            f,source,helper=self.helper_fleet(directory)
            backup_helper.backup(f)
            f.snapshot.assert_called_once_with(helper['name'])
            f.remove.assert_called_once_with(helper['name'],disposable=True)
            f.prune.assert_called_once()
            self.assertEqual(json.loads((f.state/'scheduled-backup.json').read_text())['stage'],'completed')
            self.assertFalse(any(call.args[0] in ('vm','stop','deallocate') for call in f.az.call_args_list))

    def test_failed_capture_never_prunes_and_cleans_exact_disposable_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            f,source,helper=self.helper_fleet(directory);f.snapshot.side_effect=RuntimeError('fixture failed capture')
            with self.assertRaises(RuntimeError):backup_helper.backup(f)
            f.prune.assert_not_called()
            f.remove.assert_called_once_with(helper['name'],disposable=True)
            self.assertEqual(json.loads((f.state/'scheduled-backup.json').read_text())['stage'],'failed')

    def test_readiness_failure_cannot_stop_source_or_delete_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            f,source,helper=self.helper_fleet(directory);f.wait_synced.side_effect=RuntimeError('fixture unsynced')
            with self.assertRaises(RuntimeError):backup_helper.backup(f)
            f.snapshot.assert_not_called();f.prune.assert_not_called()
            f.remove.assert_called_once_with(helper['name'],disposable=True)

    def test_workflow_approval_precedes_wake_and_cleanup_runs_after_failure(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'.github/workflows/observer-management-operations.yml').read_text()
        wake=text.split('  wake:',1)[1].split('  operate:',1)[0]
        self.assertIn('environment: observer-operations-${{ inputs.network }}',wake)
        self.assertIn('needs: wake',text)
        self.assertIn("if: always() && needs.wake.result == 'success'",text)
        role=(root/'infra/management/runner-power.bicep').read_text()
        self.assertNotIn('runCommand',role);self.assertNotIn('virtualMachines/write',role)

    def test_disposable_delete_cannot_be_used_for_a_permanent_node(self):
        f=object.__new__(Fleet);f.apply=True;f.c={'nodes':[{'name':'primary'}]};f.synced=Mock(return_value=True)
        with tempfile.TemporaryDirectory() as d:
            f.state=Path(d)
            with self.assertRaises(ValueError):f.remove('primary',disposable=True)
