"""Tests for the redacted Git-history secret scanner."""

import unittest

from scripts.check_secret_history import scan_history_patch


class SecretHistoryScannerTests(unittest.TestCase):
    def test_reports_kind_commit_and_path_without_secret_value(self):
        token = b'gh' + b'p_' + b'abcdefghijklmnopqrstuvwxyz0123456789'
        patch = b'''commit abcdef1234567890
diff --git a/example.py b/example.py
+++ b/example.py
@@ -0,0 +1 @@
+token = "''' + token + b'''"
'''
        findings = scan_history_patch(patch)
        self.assertEqual(
            findings,
            [('github_classic_token', 'abcdef123456', 'example.py')],
        )
        self.assertNotIn('ghp_', repr(findings))

    def test_ignores_placeholders_and_normal_revision_ids(self):
        patch = b'''commit abcdef1234567890
diff --git a/.env.example b/.env.example
+++ b/.env.example
@@ -0,0 +1,2 @@
+SECRET_KEY=your-secret-here
+revision = "e9602669917f"
'''
        self.assertEqual(scan_history_patch(patch), [])

    def test_deduplicates_same_kind_in_one_file_and_commit(self):
        marker = b'-----BEGIN ' + b'PRIVATE KEY-----'
        patch = b'''commit abcdef1234567890
diff --git a/example.pem b/example.pem
+++ b/example.pem
@@ -0,0 +1,2 @@
+''' + marker + b'''
+''' + marker + b'''
'''
        self.assertEqual(
            scan_history_patch(patch),
            [('private_key', 'abcdef123456', 'example.pem')],
        )


if __name__ == '__main__':
    unittest.main()
