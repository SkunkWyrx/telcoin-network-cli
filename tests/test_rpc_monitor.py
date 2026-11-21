import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/pool'))
import rpc_monitor


class RpcMonitorTests(unittest.TestCase):
    def test_lag_threshold_and_public_outage(self):
        with patch.object(rpc_monitor, 'head', side_effect=[100, 104]):
            self.assertFalse(rpc_monitor.sample()['LagIncident'])
        with patch.object(rpc_monitor, 'head', side_effect=[100, 105]):
            self.assertTrue(rpc_monitor.sample()['LagIncident'])
        with patch.object(rpc_monitor, 'head', side_effect=TimeoutError()):
            self.assertEqual(rpc_monitor.sample(), {'OutageIncident': True})

    def test_reference_failure_does_not_publish_false_recovery(self):
        with patch.object(rpc_monitor, 'head', side_effect=[100, TimeoutError()]):
            self.assertEqual(rpc_monitor.sample(), {'OutageIncident': False})

    def test_three_samples_trigger_and_healthy_sample_clears_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            with patch.object(rpc_monitor, 'STATE', state), patch.object(
                    rpc_monitor, 'sample', return_value={'LagIncident': True}), patch.object(
                    rpc_monitor, 'publish') as publish:
                for expected in (0, 0, 1):
                    rpc_monitor.main()
                    publish.assert_called_with({'LagIncident': expected})
            with patch.object(rpc_monitor, 'STATE', state), patch.object(
                    rpc_monitor, 'sample', return_value={'LagIncident': False}), patch.object(
                    rpc_monitor, 'publish') as publish:
                rpc_monitor.main()
                publish.assert_called_once_with({'LagIncident': 0})
                self.assertEqual(json.loads(state.read_text())['streaks']['LagIncident'], 0)


if __name__ == '__main__':
    unittest.main()
