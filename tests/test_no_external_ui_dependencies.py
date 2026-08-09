"""The production UI must not depend on Bootstrap or external font CDNs."""
import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "app/templates"


class TestNoExternalUiDependencies(unittest.TestCase):
    def test_no_data_bs_attributes_remain(self):
        offenders = []
        for template in TEMPLATES.rglob("*.html"):
            for match in re.finditer(r"data-bs-[a-z-]+", template.read_text()):
                offenders.append(f"{template.relative_to(REPO)}:{match.group(0)}")
        self.assertEqual(offenders, [])

    def test_no_external_asset_urls_remain(self):
        pattern = re.compile(
            r"(?:https?:)?//(?:cdn\.jsdelivr\.net|fonts\.googleapis\.com|"
            r"fonts\.gstatic\.com)",
            re.IGNORECASE,
        )
        offenders = []
        for template in TEMPLATES.rglob("*.html"):
            for match in pattern.finditer(template.read_text()):
                offenders.append(f"{template.relative_to(REPO)}:{match.group(0)}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
