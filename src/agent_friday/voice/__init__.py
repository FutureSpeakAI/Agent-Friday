"""Friday's voice worker package (voice-system-clean-sheet.md §5.2).

``python -m agent_friday.voice.worker --engine <name>`` is the child process
that holds a GPU-resident voice engine. The parent (``services/voice_workers``)
owns the lease; the child never talks to the arbiter.
"""
