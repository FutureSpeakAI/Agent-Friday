# Security Policy — Commit Hygiene & Release Checklist

This repository is **public**. Everything you commit is permanent: even after a
force-push and history rewrite, old objects can persist in forks, pull requests,
and provider caches. Treat every commit as if it will be read by the world
forever.

A previous audit found a live API key, a password, and a minor's private data in
this repo's history. They were scrubbed and the credentials rotated. The
controls below exist so it never happens again.

---

## 1. What must NEVER be committed

| Category | Examples | Do this instead |
|---|---|---|
| **API keys / tokens** | `AIza…`, `sk-…`, `sk-ant-…`, `AQ.…`, `ghp_…`, AWS `AKIA…`, Slack `xox…` | Read from environment variables / `~/.friday/` runtime config |
| **Passwords / secrets** | `FRIDAY_PASSWORD=…`, DB creds, bearer tokens | Environment variables only |
| **Private keys** | `-----BEGIN … PRIVATE KEY-----`, `bridge-token`, `node-identity` private halves | Keep in the encrypted vault; gitignore the files |
| **Personal PII** | personal emails (gmail/outlook/etc.), phone numbers, SSNs, home addresses | Placeholders (`user@example.com`) or env-driven values |
| **Private / family data** | `data/coparenting-intel.json`, OFW exports, anything naming a **minor** | Never in-repo. Store under `~/.friday/`; it is gitignored |
| **Private filesystem paths** | `C:\Users\<name>\…` (leaks OS username) | `~`, `Path.home()`, `HOME`, or `%USERPROFILE%` |
| **Startup scripts** | `start.bat`, `friday_startup.bat`, `*.bat`, `*.vbs` (they hold keys) | Gitignored; generated locally by the setup wizard |

The **business** identity that is already public is fine to keep: the name
"Stephen C. Webster", `stephen@futurespeak.ai`, and the FutureSpeak.AI brand
(see `CREDITS.md` / `LICENSE`).

---

## 2. The pre-commit hook (required)

A scanner at `.githooks/security_scan.py` blocks commits that contain any of the
patterns above. **Activate it once per clone:**

```bash
git config core.hooksPath .githooks
```

Verify it is active:

```bash
git config --get core.hooksPath        # → .githooks
```

- **False positive?** Append `# pragma: allowlist secret` to the offending line.
- **Emergency bypass** (discouraged, leaves a reflog trail): `git commit --no-verify`.

CI should also fail if `core.hooksPath` was bypassed — run the scanner against
the diff in the pipeline.

---

## 3. Before merging / releasing — checklist

- [ ] `git config --get core.hooksPath` returns `.githooks` (hook active).
- [ ] `git grep -niE "AIza|sk-ant|AQ\.|password|secret|token" -- ':!node_modules'` shows only placeholders/templates.
- [ ] `git grep -niE "@(gmail|outlook|yahoo|hotmail|icloud)\.com" -- ':!node_modules'` returns nothing.
- [ ] No `C:\\Users\\<name>` paths: `git grep -n "C:\\\\Users\\\\" -- ':!node_modules'` is clean.
- [ ] No tracked `*.bat` / `*.vbs` / `.env` / private data files: `git ls-files | grep -iE "\.(bat|vbs|env)$|coparenting|bridge-token"` is empty.
- [ ] `git ls-files | grep "^node_modules/"` is empty (deps not vendored).
- [ ] New config files ship with placeholder values only; real values come from env / `*.local.yaml`.
- [ ] Tag the release **after** these pass.

---

## 4. If a secret is committed anyway

1. **Rotate the credential immediately** — assume it is already compromised. A
   history rewrite does **not** un-leak it.
2. Remove it from the working tree and commit the fix.
3. Scrub history with [`git filter-repo`](https://github.com/newren/git-filter-repo)
   across **all branches and tags**, then force-push.
4. For a public repo, contact GitHub Support to garbage-collect unreachable
   objects and purge cached views.
5. Report it per `SECURITY.md`.
