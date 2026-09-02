# Dependency security review — 2026-09-01

Five open Dependabot alerts on `main` at `v5.10.0` (3d62eec): two critical, three
high. All five predate the v5.10.0 work. This is the reachability analysis behind
each verdict, written down because four of the five **cannot be closed by a
version bump** and someone will otherwise re-litigate them every month.

The rule applied throughout: a CVSS score describes the vulnerability, not our
exposure to it. What decides the verdict is whether the vulnerable code path is
reachable from the way Friday actually uses the package, and whether the package
reaches a user's machine at all.

---

## Where the alerts come from

Every alert names `uv.lock` as the manifest, because that is the only file in the
repo carrying *pinned* versions — `requirements.txt` and `pyproject.toml` carry
ranges (`chromadb>=0.5`), so the dependency graph cannot resolve them to a
concrete vulnerable version. That is worth stating plainly:

- **Nothing installs from `uv.lock`.** `.github/workflows/tests.yml` installs an
  explicit `pip install` list (and deliberately omits chromadb entirely). The
  Windows installer installs `packaging/windows/requirements/*.txt`. There is no
  `uv sync`, `uv run`, or `uv lock` reference anywhere in CI, docs, or the
  packaging scripts.
- So `uv.lock` is an accurate picture of *a* resolution, including optional
  extras nobody ships, and it is the file Dependabot scans. An alert on
  `uv.lock` is therefore evidence about the dependency, not evidence that the
  dependency is on anyone's machine. Each entry below states separately whether
  the package ships.

The obvious reaction — delete the unused lock and the alerts go away — is the
wrong one. `uv.lock` is the *only* file that pins concrete versions, so it is the
only reason GitHub can tell us anything at all. Removing it would not make the
dependencies safer; it would make us blind to the next advisory. It stays.

---

## 1–4. ChromaDB — four alerts, no fix available anywhere

| Alert | GHSA | Severity | Summary |
|---|---|---|---|
| #1 | `GHSA-f4j7-r4q5-qw2c` | **critical** | Pre-auth code injection via the collections endpoint |
| #5 | `GHSA-36p7-vc44-83pf` | **critical** | Authenticated code injection via collection update |
| #3 | `GHSA-2wm9-hf6c-p5cr` | high | Any authenticated user can read/write any tenant's collection |
| #4 | `GHSA-xph7-9rjv-w5fr` | high | `SimpleRBACAuthorizationProvider` ignores tenant/database/collection scope |

**Version and entry path.** `chromadb 1.5.9`, a **direct** dependency Friday
chose (`requirements.txt:36`, `pyproject.toml` `local` and `all` extras), pinned
at `1.5.9` in `uv.lock`.

**No upgrade exists.** All four advisories report `first_patched_version: null`,
and `1.5.9` is the **latest release on PyPI**. The pinned version is upstream's
newest version, and upstream's newest version is vulnerable. There is no bump,
minimal or otherwise, that clears these four alerts. This will stay open until
Chroma ships a fix.

**Does it ship?** Yes — `packaging/windows/requirements/memory.txt` installs
`chromadb>=0.5` on every user's machine that takes the memory tier.

**Is the vulnerable code reachable?** No. All four vulnerabilities are in
ChromaDB's **client/server deployment** — the HTTP API surface and the auth
providers that guard it:

- The two code-injection CVEs are exploited by POSTing to
  `/api/v2/tenants/{tenant}/databases/{db}/collections[/{id}]` with a malicious
  model repository and `trust_remote_code: true`. Those routes live in
  `chromadb/server/fastapi/__init__.py`.
- The two authorization CVEs concern cross-tenant access and
  `SimpleRBACAuthorizationProvider` (`chromadb/auth/simple_rbac_authz/`) — code
  that only executes inside a running Chroma server with an auth provider
  configured.

Friday runs neither. There is exactly **one** ChromaDB call site in the product,
`src/agent_friday/conversation_memory.py:165`:

```python
self._client = chromadb.PersistentClient(
    path=str(self.persist_dir),
    settings=Settings(anonymized_telemetry=False, allow_reset=False),
)
```

`PersistentClient` is in-process and file-backed. A repo-wide grep for
`HttpClient`, `AsyncHttpClient`, `chroma run`, `chroma_server`, `CHROMA_*_HOST`,
and `uvicorn`-against-chroma returns nothing outside the vendored library. There
is no server, no listening port, no tenant beyond `default_tenant`, and no auth
provider — so there is no attacker-facing endpoint and nothing for the RBAC
provider to mis-evaluate.

**The one residual path, and why it is still not a real risk.**
`SentenceTransformerEmbeddingFunction.__init__` forwards arbitrary `**kwargs`
into `SentenceTransformer(...)`, which accepts `trust_remote_code=True` — that is
the underlying primitive both code-injection CVEs reach through the HTTP route.
`build_from_config()` rebuilds that function from the *persisted* collection
configuration, so in principle a poisoned `chroma.sqlite3` could carry a
malicious `model_name` plus `kwargs`.

That path is not reachable here either, for two independent reasons:

1. Friday passes its embedding function **explicitly** to
   `get_or_create_collection`. `CollectionCommon.__init__` stores the passed
   function and never calls `load_collection_configuration_from_json`; that only
   happens in the lazy `.configuration` property, which Friday never reads
   (grep: no `.configuration` access in `src/`).
2. Even if it did, the attacker would need write access to
   `~/.friday/memory/conversations/chroma.sqlite3` — at which point they can
   equally rewrite Friday's own Python files or `start.bat`. It is not a
   boundary Friday defends, and crossing it buys the attacker nothing they do
   not already have.

`tests/unit/test_chromadb_no_server.py` now pins reason (1) and the
no-server property, so the verdict above stops being a fact about today's code
and becomes a fact the suite enforces.

**Verdict: not exploitable in Agent Friday.** Two critical + two high by label;
no reachable path in this product. Recommended handling: dismiss all four in the
Dependabot UI as *"Vulnerable code is not actually used"*, citing this document,
and re-open the question if Friday ever gains a Chroma server mode or starts
reading `collection.configuration`. **Not dismissed here** — that is a public
repository state change and Stephen's call.

---

## 2. hydra-core — patched upstream, but the patch is unreachable

`GHSA-2cp2-2r3c-7p7r` / `CVE-2026-68508`, **high**
(`CVSS:3.1/AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H` — note the *local* attack vector
and required user interaction).

**Version and entry path.** `hydra-core 1.3.2`, **transitive**. The only
reverse-dependency in the lock is `nemo-toolkit 3.0.0`, which Friday pulls in
through the `voice-local-gpu` extra (`nemo_toolkit[asr,tts]>=2.6`) — Tier-2 GPU
voice, documented in `pyproject.toml` as "opt-in ONLY, NEVER in `[all]`".

**What the vulnerability is.** `hydra.utils.instantiate()` resolves a `_target_`
string from configuration and calls it. An application that passes
attacker-controlled config — including model metadata shipped inside a
checkpoint — to `instantiate()` gets arbitrary code execution in that process.
1.3.4 adds a blacklist of obvious dangerous targets; the unreleased 1.4 line
replaces it with an allowlist.

**Is the vulnerable code reachable?** Not from Friday's own code — there is no
`hydra`, `omegaconf`, or `instantiate(` anywhere in `src/`. It is reachable from
NeMo, which loads `.nemo` checkpoint configs through Hydra. Friday's configured
Tier-2 models are pinned NVIDIA repositories
(`nvidia/nemotron-3.5-asr-streaming-0.6b`), and NeMo applies its own target
allow-list on top — the same allow-list that produced the misleading "unsafe
target" error documented in `pyproject.toml` when `nltk` was missing. That
allow-list *is* the mitigation the advisory recommends. Exploiting this would
require the user to point Tier-2 voice at an attacker-supplied checkpoint.

**Does it ship?** No. `nemo_toolkit` appears in **no**
`packaging/windows/requirements/*.txt` file and is excluded from `[all]`. The
only mention in the packaging scripts is `uninstall.ps1` cleaning up
`models\nemo`. A user reaches hydra-core only by deliberately running
`pip install agent-friday[voice-local-gpu]`.

**Why it was not bumped.** `nemo-toolkit 3.0.0` — the latest release — caps the
dependency at `hydra-core<=1.3.2,>1.3` in *every* extra Friday uses (`asr`,
`tts`, `core`, `common`). Requiring `hydra-core>=1.3.4` makes the
`voice-local-gpu` extra unresolvable: pip would refuse the install outright.
Forcing it through a `uv` override would silence the alert while changing
nothing for users, who install with pip — the alert would read as fixed when the
resolution on a real machine was untouched. Both options are worse than the
truth, so the constraint is documented in `pyproject.toml` instead and the alert
stays open.

**Verdict: real but low — dev/opt-in machines only, never shipped, mitigated by
NeMo's own allow-list.** It clears itself the moment NeMo relaxes its cap; this
should be re-checked on the next `nemo_toolkit` release.

---

## Credential- and vault-adjacent dependencies

Checked while in the tree, since a flaw here would matter more than a critical in
an unused code path. Nothing flagged, and nothing behind:

| Package | Locked | Note |
|---|---|---|
| `keyring` | 25.7.0 | current; ships in `core.txt` |
| `cryptography` | 50.0.1 | current; vault AES-256-GCM + Argon2id |
| `pynacl` | 1.6.2 | current; Ed25519 attestation |
| `google-auth` / `-oauthlib` | 2.57.0 / 1.4.1 | current |
| `oauthlib`, `pyjwt`, `certifi`, `urllib3`, `requests` | 3.3.1 / 2.13.0 / 2026.7.22 / 2.7.0 / 2.34.2 | current |

The one credential problem found is **not** a dependency vulnerability and is
tracked separately: `services/vault_passphrase.py` consults the OS keychain
(line 182) ahead of the DPAPI-protected file, so a migration that writes a
keychain entry can shadow a user's real passphrase. That is a resolver-ordering
bug in our code, not in `keyring`.

---

## Summary

| # | Package | Severity | Ships to users | Reachable | Action |
|---|---|---|---|---|---|
| 1 | chromadb | critical | yes | no — server-only route | none available; dismiss |
| 5 | chromadb | critical | yes | no — server-only route | none available; dismiss |
| 3 | chromadb | high | yes | no — no server, no tenants | none available; dismiss |
| 4 | chromadb | high | yes | no — no auth provider | none available; dismiss |
| 2 | hydra-core | high | **no** | via NeMo only, allow-listed | blocked by NeMo's cap; documented |

No release is required to protect users against any of these five. Nothing in
this review changes shipped behaviour.
