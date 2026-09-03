"""Tests for app initialization helpers."""

import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import _is_non_server_invocation, _validate_rate_limit_topology


class TestAppInit(unittest.TestCase):
    def test_non_server_invocation_detects_flask(self):
        self.assertTrue(_is_non_server_invocation(['flask', 'run']))

    def test_non_server_invocation_detects_stdin_python(self):
        self.assertTrue(_is_non_server_invocation(['python', '-']))

    def test_non_server_invocation_detects_unittest(self):
        self.assertTrue(_is_non_server_invocation(['python', '-m', 'unittest']))

    def test_non_server_invocation_false_for_gunicorn(self):
        self.assertFalse(_is_non_server_invocation(['gunicorn', 'app:app']))


class TestRateLimitTopology(unittest.TestCase):
    @staticmethod
    def _app(*, production=True, workers=1, storage='memory://', enabled=True):
        app = Flask('app')
        app.config.update(
            TESTING=False,
            DEPLOYMENT_IS_PRODUCTION=production,
            WEB_CONCURRENCY=workers,
            RATELIMIT_STORAGE_URI=storage,
            RATELIMIT_ENABLED=enabled,
        )
        return app

    def test_single_worker_memory_store_is_safe_baseline(self):
        _validate_rate_limit_topology(self._app())

    def test_production_refuses_disabled_rate_limiting(self):
        with self.assertRaisesRegex(RuntimeError, 'must be true'):
            _validate_rate_limit_topology(self._app(enabled=False))

    def test_production_refuses_process_local_multi_worker_limits(self):
        with self.assertRaisesRegex(RuntimeError, 'shared limiter store'):
            _validate_rate_limit_topology(self._app(workers=2))

    def test_production_accepts_shared_multi_worker_limits(self):
        _validate_rate_limit_topology(
            self._app(workers=2, storage='redis://redis.internal:6379/0')
        )

    def test_local_multi_worker_configuration_warns(self):
        with self.assertLogs('app', level='WARNING') as logs:
            _validate_rate_limit_topology(self._app(production=False, workers=2))
        self.assertIn('use one web worker', '\n'.join(logs.output))

    def test_gunicorn_default_is_one_worker(self):
        config_path = Path(__file__).resolve().parents[1] / 'gunicorn.conf.py'
        with patch.dict('os.environ', {}, clear=True):
            namespace = {'__file__': str(config_path)}
            exec(config_path.read_text(encoding='utf-8'), namespace)
        self.assertEqual(namespace['workers'], 1)

if __name__ == '__main__':
    unittest.main()
