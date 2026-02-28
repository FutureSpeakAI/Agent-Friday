# Security Policy

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

If you discover a security vulnerability in Agent Friday, please report it responsibly by emailing:

**[stephen@futurespeak.ai](mailto:stephen@futurespeak.ai)**

Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- The component affected (e.g., integrity system, trust engine, gateway, connector)
- Your assessment of severity (critical, high, medium, low)

You will receive an acknowledgment within 48 hours. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## What Qualifies

We are especially interested in vulnerabilities that affect:

- **cLaw integrity** — Any method to bypass, weaken, or tamper with Asimov's cLaws or the HMAC verification system
- **Trust engine** — Privilege escalation across trust tiers, unauthorized tool access by external contacts
- **Consent gates** — Any path that executes destructive actions, sends messages, or initiates financial transactions without user approval
- **Memory and personality** — Injection attacks that corrupt the agent's memory, personality, or behavioral constraints
- **Data exfiltration** — Any mechanism that transmits user data to external services without explicit consent
- **Gateway security** — Message injection, impersonation, or rate-limiting bypasses in the Telegram/Discord/email gateway

## What Doesn't Qualify

- Vulnerabilities in third-party dependencies that don't have a demonstrated impact on Agent Friday (please report these to the upstream project)
- Issues that require physical access to the user's machine with their OS credentials
- Social engineering attacks against the user themselves (outside the agent's control)
- Theoretical attacks with no demonstrated proof-of-concept

## Safe Harbor

We will not pursue legal action against researchers who:

- Report vulnerabilities in good faith following this policy
- Avoid accessing, modifying, or deleting user data beyond what is necessary to demonstrate the vulnerability
- Do not publicly disclose the vulnerability before a fix is available
- Make a reasonable effort to avoid disruption to other users

## Recognition

Security researchers who report valid vulnerabilities will be credited in the release notes for the fix (unless they prefer to remain anonymous). Significant discoveries may be acknowledged in the project's security hall of fame.

---

*Agent Friday's safety architecture is only as strong as the community that tests it. Thank you for helping us keep users safe.*
