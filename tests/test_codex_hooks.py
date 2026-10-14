"""Exercise the configured edit guards without modifying protected files.

These are command-level regression tests, not evidence that a client dispatched
PreToolUse. Tool-level dispatch must be verified separately with disposable files.
"""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS_PATH = ROOT / ".codex" / "hooks.json"


def patch(*operations):
    return "*** Begin Patch\n" + "\n".join(operations) + "\n*** End Patch\n"


def add(path):
    return f"*** Add File: {path}\n+dummy text"


def update(path, destination=None):
    move = f"\n*** Move to: {destination}" if destination is not None else ""
    return f"*** Update File: {path}{move}\n@@\n-dummy before\n+dummy after"


def delete(path):
    return f"*** Delete File: {path}"


class CodexHooksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configuration = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        cls.entries = cls.configuration["hooks"]["PreToolUse"]

    def commands_for(self, tool_name):
        commands = [
            hook["command"]
            for entry in self.entries
            if re.search(entry.get("matcher", ".*"), tool_name)
            for hook in entry["hooks"]
            if hook["type"] == "command"
        ]
        self.assertTrue(commands, f"No PreToolUse command matches {tool_name}")
        return commands

    def run_hook(self, payload, *, expected, matched_tool="apply_patch", cwd=ROOT):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        results = [
            subprocess.run(
                ["/bin/sh", "-c", command],
                input=raw,
                text=True,
                capture_output=True,
                cwd=cwd,
                timeout=10,
                check=False,
            )
            for command in self.commands_for(matched_tool)
        ]
        # A deny from any configured guard stops the operation. Unexpected
        # execution failures are errors, even when another guard denies.
        self.assertTrue(
            all(result.returncode in (0, 2) for result in results),
            [(result.returncode, result.stdout, result.stderr) for result in results],
        )
        actual = 2 if any(result.returncode == 2 for result in results) else 0
        self.assertEqual(
            expected,
            actual,
            [(result.returncode, result.stdout, result.stderr) for result in results],
        )
        if expected == 2:
            self.assertTrue(
                any(result.stderr.strip() for result in results if result.returncode == 2),
                "Denials must explain the protected or unsupported input",
            )
        return results

    def native(self, command, **extra):
        return {
            "tool_name": "apply_patch",
            "tool_input": {"command": command},
            **extra,
        }

    def legacy(self, path, tool_name="Edit", **extra_input):
        return {
            "tool_name": tool_name,
            "tool_input": {"file_path": path, **extra_input},
        }

    def test_matcher_covers_supported_editing_tools(self):
        for tool_name in ("Edit", "Write", "apply_patch", "functions.apply_patch"):
            with self.subTest(tool=tool_name):
                self.commands_for(tool_name)

    def test_legacy_allowed_files_and_edit_fields(self):
        for tool_name in ("Edit", "Write"):
            for path in (
                "notes.txt",
                ".env.example",
                "docs/.env.example",
                "app/ml_models/model.txt",
                "other/ml_models/model.json",
                "app/other/model.json",
            ):
                with self.subTest(tool=tool_name, path=path):
                    self.run_hook(
                        self.legacy(
                            path,
                            tool_name,
                            old_string="dummy before",
                            new_string="dummy after",
                            content="dummy text",
                        ),
                        matched_tool=tool_name,
                        expected=0,
                    )

    def test_legacy_protected_paths(self):
        for tool_name in ("Edit", "Write"):
            for path in (
                ".env",
                "nested/.env",
                "app/ml_models/model.json",
                "app/ml_models/nested/model.json",
                "fixture/app/ml_models/model.json",
            ):
                with self.subTest(tool=tool_name, path=path):
                    self.run_hook(
                        self.legacy(path, tool_name),
                        matched_tool=tool_name,
                        expected=2,
                    )

    def test_native_allowed_add_update_delete(self):
        for operation in (add, update, delete):
            with self.subTest(operation=operation.__name__):
                self.run_hook(self.native(patch(operation("notes.txt"))), expected=0)

    def test_native_protected_add_update_delete(self):
        for operation in (add, update, delete):
            for path in (".env", "nested/.env", "app/ml_models/model.json"):
                with self.subTest(operation=operation.__name__, path=path):
                    self.run_hook(self.native(patch(operation(path))), expected=2)

    def test_namespaced_native_tool_is_guarded(self):
        for path, expected in (("notes.txt", 0), (".env", 2)):
            with self.subTest(path=path):
                payload = self.native(patch(add(path)))
                payload["tool_name"] = "functions.apply_patch"
                self.run_hook(
                    payload, matched_tool="functions.apply_patch", expected=expected
                )

    def test_native_valid_multiline_input_is_read_without_shell_reinterpretation(self):
        command = patch(
            "*** Update File: notes with spaces.txt\n"
            "@@ section heading\n"
            " dummy context with \\n and quotes \"\n"
            "-dummy before\n"
            "+dummy after\n"
            "@@ another heading\n"
            "-dummy text\n"
            "+dummy text with a literal backslash \\\n"
            "*** End of File"
        )
        self.run_hook(self.native(command), expected=0)
        self.run_hook(json.dumps(self.native(command), indent=2), expected=0)

    def test_header_like_dummy_content_is_not_an_affected_path(self):
        self.run_hook(
            self.native(
                patch(
                    "*** Add File: notes.txt\n+*** Add File: .env\n"
                    "+*** Move to: app/ml_models/model.json"
                )
            ),
            expected=0,
        )

    def test_native_empty_add_is_valid(self):
        self.run_hook(self.native(patch("*** Add File: empty.txt")), expected=0)

    def test_native_first_update_header_is_optional_and_blank_context_is_valid(self):
        for body in (
            "-dummy before\n+dummy after",
            "@@\n\n-dummy before\n+dummy after\n\n*** End of File",
            "\n-dummy before\n+dummy after\n@@ next section\n dummy context",
        ):
            with self.subTest(body=body):
                command = patch(f"*** Update File: notes.txt\n{body}")
                self.run_hook(self.native(command), expected=0)
                self.run_hook(
                    self.native(patch(f"*** Update File: .env\n{body}")), expected=2
                )

    def test_mixed_case_aliases_are_protected(self):
        for path in (
            ".ENV", "nested/.Env", "APP/ML_MODELS/model.JSON",
            "app/Ml_Models/nested/model.Json",
        ):
            with self.subTest(path=path):
                self.run_hook(self.legacy(path), matched_tool="Edit", expected=2)
                for operation in (add, update, delete):
                    self.run_hook(self.native(patch(operation(path))), expected=2)
                self.run_hook(self.native(patch(update("notes.txt", path))), expected=2)

    def test_native_multiple_allowed_files(self):
        self.run_hook(
            self.native(patch(add("one.txt"), update("two.txt"), delete("three.txt"))),
            expected=0,
        )

    def test_native_inspects_every_file_in_a_patch(self):
        for operation in (add, update, delete):
            for protected in (".env", "app/ml_models/nested/model.json"):
                for position in range(3):
                    with self.subTest(
                        operation=operation.__name__, path=protected, position=position
                    ):
                        operations = [add("one.txt"), update("two.txt")]
                        operations.insert(position, operation(protected))
                        self.run_hook(self.native(patch(*operations)), expected=2)

    def test_native_allowed_rename(self):
        self.run_hook(
            self.native(patch(update("old notes.txt", "new notes.txt"))), expected=0
        )

    def test_native_rename_checks_source_and_destination(self):
        for protected in (".env", "app/ml_models/model.json"):
            for source, destination in (
                ("notes.txt", protected),
                (protected, "notes.txt"),
                (protected, "nested/" + protected),
            ):
                with self.subTest(source=source, destination=destination):
                    self.run_hook(
                        self.native(patch(update(source, destination))), expected=2
                    )

    def test_native_rename_destination_checked_in_later_file(self):
        self.run_hook(
            self.native(patch(add("one.txt"), update("two.txt", "nested/.env"))),
            expected=2,
        )

    def test_all_paths_present_in_legacy_and_native_payload_are_checked(self):
        for legacy_path, patch_path, expected in (
            ("notes.txt", "another.txt", 0),
            (".env", "notes.txt", 2),
            ("notes.txt", "app/ml_models/model.json", 2),
        ):
            with self.subTest(legacy_path=legacy_path, patch_path=patch_path):
                payload = self.native(patch(add(patch_path)))
                payload["tool_input"]["file_path"] = legacy_path
                self.run_hook(payload, expected=expected)

    def test_protected_paths_are_normalized(self):
        for path in (
            "./.env",
            "notes/../.env",
            "app/other/../ml_models/model.json",
            "app/ml_models/sub/../model.json",
            str(ROOT / ".env"),
            str(ROOT / "app" / "ml_models" / "model.json"),
        ):
            with self.subTest(path=path):
                self.run_hook(self.legacy(path), matched_tool="Edit", expected=2)
                self.run_hook(self.native(patch(add(path))), expected=2)

    def test_normalization_does_not_hide_original_protected_segments(self):
        # Both the spelling and resolved path matter: symlink/path traversal
        # must not turn a protected lexical path into an unexamined operation.
        for path in ("app/ml_models/../model.json", "app/ml_models/sub/../../model.json"):
            with self.subTest(path=path):
                self.run_hook(self.native(patch(add(path))), expected=2)

    def test_symlink_paths_are_checked_using_disposable_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="edge-hook-regression-") as directory:
            fixture = Path(directory)
            model_dir = fixture / "app" / "ml_models"
            model_dir.mkdir(parents=True)
            safe_dir = fixture / "safe"
            safe_dir.mkdir()
            try:
                (fixture / "env-link.txt").symlink_to(fixture / ".env")
                (fixture / "models-link").symlink_to(model_dir, target_is_directory=True)
                (fixture / "safe-link").symlink_to(safe_dir, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"Symlinks unavailable: {error}")
            for path, expected in (
                (fixture / "env-link.txt", 2),
                (fixture / "models-link" / "model.json", 2),
                (fixture / "safe-link" / "notes.txt", 0),
            ):
                with self.subTest(path=str(path)):
                    self.run_hook(
                        self.legacy(str(path)), matched_tool="Edit", expected=expected
                    )
                    self.run_hook(self.native(patch(add(str(path)))), expected=expected)

    def test_process_subdirectory_still_finds_project_guard(self):
        self.run_hook(
            self.native(patch(add("../.env"))), cwd=ROOT / "tests", expected=2
        )
        self.run_hook(
            self.native(patch(add("notes.txt"))), cwd=ROOT / "tests", expected=0
        )

    def test_payload_cwd_resolves_relative_paths(self):
        with tempfile.TemporaryDirectory(prefix="edge-hook-regression-") as directory:
            fixture = Path(directory)
            model_dir = fixture / "app" / "ml_models"
            model_dir.mkdir(parents=True)
            self.run_hook(
                self.native(patch(add("model.json")), cwd=str(model_dir)), expected=2
            )
            self.run_hook(
                self.native(patch(add("notes.txt")), cwd=str(fixture)), expected=0
            )

    def test_malformed_json_and_multiline_json_fail_closed(self):
        for raw in (
            "",
            "{",
            '{"tool_input": {"file_path": "notes.txt"}',
            '{"tool_name":"apply_patch","tool_input":{"command":"*** Begin Patch\n'
            '*** Add File: notes.txt\n+dummy text\n*** End Patch"}}',
            json.dumps(self.native(patch(add("notes.txt")))) + " trailing garbage",
            "null",
            "[]",
            '"dummy text"',
            "true",
            "NaN",
        ):
            with self.subTest(raw=raw):
                self.run_hook(raw, expected=2)

    def test_duplicate_json_keys_fail_closed(self):
        for raw in (
            '{"tool_name":"Edit","tool_name":"Write",'
            '"tool_input":{"file_path":"notes.txt"}}',
            '{"tool_name":"Edit","tool_input":{"file_path":".env",'
            '"file_path":"notes.txt"}}',
            '{"tool_name":"Edit","tool_input":{"file_path":"notes.txt"},'
            '"tool_input":{"file_path":".env"}}',
        ):
            with self.subTest(raw=raw):
                self.run_hook(raw, expected=2)

    def test_missing_or_unsupported_payloads_fail_closed(self):
        for payload in (
            {},
            {"tool_name": "Edit"},
            {"tool_input": {"file_path": "notes.txt"}},
            {"tool_name": "Read", "tool_input": {"file_path": "notes.txt"}},
            {"tool_name": "UnknownEdit", "tool_input": {"file_path": "notes.txt"}},
            {"tool_name": 1, "tool_input": {"file_path": "notes.txt"}},
            {"tool_name": "Edit", "tool_input": None},
            {"tool_name": "Edit", "tool_input": []},
            {"tool_name": "Edit", "tool_input": "notes.txt"},
            {"tool_name": "Edit", "tool_input": {}},
            {"tool_name": "Edit", "tool_input": {"path": "notes.txt"}},
            {"tool_name": "apply_patch", "tool_input": {"patch": patch(add("notes.txt"))}},
        ):
            with self.subTest(payload=payload):
                self.run_hook(payload, expected=2)

    def test_invalid_path_or_command_types_fail_closed(self):
        for value in (None, False, 1, [], {}, "", "   ", "dummy\x00text"):
            with self.subTest(value=value):
                self.run_hook(self.legacy(value), matched_tool="Edit", expected=2)
                self.run_hook(self.native(value), expected=2)

    def test_invalid_optional_cwd_fails_closed(self):
        for value in (None, False, 1, [], {}, "", "relative/path", "dummy\x00text"):
            with self.subTest(cwd=value):
                self.run_hook(self.native(patch(add("notes.txt")), cwd=value), expected=2)

    def test_native_patch_requires_complete_valid_structure(self):
        for command in (
            "dummy text",
            "*** Begin Patch\n*** End Patch",
            "*** Add File: notes.txt\n+dummy text\n*** End Patch",
            "*** Begin Patch\n*** Add File: notes.txt\n+dummy text",
            patch("*** Unsupported File: notes.txt\n+dummy text"),
            patch("*** Add File: \n+dummy text"),
            patch("*** Add File: notes.txt\ndummy text"),
            patch("*** Update File: notes.txt\n@@"),
            patch("*** Update File: notes.txt\n@@\ndummy unprefixed text"),
            patch("*** Delete File: notes.txt\n+dummy text"),
            patch("*** Add File: notes.txt\n*** Move to: other.txt\n+dummy text"),
            patch("*** Update File: notes.txt\n*** Move to: \n@@\n+dummy text"),
            patch(
                "*** Update File: notes.txt\n*** Move to: one.txt\n"
                "*** Move to: two.txt\n@@\n+dummy text"
            ),
            patch(add("notes.txt")) + "dummy trailing text",
        ):
            with self.subTest(command=command):
                self.run_hook(self.native(command), expected=2)

    def test_native_valid_prefix_cannot_hide_malformed_later_operations(self):
        for suffix in (
            "*** Unsupported File: other.txt\n+dummy text",
            "*** Update File: other.txt\n@@\ndummy unprefixed text",
            "*** Add File: other.txt\ndummy unprefixed text",
        ):
            with self.subTest(suffix=suffix):
                self.run_hook(self.native(patch(add("notes.txt"), suffix)), expected=2)

    def test_invalid_secondary_payload_cannot_be_ignored(self):
        for tool_input in (
            {"file_path": "notes.txt", "command": None},
            {"file_path": "notes.txt", "command": "unsupported command"},
            {"file_path": None, "command": patch(add("notes.txt"))},
        ):
            with self.subTest(tool_input=tool_input):
                self.run_hook(
                    {"tool_name": "apply_patch", "tool_input": tool_input}, expected=2
                )

    def test_tools_require_their_primary_payload_form(self):
        for tool_name, tool_input in (
            ("apply_patch", {"file_path": "notes.txt"}),
            ("functions.apply_patch", {"file_path": "notes.txt"}),
            ("Edit", {"command": patch(add("notes.txt"))}),
            ("Write", {"command": patch(add("notes.txt"))}),
        ):
            with self.subTest(tool=tool_name):
                self.run_hook(
                    {"tool_name": tool_name, "tool_input": tool_input},
                    matched_tool=tool_name,
                    expected=2,
                )


if __name__ == "__main__":
    unittest.main()
