# Agent Friday documentation

Start with the [README](../README.md) for what Agent Friday is. This index
tells you which document to open for a given question, and what kind of
document it is.

## Getting started

| Document | Audience | Purpose |
|---|---|---|
| [Getting started](getting-started/README.md) | New users | Download, the SmartScreen warning, verifying the file, setup, your first conversation and voice. |
| [Installation reference](getting-started/installation.md) | Users, operators | What setup does, where files go, upgrading, silent install options. |
| [First run](user-guide/getting-started.md) | New users | The first-run questions, opening Friday, the local address. |
| [Troubleshooting](user-guide/troubleshooting.md) and [FAQ](user-guide/faq.md) | Everyone | Fixes for real failures, where the logs are, backing up and uninstalling. |

## User guide

| Document | Purpose |
|---|---|
| [Settings](user-guide/settings.md) | The seven Settings sections and what you can change in each. |
| [The setup chat](user-guide/setup-chat.md) | The first-run conversation: the connection checklist, opt-in research on you, the style questions, what is stored where, and deleting your profile. |
| [Chat Hub](user-guide/chat-hub.md) | Conversations, projects and the model chip. |
| [See & Touch](user-guide/see-and-touch.md) | Friday sees what your workspace shows, points at rows, and acts on what you tick. |
| [Library](user-guide/library.md) | Documents you add, read on your PC and answered with footnotes. |
| [Media](user-guide/media.md) | The media workspace: search, previews, transcripts, tidy-up. |
| [Podcasts](user-guide/podcasts.md) | Episodes written by your local model and spoken on your PC. |
| [Workflows](user-guide/workflows.md) | Project context, checked results, reusable procedures, and shared desktop, chat and voice controls. |
| [Approvals and receipts](user-guide/approvals-and-receipts.md) | What Friday asks before it acts, cards, grants for scheduled jobs, and the signed receipts. |
| [Privacy: local and cloud](user-guide/privacy.md) | What stays on your PC, what can leave, and the egress gate's limits. |
| [Mail](user-guide/mail.md) | Messages and Gmail: connecting, what Friday can do, how sending is approved. |
| [Career](user-guide/career.md) | The walkthrough, setup files, job actions, reports, approval cards, and the manual Career search workflow. |
| [Calendar](user-guide/calendar.md) | Google Calendar and Tasks, and which changes ask first. |
| [Voice](user-guide/voice.md) | Voice engines, local voice models, and push-to-transcribe (Alt+T). |
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
| [Task observation](reference/task-observation.md) | How an outside program (or you) reads a running task's journal: the read-only credential, the three reads, event kinds, gaps. |

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
| [Which gate refused?](development/which-gate-refused.md) | The three gates that can refuse a tool call, how to tell them apart in the log, and what each one needs. |
| [Windows installer](../packaging/windows/README.md) | The maintainer's guide to the installer's design rules and layout. |
| [Local HTTPS proxy](../ops/README.md) | Optional: fronting the local server with `https://agent.friday` on one machine. |

## Design documents

[`design/hig/`](design/hig/) holds the Agent Friday™ Human Interface Guidelines,
the public rules for the interface.

## Decisions

[`decisions/`](decisions/) holds accepted architecture decisions and open
decision requests, dated.

## Release information

- [CHANGELOG](../CHANGELOG.md) The Beta 1.0.1 and Beta 1.0 entries, then the 5.x record.
- [RELEASE_NOTES](../RELEASE_NOTES.md), the human-facing notes for the current release.
- [KNOWN_ISSUES](../KNOWN_ISSUES.md), current, unresolved, user-impacting limitations.

## Sources of truth

| Fact | Where it is defined |
|---|---|
| Version number | `pyproject.toml` (`project.version`); `package.json` mirrors it for the Playwright tooling. |
| Supported Python versions | `pyproject.toml` (`requires-python`); CI tests the versions listed in `.github/workflows/tests.yml`. |
| Supported operating systems | README, *System requirements*. |
| Installation methods | [Installation reference](getting-started/installation.md). |
| Local model ladder | `src/agent_friday/services/model_plan.py` and `routing/ollama_manager.py`; `scripts/gen_installer_ladder.py` regenerates the installer's copy. |
| Provider capabilities | `src/agent_friday/services/provider_registry.py` and `routing/provider_descriptors.py`. |
| Credential storage | [SECURITY.md](../SECURITY.md), *How credentials are stored*. |
| Security architecture | [Threat model](security/threat-model.md). |
| Tool registry | `src/agent_friday/services/agent.py` (`CLAUDE_TOOLS`). |
| Settings keys | `DEFAULT_SETTINGS` in `src/agent_friday/core/__init__.py`, documented in [Configuration](user-guide/configuration.md). |
| Known issues | [KNOWN_ISSUES.md](../KNOWN_ISSUES.md). |
