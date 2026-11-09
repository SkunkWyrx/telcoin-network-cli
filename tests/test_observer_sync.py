import contextlib
import importlib.util
import io
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('sync', Path(__file__).resolve().parents[1] / 'scripts/check-observer-sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def output(self, local, network, previous=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            sync.report(local, network, previous, now=100)
        return output.getvalue()

    def test_behind_and_closing(self):
        result = self.output(100, 120, {'local': 80, 'network': 110, 'time': 90})
        self.assertIn('Blocks behind: 20', result)
        self.assertIn('observer +20 blocks; gap closed +10 blocks', result)

    def test_stalled(self):
        self.assertIn('has not advanced', self.output(100, 120, {'local': 100, 'network': 110, 'time': 90}))

    def test_reference_behind(self):
        result = self.output(120, 100)
        self.assertIn('ahead of reference', result)
        self.assertNotIn('near reference head', result)

    def test_near_head(self):
        self.assertIn('near reference head', self.output(100, 102))
