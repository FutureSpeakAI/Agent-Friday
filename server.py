import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'src'))
if __name__ == '__main__':
    # Anchor the exec target to this file's location, not the cwd — launchers
    # (shortcuts, scheduled tasks) invoke this shim from arbitrary directories.
    _target = os.path.join(_HERE, 'src', 'agent_friday', 'server.py')
    os.chdir(_HERE)  # the server reads index.html/static/ relative to the repo root
    exec(compile(open(_target, encoding='utf-8-sig').read(), _target, 'exec'))
else:
    from agent_friday.server import *
