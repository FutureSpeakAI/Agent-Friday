---
name: Bug report
about: Something in Agent Friday is not working as expected
title: '[BUG] '
labels: bug
assignees: ''
---

Please do not paste API keys, passwords, vault contents or private messages.
Look through logs and screenshots first and remove anything private. To report a
security problem, do not use this form; see the
[security policy](https://github.com/FutureSpeakAI/Agent-Friday/security/policy).

## What happened

A clear description of what you saw.

## What you expected

## Steps to reproduce

1.
2.
3.

## Your setup

- Agent Friday version (Settings > About):
- Windows version and build (press Win+R, type `winver`):
- RAM:
- Graphics card and its VRAM (Task Manager > Performance > GPU):
- How you installed it (setup program `AgentFriday-Setup-...exe`, or from source):
- Model in use (local model name, or cloud provider):

## Logs

Friday writes its logs to `%USERPROFILE%\.friday`. The useful files are:

- `friday.log` (the main log)
- `server_stderr.log` (the server's error output)
- `voice_debug.log` (voice problems)
- `logs\crashes.log` (after a crash or hang)

Paste the last lines around the problem, or attach the file after reading it
for private text.

```
paste logs here
```

## Anything else

Screenshots (with private content hidden), or other details that might help.
