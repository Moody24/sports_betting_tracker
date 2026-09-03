"""The phase-2 class manifest is the single source of truth for the framework layer."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / 'docs/superpowers/specs/phase2-class-manifest.json'


class TestClassAuditManifest(unittest.TestCase):

    def test_manifest_exists_and_matches_live_scan(self):
        out = subprocess.run(
            [sys.executable, str(REPO / 'tools/ui_class_audit.py')],
            capture_output=True, text=True, check=True)
        live = json.loads(out.stdout)
        stored = json.loads(MANIFEST.read_text())
        self.assertEqual(live, stored,
                         'manifest stale — rerun tools/ui_class_audit.py > manifest')

    def test_manifest_shape(self):
        stored = json.loads(MANIFEST.read_text())
        for key in ('grid', 'components', 'utilities', 'icons', 'unmatched'):
            self.assertIn(key, stored)
            self.assertIsInstance(stored[key], list)
        self.assertTrue(stored['components'])
        self.assertTrue(stored['utilities'])
        self.assertTrue(stored['icons'])


if __name__ == '__main__':
    unittest.main()
