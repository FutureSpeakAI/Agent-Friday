"""Backwards-compatible shim - real tray at src/agent_friday/friday_tray.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
from agent_friday.friday_tray import *
if __name__ == '__main__':
    main()
