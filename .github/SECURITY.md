# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Agent Friday, please report it responsibly:

**Email:** security@futurespeak.ai with the subject `SECURITY: Agent Friday`

Please include:
- A description of the vulnerability
- Steps to reproduce (or a proof of concept)
- The potential impact
- Any suggested fix (optional but appreciated)

We will acknowledge receipt within 48 hours and aim to provide a fix or mitigation within 7 days for critical issues.

**Do not** open a public GitHub issue for security vulnerabilities.

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | Yes       |
| < 1.0   | No        |

---

## Security Model

Agent Friday handles sensitive personal data (financial, health, legal, contacts). Its security is built on three layers:

### 1. Sovereign Vault (Data Classification)
All content is classified into three tiers:
- **TIER 1 (Public)** — flows to any model (cloud or local)
- **TIER 2 (Private)** — local models only; cloud gets a redacted placeholder
- **TIER 3 (Sensitive)** — local models only; cloud gets nothing

The policy engine (`vault_access.py`) and routing enforcement (`model_router.py`) ensure sensitive data never leaves the local machine.

### 2. Privacy Shield (PII Scrubbing)
A runtime scrubber processes every outbound message to cloud models, detecting and redacting SSNs, credit card numbers, phone numbers, email addresses, street addresses, and configurable watchlist tokens.

### 3. Governance Gate (Privilege Rings)
Every tool call passes through a governance gate with four privilege rings:
- **Ring 0** — Read-only (always allowed)
- **Ring 1** — Local writes (always allowed)
- **Ring 2** — Network access (requires authentication)
- **Ring 3** — OS control (requires explicit user enablement)

Destructive operations (`rm`, `del`, `format`, `shutdown`, `reg delete`) are hard-blocked regardless of ring level.

---

## Commit Hygiene

This repository is **public**. The following must never be committed:

| Category | Examples |
|----------|----------|
| API keys / tokens | `AIza...`, `sk-...`, `sk-ant-...`, `ghp_...` |
| Passwords / secrets | Database credentials, bearer tokens |
| Personal PII | Email addresses, phone numbers, SSNs, home addresses |
| Private / family data | Personal records, medical records, anything naming a minor |
| Private filesystem paths | `C:\Users\<name>\...` (leaks OS username) |

### Pre-commit Hook

A security scanner at `.githooks/security_scan.py` blocks commits containing these patterns. Activate it:

```bash
git config core.hooksPath .githooks
```

See [SECURITY_POLICY.md](.github/SECURITY_POLICY.md) for the full commit checklist.

---

## Dependencies

Agent Friday depends on third-party libraries listed in `requirements.txt` and `CREDITS.md`. We monitor for known vulnerabilities but do not guarantee real-time patching. If you discover a vulnerable dependency, please report it through the same security channel above.
