"""The framework layer in theme.css must cover the manifest exactly."""
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
THEME = REPO / 'app/static/css/theme.css'
MANIFEST = json.loads(
    (REPO / 'docs/superpowers/specs/phase2-class-manifest.json').read_text())


class TestFrameworkLayer(unittest.TestCase):

    def _theme_classes(self):
        return set(re.findall(r'\.([a-zA-Z][\w-]*)', THEME.read_text()))

    def test_every_grid_class_defined(self):
        missing = [c for c in MANIFEST['grid'] if c not in self._theme_classes()]
        self.assertEqual(missing, [])

    def test_every_component_class_defined(self):
        missing = [c for c in MANIFEST['components'] if c not in self._theme_classes()]
        self.assertEqual(missing, [])

    def test_every_utility_class_defined(self):
        missing = [c for c in MANIFEST['utilities'] if c not in self._theme_classes()]
        self.assertEqual(missing, [])

    def test_unmatched_classes_are_not_implemented(self):
        """JS hooks / dynamic fragments must never grow framework CSS."""
        theme = self._theme_classes()
        leaked = [c for c in MANIFEST['unmatched']
                  if c in theme and not c.endswith('-')]
        self.assertEqual(leaked, [])


if __name__ == '__main__':
    unittest.main()
