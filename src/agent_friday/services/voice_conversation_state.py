"""A small running picture of the conversation, so voice sizes and aims its answers.

A person talking to someone keeps track, without thinking about it, of what the
other person cares about right now, how much they want to hear, and what is
still open. A live voice model left to itself falls back on short answers and
"one, two or three things" lists however the conversation goes. The bridge
keeps that picture instead and shows it to the model as a one-line note joined
to his next turn:

- priorities: the topics he is on, weighted by how recently and how often he
  raises them. Weights decay every turn, so priorities shift as the
  conversation moves, and a topic he keeps coming back to rises.
- depth: "brief", "normal" or "deep". Explicit asks ("tell me more", "walk me
  through it"), why/how questions, follow-ups on the same topic and a topic he
  keeps returning to push it up; brevity cues ("keep it short", "bottom line")
  set it to brief, and they win until he asks for more.
- open threads: questions he asked that no reply has covered yet.

The model can refine the picture itself (the note_conversation_state tool);
what it says is merged in.
"""
from __future__ import annotations

import re

_STOP = {"the", "and", "for", "with", "that", "this", "what", "when", "where", "who",
         "why", "how", "does", "did", "was", "were", "you", "your", "our", "about",
         "have", "has", "had", "from", "they", "them", "then", "there", "their", "just",
         "like", "really", "okay", "yeah", "tell", "more", "some", "any", "can", "could",
         "would", "should", "will", "want", "know", "think", "going", "get", "got",
         "friday", "please", "thanks", "thank", "also", "into", "over", "much", "very",
         "mean", "said", "say", "go", "on", "it", "is", "are", "me", "my", "do"}

DEEP_CUES = re.compile(
    r"(?i)\b(tell me more|more about|go deeper|in (?:more )?detail|walk me through|explain|"
    r"elaborate|dig into|keep going|go on|unpack|break (?:it|that) down|what do you mean|"
    r"how come|the whole story|the full picture|why (?:is|are|do|does|did|would|not)|"
    r"how (?:does|do|did|would|is|are|can))\b")
BRIEF_CUES = re.compile(
    r"(?i)\b(keep it short|short version|just the headline|bottom line|quickly|in a word|"
    r"briefly|tl;?dr|one sentence|quick answer|nutshell|just tell me|skip the details)\b")
_DECAY = 0.7
#: A topic he raised in this many of the last _RETURN_WINDOW turns is one he
#: keeps coming back to.
_RETURN_TURNS = 3
_RETURN_WINDOW = 8


def new_state() -> dict:
    return {"weights": {}, "depth": "normal", "open": [], "turns": 0, "last_terms": [],
            "mentions": {}, "model_note": ""}


def _terms(text: str) -> list:
    return [w for w in re.findall(r"[a-z][a-z'-]{3,}", str(text or "").lower()) if w not in _STOP]


def update(state: dict, user_text: str, agent_text: str = "") -> dict:
    """Fold one completed turn into the state. Returns the same dict."""
    s = state if isinstance(state, dict) and "weights" in state else new_state()
    s["turns"] = int(s.get("turns") or 0) + 1
    terms = _terms(user_text)
    w = {k: v * _DECAY for k, v in (s.get("weights") or {}).items() if v * _DECAY >= 0.2}
    for t in set(terms):
        w[t] = w.get(t, 0.0) + 1.0
    s["weights"] = w

    turn = s["turns"]
    mentions = {k: [n for n in v if n > turn - _RETURN_WINDOW]
                for k, v in (s.get("mentions") or {}).items()}
    for t in set(terms):
        mentions.setdefault(t, []).append(turn)
    s["mentions"] = {k: v for k, v in mentions.items() if v}
    follow_up = bool(set(terms) & set(s.get("last_terms") or []))
    returning = any(len(s["mentions"].get(t, [])) >= _RETURN_TURNS for t in set(terms))
    u = str(user_text or "")
    if BRIEF_CUES.search(u):
        s["depth"] = "brief"
    elif DEEP_CUES.search(u) or returning:
        s["depth"] = "deep"
    elif follow_up and s["depth"] == "normal":
        s["depth"] = "deep"
    elif not terms or len(u.split()) <= 4:
        # A quick back-and-forth ("ok", "sure", "what time is it") keeps it short,
        # unless he has asked for depth on this topic and not said otherwise.
        if s["depth"] == "deep" and not follow_up:
            s["depth"] = "normal"
    s["last_terms"] = terms

    # Open threads: his questions that no reply has touched yet.
    covered = str(agent_text or "").lower()
    still_open = [q for q in (s.get("open") or [])
                  if not any(t in covered for t in _terms(q)[:3])]
    if "?" in u and terms and not any(t in covered for t in terms[:3]):
        still_open.append(u.strip()[:120])
    s["open"] = still_open[-4:]
    return s


def merge_model_note(state: dict, note: dict) -> dict:
    """What the model itself says about the conversation, merged in."""
    s = state if isinstance(state, dict) and "weights" in state else new_state()
    note = note or {}
    d = str(note.get("depth") or "").strip().lower()
    if d in ("brief", "normal", "deep"):
        s["depth"] = d
    for p in (note.get("priorities") or [])[:5]:
        for t in _terms(p) or [str(p).lower().strip()]:
            if t:
                s["weights"][t] = max(s["weights"].get(t, 0.0), 1.5)
    if note.get("open_threads") is not None:
        s["open"] = [str(x).strip()[:120] for x in (note.get("open_threads") or []) if str(x).strip()][:4]
    return s


def priorities(state: dict, n: int = 3) -> list:
    w = (state or {}).get("weights") or {}
    return [t for t, _v in sorted(w.items(), key=lambda kv: -kv[1])[:n]]


_DEPTH_WORDS = {
    "deep": ("they want depth right now: give the substance room, in "
             "connected paragraphs that build on each other, with the reasons, a concrete "
             "example and what it means for them, the way a person explains; no list unless "
             "they asked for options or steps"),
    "normal": "a natural, complete answer at the depth the subject deserves; no sentence quota or reflexive list",
    "brief": "they want it short: a sentence or two, and nothing extra",
}


#: The note's opening words. The routed speaker's lead never says "the user":
#: a small model reading it narrates "the user's calendar".
NOTE_LEAD = "[Not from the user, do not read this aloud; the conversation so far: "
SPEAKER_NOTE_LEAD = "[A note for you, never to be read aloud; the conversation so far: "


def render(state: dict, lead: str = NOTE_LEAD) -> str:
    """The one-line note the model sees (never read aloud)."""
    s = state or new_state()
    bits = []
    pr = priorities(s)
    if pr:
        bits.append("what they care about right now: " + ", ".join(pr))
    bits.append("how much to say: " + _DEPTH_WORDS.get(s.get("depth"), _DEPTH_WORDS["normal"]))
    if s.get("open"):
        bits.append("still open from earlier: " + " | ".join(s["open"]))
    return lead + "; ".join(bits) + ".]"


def signature(state: dict) -> tuple:
    """What changed enough to be worth a new note."""
    s = state or {}
    return (tuple(priorities(s)), s.get("depth"), tuple(s.get("open") or []))
