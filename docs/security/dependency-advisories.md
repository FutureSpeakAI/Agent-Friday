# Open dependency advisories

**Status:** current for the seven Dependabot alerts open against `uv.lock` on
25 September 2026. Every alert here is either out of reach in the way Friday
uses the package, or confined to an opt-in extra and guarded in code. None has
an installable fixed version.

Dependabot reads `uv.lock`. Nothing installs from that file: the Windows
installer installs from `packaging/windows/requirements/*.txt`, and a source
install resolves `pyproject.toml`. An alert is therefore a statement about what
*could* be installed, and each entry below says what actually is.

Re-check this page whenever an alert changes, a new release of the package
appears, or the code named under "What keeps it out of reach" changes.

## ChromaDB: alerts 1, 3, 4, 5

| Alert | Severity | Advisory | Summary |
|---|---|---|---|
| 1 | critical | GHSA-f4j7-r4q5-qw2c | Pre-authentication code injection through the collections HTTP endpoint (`trust_remote_code`) |
| 5 | critical | GHSA-36p7-vc44-83pf | Authenticated code injection through the collection-update HTTP endpoint (`trust_remote_code`) |
| 3 | high | GHSA-2wm9-hf6c-p5cr | Any authenticated user can read or write any tenant's collections |
| 4 | high | GHSA-xph7-9rjv-w5fr | `SimpleRBACAuthorizationProvider` ignores the tenant, database and collection in a permission |

**Affected:** every release up to 1.5.9, which is also the newest release.
There is no version to move to.

**Where it is installed:** the `local` and `all` extras, and the Windows
installer's memory tier. It is Friday's on-device conversation memory.

**What keeps it out of reach:** all four advisories are in ChromaDB's
client/server deployment, the HTTP API and the authorisation providers in front
of it. Friday uses ChromaDB only as an embedded library:
`chromadb.PersistentClient` in `conversation_memory.py`, with the embedding
function passed in explicitly. Friday never starts a Chroma server, never
constructs an `HttpClient`, and never reads `collection.configuration`, the
path that rebuilds an embedding function (and its `trust_remote_code`
argument) from stored data.

`tests/unit/test_chromadb_no_server.py` pins each of those properties.

**Re-check when:** ChromaDB publishes a release above 1.5.9 (then raise the
floor), or anything in Friday starts or connects to a Chroma server.

## Lightning (alert 8) and Hydra (alert 2)

| Alert | Severity | Advisory | Summary | Fixed in |
|---|---|---|---|---|
| 8 | high | GHSA-qqmf-gpg7-g8gw | Arbitrary code execution through checkpoint `_instantiator` hyperparameters | Lightning 2.6.6 |
| 2 | high | GHSA-2cp2-2r3c-7p7r | `hydra.utils.instantiate` with an untrusted config can execute code | hydra-core 1.3.4 |

**Where they are installed:** only as dependencies of NVIDIA NeMo, in the
`voice-local-gpu` extra (the optional GPU voice tier). That extra is not in
`all`, and the Windows installer's requirement files do not include NeMo,
Lightning, Hydra or NLTK. A user can add the GPU voice tier later from
Settings, which installs NeMo.

**Why there is no bump:** NeMo 3.0.0, the newest release, requires
`lightning<=2.4.0` and `hydra-core<=1.3.2`. The fixed versions cannot be
installed alongside any NeMo that Friday supports.

**What keeps it out of reach:** both advisories need Friday to load a
checkpoint or config that an attacker controls. NeMo loads three models:

- the two text-to-speech models are fixed constants in
  `services/nemo_voice.py`;
- the speech-recognition model is named by the `local_voice_gpu_asr_model`
  setting. `trusted_nemo_model()` accepts only an id in NVIDIA's own
  namespace (`nvidia/<name>`) and falls back to the default for anything else:
  another publisher, a URL, a file path or a relative path.

The setting could otherwise be changed by anything that can write settings,
including a model steered by injected content. Holding it to NVIDIA's
repositories closes that path.

`tests/unit/test_dependency_advisories_reach.py` pins the installer's
requirement files, the `all` extra and the model allow-list.

**Re-check when:** NeMo releases a version that allows Lightning 2.6.6 and
hydra-core 1.3.4 or later (then raise the floors), or Friday gains another path
that hands NeMo a model name or a `.nemo`/`.ckpt` file.

## NLTK: alert 6

| Alert | Severity | Advisory | Summary |
|---|---|---|---|
| 6 | high | GHSA-8mgp-746c-j5xp | Model-artifact APIs bypass the path checks and touch files outside the allowed roots |

**Affected:** every release up to 3.10.3, the newest. There is no fix.

**Where it is installed:** only in the `voice-local-gpu` extra, because NeMo's
English pronunciation step (`EnglishG2p`) imports it.

**What keeps it out of reach:** Friday's code never imports NLTK. Nothing in
Friday passes NLTK, or NeMo's pronunciation step, a path that a user, a setting
or a document supplies.

**Re-check when:** NLTK publishes a fixed release, or Friday imports NLTK
directly.
