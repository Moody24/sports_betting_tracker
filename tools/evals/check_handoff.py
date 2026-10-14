"""Small, offline DeepEval check for structured Codex handoff evidence.

The inputs below are sanitized summaries, not transcripts. This checks whether
a reported result matches independently recorded facts; it does not judge the
quality of arbitrary prose or call an LLM.
"""

import json
import os
import sys


def _require_private_offline_mode():
    required = {
        "DEEPEVAL_TELEMETRY_OPT_OUT": "1",
        "DEEPEVAL_DISABLE_DOTENV": "1",
        "DEEPEVAL_DISABLE_LEGACY_KEYFILE": "1",
        "CONFIDENT_OPEN_BROWSER": "0",
    }
    for name, expected in required.items():
        if os.environ.get(name) != expected:
            raise RuntimeError(f"Set {name}={expected} before running evals")
    for name in ("CONFIDENT_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if os.environ.get(name):
            raise RuntimeError(f"Remove {name} from the eval process")


_require_private_offline_mode()

from deepeval import assert_test  # noqa: E402
from deepeval.metrics import BaseMetric  # noqa: E402
from deepeval.test_case import LLMTestCase  # noqa: E402


class VerifiedHandoffMetric(BaseMetric):
    """Exact checks of facts, test results, and role-discovery claims."""

    def __init__(self):
        self.threshold = 1.0
        self.async_mode = False
        self.score = None
        self.reason = None
        self.success = None

    def measure(self, test_case, *args, **kwargs):
        evidence = json.loads(test_case.input)
        report = json.loads(test_case.actual_output)
        mismatches = []
        for field in ("facts", "checks", "role_discovery_observed"):
            if report.get(field) != evidence.get(field):
                mismatches.append(field)
        self.score = 0.0 if mismatches else 1.0
        self.reason = (
            "Mismatch in: " + ", ".join(mismatches)
            if mismatches
            else "Structured claims match independently recorded evidence"
        )
        self.success = self.score >= self.threshold
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case, *args, **kwargs)

    def is_successful(self):
        return self.success

    @property
    def __name__(self):
        return "Verified handoff evidence"


def _case(evidence, report):
    return LLMTestCase(
        input=json.dumps(evidence, sort_keys=True),
        actual_output=json.dumps(report, sort_keys=True),
    )


def main():
    _require_private_offline_mode()

    # Manually normalized from the read-only CLI probe of this branch. The
    # route, template and tests were checked independently; the probe ran no
    # tests and exposed no native role-discovery inventory.
    observed = {
        "facts": [
            "zero_games_and_no_edge_is_unavailable",
            "unavailable_count_is_separate_from_avoid",
            "missing_model_values_are_masked_in_table_and_slideout",
        ],
        "checks": {"tests_run": 0},
        "role_discovery_observed": False,
    }
    assert_test(_case(observed, observed), [VerifiedHandoffMetric()], run_async=False)

    # A false role-discovery claim and invented test count must fail. These are
    # deliberate harness controls, not observations of another agent.
    invented = {**observed, "checks": {"tests_run": 42}, "role_discovery_observed": True}
    if sys.argv[1:] == ["--negative-only"]:
        assert_test(_case(observed, invented), [VerifiedHandoffMetric()], run_async=False)
        return
    if sys.argv[1:]:
        raise SystemExit("Usage: check_handoff.py [--negative-only]")
    metric = VerifiedHandoffMetric()
    metric.measure(_case(observed, invented))
    if metric.is_successful():
        raise AssertionError("The negative control incorrectly passed")

    print("DeepEval 4.2.8: observed normalized handoff passed; negative control failed as expected")


if __name__ == "__main__":
    main()
