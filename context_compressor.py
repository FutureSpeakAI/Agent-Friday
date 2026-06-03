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

# Rough chars-per-token estimate used only to decide whether a payload is big
# enough to be worth compressing. Token-accurate counting is Headroom's job.
_CHARS_PER_TOKEN = 4


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
        self._stats = {
            'calls': 0,            # successful compression calls
            'tokens_saved': 0,     # cumulative tokens eliminated
            'tokens_before': 0,    # cumulative input tokens seen
            'tokens_after': 0,     # cumulative output tokens produced
            'compression_ratio': 0.0,  # overall saved / before (0.0 – 1.0)
            'last_ratio': 0.0,     # ratio of the most recent call
            'errors': 0,           # compression attempts that fell back
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

    def compress(self, messages, model='claude-opus-4-7'):
        """Compress messages using Headroom before sending to Anthropic.

        Returns the compressed messages list. On any failure (import error,
        compression error, unexpected return shape) the ORIGINAL messages are
        returned unchanged — compression is never allowed to break a chat.
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
        # Prefer Headroom's own (tiktoken-accurate) accounting; fall back to our
        # cheap char-based estimate only when the result doesn't expose counts.
        before_tokens = self._coerce_int(getattr(result, 'tokens_before', None), est_before)
        after_tokens = self._coerce_int(
            getattr(result, 'tokens_after', None),
            self._estimate_tokens(compressed),
        )
        saved = self._coerce_int(getattr(result, 'tokens_saved', None),
                                 max(0, before_tokens - after_tokens))

        # Roll the stats forward.
        self._stats['calls'] += 1
        self._stats['tokens_before'] += before_tokens
        self._stats['tokens_after'] += after_tokens
        self._stats['tokens_saved'] += saved
        tb = self._stats['tokens_before']
        self._stats['compression_ratio'] = (self._stats['tokens_saved'] / tb) if tb else 0.0
        self._stats['last_ratio'] = (saved / before_tokens) if before_tokens else 0.0

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
        s['available'] = self._headroom is not None and not self._import_failed
        return s

    # ── Internals ───────────────────────────────────────────────────────

    def _load_headroom(self):
        """Import `headroom.compress` on first use; cache the result (or the failure)."""
        if self._headroom is not None:
            return self._headroom
        if self._import_failed:
            return None
        try:
            from headroom import compress
            self._headroom = compress
            return compress
        except Exception as exc:
            self._import_failed = True
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
