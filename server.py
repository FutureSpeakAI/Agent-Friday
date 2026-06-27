"""Backwards-compatible entry point — real server at src/agent_friday/server.py"""
import sys
import os
import runpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

if __name__ == '__main__':
    runpy.run_module('agent_friday.server', run_name='__main__', alter_sys=True)
