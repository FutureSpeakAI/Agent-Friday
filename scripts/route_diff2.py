"""Check suspect UI path prefixes against server url_map as prefixes."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, ROOT)  # so 'data' resolves like a root-run server
from agent_friday.server import app  # noqa: E402

rules = sorted(r.rule for r in app.url_map.iter_rules())

suspects = [
    '/api/briefing/', '/api/calendar/day/', '/api/calendar/prep/',
    '/api/career-ops/report/', '/api/content/item/', '/api/create/',
    '/api/liquid/', '/api/voice-context/', '/api/wiki/', '/api/workspace/',
    '/api/pipeline/jobs',
]
for s in suspects:
    hits = [r for r in rules if r.startswith(s.rstrip('/'))]
    print(f'{s} -> {len(hits)} rule(s)')
    for h in hits[:6]:
        print(f'      {h}')
print()
print('--- all rules containing liquid/workspace/calendar/wiki/career/content/create ---')
for r in rules:
    if any(k in r for k in ('liquid', 'workspace', 'calendar', 'wiki',
                            'career', 'content', 'create')):
        print(' ', r)
