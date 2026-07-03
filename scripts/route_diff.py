"""Diff UI-referenced API paths against server-registered routes.

Usage: python scripts/route_diff.py
Prints UI fetch paths that have no matching Flask route (potential 404 holes)
and server routes never referenced by the UI (informational).
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

# Import the app WITHOUT running it (app.run is behind __main__ guard)
os.environ.setdefault('FRIDAY_SKIP_TLS_WARN', '1')
from agent_friday.server import app  # noqa: E402

# ---- 1. Server routes -> regex matchers ----
server_rules = []  # (rule_str, methods, compiled_regex)
for rule in app.url_map.iter_rules():
    rule_str = rule.rule
    methods = sorted(m for m in rule.methods if m not in ('HEAD', 'OPTIONS'))
    # Convert Flask rule to regex: <converter:name> or <name> -> segment matcher
    pattern = re.sub(r'<path:[^>]+>', r'.+', rule_str)
    pattern = re.sub(r'<[^>]+>', r'[^/]+', pattern)
    server_rules.append((rule_str, methods, re.compile('^' + pattern + '/?$')))


def match_server(path):
    hits = []
    for rule_str, methods, rx in server_rules:
        if rx.match(path):
            hits.append((rule_str, methods))
    return hits


# ---- 2. UI-referenced paths ----
UI_FILES = [
    os.path.join(ROOT, 'ui_parts', f)
    for f in os.listdir(os.path.join(ROOT, 'ui_parts'))
    if f.endswith('.html')
]

# Find '/api/...' string literals in JS. Capture template-literal pieces too.
lit_rx = re.compile(r'''['"`](/api/[A-Za-z0-9_\-./]*)['"`?$]''')
tmpl_rx = re.compile(r'`(/api/[^`]*)`')

ui_paths = {}  # path -> [(file, line)]
for fp in UI_FILES:
    with open(fp, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            for rx in (lit_rx, tmpl_rx):
                for m in rx.finditer(line):
                    p = m.group(1)
                    # Normalize template literal expressions ${...} -> <var>
                    p = re.sub(r'\$\{[^}]*\}', '<var>', p)
                    p = p.split('?')[0].rstrip()
                    if not p or p == '/api/':
                        continue
                    ui_paths.setdefault(p, []).append(
                        (os.path.basename(fp), i))

# ---- 3. Diff ----
def ui_path_to_regex(p):
    pattern = re.escape(p).replace(re.escape('<var>'), '[^/]+')
    return re.compile('^' + pattern + '/?$')


missing = []
for p, refs in sorted(ui_paths.items()):
    if '<var>' in p:
        # variable path: try matching against server rules structurally
        prx = ui_path_to_regex(p)
        ok = False
        for rule_str, methods, _ in server_rules:
            test = re.sub(r'<path:[^>]+>', 'XVARX/XVARX', rule_str)
            test = re.sub(r'<[^>]+>', 'XVARX', test)
            if prx.match(test.replace('XVARX/XVARX', 'xv').replace('XVARX', 'xv')):
                ok = True
                break
        # fallback: prefix check — does any server rule share the static prefix?
        if not ok:
            static_prefix = p.split('<var>')[0]
            ok = any(r.startswith(static_prefix) for r, _, _ in server_rules)
        if not ok:
            missing.append((p, refs))
    else:
        if not match_server(p):
            missing.append((p, refs))

print('=' * 70)
print('UI paths with NO matching server route (potential 404s):')
print('=' * 70)
if not missing:
    print('  (none)')
for p, refs in missing:
    locs = ', '.join(f'{f}:{ln}' for f, ln in refs[:4])
    print(f'  {p}')
    print(f'      referenced at: {locs}' + (' ...' if len(refs) > 4 else ''))

print()
print(f'Total UI-referenced api paths: {len(ui_paths)}')
print(f'Total server rules: {len(server_rules)}')
