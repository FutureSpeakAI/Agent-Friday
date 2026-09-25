"""Headroom-powered context compression for Friday Desktop.

Context compression powered by Headroom
https://github.com/chopratejas/headroom
Created by Tejas Chopra — Apache 2.0 License

Headroom compresses tool outputs, logs, files, and RAG chunks before
they reach the LLM. 60-95% fewer tokens, same answers.

────────────────────────────────────────────────────────────────────────────
Where this sits in Friday's pipeline:

    build messages → context_pruner (selects WHICH turns to keep)
                   → context_compressor (compresses the CONTENT of those turns)
                   → Anthropic API

The pruner picks the semantically relevant turns; Headroom then squeezes the
JSON tool outputs, code, and prose inside those turns. The savings compound.

Everything here is best-effort: if the Headroom import fails or a compression
call errors, we fall back to the original, uncompressed messages so a chat is
never blocked on compression.
"""

import os

# Rough chars-per-token estimate used only to decide whether a payload is big
# enough to be worth compressing. Token-accurate counting is Headroom's job.
_CHARS_PER_TOKEN = 4

#: The Headroom release the pins name (requirements.txt, pyproject.toml, the
#: Windows installer) and the suite runs against.
TESTED_HEADROOM_VERSION = "0.38.0"

# Attempts after which a Headroom that has compressed nothing stops claiming
# to be available. Each attempt is a payload large enough to be worth
# compressing (see `should_compress`), so three with no saving at all is a
# broken compressor, not an unlucky run.
_NOTHING_COMPRESSED_AFTER = 3


def _pin_headroom_environment():
    """Settings Headroom reads at import, fixed before it is imported.

    Nothing Headroom does may leave this machine except the tokenizer
    vocabulary download below. Headroom 0.3x has three separate switches:

    * HEADROOM_BEACON=off and DO_NOT_TRACK=1 -- the UPLOAD beacon (an
      anonymous session summary sent to Headroom Labs). It is ON by default
      upstream; either variable turns it off, and both are set.
    * HEADROOM_TELEMETRY=off (and the older HEADROOM_TELEMETRY_DISABLED=1) --
      local aggregate stats for Headroom's own /stats endpoint. They never
      leave the machine and Friday does not use them.
    * HEADROOM_OTEL_METRICS_ENABLED=false -- OpenTelemetry metrics export
      (off upstream unless configured; pinned so a stray environment cannot
      turn it on). HEADROOM_TELEMETRY_WARN=off drops its startup notice.
    * HEADROOM_CCR_BACKEND=memory -- Headroom 0.3x keeps the ORIGINAL
      uncompressed content so it can be retrieved later, and by default in a
      plaintext SQLite file under ~/.headroom. That would put vault reads and
      local-only tool output on disk outside the vault. In memory it lives
      only as long as this process.
    * HEADROOM_WORKSPACE_DIR -- anything else it writes stays in Friday's home.
    * TIKTOKEN_CACHE_DIR -- the tokenizer vocabulary is cached in Friday's
      home, so once present it is never fetched again.
    """
    os.environ["HEADROOM_BEACON"] = "off"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["HEADROOM_TELEMETRY"] = "off"
    os.environ["HEADROOM_TELEMETRY_DISABLED"] = "1"
    os.environ["HEADROOM_OTEL_METRICS_ENABLED"] = "false"
    os.environ["HEADROOM_TELEMETRY_WARN"] = "off"
    os.environ["HEADROOM_CCR_BACKEND"] = "memory"
    try:
        from agent_friday.paths import friday_home
        home = friday_home()
        os.environ.setdefault("HEADROOM_WORKSPACE_DIR", str(home / "headroom"))
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(home / "cache" / "tiktoken"))
    except Exception:
        pass


class ContextCompressor:
    """Headroom-powered context compression for Friday Desktop.

    Uses the Headroom library (https://github.com/chopratejas/headroom)
    by Tejas Chopra — Apache 2.0 license.

    Compresses tool outputs, JSON, code, and prose in the message history
    before sending to the LLM. 60-95% fewer tokens, same answer quality.
    """

    def __init__(self, enabled=True, min_tokens_to_compress=1000):
        self._enabled = bool(enabled)
        self._min_tokens = int(min_tokens_to_compress)
        self._headroom = None        # lazy-loaded `compress` callable
        self._import_failed = False  # don't retry a broken import every call
        self._unavailable_reason = None
        self._version = None
        self._stats = {
            'calls': 0,            # successful compression calls
            'tokens_saved': 0,     # cumulative tokens eliminated
            'tokens_before': 0,    # cumulative input tokens seen
            'tokens_after': 0,     # cumulative output tokens produced
            'compression_ratio': 0.0,  # overall saved / before (0.0 – 1.0)
            'last_ratio': 0.0,     # ratio of the most recent call
            'errors': 0,           # compression attempts that fell back
            'passthrough': 0,      # calls Headroom returned without compressing
            'by_seat': {},         # "local" | "cloud" -> compressions
        }

    # ── Construction from settings ──────────────────────────────────────

    @classmethod
    def from_settings(cls, cfg):
        """Build a compressor from a ~/.friday/settings.json `context_compression` block."""
        cfg = cfg or {}
        return cls(
            enabled=cfg.get('enabled', True),
            min_tokens_to_compress=cfg.get('min_tokens_to_compress', 1000),
        )

    def configure(self, cfg):
        """Update thresholds in place (keeps the lazily-loaded Headroom handle)."""
        cfg = cfg or {}
        self._enabled = bool(cfg.get('enabled', self._enabled))
        self._min_tokens = int(cfg.get('min_tokens_to_compress', self._min_tokens))
        return self

    # ── Public API ──────────────────────────────────────────────────────

    def should_compress(self, messages):
        """True if compression is enabled and the payload is large enough to bother.

        We only compress when the estimated token count clears the configured
        floor — compressing a tiny message list costs more (latency, a model
        round-trip inside Headroom) than it saves.
        """
        if not self._enabled or not messages:
            return False
        return self._estimate_tokens(messages) >= self._min_tokens

    def compress(self, messages, model='claude-opus-5-5', seat=None):
        """Compress messages using Headroom before they go to the model.

        Returns the compressed messages list. On any failure (import error,
        compression error, unexpected return shape) the ORIGINAL messages are
        returned unchanged — compression is never allowed to break a chat.
        A call Headroom returns without compressing counts as a pass-through,
        not as a compression. `seat` ("local" / "cloud") labels the stats.
        """
        if not self._enabled or not messages:
            return messages

        compress_fn = self._load_headroom()
        if compress_fn is None:
            return messages

        est_before = self._estimate_tokens(messages)
        try:
            result = compress_fn(messages, model=model)
        except Exception as exc:
            self._stats['errors'] += 1
            print(f"  [HEADROOM] compression failed, using uncompressed messages: {exc}")
            return messages

        compressed = self._extract_messages(result, fallback=messages)
        if compressed is messages and not getattr(result, 'tokens_before', 0):
            # Headroom handed the input back untouched (the 0.20.15 wheel does
            # this when its native core is missing). Not a compression.
            self._stats['passthrough'] += 1
            return messages
        # Prefer Headroom's own (tiktoken-accurate) accounting; fall back to our
        # cheap char-based estimate only when the result doesn't expose counts.
        before_tokens = self._coerce_int(getattr(result, 'tokens_before', None), est_before)
        after_tokens = self._coerce_int(
            getattr(result, 'tokens_after', None),
            self._estimate_tokens(compressed),
        )
        saved = self._coerce_int(getattr(result, 'tokens_saved', None),
                                 max(0, before_tokens - after_tokens))
        if saved <= 0:
            # Counts reported, nothing removed: still a pass-through, however
            # the result is shaped. Only a saving is a compression.
            self._stats['passthrough'] += 1
            return messages

        # Roll the stats forward.
        self._stats['calls'] += 1
        self._stats['tokens_before'] += before_tokens
        self._stats['tokens_after'] += after_tokens
        self._stats['tokens_saved'] += saved
        tb = self._stats['tokens_before']
        self._stats['compression_ratio'] = (self._stats['tokens_saved'] / tb) if tb else 0.0
        self._stats['last_ratio'] = (saved / before_tokens) if before_tokens else 0.0
        if seat:
            self._stats['by_seat'][seat] = self._stats['by_seat'].get(seat, 0) + 1

        pct = round(self._stats['last_ratio'] * 100)
        # Keep the required "{before} → {after}" log line, but never let a console
        # that can't encode the arrow (Windows cp1252) crash compression — that
        # would discard a successful result and silently disable the feature.
        try:
            print(f"Headroom compressed: {before_tokens} → {after_tokens} tokens ({pct}% saved)")
        except UnicodeEncodeError:
            print(f"Headroom compressed: {before_tokens} -> {after_tokens} tokens ({pct}% saved)")
        return compressed

    def get_stats(self):
        """Return compression statistics."""
        s = dict(self._stats)
        s['enabled'] = self._enabled
        s['min_tokens_to_compress'] = self._min_tokens
        if self._headroom is None and not self._import_failed and self._enabled:
            self._load_headroom()      # answer "is it available?" truthfully
        s['available'] = self._headroom is not None and not self._import_failed
        attempts = self._stats['passthrough'] + self._stats['errors']
        if s['available'] and not self._stats['calls']                 and attempts >= _NOTHING_COMPRESSED_AFTER:
            # Loaded, called, and never once made anything smaller. Saying
            # "available" here is how 0.20.15 looked like a working 0%.
            s['available'] = False
            s['reason'] = (
                "headroom-ai %s loaded but compressed nothing in %d attempts "
                "(%d returned unchanged, %d failed)" % (
                    self._version or "?", attempts,
                    self._stats['passthrough'], self._stats['errors']))
        s['by_seat'] = dict(self._stats['by_seat'])
        if self._unavailable_reason:
            s['reason'] = self._unavailable_reason
        if self._version:
            s['version'] = self._version
        return s

    def compress_new(self, messages, start, model='claude-opus-5-5', seat=None):
        """Compress only messages[start:] (what a round just added) and return
        the whole list. Earlier messages were compressed when they were new;
        compressing them again every round costs time and changes a prefix
        the provider may be caching."""
        if not self._enabled or start >= len(messages or []):
            return messages
        new = list(messages[start:])
        if not self.should_compress(new):
            return messages
        out = self.compress(new, model=model, seat=seat)
        if out is new:
            return messages
        return list(messages[:start]) + list(out)

    # ── Internals ───────────────────────────────────────────────────────

    def _load_headroom(self):
        """Import `headroom.compress` on first use; cache the result (or the failure)."""
        if self._headroom is not None:
            return self._headroom
        if self._import_failed:
            return None
        _pin_headroom_environment()
        try:
            import headroom as _hr
            self._version = getattr(_hr, '__version__', None)
        except Exception as exc:
            self._import_failed = True
            self._unavailable_reason = "headroom-ai is not installed (%s)" % exc
            print(f"  [HEADROOM] library unavailable, compression disabled: {exc}")
            return None
        try:
            # Without its native core Headroom's compress() quietly returns
            # its input, which read as a working compressor saving 0%.
            import headroom._core  # noqa: F401
        except Exception:
            self._import_failed = True
            self._unavailable_reason = (
                "headroom-ai %s is installed without its native core "
                "(headroom._core), so it cannot compress" % (self._version or "?"))
            print(f"  [HEADROOM] {self._unavailable_reason}")
            return None
        try:
            from headroom import compress
            self._headroom = compress
            return compress
        except Exception as exc:
            self._import_failed = True
            self._unavailable_reason = "headroom-ai failed to load (%s)" % exc
            print(f"  [HEADROOM] library unavailable, compression disabled: {exc}")
            return None

    @staticmethod
    def _extract_messages(result, fallback):
        """Pull the compressed message list out of Headroom's result object.

        Headroom returns a result object exposing `.messages`; we also accept a
        bare list defensively, and fall back to the originals on anything else.
        """
        msgs = getattr(result, 'messages', None)
        if msgs is None and isinstance(result, list):
            msgs = result
        if isinstance(msgs, list) and msgs:
            return msgs
        return fallback

    @staticmethod
    def _coerce_int(value, fallback):
        """Return value as a non-negative int, or fallback if it isn't a usable number."""
        if isinstance(value, bool):
            return fallback
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
        return fallback

    @classmethod
    def _estimate_tokens(cls, messages):
        """Cheap char-based token estimate over the string content of a message list."""
        chars = 0
        for m in messages or []:
            content = m.get('content') if isinstance(m, dict) else None
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, list):
                # Anthropic content blocks: sum any text fields.
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get('text'), str):
                        chars += len(block['text'])
        return chars // _CHARS_PER_TOKEN
