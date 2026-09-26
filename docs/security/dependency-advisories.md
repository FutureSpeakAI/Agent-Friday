# Dependency advisories

**Status:** current for the seven Dependabot alerts raised against `uv.lock`
up to 26 September 2026.
- Three of them, for Lightning, Hydra and NLTK, are resolved by taking the GPU
  voice tier's NeMo stack out of the dependency set entirely.
- Four, for ChromaDB, have no fixed release. Their vulnerable code is not
  reachable in the way Friday uses ChromaDB, so they are dismissed on GitHub as
  "vulnerable code not used", with a link to this page.

Dependabot reads `pyproject.toml` and `uv.lock`. Nothing installs from
`uv.lock`:
- The Windows installer installs from `packaging/windows/requirements/*.txt`.
- A source install resolves `pyproject.toml`.
- The GPU voice tier is installed by its own step in Settings (see below).

Re-check this page whenever an alert is raised or reopened, a new release of
the package appears, or the code named under "What keeps it out of reach"
changes.

## ChromaDB: alerts 1, 3, 4, 5 (dismissed: vulnerable code not used)

| Alert | Severity | Advisory | Summary |
|---|---|---|---|
| 1 | critical | GHSA-f4j7-r4q5-qw2c | Pre-authentication code injection through the collections HTTP endpoint (`trust_remote_code`) |
| 5 | critical | GHSA-36p7-vc44-83pf | Authenticated code injection through the collection-update HTTP endpoint (`trust_remote_code`) |
| 3 | high | GHSA-2wm9-hf6c-p5cr | Any authenticated user can read or write any tenant's collections |
| 4 | high | GHSA-xph7-9rjv-w5fr | `SimpleRBACAuthorizationProvider` ignores the tenant, database and collection in a permission |

**Affected:** every release from 0.4.17 up to 1.5.9, and 1.5.9 is the newest
release. There is no fixed version to move to. A pin below 0.4.17 would avoid
the ranges, but Friday needs 0.5 or later, and a two-year-old release would
bring its own unfixed problems, so there is no safe pin either.

**Where it is installed:** the `local` and `all` extras, and the Windows
installer's memory tier. It is Friday's on-device conversation memory.

**What keeps it out of reach:** all four advisories are in ChromaDB's
client/server deployment: the HTTP API and the authorisation providers in
front of it. Friday uses ChromaDB only as an embedded library:
`chromadb.PersistentClient` in `conversation_memory.py`, with the embedding
function passed in explicitly. Friday:
- never starts a Chroma server;
- never constructs an `HttpClient`;
- never reads `collection.configuration`, the path that rebuilds an embedding
  function (and its `trust_remote_code` argument) from stored data.

`tests/unit/test_chromadb_no_server.py` pins each of those properties.

**Re-check when:** ChromaDB publishes a release above 1.5.9 (then raise the
floor and let the alerts close as fixed), or anything in Friday starts or
connects to a Chroma server (then reopen the alerts).

## Lightning (alert 8), Hydra (alert 2) and NLTK (alert 6): resolved

| Alert | Severity | Advisory | Summary | Fixed in |
|---|---|---|---|---|
| 8 | high | GHSA-qqmf-gpg7-g8gw | Arbitrary code execution through checkpoint `_instantiator` hyperparameters | Lightning 2.6.6 |
| 2 | high | GHSA-2cp2-2r3c-7p7r | `hydra.utils.instantiate` with an untrusted config can execute code | hydra-core 1.3.4 |
| 6 | high | GHSA-8mgp-746c-j5xp | NLTK model-artifact APIs bypass the path checks | none |

**What changed:** all three arrived only with NVIDIA NeMo, the optional GPU
voice tier. NeMo 3.0.0, the newest release, requires `lightning<=2.4.0` and
`hydra-core<=1.3.2`, below the fixed releases. The `voice-local-gpu` extra
therefore no longer exists. NeMo, Lightning, Hydra and NLTK are not in
`pyproject.toml` or `uv.lock`, and the alerts close as fixed.

**How the GPU voice tier is installed now:** Settings runs the voice
installer's `voice-local-gpu` target (`services/voice_installer.py`). It
installs a pinned CUDA torch pair and `nemo_toolkit[asr]==3.0.0` in one step,
on the machine of the person who asked for it.

**What still keeps the NeMo advisories out of reach on such a machine:** both
code-execution advisories need Friday to load a checkpoint or config that an
attacker controls. NeMo loads three models:
- the two text-to-speech models are fixed constants in
  `services/nemo_voice.py`;
- the speech-recognition model is named by the `local_voice_gpu_asr_model`
  setting. `trusted_nemo_model()` accepts only an id in NVIDIA's own namespace
  (`nvidia/<name>`) and falls back to the default for anything else.

Friday's code never imports NLTK.

`tests/unit/test_dependency_advisories_reach.py` pins four things:
- the NeMo stack is absent from `pyproject.toml`, `uv.lock` and the installer
  requirement files;
- the voice installer pins NeMo;
- the model allow-list holds;
- the ASR class applies it.

**Re-check when:** NeMo releases a version that allows Lightning 2.6.6 and
hydra-core 1.3.4 or later (then move the installer's pin), or Friday gains
another path that hands NeMo a model name or a `.nemo`/`.ckpt` file.
