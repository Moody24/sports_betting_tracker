"""Coverage for the self-hosted inline SVG icon system."""
import json
import re
import unittest
from pathlib import Path

from flask import render_template_string

from tests.helpers import BaseTestCase


REPO = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (REPO / "docs/superpowers/specs/phase2-class-manifest.json").read_text()
)


class TestIconMacro(BaseTestCase):
    def _render(self, name):
        with self.app.test_request_context():
            return render_template_string(
                '{% from "_macros.html" import icon %}{{ icon(name) }}',
                name=name,
            )

    def test_known_icon_renders_accessible_inline_svg(self):
        rendered = self._render("graph-up")
        self.assertIn("<svg", rendered)
        self.assertIn('viewBox="0 0 16 16"', rendered)
        self.assertIn('aria-hidden="true"', rendered)
        self.assertIn('focusable="false"', rendered)

    def test_unknown_icon_renders_greppable_comment(self):
        rendered = self._render("definitely-not-an-icon")
        self.assertIn("icon:missing", rendered)
        self.assertNotIn("<svg", rendered)

    def test_every_manifest_icon_has_markup(self):
        for name in MANIFEST["icons"]:
            self.assertIn("<svg", self._render(name), f"missing icon: {name}")

    def test_no_bootstrap_icon_classes_remain(self):
        offenders = []
        for template in (REPO / "app/templates").rglob("*.html"):
            if re.search(r"\bbi-[a-z0-9-]+", template.read_text()):
                offenders.append(str(template.relative_to(REPO)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
