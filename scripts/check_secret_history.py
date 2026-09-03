#!/usr/bin/env python3
"""Fail when high-confidence credential formats occur anywhere in Git history."""

from __future__ import annotations

import re
import subprocess
import sys


SECRET_PATTERNS = {
    'aws_access_key': re.compile(rb'AKIA[0-9A-Z]{16}'),
    'github_classic_token': re.compile(rb'gh[pousr]_[A-Za-z0-9]{36,}'),
    'github_fine_grained_token': re.compile(rb'github_pat_[A-Za-z0-9_]{20,}'),
    'openai_api_key': re.compile(rb'sk-(?:proj-)?[A-Za-z0-9_-]{20,}'),
    'private_key': re.compile(
        rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'
    ),
    'slack_token': re.compile(rb'xox[baprs]-[A-Za-z0-9-]{20,}'),
}


def scan_history_patch(patch: bytes) -> list[tuple[str, str, str]]:
    """Return redacted (kind, commit, path) findings from a Git patch stream."""
    commit = 'unknown'
    path = 'unknown'
    findings = []
    seen = set()

    for line in patch.splitlines():
        if line.startswith(b'commit '):
            commit = line.split(maxsplit=1)[1].decode('ascii', errors='replace')[:12]
            path = 'unknown'
            continue
        if line.startswith(b'+++ b/'):
            path = line[6:].decode('utf-8', errors='replace')
            continue
        if not line.startswith((b'+', b'-')) or line.startswith((b'+++', b'---')):
            continue
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(line[1:]):
                finding = (kind, commit, path)
                if finding not in seen:
                    findings.append(finding)
                    seen.add(finding)
    return findings


def main() -> int:
    result = subprocess.run(
        ['git', 'log', '-p', '--all', '--no-ext-diff', '--unified=0'],
        check=True,
        stdout=subprocess.PIPE,
    )
    findings = scan_history_patch(result.stdout)
    if not findings:
        print('Git history secret scan passed.')
        return 0

    print(f'Git history secret scan found {len(findings)} high-confidence match(es):')
    for kind, commit, path in findings:
        print(f'- {kind}: commit={commit} path={path}')
    print('Values are intentionally redacted. Rotate first, then rewrite history deliberately.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
