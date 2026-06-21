"""Cross-session emotional arc for Agent Friday — a lightweight, local-only read
on how the user has been *feeling* over time, so Friday can adapt its tone.

This is deliberately small and dependency-free:

  • score_sentiment(text)  — a word-boundary lexicon scorer over conversational
                             emotion words. Returns (score in [-1, 1], label).
                             Distinct from news_engine._article_sentiment, which
                             is tuned for headlines (win/loss/crash), not how a
                             person sounds in chat (thanks/frustrated/love/ugh).
  • EmotionalArc           — persists a rolling exponential-moving-average of the
                             user's sentiment to ~/.friday/memory/emotional_arc.json
                             plus a short recent-window and per-day rollups, and
                             derives prompt-ready tone guidance from it.

Design rules mirror conversation_memory.py:
  • Degrade gracefully. Every method is best-effort and never raises into the
    chat path; a missing/corrupt state file just resets to neutral.
  • Lazy + cheap. No model, no network — pure regex over the message text.
  • Local only. Sentiment runs on-device; nothing here is sent to a provider.
    Only the *derived* tone guidance (a generic instruction, no message text)
    is appended to the system prompt.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"
MEMORY_DIR = FRIDAY_DIR / "memory"
DEFAULT_STATE_FILE = MEMORY_DIR / "emotional_arc.json"

# EMA smoothing: how much each new turn moves the accumulated mood. Lower =
# steadier (one grumpy message won't flip Friday's whole posture); higher =
# more reactive. 0.25 ≈ "the last ~4 turns dominate".
EMA_ALPHA = 0.25
# Buckets on the [-1, 1] EMA. Tuned so it takes a sustained lean, not a single
# turn, to leave neutral.
POS_THRESHOLD = 0.18
NEG_THRESHOLD = -0.18
RECENT_WINDOW = 30          # how many recent turns to keep for trend/inspection

# ── Conversational sentiment lexicon ─────────────────────────────────
# Word-boundary anchored so "thanks" matches but "thanksgiving" doesn't drag.
# Multi-word phrases are matched separately below. This is intentionally coarse
# — it only needs to be directionally right in aggregate, not per-message exact.
_POS_WORDS = re.compile(
    r"\b(thank|thanks|thankyou|great|greatly|awesome|love|loved|loving|perfect|"
    r"perfectly|excellent|amazing|wonderful|happy|glad|appreciate|appreciated|"
    r"nice|good|helpful|brilliant|fantastic|excited|exciting|cool|yay|woohoo|"
    r"proud|relieved|better|best|fun|enjoy|enjoyed|enjoying|pleased|delighted|"
    r"grateful|wow|nailed|works|working|solved|fixed|clear|clever|smart|"
    r"beautiful|elegant|smooth|win|wins)\b", re.I)
_NEG_WORDS = re.compile(
    r"\b(frustrat\w*|annoy\w*|angry|mad|hate|hated|terrible|awful|broken|wrong|"
    r"useless|stupid|dumb|bad|fail|failed|failing|failure|confus\w*|worried|"
    r"worry|anxious|stress\w*|sad|upset|disappoint\w*|ugh|damn|crap|hell|stuck|"
    r"problem|problems|issue|issues|error|errors|bug|bugs|buggy|slow|sluggish|"
    r"worse|worst|hard|difficult|impossible|nightmare|hopeless|tired|exhausted|"
    r"sick|fed up|garbage|junk|sucks|suck|pointless|wtf)\b", re.I)
# Phrases carry more signal than their parts — weight them double.
_POS_PHRASES = re.compile(
    r"(thank you|thanks so much|that works|works now|much better|good job|"
    r"well done|love it|love this|you're the best|that's perfect|so helpful)",
    re.I)
_NEG_PHRASES = re.compile(
    r"(does ?n'?t work|not working|did ?n'?t work|still broken|still not|"
    r"this is broken|come on|are you kidding|that's wrong|you keep|i'?m "
    r"frustrated|i give up|never mind|forget it|that's not what)", re.I)


def score_sentiment(text):
    """Return (score, label) for a chat message.

    score  float in [-1, 1] — positive minus negative, normalised by length so a
           long balanced message doesn't read as strongly polarised.
    label  'positive' | 'neutral' | 'negative'

    Empty / non-text input scores 0.0 / 'neutral'.
    """
    s = (text or "").strip()
    if not s:
        return 0.0, "neutral"
    pos = len(_POS_WORDS.findall(s)) + 2 * len(_POS_PHRASES.findall(s))
    neg = len(_NEG_WORDS.findall(s)) + 2 * len(_NEG_PHRASES.findall(s))
    if pos == 0 and neg == 0:
        return 0.0, "neutral"
    # Normalise by the total emotional-word count so the score is a *balance*
    # in [-1, 1], not an unbounded count.
    raw = (pos - neg) / float(pos + neg)
    if raw > 0.15:
        label = "positive"
    elif raw < -0.15:
        label = "negative"
    else:
        label = "neutral"
    return round(raw, 4), label


class EmotionalArc:
    """Persistent, accumulated read on the user's emotional state across sessions.

    Thread-safe for the server's access pattern (background-thread writes from the
    chat hot path, request-thread reads). All methods are best-effort; on any I/O
    or parse failure the arc resets to a neutral in-memory state rather than
    raising.
    """

    def __init__(self, state_file=None):
        self.state_file = Path(state_file) if state_file else DEFAULT_STATE_FILE
        self._lock = threading.Lock()
        self._state = None  # lazy-loaded

    # ── persistence ──────────────────────────────────────────────────
    def _load(self):
        if self._state is not None:
            return self._state
        try:
            self._state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(self._state, dict):
                raise ValueError("state is not an object")
        except Exception:
            self._state = {"ema": 0.0, "count": 0, "recent": [],
                           "daily": {}, "updated": None}
        # Defensive defaults for older/partial files.
        self._state.setdefault("ema", 0.0)
        self._state.setdefault("count", 0)
        self._state.setdefault("recent", [])
        self._state.setdefault("daily", {})
        return self._state

    def _save(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(self._state, indent=2), encoding="utf-8")
        except Exception as e:  # best-effort — never raise into chat
            print(f"  [ARC] emotional-arc save skipped (non-fatal): {e}")

    # ── writes ───────────────────────────────────────────────────────
    def record(self, text, timestamp=None, session_id=None):
        """Score one USER message and fold it into the accumulated arc.

        Returns the per-message {score, label} (also useful for tagging the
        turn). No-ops to a neutral result if scoring or persistence fails.
        """
        try:
            score, label = score_sentiment(text)
            # A purely neutral turn (no emotional words at all) shouldn't pull the
            # EMA toward zero — that would wash out a real mood over a few factual
            # questions. Record it for the trend log but leave the EMA untouched.
            ts = timestamp or datetime.now().isoformat()
            with self._lock:
                st = self._load()
                has_signal = bool(text and (label != "neutral" or score != 0.0))
                if has_signal:
                    st["ema"] = round(
                        EMA_ALPHA * score + (1.0 - EMA_ALPHA) * float(st["ema"]), 4)
                st["count"] = int(st.get("count", 0)) + 1
                st["updated"] = ts
                rec = {"ts": ts, "date": ts[:10], "score": score, "label": label}
                if session_id:
                    rec["session_id"] = str(session_id)
                st["recent"].append(rec)
                if len(st["recent"]) > RECENT_WINDOW:
                    st["recent"] = st["recent"][-RECENT_WINDOW:]
                # Per-day rollup (running average + counts) for the dossier/insights.
                day = st["daily"].setdefault(
                    ts[:10], {"sum": 0.0, "count": 0, "pos": 0, "neg": 0})
                day["sum"] = round(float(day["sum"]) + score, 4)
                day["count"] = int(day["count"]) + 1
                if label == "positive":
                    day["pos"] = int(day.get("pos", 0)) + 1
                elif label == "negative":
                    day["neg"] = int(day.get("neg", 0)) + 1
                # Cap the daily history so the file can't grow without bound.
                if len(st["daily"]) > 120:
                    for k in sorted(st["daily"])[:-120]:
                        st["daily"].pop(k, None)
                self._save()
            return {"score": score, "label": label}
        except Exception as e:
            print(f"  [ARC] record skipped (non-fatal): {e}")
            return {"score": 0.0, "label": "neutral"}

    # ── reads ────────────────────────────────────────────────────────
    def _mood_label(self, ema):
        if ema >= POS_THRESHOLD:
            return "positive"
        if ema <= NEG_THRESHOLD:
            return "negative"
        return "neutral"

    def state(self):
        """Return the current accumulated arc as a plain dict.

        {ema, mood, trend, count, recent_count, updated, last_label}
        `trend` is 'rising' | 'falling' | 'steady' over the recent window.
        """
        with self._lock:
            st = self._load()
            ema = float(st.get("ema", 0.0))
            recent = list(st.get("recent", []))
        mood = self._mood_label(ema)
        trend = "steady"
        scored = [r for r in recent if isinstance(r.get("score"), (int, float))]
        if len(scored) >= 4:
            half = len(scored) // 2
            early = sum(r["score"] for r in scored[:half]) / max(half, 1)
            late = sum(r["score"] for r in scored[half:]) / max(len(scored) - half, 1)
            if late - early > 0.12:
                trend = "rising"
            elif early - late > 0.12:
                trend = "falling"
        return {
            "ema": round(ema, 4),
            "mood": mood,
            "trend": trend,
            "count": int(st.get("count", 0)),
            "recent_count": len(recent),
            "updated": st.get("updated"),
            "last_label": (recent[-1].get("label") if recent else "neutral"),
            "available": True,
        }

    def tone_guidance(self):
        """Prompt-ready tone instruction derived from the accumulated state.

        Returns '' when the arc is neutral / too thin to act on — so a brand-new
        or emotionally-flat history adds nothing to the system prompt. The text
        is generic (no message content), safe to send to any provider.
        """
        st = self.state()
        if st["count"] < 3:
            return ""
        mood, trend = st["mood"], st["trend"]
        if mood == "negative":
            extra = ("" if trend != "falling" else
                     " Their mood has been trending more negative within this "
                     "stretch, so tread carefully.")
            return (
                "\n== EMOTIONAL CONTINUITY (adapt your tone) ==\n"
                "Across recent conversations the user has sounded frustrated or "
                "under stress." + extra + " Be especially patient, warm, and "
                "concise. Lead with acknowledging the difficulty, skip the "
                "over-explaining and hedging, and focus on concretely unblocking "
                "them. Do not be relentlessly cheerful — meet them where they "
                "are.\n")
        if mood == "positive":
            return (
                "\n== EMOTIONAL CONTINUITY (adapt your tone) ==\n"
                "Recent conversations with the user have had a warm, upbeat tone. "
                "Match that energy — friendly, collaborative, a little playful is "
                "welcome — while still being substantive.\n")
        # Neutral mood but a clear downward trend is worth a gentle softening.
        if trend == "falling":
            return (
                "\n== EMOTIONAL CONTINUITY (adapt your tone) ==\n"
                "The user's tone has drifted a little more negative recently. Stay "
                "patient and solution-focused, and keep replies tight.\n")
        return ""


# ── process-wide singleton ────────────────────────────────────────────
_instance = None
_instance_lock = threading.Lock()


def get_emotional_arc(state_file=None):
    """Return the process-wide EmotionalArc, building it lazily."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EmotionalArc(state_file=state_file)
    return _instance


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    arc = get_emotional_arc()
    for m in ["thanks, that's perfect!", "ugh this is still broken",
              "why doesn't this work, so frustrated", "what time is it"]:
        print(m, "->", arc.record(m))
    print(json.dumps(arc.state(), indent=2))
    print(arc.tone_guidance())
