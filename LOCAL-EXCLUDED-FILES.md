# Files excluded from this repo on purpose

Some files live in this working tree and are deliberately kept out of git. They
are listed in `.git/info/exclude`, **not** in `.gitignore`, because `.gitignore`
is itself committed — a rule that hides a sensitive file publishes that file's
name to everyone who clones the repo. `.git/info/exclude` is local to one clone
and is never pushed.

That local-only property has an obvious cost: the exclusion is invisible to
everybody else, and easy for the person who set it up to forget. **This file is
the answer to that.** The knowledge lives in the repo even though the contents
do not.

This repository is **public** (`github.com/FutureSpeakAI/Agent-Friday`). Treat
every entry below as "must not be pushed", not "not worth committing".

## What is excluded, and why

| Path | Why |
|---|---|
| `docs/audits/release-readiness.md` | A security audit that inventories where this project's real credentials are — which refs, which untracked launch scripts, which values. The secrets themselves are elsewhere; **this document is the map to them.** On a public repo a map is worse than a stray key, because it is a finding aid. Do not commit, do not paste into an issue, do not attach to a release. |
| `docs/audits/voice-session-2026-08-25-triage-and-spec.md` | Session forensics containing local machine paths and personal context. The document carries its own "DO NOT COMMIT TO THE PUBLIC REPO AS-IS" banner at the top. Publishable only after a scrub pass. |
| `.agents/` | ~100 files of third-party skill definitions vendored from `higgsfield-ai/skills`. Not our work, not part of the product, and the licence has not been reviewed for redistribution. |
| `skills-lock.json` | The lockfile that pins the `.agents/` contents. Excluded with them so the two do not drift apart in opposite directions. |
| `Microsoft/` | Build residue, not authored content: PowerShell writes `Microsoft/Windows/PowerShell/ModuleAnalysisCache` into whatever directory is standing in for `%LOCALAPPDATA%`, so a profile-redirected run (the installer isolation rehearsal) drops an 86 KB binary cache in the repo root. Deleted 2026-08-29; excluded so it does not come back. |

## Not on this list, but also untracked

Several untracked design and audit documents under `docs/` are **not** excluded —
they still appear in `git status`. They are unresolved, not unsafe: whether they
should be committed is a judgment call awaiting Stephen. One of them,
`docs/design/voice-tools-and-transcript-collision.md`, contains a single absolute
user path that should be scrubbed before it is committed anywhere.

The launch scripts holding live API keys (`start.bat` and friends) are covered by
`.gitignore` already and are not repeated here.

## Working with this

Check whether a path is excluded, and by which rule:

```
git check-ignore -v <path>
```

See everything hidden from a normal `git status`:

```
git status --porcelain --ignored
```

To reverse any of this, delete the corresponding line from `.git/info/exclude`.
Nothing here is baked into history — that is the point.

**Before running `git add -A` in this tree, read this file.** These exclusions
exist precisely because that command, run once without thinking, would publish a
document that indexes where the real secrets are. That accident is not
recoverable by deleting the file afterwards; a public repo is scraped, forked and
mirrored.

---
*Established 2026-08-29, during working-tree triage after the v5.7.0 release.*
