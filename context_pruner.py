"""Semantic context pruning for long-running agent conversations.

When a conversation grows past a threshold we stop truncating from the oldest
turn (which silently drops whatever the user mentioned early on) and instead run
embedding-based retrieval over the conversation's own history — RAG over your
own chat. The turns most semantically relevant to the *current* prompt are kept;
the rest are dropped for this one API call while the full history stays intact in
the session so future turns can still retrieve from the complete archive.

Design notes:
  * Lazy model load — the sentence-transformer is only imported/instantiated on
    the first call to prune(), so server startup stays fast.
  * Embedding cache keyed by content hash — past turns never change, so each one
    is embedded at most once across the process lifetime.
  * Scoring uses the *user* side of each turn (what was asked), but the retained
    context includes both the user message and the assistant reply.
  * System messages are sacred — never scored, never dropped, always kept first.
"""

import hashlib

import numpy as np

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class ContextPruner:
    """Semantic context pruning for long-running agent conversations."""

    def __init__(self, model_name=DEFAULT_MODEL, max_turns=50, keep_recent=4, top_k=10):
        """
        model_name: sentence-transformer model for embeddings
        max_turns:  threshold before pruning kicks in (number of message pairs)
        keep_recent: always keep this many recent turn pairs verbatim
        top_k:      number of semantically relevant archived turns to retrieve
        """
        self.model_name = model_name or DEFAULT_MODEL
        self.max_turns = int(max_turns)
        self.keep_recent = int(keep_recent)
        self.top_k = int(top_k)
        self._model = None          # lazily loaded on first prune()
        self._cache = {}            # content-hash -> embedding (np.ndarray)

    # ── Public API ──────────────────────────────────────────────────────

    @classmethod
    def from_settings(cls, cfg):
        """Build a pruner from a ~/.friday/settings.json `context_pruning` block."""
        cfg = cfg or {}
        return cls(
            model_name=cfg.get("model", DEFAULT_MODEL),
            max_turns=cfg.get("max_turns", 50),
            keep_recent=cfg.get("keep_recent", 4),
            top_k=cfg.get("top_k", 10),
        )

    def configure(self, cfg):
        """Update thresholds in place (keeps the loaded model + embedding cache)."""
        cfg = cfg or {}
        self.max_turns = int(cfg.get("max_turns", self.max_turns))
        self.keep_recent = int(cfg.get("keep_recent", self.keep_recent))
        self.top_k = int(cfg.get("top_k", self.top_k))
        # A model swap can't reuse cached vectors — invalidate if it changed.
        new_model = cfg.get("model", self.model_name)
        if new_model != self.model_name:
            self.model_name = new_model
            self._model = None
            self._cache = {}
        return self

    def should_prune(self, messages):
        """Returns True if the message list exceeds the threshold."""
        body = [m for m in messages if m.get("role") != "system"]
        turns = len(body) // 2
        return turns > self.max_turns

    def prune(self, messages, current_prompt):
        """Keep the semantically relevant turns instead of dropping the oldest.

        1. Always keep: system message(s) at the start
        2. Always keep: the last `keep_recent` turn pairs + the current prompt
        3. From the remaining "archive" turns, embed each (and the current prompt)
        4. Score by cosine similarity
        5. Take the top_k most relevant archived turns
        6. Sort the selected archived turns chronologically
        7. Reassemble: system + top_k_archived + recent + current_prompt
        8. Return the pruned message list
        """
        system_msgs = [m for m in messages if m.get("role") == "system"]
        body = [m for m in messages if m.get("role") != "system"]
        if not body:
            return list(messages)

        # The just-appended current turn rides at the very end, untouched.
        current_msg = body[-1]
        history = body[:-1]

        keep_n = self.keep_recent * 2
        recent = history[-keep_n:] if keep_n else []
        archive = history[:-keep_n] if keep_n else list(history)

        # Group the archive into (user, assistant) turn pairs, preserving order.
        pairs = []
        i = 0
        while i < len(archive):
            if i + 1 < len(archive):
                pairs.append((i, [archive[i], archive[i + 1]]))
                i += 2
            else:
                pairs.append((i, [archive[i]]))
                i += 1

        if not pairs:
            # Nothing archived to retrieve from — leave the list as-is.
            return list(system_msgs) + list(recent) + [current_msg]

        query_emb = self._embed(current_prompt or "")
        scored = []
        for idx, pair in pairs:
            text = self._pair_query_text(pair)
            sim = self._similarity(query_emb, self._embed(text)) if text else -1.0
            scored.append((sim, idx, pair))

        # Top-k by relevance, then re-sort chronologically so the kept turns read
        # in the order they actually happened.
        top = sorted(scored, key=lambda s: s[0], reverse=True)[: self.top_k]
        top.sort(key=lambda s: s[1])

        archived_msgs = []
        for _sim, _idx, pair in top:
            archived_msgs.extend(pair)

        return list(system_msgs) + archived_msgs + list(recent) + [current_msg]

    # ── Internals ───────────────────────────────────────────────────────

    @staticmethod
    def _pair_query_text(pair):
        """The text used to score a turn pair — prefer the user side."""
        for m in pair:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                return m["content"]
        # Fall back to any string content in the pair.
        return " ".join(
            m.get("content", "") for m in pair if isinstance(m.get("content"), str)
        )

    def _get_model(self):
        """Load the sentence-transformer model on first use (not at import)."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _embed(self, text):
        """Embed a single text string, with caching keyed by content hash."""
        text = text or ""
        key = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        emb = self._get_model().encode(
            text, convert_to_numpy=True, normalize_embeddings=True
        )
        emb = np.asarray(emb, dtype="float32")
        self._cache[key] = emb
        return emb

    @staticmethod
    def _similarity(emb1, emb2):
        """Cosine similarity between two embeddings."""
        a = np.asarray(emb1, dtype="float32")
        b = np.asarray(emb2, dtype="float32")
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))
