"""Contract tests for server-owned display configuration."""

import unittest
from pathlib import Path

from flask import render_template_string

from app.config_display import (
    INDICATOR_TIERS,
    POS_EDGE_APPLICABLE_PROPS,
    PROP_LABELS_LONG,
    PROP_LABELS_SHORT,
    PROP_STAT_KEY,
    get_client_display_config,
    get_template_display_config,
)
from tests.helpers import BaseTestCase


REPO = Path(__file__).resolve().parents[1]


class DisplayConfigContractTests(unittest.TestCase):
    def test_client_contract_is_derived_from_canonical_mappings(self):
        config = get_client_display_config()

        self.assertEqual(PROP_LABELS_LONG, config["market_labels"])
        self.assertEqual(PROP_LABELS_SHORT, config["market_labels_short"])
        self.assertEqual(PROP_STAT_KEY, config["prop_to_stat_col"])
        self.assertEqual(list(POS_EDGE_APPLICABLE_PROPS), config["pos_edge_applicable_props"])
        self.assertEqual(
            {key: value["label"] for key, value in INDICATOR_TIERS.items()},
            config["indicator_labels"],
        )

    def test_template_context_exposes_only_consumed_display_contracts(self):
        self.assertEqual(
            {"PROP_LABELS_SHORT", "INDICATOR_TIERS", "CLIENT_DISPLAY_CONFIG"},
            set(get_template_display_config()),
        )

    def test_obsolete_static_mirror_is_absent(self):
        self.assertFalse((REPO / "app/static/js/display_config.js").exists())
        base = (REPO / "app/templates/base.html").read_text()
        self.assertNotIn("display_config.js", base)


class DisplayMacroContractTests(BaseTestCase):
    def test_prop_label_macro_reads_context_mapping(self):
        with self.app.test_request_context():
            rendered = render_template_string(
                '{% from "_macros.html" import prop_label_short with context %}'
                '{{ prop_label_short("player_points") }}',
                **get_template_display_config(),
            )
        self.assertEqual("PTS", rendered)


if __name__ == "__main__":
    unittest.main()
