import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
if __name__ == '__main__':
    exec(compile(open(os.path.join('src','agent_friday','server.py'), encoding='utf-8-sig').read(), 'src/agent_friday/server.py', 'exec'))
else:
    from agent_friday.server import *
