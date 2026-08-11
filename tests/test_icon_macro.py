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

    def test_icon_is_a_global_not_a_leaked_import(self):
        """`icon()` must resolve without `{% from "_macros.html" import icon %}`.

        Twelve templates call `icon()` and only one imports it; the rest used to
        resolve it because base.html's top-level import leaked into their block
        context. That made any reordering of base.html — for instance extracting
        the document head into its own include — silently degrade every icon in
        the app to an HTML comment. It renders as nothing and passes every other
        test, so it is guarded here explicitly.
        """
        with self.app.test_request_context():
            rendered = render_template_string("{{ icon('graph-up') }}")
        self.assertIn("<svg", rendered)

    def test_bootstrap_icon_debt_in_js_does_not_grow(self):
        """No new blank-rendering `bi-*` icons in JS.

        No Bootstrap Icons CSS or font is loaded anywhere (base.html links only
        theme.css, and CSP pins font-src to 'self'), so every `<i class="bi ...">`
        these files inject renders as an empty element. This is pre-existing debt
        retired with the page that owns each file; the counts below are a ratchet
        so it cannot get worse in the meantime.
        """
        budget = {
            "bet_builder.js": 3,  # dead file, unreferenced by any template
            "betslip.js": 6,  # retires with Bet Builder
            "unified_bet_builder.js": 3,  # retires with Bet Builder
        }
        actual = {}
        for js in sorted((REPO / "app/static/js").glob("*.js")):
            found = len(re.findall(r"\bbi-[a-z0-9-]+", js.read_text()))
            if found:
                actual[js.name] = found
        self.assertEqual(
            actual,
            budget,
            "bi-* icon debt changed. Reduce the budget when you retire a file; "
            "never raise it — these icons render blank.",
        )


if __name__ == "__main__":
    unittest.main()
