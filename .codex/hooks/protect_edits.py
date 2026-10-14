"""Fail-closed PreToolUse guard for legacy edits and native apply_patch calls.

This script only reads hook input and path metadata; it never reads file content
or executes the proposed edit. Exit 2 asks Codex to deny the pending tool call.
"""

import json
import os
from pathlib import Path
import sys


class InvalidEdit(ValueError):
    """The input cannot be checked safely."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEdit("duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(_value):
    raise InvalidEdit("nonstandard JSON constant")


def path_value(value):
    if not isinstance(value, str) or not value.strip():
        raise InvalidEdit("missing or invalid file path")
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InvalidEdit("control character in file path")
    if "\\" in value:
        raise InvalidEdit("unsupported backslash in file path")
    return value


def patch_paths(command):
    """Validate the native patch grammar and collect sources AND destinations.

    Hunk content carries its space/+/- prefix (or a bare blank context line),
    so source text resembling a header cannot hide another file operation.
    """
    if not isinstance(command, str) or "\x00" in command:
        raise InvalidEdit("missing or invalid patch command")
    lines = command.strip().split("\n")
    lines = [line.removesuffix("\r") for line in lines]
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise InvalidEdit("unsupported patch envelope")
    paths = []
    index = 1
    stop = len(lines) - 1
    prefixes = {
        "*** Add File: ": "add",
        "*** Update File: ": "update",
        "*** Delete File: ": "delete",
    }
    while index < stop:
        header = lines[index]
        operation = None
        for prefix, kind in prefixes.items():
            if header.startswith(prefix):
                paths.append(path_value(header[len(prefix):]))
                operation = kind
                break
        if operation is None:
            raise InvalidEdit("unsupported patch operation")
        index += 1
        if operation == "delete":
            continue
        if operation == "add":
            while index < stop and lines[index].startswith("+"):
                index += 1
            continue
        if index < stop and lines[index].startswith("*** Move to: "):
            paths.append(path_value(lines[index][len("*** Move to: "):]))
            index += 1
        hunks = 0
        while index < stop:
            if lines[index] == "@@" or lines[index].startswith("@@ "):
                index += 1
            elif hunks or not (lines[index] == "" or lines[index].startswith((" ", "+", "-"))):
                break
            # The first native update hunk may omit its @@ header.
            hunks += 1
            body_start = index
            while index < stop and (
                lines[index] == "" or lines[index].startswith((" ", "+", "-"))
            ):
                index += 1
            if body_start == index:
                raise InvalidEdit("empty update hunk")
            if index < stop and lines[index] == "*** End of File":
                index += 1
                break
        if not hunks:
            raise InvalidEdit("missing update hunk")
    if not paths:
        raise InvalidEdit("patch has no affected paths")
    return paths


def affected_paths(payload):
    if not isinstance(payload, dict):
        raise InvalidEdit("hook input must be an object")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or tool_name not in {
        "Edit", "Write", "apply_patch", "functions.apply_patch",
    }:
        raise InvalidEdit("unsupported matched editing tool")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise InvalidEdit("tool_input must be an object")
    primary = "file_path" if tool_name in {"Edit", "Write"} else "command"
    if primary not in tool_input:
        raise InvalidEdit("missing primary editing input")
    paths = []
    if "file_path" in tool_input:
        paths.append(path_value(tool_input["file_path"]))
    if "command" in tool_input:
        paths.extend(patch_paths(tool_input["command"]))
    if not paths:
        raise InvalidEdit("unsupported editing payload")
    cwd = payload.get("cwd", os.getcwd())
    if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
        raise InvalidEdit("invalid working directory")
    return paths, Path(cwd)


def protected_reason(path):
    # Resolve does not restore canonical casing on case-insensitive filesystems.
    # Conservative case folding prevents aliases such as .ENV and model.JSON.
    parts = tuple(part.casefold() for part in path.parts)
    if path.name.casefold() == ".env":
        return "do not edit .env directly; update .env.example for documentation instead"
    if path.suffix.casefold() == ".json" and any(
        parts[index:index + 2] == ("app", "ml_models")
        for index in range(len(parts) - 2)
    ):
        return "ML model JSON artifacts are generated files; regenerate via the approved CLI"
    return None


def check(payload):
    paths, cwd = affected_paths(payload)
    for value in paths:
        original = Path(value)
        absolute = original if original.is_absolute() else cwd / original
        # Check lexical, normalized and symlink-resolved paths. Never open the
        # proposed target; resolving its existing parents also covers new files.
        candidates = (original, absolute, Path(os.path.abspath(absolute)), absolute.resolve())
        for candidate in candidates:
            reason = protected_reason(candidate)
            if reason:
                raise InvalidEdit(reason)


def main():
    try:
        payload = json.load(
            sys.stdin, object_pairs_hook=unique_object, parse_constant=invalid_constant,
        )
        check(payload)
    except InvalidEdit as exc:
        # InvalidEdit messages are fixed policy text, never proposed content.
        print(f"Blocked: {exc}.", file=sys.stderr)
        return 2
    except (ValueError, TypeError, OSError, RuntimeError):
        # Never echo the payload: legacy inputs may contain secret file content.
        print("Blocked: protected path or malformed/unsupported editing input.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
