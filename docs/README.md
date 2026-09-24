# Agent Friday documentation

Start with the [README](../README.md) for what Agent Friday is. This index
tells you which document to open for a given question, and what kind of
document it is.

## Getting started

| Document | Audience | Purpose |
|---|---|---|
| [Getting started](user-guide/getting-started.md) | New users | Install on Windows, the first-run questions, opening Friday, the local address. |
| [Tutorial](getting-started/tutorial.md) | New users | From nothing to a first conversation, then it stops. |
| [Installation](getting-started/installation.md) | Users, operators | Every supported install path, prerequisites, GPU and Ollama setup, troubleshooting. |

## User guide

| Document | Purpose |
|---|---|
| [Approvals and receipts](user-guide/approvals-and-receipts.md) | What Friday asks before it acts, cards, grants for scheduled jobs, and the signed receipts. |
| [Privacy: local and cloud](user-guide/privacy.md) | What stays on your PC, what can leave, and the egress gate's limits. |
| [Mail](user-guide/mail.md) | Messages and Gmail: connecting, what Friday can do, how sending is approved. |
| [Calendar](user-guide/calendar.md) | Google Calendar and Tasks, and which changes ask first. |
| [Voice](user-guide/voice.md) | Voice engines, local voice, and push-to-transcribe (Alt+T). |
| [Documents](user-guide/documents.md) | Word, Excel and PowerPoint files through OfficeCLI. |
| [Scheduled jobs](user-guide/scheduled-jobs.md) | Where jobs run, local-only defaults, and grants. |
| [Phone](user-guide/phone.md) | Texts, voicemail and calls over your own Twilio account (off by default). |
| [Backup and restore](user-guide/backup-and-restore.md) | What to back up, what cannot be recovered, restoring on a new PC. |
| [Updating and uninstalling](user-guide/updating-and-uninstalling.md) | Keeping your data across versions, and removing Friday. |
| [Configuration](user-guide/configuration.md) | Every setting, environment variable, provider key, and the `~/.friday` layout. |
| [File grants](user-guide/file-grants.md) | Letting Friday send a specific document to the cloud, deliberately and on the record. |
| [Background network activity](user-guide/background-network.md) | Every connection Friday makes on its own, and how to disable each. |
| [PDF documents](user-guide/pdf-documents.md) | Reading scans with local OCR, filling forms into a new file, and signing only on an approval card. |
| [Skills](user-guide/skills.md) | The skill system, versioned optimisation, and the auto-research loop. |
| [Local voice, GPU tier](user-guide/local-voice-gpu-tier.md) | The NVIDIA NeMo voice tier behind the same WebSocket contract as the CPU tier. |

## Reference

| Document | Purpose |
|---|---|
| [API](reference/api.md) | Every HTTP endpoint with method, path, request and response. |
| [Roles and model identity](reference/roles-and-model-identity.md) | The contract between the residency layer and anything that renders a model picker or binds a model to a conversation. |
| [Task observation](reference/task-observation.md) | How an orchestrator (or you) reads a running task's journal: the read-only credential, the three reads, event kinds, gaps. |

## Architecture and security

| Document | Purpose |
|---|---|
| [Architecture](../ARCHITECTURE.md) | Processes, the request flow, tool dispatch and the governance hook chain. |
| [Architecture overview](architecture/overview.md) | Diagrams: system, a chat turn, the checkpoint, vault tiers, voice. |
| [Threat model](security/threat-model.md) | What is defended against, what is not, and the guarantee each mechanism provides. |
| [Security policy](../SECURITY.md) | Reporting, supported versions, where credentials live, what the runtime enforces. |

## Development

| Document | Purpose |
|---|---|
| [Contributing](../CONTRIBUTING.md) | Setup, required checks, sensitive subsystems, how to submit changes. |
| [Repository guards](development/repository-guards.md) | The pre-commit hook and the static checks that protect specific invariants. |
| [UI build](development/ui-build.md) | Which UI file is authoritative and how the build refuses to lose components. |
| [Release process](development/release-process.md) | Versioning, tagging, building the Windows installer, what ships and what does not. |
| [Failure classes](development/failure-classes.md) | The failure classes this codebase has produced, stated as rules a contributor can apply. |
| [Windows installer](../packaging/windows/README.md) | The maintainer's guide to the installer's design rules and layout. |
| [Local HTTPS proxy](../ops/README.md) | Optional: fronting the local server with `https://agent.friday` on one machine. |

## Design documents

Design documents carry a status header (`active`, `partially-implemented`,
`implemented`, `superseded`, `historical`), the date they were last verified
against the code, and the modules that implement them. Code beats
documentation: when a design and the tree disagree, the status header and its
implementation notes say so.

- [`design/active/`](design/active/) — specifications that still drive work, including partially built ones.
- [`design/implemented/`](design/implemented/) — the design records of shipped subsystems.
- [`design/historical/`](design/historical/) — superseded designs and position papers whose outcome was a decision, kept for the reasoning.

## Decisions

[`decisions/`](decisions/) holds accepted architecture decisions and open
decision requests, dated.

## Engineering history

[`history/audits/`](history/audits/) holds dated investigations, forensics
and audit reports. They describe the tree on the day they were written and are
kept verbatim as records; the current state of any subsystem is in the
documents above.

## Release information

- [CHANGELOG](../CHANGELOG.md) — the historical record of shipped changes.
- [RELEASE_NOTES](../RELEASE_NOTES.md) — the human-facing notes for the current release.
- [KNOWN_ISSUES](../KNOWN_ISSUES.md) — current, unresolved, user-impacting limitations.

## Sources of truth

| Fact | Where it is defined |
|---|---|
| Version number | `pyproject.toml` (`project.version`); `package.json` mirrors it for the Playwright tooling. |
| Supported Python versions | `pyproject.toml` (`requires-python`); CI tests the versions listed in `.github/workflows/tests.yml`. |
| Supported operating systems | README, *Requirements*. |
| Installation methods | [Installation](getting-started/installation.md), *Supported installation paths*. |
| Local model ladder | `src/agent_friday/services/model_plan.py` and `routing/ollama_manager.py`; `scripts/gen_installer_ladder.py` regenerates the installer's copy. |
| Provider capabilities | `src/agent_friday/services/provider_registry.py` and `routing/provider_descriptors.py`. |
| Credential storage | [SECURITY.md](../SECURITY.md), *How credentials are stored*. |
| Security architecture | [Threat model](security/threat-model.md). |
| Tool registry | `src/agent_friday/services/agent.py` (`CLAUDE_TOOLS`) and [design/active/one-tool-registry.md](design/active/one-tool-registry.md). |
| Settings keys | `DEFAULT_SETTINGS` in `src/agent_friday/core/__init__.py`, documented in [Configuration](user-guide/configuration.md). |
| Known issues | [KNOWN_ISSUES.md](../KNOWN_ISSUES.md). |
