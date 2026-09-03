"""Executable checks for the inactive-but-retained deployment configuration."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def test_railway_runs_migrations_as_blocking_predeploy_step(self):
        config = (ROOT / 'railway.toml').read_text(encoding='utf-8')
        self.assertIn(
            'preDeployCommand = "python -m flask --app run db upgrade heads"',
            config,
        )
        self.assertIn('preDeployTimeoutSeconds = 300', config)

    def test_web_entrypoint_does_not_run_migrations(self):
        entrypoint = ROOT / 'docker-entrypoint.sh'
        source = entrypoint.read_text(encoding='utf-8')
        self.assertNotIn('upgrade', source)
        self.assertNotIn('migrat', source.lower())
        self.assertIn('exec gunicorn', source)
        subprocess.run(['sh', '-n', str(entrypoint)], check=True)

    def test_example_uses_safe_single_worker_rate_limit_baseline(self):
        example = (ROOT / '.env.example').read_text(encoding='utf-8')
        self.assertIn('WEB_CONCURRENCY=1', example)
        self.assertIn('RATELIMIT_STORAGE_URI=memory://', example)
        self.assertIn('RATELIMIT_ENABLED=true', example)
        self.assertIn('AUTO_DB_UPGRADE=false', example)


if __name__ == '__main__':
    unittest.main()
