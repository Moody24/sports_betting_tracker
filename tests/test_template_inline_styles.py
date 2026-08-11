"""Inline `style` attributes in templates may set custom properties only.

The UI V1 baseline's first global gate used to be "no inline styles in
templates", checked by hand with `rg -n 'style="' app/templates`. That rule
was aimed at the right problem — presentational CSS smeared through markup —
but it also bans the one legitimate use: handing a server-computed number to
the stylesheet.

A prop's projected value, a bet's pace, or a distribution's interquartile
range cannot be expressed as a class without quantising it into hundreds of
buckets. The standard answer is a custom property:

    <span class="fan-iqr" style="--a: 32.5%; --b: 53.3%"></span>

with every actual declaration living in theme.css. This test replaces the
honour-system grep with the sharper rule: an inline style may contain custom
properties and nothing else. `style="color: red"` still fails.
"""
import re
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / 'app' / 'templates'

STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
# Jinja expressions may contain ';' or ':' (e.g. a dict literal or a filter
# argument), so blank them out before splitting the declaration list.
JINJA_RE = re.compile(r'\{\{.*?\}\}|\{%.*?%\}', re.DOTALL)


def _offending_declarations(style_value: str) -> list[str]:
    """Return declarations in `style_value` that are not custom properties."""
    masked = JINJA_RE.sub('X', style_value)
    bad = []
    for declaration in masked.split(';'):
        declaration = declaration.strip()
        if not declaration:
            continue
        prop = declaration.split(':', 1)[0].strip()
        if not prop.startswith('--'):
            bad.append(declaration)
    return bad


class TestTemplateInlineStyles(unittest.TestCase):
    def test_inline_styles_set_custom_properties_only(self):
        violations = []
        for template in sorted(TEMPLATES.rglob('*.html')):
            text = template.read_text(encoding='utf-8')
            for line_no, line in enumerate(text.splitlines(), start=1):
                for style_value in STYLE_ATTR_RE.findall(line):
                    for declaration in _offending_declarations(style_value):
                        rel = template.relative_to(TEMPLATES.parents[2])
                        violations.append(f'{rel}:{line_no}: {declaration!r}')

        self.assertEqual(
            violations,
            [],
            'Inline style attributes may only set CSS custom properties '
            '(e.g. style="--pace: 62%"). Move real declarations into '
            'theme.css:\n  ' + '\n  '.join(violations),
        )

    def test_detector_rejects_a_real_declaration(self):
        # Guards the guard: a permissive regex here would silently disable
        # the gate above.
        self.assertEqual(_offending_declarations('--pace: 62%'), [])
        self.assertEqual(_offending_declarations('--a: 1%; --b: 2%'), [])
        self.assertEqual(_offending_declarations('--w: {{ pct }}%'), [])
        self.assertEqual(_offending_declarations('color: red'), ['color: red'])
        self.assertEqual(
            _offending_declarations('--ok: 1%; display: none'),
            ['display: none'],
        )


if __name__ == '__main__':
    unittest.main()
