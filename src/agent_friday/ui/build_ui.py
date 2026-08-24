#!/usr/bin/env python3
"""Friday Desktop OS — UI Assembler
Combines ui_parts/ into a single index.html
Run: python build_ui.py

JSX precompile: app.html ships as one large <script type="text/babel"> block.
Transforming it in the browser costs 10-17 s on a cold load (and requires the
@babel/standalone CDN, which breaks the UI offline), so when node +
node_modules/@babel/standalone are available the JSX is compiled at build time
and index.html gets a plain <script> plus no Babel CDN dependency. Without
node the build falls back to the original in-browser-transform output.

REGRESSION GUARD (2026-08-24): index.html is the file the server serves
(routes/core_routes.py opens it directly) and it has been hand-edited ahead of
ui_parts/app.html since 2026-08-18 — app.html says so in its own first eight
lines. app.html is now a strict SUBSET: every top-level component in it also
exists in index.html, while eighteen components (the whole conversations
feature, the model picker, Settings -> Intelligence) exist only in index.html.
Running this build unguarded therefore DELETES real, shipped code, and does it
silently. So the build now refuses to write output that drops any top-level
component the existing index.html defines. Pass --force to override once you
have read the list it prints.
"""
import os
import re
import subprocess
import sys
import tempfile

_repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..')
parts_dir = os.path.join(_repo_root, 'ui_parts')
output = os.path.join(_repo_root, 'index.html')

BABEL_OPEN = '<script type="text/babel">'
# Matches the Babel CDN loader line in head.html regardless of pinned version.
BABEL_CDN_RE = re.compile(
    r'^\s*<script src="https://unpkg\.com/@babel/standalone[^"]*"[^>]*></script>\s*$',
    re.MULTILINE)
BABEL_CHECK = "if(typeof Babel==='undefined')missing.push('Babel');"

# The script runs from a temp dir, so @babel/standalone must be required by
# absolute path (node resolves node_modules relative to the script, not cwd).
_TRANSFORM_JS = r"""
const fs = require('fs');
const babel = require(process.argv[4]);
const src = fs.readFileSync(process.argv[2], 'utf8');
const out = babel.transform(src, {presets: ['react'], compact: false});
fs.writeFileSync(process.argv[3], out.code, 'utf8');
"""


def _precompile_jsx(jsx: str):
    """Compile JSX -> plain JS via node + @babel/standalone; None on failure."""
    if not os.path.isdir(os.path.join(_repo_root, 'node_modules', '@babel', 'standalone')):
        return None
    tmp_dir = tempfile.mkdtemp(prefix='friday_jsx_')
    src_p = os.path.join(tmp_dir, 'app.jsx')
    out_p = os.path.join(tmp_dir, 'app.js')
    js_p = os.path.join(tmp_dir, 'transform.js')
    try:
        with open(src_p, 'w', encoding='utf-8') as f:
            f.write(jsx)
        with open(js_p, 'w', encoding='utf-8') as f:
            f.write(_TRANSFORM_JS)
        babel_mod = os.path.join(_repo_root, 'node_modules', '@babel', 'standalone')
        proc = subprocess.run(
            ['node', js_p, src_p, out_p, babel_mod],
            cwd=_repo_root, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            print(f'JSX precompile failed (falling back to in-browser Babel):\n{proc.stderr[:2000]}')
            return None
        with open(out_p, 'r', encoding='utf-8') as f:
            return f.read()
    except (OSError, subprocess.SubprocessError) as e:
        print(f'JSX precompile unavailable ({e}); falling back to in-browser Babel')
        return None
    finally:
        for p in (src_p, out_p, js_p):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


# Read and combine parts in order
parts = ['head.html', 'styles_and_scene.html', 'app.html']
contents = {}
for part in parts:
    path = os.path.join(parts_dir, part)
    with open(path, 'r', encoding='utf-8') as f:
        contents[part] = f.read()

app = contents['app.html']
precompiled = False
# app.html is ONE babel script followed by the closing </body></html>; the
# last </script> in the file is its close (earlier matches are regex literals
# inside the JS, not tags).
#
# The open tag is LOCATED rather than required at offset 0: app.html gained a
# leading HTML comment on 2026-08-18, and the old `startswith` test silently
# turned precompilation off from that commit onward — the fallback prints
# nothing, so the only symptom was a slower, CDN-dependent, offline-broken UI.
# Any preamble before the tag is preserved verbatim.
open_idx = app.find(BABEL_OPEN)
close_idx = app.rfind('</script>')
if open_idx != -1 and close_idx > open_idx:
    preamble = app[:open_idx]
    jsx = app[open_idx + len(BABEL_OPEN):close_idx]
    tail = app[close_idx + len('</script>'):]
    compiled = _precompile_jsx(jsx)
    if compiled is not None:
        contents['app.html'] = preamble + '<script>\n' + compiled + '\n</script>' + tail
        # Babel is no longer needed at runtime: drop the CDN loader and its
        # missing-dependency check so the UI works fully offline.
        head = BABEL_CDN_RE.sub('', contents['head.html'])
        head = head.replace(BABEL_CHECK, "/* Babel not needed: JSX precompiled */;")
        contents['head.html'] = head
        precompiled = True

combined = ''.join(contents[p] + '\n' for p in parts)

# --- Regression guard -------------------------------------------------------
# Compare top-level component declarations, which survive JSX precompilation
# unchanged (Babel rewrites the markup inside a function, not the function's
# name), so this compares like with like whichever branch ran above.
_FN_RE = re.compile(r'^function ([A-Za-z0-9_]+)', re.MULTILINE)


def _components(html: str) -> set:
    return set(_FN_RE.findall(html))


_force = '--force' in sys.argv
if os.path.exists(output) and not _force:
    with open(output, 'r', encoding='utf-8') as f:
        existing = f.read()
    lost = sorted(_components(existing) - _components(combined))
    if lost:
        print(
            f'REFUSING to write {output}.\n'
            f'\n'
            f'The assembled output drops {len(lost)} top-level component(s) that the\n'
            f'current index.html defines. index.html is the file the server serves and\n'
            f'it is ahead of ui_parts/app.html; writing this build would delete shipped\n'
            f'code:\n'
            f'\n'
            f'  {", ".join(lost)}\n'
            f'\n'
            f'Size: existing {len(existing):,} bytes -> assembled {len(combined):,} bytes.\n'
            f'\n'
            f'index.html is the source of truth. Edit it directly. ui_parts/app.html is\n'
            f'a stale hand-maintained mirror kept only for history; see its header note\n'
            f'and docs/audits/release-readiness.md.\n'
            f'\n'
            f'Re-run with --force if you genuinely intend to discard the components above.'
        )
        raise SystemExit(1)

# Write combined output
with open(output, 'w', encoding='utf-8') as f:
    f.write(combined)

mode = 'JSX precompiled' if precompiled else 'in-browser Babel (fallback)'
print(f'Assembled {len(combined)} bytes from {len(parts)} parts -> {output}  [{mode}]')
print(f'Parts: {", ".join(parts)}')
