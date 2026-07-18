# Phase 2 Increment A — De-Bootstrap Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Bootstrap CSS + Icons CDNs and Google Fonts; the app renders pixel-equivalent from self-hosted fonts + the single `theme.css`, with zero external requests.

**Architecture:** Reimplement the ~20 Bootstrap class names actually in use under the same names in a delimited "framework layer" section of `theme.css` (CSS grid + existing `:root` tokens). Icons become a Jinja `icon()` macro rendering inline SVG. The 4 `data-bs-*` behaviors become one delegated vanilla-JS handler. Spec: `docs/superpowers/specs/2026-07-17-phase-2-ui-rebuild-design.md`.

**Tech Stack:** Flask/Jinja2, hand-rolled CSS (no build step), vanilla JS, unittest.

## Global Constraints

- Branch: `phase-2-increment-a` off current `main`. Conventional commits, **no Co-Authored-By**. No merge, no push.
- Test runner is **unittest**: `SECRET_KEY=test python -m coverage run -m unittest discover -s tests` — FOREGROUND only, and always with `set -o pipefail` when piping.
- Gates before done: full suite OK, coverage ≥ 80%, `ruff check .`, `bandit -q -r app -x tests -ll`.
- Design-system law: all colors from existing `:root` custom properties; fonts Syne/Outfit/JetBrains Mono only; NO glow/text-shadow/box-shadow effects; everything in the single `app/static/css/theme.css`; no new JS libraries.
- Visual parity is the gate: no layout/spacing/color changes vs main. The definition-of-done checklist (breakpoints 576/768/992, live-progress rows, control regression) runs manually per template before finishing.

---

### Task 1: Class-usage audit manifest

**Files:**
- Create: `tools/ui_class_audit.py`
- Create: `docs/superpowers/specs/phase2-class-manifest.json` (generated)
- Test: `tests/test_ui_class_audit.py`

**Interfaces:**
- Produces: `phase2-class-manifest.json` = `{"grid": [...], "components": [...], "utilities": [...], "icons": [...]}` — the EXACT class and glyph lists Tasks 2–5 implement. Later tasks read this file; nothing is implemented that isn't in it (YAGNI enforced by manifest).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ui_class_audit.py
"""The manifest is the single source of truth for the framework layer."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / 'docs/superpowers/specs/phase2-class-manifest.json'


class TestClassAuditManifest(unittest.TestCase):

    def test_manifest_exists_and_matches_live_scan(self):
        out = subprocess.run(
            [sys.executable, str(REPO / 'tools/ui_class_audit.py')],
            capture_output=True, text=True, check=True)
        live = json.loads(out.stdout)
        stored = json.loads(MANIFEST.read_text())
        self.assertEqual(live, stored,
                         'manifest stale — rerun tools/ui_class_audit.py > manifest')

    def test_manifest_shape(self):
        stored = json.loads(MANIFEST.read_text())
        for key in ('grid', 'components', 'utilities', 'icons'):
            self.assertIn(key, stored)
            self.assertTrue(stored[key], f'{key} list empty')
```

- [ ] **Step 2: Run it — expect FAIL (no tool, no manifest)**

Run: `SECRET_KEY=test python -m unittest tests.test_ui_class_audit -v` → FileNotFoundError/CalledProcessError.

- [ ] **Step 3: Implement the audit tool**

```python
# tools/ui_class_audit.py
"""Scan templates for Bootstrap-vocabulary classes + bi-* glyphs in use.

Output (stdout, JSON): {"grid": [...], "components": [...],
"utilities": [...], "icons": [...]} — sorted, deduped. The framework layer
in theme.css implements EXACTLY these names; nothing speculative.
"""
import json
import re
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / 'app' / 'templates'

GRID_RE = re.compile(r'^(row|col(-(sm|md|lg|xl))?(-\d{1,2}|-auto)?|g-\d|gx-\d|gy-\d|container(-fluid)?)$')
COMPONENT_RE = re.compile(r'^(btn|card|badge|alert|table|nav|navbar|form|input|spinner|list-group|modal|dropdown|accordion|toast|progress|close)((-|$).*)?$')

def scan():
    classes, icons = set(), set()
    for f in TEMPLATES.rglob('*.html'):
        text = f.read_text()
        for m in re.finditer(r'class="([^"]*)"', text):
            classes.update(c for c in m.group(1).split() if not c.startswith('{'))
        icons.update(re.findall(r'\bbi-([a-z0-9-]+)', text))
    grid = sorted(c for c in classes if GRID_RE.match(c))
    components = sorted(c for c in classes if COMPONENT_RE.match(c) and not GRID_RE.match(c))
    # utilities = the remainder that are Bootstrap-vocabulary (spacing/display/
    # flex/text/etc.), i.e. everything not custom (custom = defined in theme.css)
    theme = (TEMPLATES.parents[1] / 'static' / 'css' / 'theme.css').read_text()
    custom = set(re.findall(r'\.([a-zA-Z][\w-]*)', theme))
    utilities = sorted(c for c in classes
                       if c not in custom and not GRID_RE.match(c)
                       and not COMPONENT_RE.match(c) and not c.startswith('bi'))
    return {'grid': grid, 'components': components,
            'utilities': utilities, 'icons': sorted(icons)}

if __name__ == '__main__':
    json.dump(scan(), sys.stdout, indent=1, sort_keys=True)
```

- [ ] **Step 4: Generate the manifest, eyeball it, re-run test → PASS**

Run: `python tools/ui_class_audit.py > docs/superpowers/specs/phase2-class-manifest.json`
Then: `SECRET_KEY=test python -m unittest tests.test_ui_class_audit -v` → OK.
Eyeball the utilities list — anything that is NOT a real Bootstrap utility (e.g. a custom class theme.css missed) gets a regex fix in the tool, not a manual manifest edit.

- [ ] **Step 5: Commit** — `feat: UI class-usage audit tool + phase-2 manifest`

---

### Task 2: Framework layer — grid

**Files:**
- Modify: `app/static/css/theme.css` (append new delimited section at end)
- Test: `tests/test_theme_framework_layer.py`

**Interfaces:**
- Produces: `.row`, every `col-*` in the manifest's `grid` list, and gutter vars, implemented on CSS grid at breakpoints 576/768/992px. Consumed by all templates unchanged.

- [ ] **Step 1: Failing test — theme.css must define every manifest grid class**

```python
# tests/test_theme_framework_layer.py
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
THEME = (REPO / 'app/static/css/theme.css')
MANIFEST = json.loads((REPO / 'docs/superpowers/specs/phase2-class-manifest.json').read_text())


class TestFrameworkLayer(unittest.TestCase):

    def _theme_classes(self):
        return set(re.findall(r'\.([a-zA-Z][\w-]*)', THEME.read_text()))

    def test_every_grid_class_defined(self):
        missing = [c for c in MANIFEST['grid'] if c not in self._theme_classes()]
        self.assertEqual(missing, [])

    def test_every_component_class_defined(self):
        missing = [c for c in MANIFEST['components'] if c not in self._theme_classes()]
        self.assertEqual(missing, [])

    def test_every_utility_class_defined(self):
        missing = [c for c in MANIFEST['utilities'] if c not in self._theme_classes()]
        self.assertEqual(missing, [])
```

- [ ] **Step 2: Run — grid test FAILS (components/utilities fail too until Tasks 3–4; that's expected — run only the grid test now):**
`SECRET_KEY=test python -m unittest tests.test_theme_framework_layer.TestFrameworkLayer.test_every_grid_class_defined -v`

- [ ] **Step 3: Append the framework-layer grid to theme.css**

```css
/* ═══════════════════════════════════════════════════════════════════
   FRAMEWORK LAYER (Phase 2 Increment A) — owned replacements for the
   Bootstrap vocabulary in use. Manifest: phase2-class-manifest.json.
   Section order: grid → components → utilities. Tokens only; no new
   colors, no effects.  ═══════════════════════════════════════════ */
:root { --fw-gutter-x: 1.5rem; --fw-gutter-y: 0; }

.row {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  column-gap: var(--fw-gutter-x);
  row-gap: var(--fw-gutter-y);
}
.row > * { min-width: 0; grid-column: span 12; }

.col-12 { grid-column: span 12; }
.col-6  { grid-column: span 6; }
.col-4  { grid-column: span 4; }
/* …one rule per manifest grid entry (col-3, col-auto, g-3, …) exactly as
   listed by phase2-class-manifest.json — implement each with span N,
   auto column, or gap var override. gx-N/gy-N set --fw-gutter-x/y. */

@media (min-width: 576px) {
  .col-sm-6 { grid-column: span 6; }
  /* …every col-sm-* in the manifest */
}
@media (min-width: 768px) {
  /* every col-md-* in the manifest */
}
@media (min-width: 992px) {
  /* every col-lg-* in the manifest */
}
```

The `…` lines are NOT optional: enumerate every manifest entry. The test in Step 1 is the completeness check — it fails on any missed name.

- [ ] **Step 4: Grid test PASSES; visual spot-check** — run the app (`flask run`), open dashboard + bets pages with Bootstrap CDN link TEMPORARILY commented out in a local scratch copy, confirm columns land identically at the 3 breakpoints, then restore the link (CDN removal is Task 7).
- [ ] **Step 5: Commit** — `feat: framework layer grid (owned row/col on CSS grid)`

---

### Task 3: Framework layer — components

**Files:** Modify `app/static/css/theme.css`; test file from Task 2.

**Interfaces:** every `components` manifest class (`btn`, `btn-sm`, `btn-primary`, `btn-outline-secondary`, `btn-outline-danger`, `card`, `card-body`, `badge`, `form-control`, `form-select`, `form-label`, `input-group`, `table`, `alert`, `nav-link`, `spinner-border`, `close`/`btn-close`, per manifest) styled from tokens.

- [ ] **Step 1: Run the failing component test** (Task 2 file): `...test_every_component_class_defined -v` → FAIL.
- [ ] **Step 2: Append component CSS.** Reference implementation for the core set — match current rendered look (inspect computed styles on main before writing; parity beats Bootstrap's spec):

```css
/* components ─────────────────────────────────────────────────────── */
.btn {
  display: inline-flex; align-items: center; justify-content: center;
  gap: .4rem; font: 500 .95rem/1.5 'Outfit', sans-serif;
  padding: .375rem .75rem; border-radius: .5rem;
  border: 1px solid transparent; cursor: pointer;
  background: none; color: var(--text-1);
  transition: background-color .15s ease, border-color .15s ease;
}
.btn:disabled { opacity: .55; pointer-events: none; }
.btn-sm { padding: .25rem .5rem; font-size: .85rem; border-radius: .4rem; }
.btn-primary { background: var(--accent); color: #0D0B08; border-color: var(--accent); }
.btn-primary:hover { filter: brightness(1.08); }
.btn-outline-secondary { border-color: var(--border-1); color: var(--text-2); }
.btn-outline-secondary:hover { border-color: var(--text-2); color: var(--text-1); }
.btn-outline-danger { border-color: #8B3A3A; color: #c96a6a; }
.btn-outline-danger:hover { background: #8B3A3A22; }

.card { background: var(--surface-1); border: 1px solid var(--border-1);
        border-radius: .75rem; }
.card-body { padding: 1rem 1.25rem; }

.badge { display: inline-block; font: 600 .72rem/1 'Outfit', sans-serif;
         padding: .3em .55em; border-radius: .375rem; }

.form-control, .form-select {
  display: block; width: 100%; font: 400 .95rem/1.5 'Outfit', sans-serif;
  color: var(--text-1); background: var(--surface-2);
  border: 1px solid var(--border-1); border-radius: .5rem;
  padding: .375rem .75rem;
}
.form-control:focus, .form-select:focus {
  outline: 2px solid var(--accent); outline-offset: 1px; border-color: var(--accent);
}
/* …and the remaining manifest components (table, alert, nav-link,
   input-group, spinner-border keyframes, form-label, btn-close) — same
   token-only approach; copy current computed values from main for parity. */
```

Replace `var(--text-1)`-style names with the ACTUAL custom-property names in theme.css `:root` (read them first — do not invent new tokens).

- [ ] **Step 3: Component test PASSES.** Visual spot-check as in Task 2 Step 4.
- [ ] **Step 4: Commit** — `feat: framework layer components`

---

### Task 4: Framework layer — utilities

**Files:** Modify `app/static/css/theme.css`; test from Task 2.

- [ ] **Step 1:** `...test_every_utility_class_defined -v` → FAIL.
- [ ] **Step 2:** Append one rule per manifest utility, Bootstrap-equivalent semantics. Mapping table (implement only names present in the manifest): `d-flex→display:flex`, `d-none→display:none`, `align-items-center`, `justify-content-between`, `flex-column`, `gap-2→gap:.5rem`, `w-100→width:100%`, `text-center`, `text-muted→color:var(<muted token>)`, `text-end`, `fw-bold→font-weight:600`, `small→font-size:.875em`, spacing scale `m*/p*-{0..5}` = `0/.25/.5/1/1.5/3rem` (e.g. `mb-3→margin-bottom:1rem`), `mt-auto`, `ms-auto`, `position-relative`, `visually-hidden` (full a11y pattern), responsive `d-sm-*`/`d-md-*` variants inside the matching media queries.
- [ ] **Step 3:** Utility test PASSES → **all three framework-layer tests green.**
- [ ] **Step 4: Commit** — `feat: framework layer utilities`

---

### Task 5: Icon macro + migrate all bi-* uses

**Files:**
- Modify: `app/templates/_macros.html`
- Create: `app/templates/_icons.html` (glyph dict as a Jinja mapping)
- Modify: every template containing `bi-` (15 files max)
- Test: `tests/test_icon_macro.py`

**Interfaces:**
- Produces: `{% from "_macros.html" import icon %}` → `{{ icon('graph-up') }}` → `<svg class="icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="…"/></svg>`. Unknown glyph → HTML comment `<!-- icon:missing NAME -->` (renders nothing, greppable).

- [ ] **Step 1: Failing tests**

```python
# tests/test_icon_macro.py
import json
import re
import unittest
from pathlib import Path
from flask import render_template_string
from tests.helpers import BaseTestCase

REPO = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((REPO / 'docs/superpowers/specs/phase2-class-manifest.json').read_text())


class TestIconMacro(BaseTestCase):

    def _render(self, name):
        with self.app.test_request_context():
            return render_template_string(
                '{% from "_macros.html" import icon %}{{ icon(name) }}', name=name)

    def test_known_glyph_renders_inline_svg(self):
        out = self._render('graph-up')
        self.assertIn('<svg', out)
        self.assertIn('viewBox="0 0 16 16"', out)
        self.assertIn('currentColor', out)

    def test_unknown_glyph_renders_greppable_comment(self):
        out = self._render('definitely-not-a-glyph')
        self.assertIn('icon:missing', out)
        self.assertNotIn('<svg', out)

    def test_every_manifest_icon_has_a_path(self):
        for name in MANIFEST['icons']:
            out = self._render(name)
            self.assertIn('<svg', out, f'missing glyph: {name}')

    def test_no_bi_classes_left_in_templates(self):
        offenders = []
        for f in (REPO / 'app/templates').rglob('*.html'):
            if re.search(r'\bbi-[a-z0-9-]+', f.read_text()):
                offenders.append(str(f))
        self.assertEqual(offenders, [])
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `_icons.html` = `{% set ICON_PATHS = {'graph-up': 'M0 0h1v15h15v1H0V0Zm10 3.5a.5.5 0 0 1 .5-.5h4a.5.5 0 0 1 .5.5v4a.5.5 0 0 1-1 0V4.9l-3.613 4.417a.5.5 0 0 1-.74.037L7.06 6.767l-3.656 5.027a.5.5 0 0 1-.808-.588l4-5.5a.5.5 0 0 1 .758-.06l2.609 2.61L13.445 4H10.5a.5.5 0 0 1-.5-.5Z', … } %}` — one entry per manifest icon. Fetch each path with: `curl -s https://raw.githubusercontent.com/twbs/icons/v1.11.3/icons/<name>.svg` and copy the `d="…"` attribute(s) verbatim (multi-path glyphs concatenate `<path>` elements). Macro in `_macros.html`:

```jinja
{% from "_icons.html" import ICON_PATHS %}
{% macro icon(name, size=16, class='') -%}
  {%- if name in ICON_PATHS -%}
  <svg class="icon {{ class }}" width="{{ size }}" height="{{ size }}"
       viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"
       xmlns="http://www.w3.org/2000/svg"><path d="{{ ICON_PATHS[name] }}"/></svg>
  {%- else -%}<!-- icon:missing {{ name }} -->{%- endif -%}
{%- endmacro %}
```

(If `{% from %}` of a `set` doesn't import cleanly in this Jinja version, `{% include "_icons.html" %}` inside the macro's template instead — pick whichever renders in Step 4.)

- [ ] **Step 4: Migrate templates:** each `<i class="bi bi-NAME ..."></i>` → `{{ icon('NAME') }}` (carry any extra classes into the macro's `class=` arg). Mechanical, one commit-sized pass; the Step-1 grep test is the completeness check.
- [ ] **Step 5: All icon tests PASS. Run the template-rendering test modules** (templates changed): `SECRET_KEY=test python -m unittest tests.test_coverage tests.test_bets tests.test_analysis tests.test_auth tests.test_parlay_redesign -v` — these are the modules that render pages via `self.client.get`.
- [ ] **Step 6: Commit** — `feat: inline-SVG icon macro; migrate all bootstrap-icons uses`

---

### Task 6: Vanilla toggle handler (replace data-bs-*)

**Files:**
- Modify: `app/static/js/script.js` (append)
- Modify: the 3 templates using `data-bs-toggle`/`target`/`dismiss`
- Test: `tests/test_no_bootstrap_attrs.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_no_bootstrap_attrs.py
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class TestNoBootstrapAttrs(unittest.TestCase):
    def test_no_data_bs_attributes_left(self):
        offenders = []
        for f in (REPO / 'app/templates').rglob('*.html'):
            for m in re.finditer(r'data-bs-[a-z-]+', f.read_text()):
                offenders.append(f'{f.name}:{m.group(0)}')
        self.assertEqual(offenders, [])
```

- [ ] **Step 2: FAIL (5 attrs).**
- [ ] **Step 3:** Read the 3 usages first; implement equivalents: `data-bs-toggle="collapse" data-bs-target="#x"` → `data-toggle="#x"`; `data-bs-dismiss="alert"` → `data-dismiss`. Append to `script.js`:

```javascript
// Phase 2A: owned replacements for the removed Bootstrap JS behaviors.
document.addEventListener('click', (e) => {
  const toggler = e.target.closest('[data-toggle]');
  if (toggler) {
    const target = document.querySelector(toggler.getAttribute('data-toggle'));
    if (target) target.classList.toggle('is-open');
  }
  const dismisser = e.target.closest('[data-dismiss]');
  if (dismisser) dismisser.closest('.alert, [data-dismissable]')?.remove();
});
```

Add the matching `.is-open` display rule to theme.css for the collapsed element (read what the collapse currently shows/hides; replicate exactly). `data-bs-theme="dark"` on `<html>` is deleted in Task 8 (nothing consumes it after the CDN dies).
- [ ] **Step 4: Delete `data-bs-theme="dark"` from base.html in this task too** (nothing consumes it once the CDN is gone, and removing it here keeps the grep test absolute — no allowlist). Test PASSES. Manually exercise the toggled/dismissed controls in the running app.
- [ ] **Step 5: Commit** — `feat: vanilla toggle/dismiss handler; drop data-bs attributes`

---

### Task 7: Self-hosted fonts

**Files:**
- Create: `app/static/fonts/` (woff2 files)
- Modify: `app/static/css/theme.css` (@font-face block at top)
- Modify: `app/templates/base.html` (remove Google Fonts links)

- [ ] **Step 1:** Download exact weights in use via google-webfonts-helper, e.g.:
`curl -s "https://gwfh.mranftl.com/api/fonts/syne?download=zip&subsets=latin&variants=600,700,800&formats=woff2" -o /tmp/syne.zip` (repeat: outfit 400,500,600; jetbrains-mono 400,500,600). Unzip into `app/static/fonts/`.
- [ ] **Step 2:** Add `@font-face` rules at the TOP of theme.css (one per family+weight, `font-display: swap`, `url('../fonts/<file>.woff2') format('woff2')`).
- [ ] **Step 3:** Delete the three Google Fonts `<link>` lines from base.html. Reload app — fonts identical (check computed font-family in devtools), no fonts.googleapis.com requests.
- [ ] **Step 4: Commit** — `feat: self-host Syne/Outfit/JetBrains Mono woff2`

---

### Task 8: Kill the CDNs + final parity gate

**Files:**
- Modify: `app/templates/base.html` (delete Bootstrap CSS + Icons CDN links, delete `data-bs-theme` if not already gone)
- Test: `tests/test_no_bootstrap_attrs.py` (extend)

- [ ] **Step 1: Extend the grep test:**

```python
    def test_no_external_asset_urls_in_templates(self):
        pattern = re.compile(r'(cdn\.jsdelivr|fonts\.googleapis|fonts\.gstatic|bootstrap)', re.I)
        offenders = []
        for f in (REPO / 'app/templates').rglob('*.html'):
            for m in pattern.finditer(f.read_text()):
                offenders.append(f'{f.name}:{m.group(0)}')
        self.assertEqual(offenders, [])
```

- [ ] **Step 2: FAIL → delete the two CDN `<link>`s → PASS.**
- [ ] **Step 3: Manual parity gate (definition-of-done):** every page (home, dashboard, bets list, bet builder, login/register, errors) at 375/768/1280 widths; control-regression checklist; browser Network panel = zero external requests; console = no 404s (icons/fonts all local).
- [ ] **Step 4: Full gates:** `set -o pipefail; SECRET_KEY=test python -m coverage run -m unittest discover -s tests 2>&1 | tail -3 && python -m coverage report --include="app/*" | tail -2 && ruff check . && bandit -q -r app -x tests -ll` — all green, coverage ≥ 80%.
- [ ] **Step 5: Commit** — `feat: remove Bootstrap + fonts CDNs — UI fully self-hosted`
- [ ] **Step 6:** Record any deviations in the spec's Deviations section; hand back for whole-branch review.
