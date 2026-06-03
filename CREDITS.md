# Credits & Acknowledgments

Friday Desktop stands on the shoulders of open-source work. This file records the
third-party libraries and projects that materially shape Friday's architecture.

## Third-Party Libraries & Inspirations

### Headroom — Context Compression
- **Repository:** https://github.com/chopratejas/headroom
- **Author:** Tejas Chopra
- **License:** Apache 2.0
- **Used for:** Compressing tool outputs, JSON, code, and prose in conversation context before sending to LLMs
- **Impact:** 60-95% token reduction on tool outputs with preserved answer quality

In Friday's chat pipeline, Headroom is the compression layer beneath the semantic
context pruner (`context_pruner.py`). The pruner selects *which* conversation turns
to keep via embedding retrieval; Headroom then compresses the *content* of those
turns. The two compound: prune selects, Headroom squeezes. Friday's wrapper lives in
`context_compressor.py`, and savings are exposed at `GET /api/compression-stats`.

**Build note (native core).** Headroom's heavy transforms are implemented in a
compiled Rust extension, `headroom._core` — a hard import with no Python fallback.
The plain `pip install "headroom-ai[all]"` builds it from source, which requires a
Rust toolchain and, on Windows, the MSVC build tools (`cl.exe`/`link.exe` from
Visual Studio Build Tools). If the core isn't present, Headroom's pipeline falls
back to passing messages through unchanged. Friday is built for exactly this: the
wrapper imports lazily and degrades gracefully, so a missing or unbuildable core
never breaks a chat — and full compression activates automatically, with no code
change, the moment `headroom._core` becomes importable.
