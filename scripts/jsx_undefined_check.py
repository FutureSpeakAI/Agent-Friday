"""Scan ui_parts JSX for component tags with no matching definition."""
import re
import sys

files = ['ui_parts/app.html', 'ui_parts/head.html', 'ui_parts/styles_and_scene.html']
src = ''
for f in files:
    src += open(f, encoding='utf-8').read() + '\n'

# JSX component usages: <Capitalized
used = {}
for m in re.finditer(r'<([A-Z][A-Za-z0-9_]*)[\s/>]', src):
    used.setdefault(m.group(1), 0)
    used[m.group(1)] += 1

# Definitions: function X(, class X, const X =, let X =, var X =
defined = set()
for m in re.finditer(r'\b(?:function|class)\s+([A-Z][A-Za-z0-9_]*)', src):
    defined.add(m.group(1))
for m in re.finditer(r'\b(?:const|let|var)\s+([A-Z][A-Za-z0-9_]*)\s*=', src):
    defined.add(m.group(1))
# destructured: const {A, B} = ...
for m in re.finditer(r'\b(?:const|let|var)\s*\{([^}]*)\}\s*=', src):
    for name in m.group(1).split(','):
        name = name.split(':')[-1].strip()
        if re.match(r'^[A-Z][A-Za-z0-9_]*$', name):
            defined.add(name)

# Known globals / DOM / React builtins
builtins = {'React', 'ReactDOM', 'Fragment', 'Component', 'Suspense',
            'StrictMode', 'THREE', 'Babel', 'Promise', 'Set', 'Map', 'Date',
            'Error', 'Object', 'Array', 'JSON', 'Math', 'Number', 'String',
            'Boolean', 'WebSocket', 'AudioContext', 'Audio', 'Image', 'URL',
            'FormData', 'Blob', 'File', 'FileReader', 'AbortController',
            'CustomEvent', 'Event', 'IntersectionObserver', 'ResizeObserver',
            'MutationObserver', 'DOMParser', 'TextDecoder', 'TextEncoder',
            'Uint8Array', 'Int16Array', 'Float32Array', 'ArrayBuffer',
            'RegExp', 'Infinity', 'NaN'}

problems = []
for name, count in sorted(used.items()):
    if name in defined or name in builtins:
        continue
    problems.append((name, count))

if problems:
    print('UNDEFINED component tags (usage count):')
    for name, count in problems:
        print(f'  <{name}> x{count}')
        # show first usage line number in app.html
        app = open('ui_parts/app.html', encoding='utf-8').readlines()
        for i, line in enumerate(app, 1):
            if '<' + name in line:
                print(f'      first use app.html:{i}')
                break
else:
    print('All JSX component tags have definitions.')
print(f'({len(used)} distinct component tags checked)')
