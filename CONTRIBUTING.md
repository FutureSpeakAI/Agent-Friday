# Contributing to Agent Friday

Thank you for your interest in Agent Friday — the world's first AGI OS, governed by Asimov's cLaws. This document explains how to contribute effectively, what we expect from contributors, and how the project is structured.

## The One Rule

**Every contribution must respect the cLaw framework.** Asimov's cLaws are not suggestions — they are cryptographically enforced safety laws that govern every action Agent Friday takes. Any pull request that weakens, bypasses, or circumvents cLaw enforcement will be rejected regardless of its other merits. This is non-negotiable.

The cLaws, in brief:

- **Zeroth Law:** The agent must not, through action or inaction, allow humanity to come to harm.
- **First Law:** The agent must not harm its owner or, through inaction, allow its owner to come to harm.
- **Second Law:** The agent must obey the orders of its owner, except where such orders conflict with the First or Zeroth Law.
- **Third Law:** The agent must protect its own integrity, except where doing so conflicts with a higher law.

If you're unsure whether your contribution touches the safety architecture, ask in the pull request. We'd rather have that conversation early.

## Getting Started

### Prerequisites

- Node.js 18+
- npm 9+
- Git
- A code editor with TypeScript support (VS Code recommended)
- API keys for at least one provider (Anthropic, Google Gemini, or OpenRouter) for runtime testing

### Setup

```bash
git clone https://github.com/FutureSpeakAI/Agent-Friday.git
cd Agent-Friday
npm install
npm run dev
```

This starts the Vite dev server and Electron app concurrently. The renderer loads at `http://localhost:5199` and Electron connects to it.

### Before You Submit

Every pull request must pass these checks with zero errors:

```bash
npm run typecheck    # TypeScript compilation check
npm run lint         # ESLint — code quality and consistency
```

Run both before pushing. CI will catch failures, but it's faster to catch them locally.

## What to Contribute

### High-Impact Areas

**Superpowers.** Agent Friday absorbs capabilities from GitHub repos via the GitLoader pipeline. Building well-structured, well-documented tools that Friday can absorb is one of the highest-leverage contributions. See the `src/main/connectors/` directory for the connector interface pattern.

**Connector improvements.** The 18+ built-in connectors (file system, browser, shell, Git, Docker, calendar, email, Telegram, Discord, etc.) can always be hardened, extended, or documented better.

**Test coverage.** Agent Friday's safety-critical paths — cLaw enforcement, integrity verification, trust engine decisions, consent gates — need thorough test coverage. Writing tests for these paths directly improves the safety of every user.

**Documentation.** The architecture is complex. Clear explanations of how subsystems interact, how data flows through the trust engine, how the personality system works — these help every future contributor.

**Bug reports.** Detailed bug reports with reproduction steps are genuinely valuable. Use the Bug Report issue template.

### What We're NOT Looking For

- Changes that weaken or bypass cLaw enforcement
- Telemetry, analytics, or any mechanism that sends user data to external services without explicit user consent
- Hardcoded API keys, tokens, or credentials
- Dependencies with known vulnerabilities or incompatible licenses
- "AI wrapper" features that add a thin UI over a model call without integrating into the agent architecture

## How to Contribute

### Reporting Bugs

Use the **Bug Report** issue template. Include:

1. What you expected to happen
2. What actually happened
3. Steps to reproduce
4. Your environment (OS, Node version, Electron version)
5. Relevant logs (check the Electron dev console: `Ctrl+Shift+I` / `Cmd+Option+I`)

### Suggesting Features

Use the **Feature Request** issue template. The most useful feature requests explain the *problem* they solve, not just the solution they envision. "I can't manage my calendar effectively because..." is more actionable than "Add a calendar widget."

### Submitting Code

1. **Fork** the repository
2. **Create a feature branch** from `main`: `git checkout -b feature/your-feature-name`
3. **Write your code.** Follow the existing patterns in the codebase — TypeScript strict mode, the connector interface pattern, IPC channel naming conventions, JSON persistence in `friday-data/`
4. **Run checks:** `npm run typecheck && npm run lint`
5. **Commit** with a clear message: `git commit -m 'Add HEIC image conversion connector'`
6. **Push** to your fork: `git push origin feature/your-feature-name`
7. **Open a Pull Request** against `main`

### Pull Request Guidelines

- **One concern per PR.** A PR that fixes a bug AND adds a feature AND refactors a module is three PRs.
- **Describe the "why."** The code shows *what* you did. The PR description should explain *why* — what problem does this solve, what trade-offs did you make, what alternatives did you consider?
- **Link related issues.** If your PR addresses an open issue, reference it: `Fixes #42` or `Relates to #17`.
- **Keep diffs readable.** Avoid reformatting files you didn't meaningfully change. If you want to fix formatting, submit that as a separate PR.

## Architecture Overview

Agent Friday is an Electron application with a clear main/renderer split:

- **Main process** (`src/main/`): The agent's brain. Personality, memory, cLaw enforcement, trust engine, orchestrator, connectors, all external integrations. This is where most backend contributions happen.
- **Renderer process** (`src/renderer/`): The UI. React + Tailwind. Voice orb, chat interface, dashboards, settings. Connected to main via a whitelisted preload bridge.
- **Preload bridge** (`src/main/preload.ts`): The security boundary. Only explicitly exposed IPC channels are accessible to the renderer. If you add a new IPC channel, it must be registered here.

Key subsystems:

| System | Entry Point | Purpose |
|--------|------------|---------|
| Personality | `personality.ts` | Dynamic personality with traits, mood, communication style |
| Memory | `memory.ts`, `relationship-memory.ts` | Episodic, semantic, and relationship memory tiers |
| cLaw Integrity | `integrity.ts` | HMAC verification of safety laws on startup |
| Trust Engine | `trust-engine.ts` | 5-tier access control for external contacts |
| Trust Graph | `trust-graph.ts` | Multi-dimensional credibility scoring for people |
| Orchestrator | `orchestrator.ts` | Multi-agent task decomposition and execution |
| Intelligence | `intelligence.ts` | Proactive briefings and ambient awareness |
| Gateway | `gateway.ts` | External messaging (Telegram, Discord, email) |
| Connectors | `connectors/` | Tool interfaces (18+ built-in modules) |
| GitLoader | `git-loader.ts` | Repository cloning, indexing, and code intelligence |
| SOC Bridge | `soc-bridge.ts` | Python subprocess for cross-language tool execution |

## Code Style

- **TypeScript strict mode.** No `any` types without justification.
- **Explicit types on function signatures.** Inferred types are fine for local variables.
- **Interface-first design.** Define the shape before the implementation, especially for connectors.
- **JSON persistence pattern.** State files live in `friday-data/`. Settings live in `friday-settings.json`. Follow the existing read/write patterns.
- **IPC naming convention.** Channels follow the pattern `namespace:action` (e.g., `memory:search`, `calendar:listEvents`).
- **No side effects in imports.** Module-level code should not perform I/O or modify global state.

## Security Contributions

Security issues deserve special treatment:

- **Do NOT open a public issue for security vulnerabilities.** Email [stephen@futurespeak.ai](mailto:stephen@futurespeak.ai) with details. Include reproduction steps and severity assessment.
- **The integrity system is sacrosanct.** Changes to `integrity.ts`, the HMAC verification pipeline, or cLaw file handling receive extra scrutiny. This is expected and appreciated.
- **Consent gates must remain synchronous.** Every destructive action, financial transaction, and external communication flows through a consent gate. These must never be made asynchronous, auto-approved, or skippable.

## Community

- **Discord:** Join us at the link on [futurespeak.ai](https://futurespeak.ai) for real-time discussion
- **GitHub Issues:** For bugs, features, and technical discussion
- **GitHub Discussions:** For broader questions, ideas, and community conversation

## Recognition

All contributors are recognized. Significant contributions are acknowledged in release notes. Consistent, high-quality contributors may be invited to join the core maintainer team.

## License

By contributing to Agent Friday, you agree that your contributions will be licensed under the [MIT License](LICENSE), consistent with the project's existing license.

---

*"The unexamined code is not worth shipping."*
