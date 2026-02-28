"""Tests for app initialization helpers."""

import unittest

from app import _is_non_server_invocation


class TestAppInit(unittest.TestCase):
    def test_non_server_invocation_detects_flask(self):
        self.assertTrue(_is_non_server_invocation(['flask', 'run']))

    def test_non_server_invocation_detects_stdin_python(self):
        self.assertTrue(_is_non_server_invocation(['python', '-']))

    def test_non_server_invocation_detects_unittest(self):
        self.assertTrue(_is_non_server_invocation(['python', '-m', 'unittest']))

    def test_non_server_invocation_false_for_gunicorn(self):
        self.assertFalse(_is_non_server_invocation(['gunicorn', 'app:app']))

if __name__ == '__main__':
    unittest.main()
