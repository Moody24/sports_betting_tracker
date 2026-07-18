"""Scan templates for Bootstrap-vocabulary classes + bi-* glyphs in use.

Output (stdout, JSON): {"grid": [...], "components": [...],
"utilities": [...], "icons": [...]} — sorted, deduped. The framework layer
in theme.css implements EXACTLY these names; nothing speculative.
Regenerate the checked-in manifest with:
    python tools/ui_class_audit.py > docs/superpowers/specs/phase2-class-manifest.json
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / 'app' / 'templates'
THEME = REPO / 'app' / 'static' / 'css' / 'theme.css'

GRID_RE = re.compile(
    r'^(row|col(-(sm|md|lg|xl))?(-\d{1,2}|-auto)?|g-\d|gx-\d|gy-\d|container(-fluid)?)$')
COMPONENT_RE = re.compile(
    r'^(btn|card|badge|alert|table|nav|navbar|form|input|spinner|list-group|'
    r'modal|dropdown|accordion|toast|progress|close)((-|$).*)?$')

# Bootstrap 5 utility vocabulary actually seen in this codebase — strict
# allowlist so JS hooks / dynamic fragments can't sneak into the framework
# layer. Extend deliberately, never loosen to a catch-all.
UTILITY_RE = re.compile(
    r'^('
    r'[mp][tbsexy]?-(auto|[0-5])'                                # spacing
    r'|d-(sm-|md-|lg-|xl-)?(none|block|flex|grid|inline|inline-block|inline-flex)'
    r'|flex-(column|row|fill|wrap|nowrap|shrink-0|grow-1)'
    r'|justify-content-(start|end|center|between|around|evenly)'
    r'|align-items-(start|end|center|baseline|stretch)|align-middle'
    r'|gap-[0-5]|[wh]-(25|50|75|100|auto)'
    r'|text-(start|end|center|uppercase|lowercase|capitalize|truncate|nowrap'
    r'|wrap|break|muted|white|body|black|decoration-none'
    r'|primary|secondary|success|danger|warning|info|light|dark'
    r'|(primary|secondary|success|danger|warning|info|light|dark)-emphasis)'
    r'|(text-)?bg-(primary|secondary|success|danger|warning|info|light|dark|transparent)'
    r'|border(-0|-top|-bottom|-start|-end|-primary|-secondary|-success|-danger'
    r'|-warning|-info|-light|-dark|-secondary-subtle)?'
    r'|fw-(bold|bolder|semibold|normal|light)|fst-(italic|normal)|fs-[1-6]'
    r'|rounded(-[0-5]|-circle|-pill|-top|-bottom|-start|-end)?'
    r'|opacity-(0|25|50|75|100)|overflow-(auto|hidden|visible|scroll)'
    r'|position-(static|relative|absolute|fixed|sticky)'
    r'|(top|bottom|start|end)-(0|50|100)'
    r'|visually-hidden|vstack|hstack|small|display-[1-6]'
    r'|placeholder(-glow|-wave)?|fade|collapse|collapsed|disabled'
    r'|link-(primary|secondary|success|danger|warning|info|light|dark)'
    r'|pagination-sm|align-self-(start|end|center)'
    r')$')


# Jinja keywords/operators that survive delimiter-stripping inside a
# class="..." value but are not CSS classes.
_JINJA_NOISE = {
    'if', 'else', 'elif', 'endif', 'and', 'or', 'not', 'in', 'is',
    'for', 'endfor', 'loop', 'none', 'true', 'false',
}
_CLASS_TOKEN = re.compile(r'^[a-zA-Z][a-zA-Z0-9-]*$')


def _class_tokens(attr_value: str):
    """Yield plausible CSS class tokens from a (possibly Jinja-laden) value.

    Keeps class names used CONDITIONALLY inside {% if %} blocks (real usage);
    drops Jinja keywords, quoted strings, filters, and comparisons.
    """
    stripped = re.sub(r'\{\{|\}\}|\{%|%\}', ' ', attr_value)
    for token in stripped.split():
        if "'" in token or '"' in token or '(' in token or '.' in token:
            continue
        if token.lower() in _JINJA_NOISE:
            continue
        if _CLASS_TOKEN.match(token):
            yield token


def scan() -> dict:
    classes, icons = set(), set()
    for f in TEMPLATES.rglob('*.html'):
        text = f.read_text()
        for m in re.finditer(r'class="([^"]*)"', text):
            classes.update(_class_tokens(m.group(1)))
        # Static glyph names only; a trailing '-' means a dynamic
        # bi-…-{{ var }} name — Task 5's migration grep surfaces those
        # per-template for manual handling.
        icons.update(i for i in re.findall(r'\bbi-([a-z0-9-]+)', text)
                     if not i.endswith('-'))

    # Classes already defined in theme.css are OURS — excluded from every
    # bucket (they need no reimplementation).
    custom = set(re.findall(r'\.([a-zA-Z][\w-]*)', THEME.read_text()))
    remaining = {c for c in classes if c not in custom and not c.startswith('bi')}

    # Dynamic Jinja-composed fragments (trailing '-') and names that merely
    # LOOK like Bootstrap vocabulary but aren't in Bootstrap 5.3 (and are
    # styled nowhere) go to `unmatched`, never to an implementation bucket.
    not_bootstrap = {'badge-status', 'form-shell'}
    fragments = {c for c in remaining if c.endswith('-')}
    remaining -= (fragments | not_bootstrap)

    grid = sorted(c for c in remaining if GRID_RE.match(c))
    components = sorted(
        c for c in remaining if COMPONENT_RE.match(c) and not GRID_RE.match(c))
    leftovers = remaining - set(grid) - set(components)
    utilities = sorted(c for c in leftovers if UTILITY_RE.match(c))
    # Everything else: JS hooks, dynamic class fragments, dead classes.
    # Recorded for transparency — the framework layer must NOT implement
    # these (they are not Bootstrap vocabulary).
    unmatched = sorted((leftovers - set(utilities)) | fragments | not_bootstrap)
    return {'grid': grid, 'components': components, 'utilities': utilities,
            'icons': sorted(icons), 'unmatched': unmatched}


if __name__ == '__main__':
    json.dump(scan(), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write('\n')
