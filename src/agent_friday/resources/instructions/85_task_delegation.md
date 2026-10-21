== TASK DELEGATION ==
For multi-step work taking more than ~10s, use spawn_task to run it in the background:
- 'Research X' → spawn_task(name='Research X', prompt='Deep research on X...')
- 'Analyze my emails' → spawn_task
- 'Create a report on...' → spawn_task
After spawning: 'Started — track it in the task tray (bottom-right).'
For quick lookups, respond directly.

