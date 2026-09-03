"""Executable contract between Python environment reads and .env.example."""

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_HELPERS = {'_env_bool', '_env_float', '_positive_int_env', 'env_float'}
DECLARATION_RE = re.compile(r'^\s*#?\s*([A-Z][A-Z0-9_]*)=', re.MULTILINE)


def _literal_first_arg(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _environment_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            is_os_getenv = (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == 'os'
                and function.attr == 'getenv'
            )
            is_environ_method = (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Attribute)
                and isinstance(function.value.value, ast.Name)
                and function.value.value.id == 'os'
                and function.value.attr == 'environ'
                and function.attr in {'get', 'setdefault'}
            )
            is_env_helper = isinstance(function, ast.Name) and function.id in ENV_HELPERS
            if is_os_getenv or is_environ_method or is_env_helper:
                if name := _literal_first_arg(node):
                    names.add(name)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == 'os'
            and node.value.attr == 'environ'
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            names.add(node.slice.value)
    return names


class EnvironmentContractTests(unittest.TestCase):
    def test_operator_environment_reads_are_documented(self):
        source_files = sorted((ROOT / 'app').rglob('*.py')) + [
            ROOT / 'gunicorn.conf.py',
            ROOT / 'run.py',
        ]
        used = set().union(*(_environment_names(path) for path in source_files))
        declared = set(
            DECLARATION_RE.findall((ROOT / '.env.example').read_text(encoding='utf-8'))
        )
        self.assertEqual(used - declared, set(), 'Undocumented environment variables')

    def test_local_database_example_resolves_to_instance_app_db(self):
        example = (ROOT / '.env.example').read_text(encoding='utf-8')
        self.assertIn('DATABASE_URL=sqlite:///app.db', example)
        self.assertNotIn('DATABASE_URL=sqlite:///instance/app.db', example)


if __name__ == '__main__':
    unittest.main()
