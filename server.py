import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, 'src'))


def _put_back_an_interrupted_rollback():
    """A boot-guard rollback killed between setting the live package aside and
    putting its known-good copy in place leaves src/agent_friday missing, and
    the guard that would repair it lives inside the package (north star
    NS-34.16-3). Put the set-aside package back (or, if there is none, a
    complete staged copy); the guard's own journal repair then runs as usual.
    Standard library only, and nothing happens when the package is there."""
    src = os.path.join(_HERE, 'src')
    if os.path.isdir(os.path.join(src, 'agent_friday')):
        return
    try:
        names = os.listdir(src)
    except OSError:
        return
    for prefix in ('.friday-restore-aside-agent_friday-', '.friday-restore-incoming-agent_friday-'):
        found = sorted((n for n in names if n.startswith(prefix) and not n.endswith('.partial')),
                       key=lambda n: os.path.getmtime(os.path.join(src, n)), reverse=True)
        if found:
            os.rename(os.path.join(src, found[0]), os.path.join(src, 'agent_friday'))
            return


if __name__ == '__main__':
    _put_back_an_interrupted_rollback()
    # Anchor the exec target to this file's location, not the cwd — launchers
    # (shortcuts, scheduled tasks) invoke this shim from arbitrary directories.
    _target = os.path.join(_HERE, 'src', 'agent_friday', 'server.py')
    os.chdir(_HERE)  # the server reads index.html/static/ relative to the repo root
    exec(compile(open(_target, encoding='utf-8-sig').read(), _target, 'exec'))
else:
    _put_back_an_interrupted_rollback()
    from agent_friday.server import *
