"""Guards that keep the shared architecture contract discoverable and current."""

import re
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "architecture" / "system-contract.md"
CONTRACT_REF = "docs/architecture/system-contract.md"


class SystemContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = CONTRACT_PATH.read_text(encoding="utf-8")

    def test_contract_is_discoverable_by_both_agents(self):
        for relative_path in ("AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md"):
            with self.subTest(path=relative_path):
                content = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(CONTRACT_REF, content)

    def test_persistent_model_inventory_is_registered(self):
        models_source = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
        model_names = re.findall(
            r"^class\s+(\w+)\([^\n]*\bdb\.Model\b[^\n]*\):",
            models_source,
            flags=re.MULTILINE,
        )

        self.assertEqual(16, len(model_names))
        self.assertIn("16 SQLAlchemy models", self.contract)
        for model_name in model_names:
            with self.subTest(model=model_name):
                self.assertIn(f"`{model_name}`", self.contract)

    def test_scheduler_inventory_is_registered(self):
        scheduler_source = (
            ROOT / "app" / "services" / "scheduler.py"
        ).read_text(encoding="utf-8")
        job_count = scheduler_source.count("scheduler.add_job(")

        self.assertEqual(22, job_count)
        self.assertIn("22 registered\nAPScheduler jobs", self.contract)

    def test_registered_sports_are_declared(self):
        service_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "app" / "services").glob("*.py")
        )
        registered_sports = set(
            re.findall(r"SPORT_REGISTRY\[[\"']([^\"']+)[\"']\]", service_sources)
        )

        self.assertEqual({"nba"}, registered_sports)
        self.assertIn("NBA as the only registered `SportService`", self.contract)

    def test_routes_do_not_import_other_route_modules(self):
        routes_dir = ROOT / "app" / "routes"
        violations = []
        for path in routes_dir.glob("*.py"):
            if path.name == "bet.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (
                    node.module or ""
                ).startswith("app.routes"):
                    violations.append(f"{path.name}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("app.routes"):
                            violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], violations)

    def test_services_do_not_import_entrypoint_modules(self):
        violations = []
        for path in (ROOT / "app" / "services").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom):
                    modules.append(node.module or "")
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                if any(
                    module.startswith(("app.routes", "app.cli"))
                    for module in modules
                ):
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
