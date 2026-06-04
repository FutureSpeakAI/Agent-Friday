# Security

Agent Friday is a **local-first, privacy-by-default** personal AI desktop. It is
designed so that secrets and personal data stay on the user's machine and never
enter version control. This document explains how that is enforced and how to
report a problem.

> Quick links: detailed commit rules and the release checklist live in
> [`.github/SECURITY_POLICY.md`](.github/SECURITY_POLICY.md).

---

## How secrets are managed

- **API keys & passwords** (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
  `FRIDAY_PASSWORD`, etc.) are read from **environment variables** at runtime.
  The setup wizard writes them into a **local, gitignored** startup script
  (`start.bat` / `friday_startup.bat`) that never leaves the machine.
- **Per-user configuration** lives under `~/.friday/` (and `*.local.yaml`
  overrides next to each skill's `config.yaml`). These paths are outside the
  repo and are gitignored.
- **Private keys, vault tokens, and federation identities** live in the
  encrypted vault under `~/.friday/`. The corresponding repo paths
  (`.asimovs-mind/vault/bridge-token`, `port`, etc.) are gitignored.
- **Filesystem paths** in source use `~`, `Path.home()`, `HOME`, or
  `%USERPROFILE%` — never a hardcoded `C:\Users\<name>\…`, which would leak the
  OS username.

`.env.example` ships as a **template with placeholder values only**. Copy it to
your local environment and fill in real values there.

## What should NEVER be committed

- API keys / tokens of any kind (`AIza…`, `sk-…`, `sk-ant-…`, `AQ.…`, `ghp_…`, `AKIA…`).
- Passwords, bearer tokens, or other credentials.
- Private keys (`-----BEGIN … PRIVATE KEY-----`) or vault `bridge-token` values.
- Personal PII: personal emails (gmail/outlook/etc.), phone numbers, SSNs, home addresses.
- Private or family data — especially anything naming a **minor**
  (e.g. co-parenting / OurFamilyWizard exports). Store it under `~/.friday/`.
- Startup scripts (`*.bat`, `*.vbs`) and `.env` files.
- Vendored dependencies (`node_modules/`).

The project creator's business identity (FutureSpeak.AI) is intentionally in
`CREDITS.md` / `LICENSE` and is fine.

## Pre-commit hook (required)

A scanner blocks commits containing secrets or PII. **Enable it once after
cloning:**

```bash
git config core.hooksPath .githooks
```

It scans staged changes for the patterns above and blocks the commit with a
clear message if any are found. False positives can be allowlisted per-line with
`# pragma: allowlist secret`. See
[`.github/SECURITY_POLICY.md`](.github/SECURITY_POLICY.md) for full details and
the pre-release checklist.

## Reporting a vulnerability or an exposed secret

If you discover a security issue — including a secret or PII that was committed:

- **Email:** security@futurespeak.ai with the subject `SECURITY: Agent Friday`.
- Or open a **private** GitHub Security Advisory (Security ▸ Advisories ▸
  *Report a vulnerability*). **Do not** open a public issue for an active secret.

Please include what you found, where (file / commit), and the impact. If a live
credential is exposed, we will rotate it immediately; a history rewrite does not
un-leak a credential, so rotation always comes first.

## Supported versions

This is an actively developed personal project; security fixes target the latest
`main`. Pin a tag for stability and watch the repo for security-related releases.
