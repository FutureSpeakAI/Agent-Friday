"""The plan for one Gemini Live call: what it is told and what it may call.

Beta 1.1 package CV-A (laya_in_every_mode_addendum section 3: LV-1, LV-2, LV-6).

Gemini Live re-sends the whole prompt on EVERY turn, and the prompt was the
full text-chat system prompt plus 91 tool declarations with full descriptions
(about 33K tokens on an empty install, before any audio or history). This
module is the one place that decides, at session start and at each leg
boundary, what goes into that prompt. It is pure planning: it never opens a
socket, never calls a model, and every hook in routes/voice.py and
services/voice_engine.py is a thin call into it that fails open (an exception
here means "send what we sent before", never "send nothing").

One setting, `voice_live_trim`, governs the cap AND the kits:
  off     today's session, byte for byte.
  shadow  (default) the declared instruction and tools are exactly today's. The
          plan still runs and logs, on this PC and without any text, what the
          cap would drop (block names and tokens) and what the kit would drop,
          then whether Gemini later called a dropped tool (tool miss) or asked
          for context that the cap would have dropped (context miss: ask_friday,
          search_wiki, search_past_conversations ... in a session whose cap
          would have dropped context). `shadow_stats` turns the log into the
          two rates; the go threshold for "on" is <= 3% on each.
  on      both are applied.

LV-1  A capped, voice-only instruction (`fit_instruction`).
      The assembled instruction is cut into its "== NAME ==" blocks. Safety and
      voice text is MANDATORY: identity and voice rules, the persona, the cLaws,
      the action-permission policy, honest degradation, the creative policy,
      the vault gating text, the tool-surface note. Mandatory blocks are never
      dropped, whatever the cap. Text-chat-only boilerplate (the text toolbox,
      computer control, package install ...) is not for a voice call and goes
      whenever the cap is on. Then optional context goes, in a fixed order,
      until the instruction fits `voice_live_instruction_max_tokens`.
      Context slices are ordered by slice_head's plan (withheld first). Only
      ever SUBTRACTS: it adds nothing, rewrites nothing, and the egress gate
      still runs on whatever is left (routes/voice._gate_voice_system_instruction).
      If the mandatory floor alone is over the cap, the floor is sent and the
      report says `floor_over_cap`: the cap never wins over safety text.

LV-2  A Laya-chosen tool kit (`choose_kit`), behind `voice_live_tool_kits`.
      Only with voice_live_trim "on" is the kit declared;
      off and shadow declare everything as before.
      on      the kit is declared with compact descriptions. `ask_friday` and
              `more_tools` are always in it. `more_tools(group)` widens the set
              for the NEXT leg; tools change only at a leg boundary.

LV-6  Prefetch and a soft budget.
      `detect_prefetch` reads Live's own input transcription: a person named
      -> that person's recent mail headers; "tomorrow" or a date -> the
      calendar. It only warms local caches that already exist; nothing is sent
      to the model until the model asks. `SessionPlan.on_turn_complete` keeps a
      per-session cost estimate; with `voice_live_soft_budget_usd` > 0 it hands
      back ONE heads-up per call. Nothing here ever cuts an open call.

Token counts here are an estimate (`estimate_tokens`, characters / 3.5, which
over-counts English by about 25% against a BPE tokenizer, so a cap is met with
room to spare). tools/voice_session_tokens.py counts with a real local
tokenizer.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_log = logging.getLogger("friday.live_session_plan")

# ── Settings ────────────────────────────────────────────────────────────────

#: Mirrored in core.DEFAULT_SETTINGS and docs/user-guide/configuration.md.
DEFAULTS = {
    # "off" | "shadow" | "on": governs the cap and the kits together. Shadow
    # sends today's session and logs what trimming would have dropped.
    "voice_live_trim": "shadow",
    # The cap "on" applies; 0 = no cap even when on. The default is
    # from tools/voice_session_tokens.py: the mandatory floor measures about
    # 5.0K estimated tokens (voice rules, persona, cLaws, action policy, honesty
    # text, tool-surface note) and the public spec sheet adds 3.3K, so 9000
    # keeps both and leaves about 0.7K for recalled context; the rest of a
    # populated install's context goes first. The estimate over-counts a BPE
    # tokenizer by about 25%.
    "voice_live_instruction_max_tokens": 9000,
    "voice_live_tool_kit_size": 20,
    # Context-window compression for a call. 0 / 0 = Google's defaults. The
    # trigger is the prompt size at which the oldest turns are folded away; the
    # target is what is kept. They are sized above the static start (the capped
    # instruction + the kit, about 8K) so a call keeps room for its history.
    "voice_live_compression_trigger_tokens": 24000,
    "voice_live_compression_target_tokens": 12000,
    # 0 = off. USD for one call; ONE spoken heads-up, never a cut.
    "voice_live_soft_budget_usd": 0.0,
}

TRIM_MODES = ("off", "shadow", "on")


def _num(settings: dict, key: str, kind=int):
    try:
        v = (settings or {}).get(key)
        v = DEFAULTS[key] if v is None else v
        return kind(v)
    except (TypeError, ValueError):
        return kind(DEFAULTS[key])


def trim_mode(settings: dict) -> str:
    m = str((settings or {}).get("voice_live_trim") or DEFAULTS["voice_live_trim"]).strip().lower()
    return m if m in TRIM_MODES else DEFAULTS["voice_live_trim"]


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (characters / 3.5, rounded up)."""
    return int(math.ceil(len(text or "") / 3.5))


# ── LV-1: the capped, voice-only instruction ────────────────────────────────

_BLANKS = " \t"


def _header_name(line: str) -> Optional[str]:
    """The name of a header line, or None. A header line is "==" or "===", blanks, a name that
    does not start with "=", blanks, the same "==" or "===" again, and blanks to the end:
    "=== MEMORY ===", "==  Tools \t==  ". The name keeps everything between the blanks, so
    "== a == b ==" is "a == b". Read with string operations in one pass over the line."""
    if line.startswith("==="):
        mark = "==="
    elif line.startswith("=="):
        mark = "=="
    else:
        return None
    rest = line[len(mark):]
    body = rest.rstrip(_BLANKS)
    if not body.endswith(mark):
        return None
    end = len(body) - len(mark)                      # where the closing mark starts
    lead = len(rest) - len(rest.lstrip(_BLANKS))     # blanks after the opening mark
    name = rest[lead:end].rstrip(_BLANKS)
    if name and not name.startswith("="):
        return name
    if lead == 0:
        return None
    # The name may not start with "=" and may not be empty, but it may start with a blank:
    # it then takes the last blank after the opening mark ("== ====" is " ==", "==  ==" is " ").
    return rest[lead - 1:end].rstrip(_BLANKS) or rest[lead - 1]


def _header_marks(text: str) -> List[Tuple[int, str]]:
    """(offset of the line, header name) for every header line in ``text``."""
    marks: List[Tuple[int, str]] = []
    pos = 0
    for line in text.split("\n"):
        name = _header_name(line)
        if name is not None:
            marks.append((pos, name))
        pos += len(line) + 1
    return marks

# Mandatory: never dropped, whatever the cap. Matched on the header, upper-cased.
_MANDATORY = re.compile(
    r"ASIMOV|CLAW|ACTION PERMISSION|TAKING ACTIONS|AUTONOMOUS OPERATION|"
    r"HONEST DEGRADATION|^HONESTY|CREATIVE POLICY|VOICE TOOL SURFACE|"
    r"AGENT PERSONALITY|YOUR PERSONALITY|YOUR CHARACTER|RESPONSE PREFERENCES|"
    r"CURRENT MOOD|AUTHORITATIVE CLOCK|CREW ROOM|VAULT|PRIVACY|SAFETY|"
    r"AFFECTIVE|SPOKEN DELIVERY")

# Text-chat-only blocks: they describe a toolbox voice does not have (the
# VOICE TOOL SURFACE note says so outright) or developer-session context.
# Dropped whenever the cap is on.
_TEXT_CHAT_ONLY = re.compile(
    r"^TOOLS$|COMPUTER CONTROL|SELF-IMPROVEMENT|TASK DELEGATION|PACKAGE INSTALLATION|"
    r"WHAT YOU CAN DO ON THIS COMPUTER|CONNECTORS|CAPABILITY DISCOVERY|"
    r"SAY WHAT YOU ARE ABOUT TO DO|PROJECT CONTEXT FILES|CAPABILITY STATE")

# Optional, dropped in this order once the cap bites (earlier = first to go).
_RANKS = (
    (re.compile(r"SELF-KNOWLEDGE"), 20),
    (re.compile(r"THIS SEAT|LEARNED HEURISTICS|USER MODEL"), 40),
    (re.compile(r"ABOUT AGENT FRIDAY|WHAT YOU ACTUALLY ARE|CORE MEMORY"), 50),
    (re.compile(r"HONEST LIMITS"), 60),
)
_CONTEXT_RANK = 30           # everything else: recalled context, tasks, clock

#: slice_head names for the optional context blocks (derive_section_name style).
_SLICE_ORDER = {"withhold": 0, "stub": 1, "include": 2}


@dataclass
class Block:
    name: str
    text: str
    kind: str                    # "mandatory" | "chat_only" | "optional"
    rank: int = 0
    slice: Optional[str] = None


@dataclass
class InstructionReport:
    cap: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    floor_tokens: int = 0
    floor_over_cap: bool = False
    applied: bool = False
    dropped: List[Tuple[str, int, str]] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["dropped"] = [list(x) for x in self.dropped]
        return d


def _section_name(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")[:60]


def _classify(header: str) -> Tuple[str, int]:
    up = header.upper()
    if _MANDATORY.search(up):
        return "mandatory", 0
    if _TEXT_CHAT_ONLY.search(up):
        return "chat_only", 0
    for rx, rank in _RANKS:
        if rx.search(up):
            return "optional", rank
    return "optional", _CONTEXT_RANK


def split_blocks(text: str) -> List[Block]:
    """Cut an assembled instruction into its header-led blocks.

    Text before the first header (identity and voice rules) is one MANDATORY
    block. The action-permission policy is cut at its exact length, so text
    appended after it (continuity, tone) is its own optional block rather than
    riding on the policy's protection.
    """
    text = text or ""
    try:
        from agent_friday.services.action_policy import ACTION_PERMISSION_POLICY as _POLICY
    except Exception:  # pragma: no cover
        _POLICY = None
    marks = _header_marks(text)
    if not marks:
        return [Block("preamble", text, "mandatory")] if text else []
    cuts: List[Tuple[int, str]] = []
    if marks[0][0] > 0:
        cuts.append((0, "\x00preamble"))
    cuts.extend(marks)
    out: List[Block] = []
    for i, (start, header) in enumerate(cuts):
        end = cuts[i + 1][0] if i + 1 < len(cuts) else len(text)
        chunk = text[start:end]
        if header == "\x00preamble":
            out.append(Block("preamble", chunk, "mandatory"))
            continue
        kind, rank = _classify(header)
        if _POLICY and chunk.startswith(_POLICY[:40]) and chunk.startswith(_POLICY):
            out.append(Block(_section_name(header), chunk[:len(_POLICY)], "mandatory"))
            rest = chunk[len(_POLICY):]
            if rest.strip():
                out.append(Block("after_policy", rest, "optional", _CONTEXT_RANK))
            continue
        out.append(Block(_section_name(header), chunk, kind, rank))
    return out


def _slice_plan(hint: str, settings: dict) -> Dict[str, dict]:
    """slice_head's plan for this session, {slice: {action, p, reason}} or {}.

    No reading (encoder cold) is `{}`: the static order is used. Never blocks
    and never raises. The plan only ORDERS what is dropped under cap pressure;
    when the owner chose "Send only what each turn needs" the Slicer is
    `active` and a "withhold" block is dropped even under the cap.
    """
    try:
        from agent_friday.services import slice_head as sh
        if not hint:
            return {}
        # The head never blocks on its encoder, but a ready encoder embeds the
        # hint in the calling thread: give it SLICE_BUDGET_S and move on.
        box: list = []
        t = threading.Thread(target=lambda: box.append(sh.scores_for(hint)),
                             name="live-slice-plan", daemon=True)
        t.start()
        t.join(SLICE_BUDGET_S)
        scores = box[0] if box else None
        if not scores:
            return {}
        sl = sh.Slicer(scores, cloud=True, opted=sh.opted_in(settings))
        plan = dict(sl.plan)
        for v in plan.values():
            v["applied"] = bool(sl.active)
        return plan
    except Exception as e:  # noqa: BLE001
        _log.debug("slice plan skipped: %s", e)
        return {}


#: How long session start waits for slice_head's reading before it goes on
#: without one (the latency gate: the plan adds <= 50 ms p95).
SLICE_BUDGET_S = 0.020

_SURFACE_HEAD = "=== VOICE TOOL SURFACE"
_BULLET = re.compile(r"^  • (\S+)\n", re.M)
_EXACTLY = re.compile(r"EXACTLY \d+ tools")


def narrow_surface_note(text: str, declared: Iterable[str]) -> str:
    """Make the VOICE TOOL SURFACE note name only the tools this leg declares.

    The note exists so the model never believes in a tool it was not handed;
    with a kit, naming all 91 would recreate exactly that failure.
    """
    keep = set(declared)
    at = text.find(_SURFACE_HEAD)
    if at < 0:
        return text
    head, tail = text[:at], text[at:]
    # Only the block's own bullet list (up to the next blank line after it).
    names = [m.group(1) for m in _BULLET.finditer(tail)]
    if not names:
        return text
    tail = _BULLET.sub(lambda m: m.group(0) if m.group(1) in keep else "", tail)
    tail = _EXACTLY.sub("EXACTLY %d tools" % len([n for n in names if n in keep]), tail, count=1)
    if "more_tools" in keep and "more_tools" not in names:
        pass
    note = ("If you need a tool that is not in this list, call more_tools with its "
            "group (or 'everything'): it is added when the call reconnects, "
            "a moment later. Do not promise it before it has arrived.\n\n")
    marker = "EVERYTHING ELSE CHAT CAN DO IS REACHABLE"
    if marker in tail and "more_tools with its" not in tail:
        tail = tail.replace(marker, note + marker, 1)
    return head + tail


def fit_instruction(text: str, cap: int, *, slice_plan: Optional[Dict[str, dict]] = None,
                    declared: Optional[Iterable[str]] = None) -> Tuple[str, InstructionReport]:
    """The voice-only instruction, within `cap` tokens where safety allows.

    cap <= 0 returns `text` untouched. Never raises; on any internal failure,
    or if the safety text would not survive, returns the input unchanged.
    """
    rep = InstructionReport(cap=int(cap or 0), tokens_before=estimate_tokens(text))
    if not text or cap is None or cap <= 0:
        rep.tokens_after = rep.tokens_before
        rep.reason = "cap off"
        return text, rep
    try:
        work = narrow_surface_note(text, declared) if declared is not None else text
        blocks = split_blocks(work)
        plan = slice_plan or {}
        try:
            from agent_friday.services import slice_head as sh
        except Exception:  # pragma: no cover
            sh = None
        for b in blocks:
            if b.kind == "optional" and sh is not None:
                b.slice = sh.section_slice(b.name)
        keep: List[Block] = []
        for b in blocks:
            if b.kind == "chat_only":
                rep.dropped.append((b.name, estimate_tokens(b.text), "text-chat only"))
                continue
            if (b.kind == "optional" and b.slice and plan.get(b.slice, {}).get("applied")
                    and plan[b.slice].get("action") == "withhold"):
                rep.dropped.append((b.name, estimate_tokens(b.text), "not needed (slice_head)"))
                continue
            keep.append(b)

        def total():
            return sum(estimate_tokens(b.text) for b in keep)

        rep.floor_tokens = sum(estimate_tokens(b.text) for b in keep if b.kind == "mandatory")

        def order(b: Block):
            act = plan.get(b.slice, {}).get("action") if b.slice else None
            return (b.rank, _SLICE_ORDER.get(act, 2), -estimate_tokens(b.text))

        droppable = sorted([b for b in keep if b.kind == "optional"], key=order)
        while total() > cap and droppable:
            victim = droppable.pop(0)
            keep.remove(victim)
            rep.dropped.append((victim.name, estimate_tokens(victim.text), "over the cap"))
        out = "".join(b.text for b in keep)
        rep.tokens_after = estimate_tokens(out)
        rep.floor_over_cap = rep.tokens_after > cap
        rep.applied = True
        # Fail closed on the safety text: if the policy or the cLaws header was
        # in the input and is not in the output, send the input as it was.
        if not safety_ok(text, out):
            _log.error("live instruction cap would have dropped safety text; sending it uncapped")
            rep.applied = False
            rep.reason = "safety text would be lost"
            rep.tokens_after = rep.tokens_before
            return text, rep
        return out, rep
    except Exception as e:  # noqa: BLE001
        _log.error("live instruction cap failed (%s); sending it uncapped", e)
        rep.reason = "error: %s" % type(e).__name__
        rep.tokens_after = rep.tokens_before
        return text, rep


def safety_ok(before: str, after: str) -> bool:
    """True when every piece of safety text in `before` is still in `after`."""
    try:
        from agent_friday.services.action_policy import ACTION_PERMISSION_POLICY as pol
    except Exception:  # pragma: no cover
        pol = None
    if pol and pol in before and pol not in after:
        return False
    for h in _asimov_headers(before):
        if h not in after:
            return False
    return True


#: The start of an ASIMOV header line: "==" or "===", spaces, then the name up to the first "=".
_ASIMOV_HEAD = re.compile(r"={2,3}[ \t]*(ASIMOV[^\n=]*)", re.I)


def _asimov_headers(text: str) -> List[str]:
    """The name of every "== ASIMOV ... ==" line in ``text``, without the spaces before the
    closing "==". Every line is read, however long: the name is the greedy run up to the
    first "=", trimmed, so there is one way to match and the scan is linear."""
    out: List[str] = []
    for line in text.split("\n"):
        m = _ASIMOV_HEAD.match(line)
        if m and line.startswith("==", m.end()):
            out.append(m.group(1).rstrip(" \t"))
    return out


# ── LV-2: the tool kit ──────────────────────────────────────────────────────

#: Tools a caller reaches for when the answer lives in the owner's own context.
#: In a session whose cap dropped context, one of these is a "context miss".
CONTEXT_TOOLS = frozenset({"ask_friday", "search_wiki", "search_past_conversations", "search_library",
                           "query_trust_graph"})

#: Always declared when kits are on. approvals and share cards must stay
#: answerable by voice; ask_friday is the local-brain relay; more_tools is the
#: way out of a kit that was too small.
CORE_TOOLS = ("ask_friday", "more_tools", "delegate_to_friday", "answer_card",
              "answer_share_request", "undo_action", "navigate_workspace",
              "note_conversation_state", "call_cost")
ALWAYS = ("ask_friday", "more_tools")

#: Spoken-for tools when nothing else says what the call is about.
DEFAULT_FILL = ("query_calendar", "check_email", "search_news", "get_briefing",
                "search_web", "open_url", "navigate_to", "search_wiki", "spawn_task")

#: The tools a group is most often wanted for, best first. A group lists many
#: tools (code has a dozen); the kit takes these before the rest of it.
GROUP_HEADS = {
    "web": ("search_web", "open_url"), "files": ("read_file", "search_files", "write_file", "open_path"),
    "code": ("open_project", "codebase_run", "codebase_read"), "media": ("media_play", "podcast_play", "media_turn"),
    "settings": ("set_setting", "voice_preferences"), "workflows": ("spawn_task", "run_workflow", "workflow_status"),
    "workspace": ("show_my_day", "set_chat_tray"), "mail": ("check_email", "search_email", "organize_email"),
    "calendar": ("query_calendar", "organize_calendar"), "news": ("search_news", "get_briefing", "discuss_story"),
    "wiki_knowledge": ("search_wiki", "search_library", "search_past_conversations"),
    "system_desktop": ("check_situation", "open_path"), "crew": ("list_crew", "ask_crew"),
    "career": ("career_status", "career_scan"), "people_trust": ("check_email",),
}

_WORKSPACE_GROUPS = {
    "mail": ("mail", "calendar"), "email": ("mail", "calendar"),
    "calendar": ("calendar", "tasks"), "tasks": ("tasks", "calendar"),
    "news": ("news", "web"), "media": ("media",), "podcasts": ("media",),
    "career": ("career", "files"), "code": ("code",), "codebase": ("code",),
    "library": ("wiki_knowledge", "files"), "wiki": ("wiki_knowledge",),
    "files": ("files",), "settings": ("settings", "voice"), "system": ("settings", "system_desktop"),
    "crew": ("crew",), "workflows": ("workflows",), "creative": ("creative", "media"),
    "phone": ("phone",), "trust": ("people_trust",), "contacts": ("people_trust", "mail"),
    "home": ("calendar", "mail", "news"), "marketplace": ("workspace",),
}

_HOUR_GROUPS = (
    (range(5, 11), ("calendar", "mail", "news")),
    (range(11, 17), ("workspace", "files", "web")),
    (range(17, 24), ("media", "news")),
)

_STOP = frozenset("a an the to of and or for in on at my me i you it is are be do can could would please "
                  "what whats how when where who this that with from about tell show open get".split())


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP]


class UsageStore:
    """Per-tool use counts on this PC (a small JSON file; never leaves it)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self._loaded = False

    def _file(self) -> Optional[Path]:
        if self.path is not None:
            return self.path
        try:
            from agent_friday.core import FRIDAY_DIR
            return Path(FRIDAY_DIR) / "voice_tool_usage.json"
        except Exception:
            return None

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        f = self._file()
        try:
            if f and f.exists():
                self._data = json.loads(f.read_text(encoding="utf-8")) or {}
        except Exception:
            self._data = {}

    def record(self, name: str, now: Optional[float] = None) -> None:
        with self._lock:
            self._load()
            e = self._data.setdefault(name, {"n": 0, "last": 0.0})
            e["n"] = int(e.get("n", 0)) + 1
            e["last"] = float(now if now is not None else time.time())
            f = self._file()
            try:
                if f:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(json.dumps(self._data), encoding="utf-8")
            except Exception:
                pass

    def recency(self, name: str, now: Optional[float] = None) -> float:
        """0..1: how recently and how often. Half-life one week."""
        with self._lock:
            self._load()
            e = self._data.get(name)
        if not e:
            return 0.0
        age = max(0.0, (now if now is not None else time.time()) - float(e.get("last", 0)))
        return min(1.0, (0.4 + 0.15 * min(4, int(e.get("n", 0)))) * 0.5 ** (age / 604800.0))


def _group_names(tool: str) -> List[str]:
    try:
        from agent_friday.services import tool_catalogue as tc
        return tc.groups_of(tool)
    except Exception:
        return []


def group_ids() -> List[str]:
    try:
        from agent_friday.services import tool_catalogue as tc
        return sorted((tc.load_groups().get("groups") or {}).keys())
    except Exception:
        return []


def _group_scores(workspace: str, hour: Optional[int]) -> Dict[str, float]:
    s: Dict[str, float] = {}
    for g in _WORKSPACE_GROUPS.get((workspace or "").strip().lower(), ()):
        s[g] = s.get(g, 0.0) + 1.0
    if hour is not None:
        for rng, gs in _HOUR_GROUPS:
            if hour in rng:
                for g in gs:
                    s[g] = s.get(g, 0.0) + 0.4
    return s


def choose_kit(available: Sequence[str], *, size: int = 18, workspace: str = "",
               hour: Optional[int] = None, opening: str = "", usage: Optional[UsageStore] = None,
               descriptions: Optional[Dict[str, str]] = None, now: Optional[float] = None) -> List[str]:
    """About `size` tools from `available`, scored on workspace, time of day,
    recent use and the opening utterance. Core tools first; deterministic."""
    avail = list(dict.fromkeys(available))
    have = set(avail)
    kit: List[str] = [n for n in CORE_TOOLS if n in have]
    for n in ALWAYS:        # even if the caller forgot to list it
        if n not in kit:
            kit.append(n)
    gscore = _group_scores(workspace, hour)
    owords = set(_words(opening))
    for g in group_ids():   # a group named out loud counts as the workspace
        if g.replace("_", " ") in (opening or "").lower() or g in owords:
            gscore[g] = gscore.get(g, 0.0) + 1.5
    scored = []
    for n in avail:
        if n in kit:
            continue
        groups = _group_names(n)
        sc = sum(gscore.get(g, 0.0) for g in groups)
        for g in groups:       # the head of a wanted group beats its long tail
            heads = GROUP_HEADS.get(g, ())
            if n in heads and gscore.get(g):
                sc += 0.6 - 0.12 * heads.index(n)
        if n in DEFAULT_FILL:  # a small baseline: what most calls reach for
            sc += 0.3
        if usage is not None:
            sc += 1.2 * usage.recency(n, now)
        if owords:
            tw = set(_words(n.replace("_", " ") + " " + (descriptions or {}).get(n, "")[:120]))
            sc += 0.8 * len(owords & tw)
        scored.append((-sc, n))
    scored.sort()
    for neg, n in scored:
        if len(kit) >= size:
            break
        if -neg > 0:
            kit.append(n)
    for n in DEFAULT_FILL:
        if len(kit) >= max(12, min(size, 12)):
            break
        if n in have and n not in kit:
            kit.append(n)
    return kit


def tools_of_group(group: str, available: Iterable[str]) -> List[str]:
    have = set(available)
    try:
        from agent_friday.services import tool_catalogue as tc
        return [n for n in tc.tools_in_group(group) if n in have]
    except Exception:
        return []


# ── LV-6: prefetch from the input transcription ─────────────────────────────

_CUE = r"(?:from|email|e-mail|mail|message|text|tell|ask|call|with|meeting with|reply to|write to|about)"
_PERSON = re.compile(r"\b" + _CUE + r"\s+((?:[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]{1,}){0,2}))")
_NOT_A_PERSON = frozenset(
    "Friday Google Gemini Monday Tuesday Wednesday Thursday Saturday Sunday January February March April May June "
    "July August September October November December Tomorrow Today Tonight Yesterday I The This That Agent "
    "Laya Gmail Calendar Chrome Windows".split())
_DATE = re.compile(r"\b(tomorrow|tonight|next week|this weekend|(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                   r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}|\d{1,2}/\d{1,2})\b",
                   re.I)


def detect_prefetch(text: str) -> List[Tuple[str, str]]:
    """[(kind, arg)] to warm for this utterance. kind: "mail_from" | "calendar".

    Pure: no I/O. A person is a capitalised name after a cue word ("email from
    Dana"); a date is "tomorrow", a weekday, a month and day, or d/m.
    """
    out: List[Tuple[str, str]] = []
    for m in _PERSON.finditer(text or ""):
        name = m.group(1).strip()
        first = name.split()[0]
        if first in _NOT_A_PERSON:
            continue
        out.append(("mail_from", name))
    if _DATE.search(text or ""):
        out.append(("calendar", "upcoming"))
    return list(dict.fromkeys(out))


class Prefetcher:
    """Warms local caches from what was said. Local only.

    `mail_headers(person)` and `calendar()` are injected; the defaults read
    the same short-lived caches warm_voice_reads fills (services/voice_engine),
    so a call that never mentions anyone costs nothing and nothing is sent to
    the model: the cached result waits for a tool call to ask for it.
    """
    MAX_PER_SESSION = 4

    def __init__(self, mail_headers: Optional[Callable[[str], object]] = None,
                 calendar: Optional[Callable[[], object]] = None, run_async: bool = True):
        self._mail = mail_headers
        self._cal = calendar
        self._seen: set = set()
        self.done: List[Tuple[str, str]] = []
        self._async = run_async

    def _default_mail(self, person: str):
        from agent_friday.services import voice_engine as ve
        msgs = ve._voice_mail() or []
        low = person.lower()
        return [{"from": m.get("from") or m.get("sender"), "subject": m.get("subject"),
                 "date": m.get("date")} for m in msgs
                if isinstance(m, dict) and low in str(m.get("from") or m.get("sender") or "").lower()][:8]

    def _default_cal(self):
        from agent_friday.services import voice_engine as ve
        return ve._voice_calendar()

    def on_text(self, text: str) -> List[Tuple[str, str]]:
        todo = []
        for item in detect_prefetch(text):
            if item in self._seen or len(self._seen) >= self.MAX_PER_SESSION:
                continue
            self._seen.add(item)
            todo.append(item)
        if not todo:
            return []

        def run():
            for kind, arg in todo:
                try:
                    if kind == "mail_from":
                        (self._mail or self._default_mail)(arg)
                    else:
                        (self._cal or self._default_cal)()
                    self.done.append((kind, arg))
                except Exception as e:  # noqa: BLE001
                    _log.info("voice prefetch %s skipped: %s", kind, e)
        if self._async:
            threading.Thread(target=run, name="voice-prefetch", daemon=True).start()
        else:
            run()
        return todo


# ── The session plan ─────────────────────────────────────────────────────────

#: Handed to the model as a "notice" (routes/voice._INJECT_LEADS adds the
#: "Not from the user:" lead and the closing bracket).
HEADS_UP_NOTE = ("this call has been running a while and is getting long. Say ONE short "
                 "sentence about it, in your own words, such as 'This call is getting long; "
                 "I'll keep going.' Then carry on as normal and do not mention it again.")


def _shadow_path() -> Optional[Path]:
    try:
        from agent_friday.core import FRIDAY_DIR
        return Path(FRIDAY_DIR) / "voice_kit_shadow.jsonl"
    except Exception:
        return None


def shadow_stats(path: Optional[Path] = None) -> dict:
    """The two go/no-go rates from the local shadow log. Counts only.

    tool_miss_rate     calls to a tool the kit would not have declared / all calls.
    context_miss_rate  sessions that reached for the owner's context (ask_friday,
                       a memory or wiki search ...) while the cap would have dropped
                       context / sessions where the cap would have dropped context.
    The go threshold for voice_live_trim "on" is <= 0.03 on both.
    """
    p = Path(path) if path else _shadow_path()
    calls = misses = sessions = 0
    drop_sessions: set = set()
    ctx_sessions: set = set()
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ev = r.get("event")
            if ev == "plan":
                sessions += 1
            elif ev == "instruction":
                if r.get("would_drop") and any(x[2] != "text-chat only" for x in r["would_drop"]):
                    drop_sessions.add(r.get("session"))
            elif ev == "call":
                calls += 1
                misses += 1 if r.get("miss") else 0
                if r.get("context_miss"):
                    ctx_sessions.add(r.get("session"))
    except Exception:
        pass
    ctx_sessions &= drop_sessions
    return {"sessions": sessions, "calls": calls, "misses": misses,
            "miss_rate": (misses / calls) if calls else 0.0,
            "tool_miss_rate": (misses / calls) if calls else 0.0,
            "sessions_with_dropped_context": len(drop_sessions),
            "context_miss_sessions": len(ctx_sessions),
            "context_miss_rate": (len(ctx_sessions) / len(drop_sessions)) if drop_sessions else 0.0}


class SessionPlan:
    """One call's plan. Built before connect; consulted at each leg."""

    def __init__(self, settings: Optional[dict], available: Sequence[str], *,
                 workspace: str = "", opening: str = "", now: Optional[float] = None,
                 usage: Optional[UsageStore] = None, shadow_path=None,
                 descriptions: Optional[Dict[str, str]] = None):
        self.settings = dict(settings or {})
        self.available = list(dict.fromkeys(available))
        self.mode = trim_mode(self.settings)
        self.dropped_context: Dict[str, int] = {}
        self.size = max(8, min(40, _num(self.settings, "voice_live_tool_kit_size")))
        self.usage = usage if usage is not None else UsageStore()
        self.session_id = "%x" % int((now if now is not None else time.time()) * 1000)
        hour = datetime.fromtimestamp(now).hour if now is not None else datetime.now().hour
        self.kit = (choose_kit(self.available, size=self.size, workspace=workspace, hour=hour,
                               opening=opening, usage=self.usage, descriptions=descriptions)
                    if self.mode != "off" else list(self.available))
        self.extra_groups: List[str] = []
        self.everything = False
        self.relegs_pending = False
        self._shadow_path = Path(shadow_path) if shadow_path else None
        self.instruction_report = InstructionReport()
        self._prefit = ""
        self._hint = ""
        self._sig = tuple(self.declared_names())
        self.prefetch = Prefetcher()
        # Budget
        self.model = ""
        self._last_usage: Optional[Tuple[int, int]] = None
        self.turns = 0
        self.session_in = 0
        self.session_out = 0
        self.session_usd = 0.0
        self.heads_up_given = False
        self._log_plan()

    # -- LV-1 -----------------------------------------------------------------
    def fit(self, text: str, *, hint: str = "") -> str:
        self._prefit, self._hint = text, hint
        cap = _num(self.settings, "voice_live_instruction_max_tokens")
        if cap <= 0:
            self.instruction_report = InstructionReport(
                tokens_before=estimate_tokens(text), tokens_after=estimate_tokens(text), reason="cap off")
            return text
        if self.mode == "off":
            self.instruction_report = InstructionReport(
                tokens_before=estimate_tokens(text), tokens_after=estimate_tokens(text), reason="trim off")
            return text
        # In shadow the report is for the instruction "on" would send.
        declared = self.would_declare()
        out, rep = fit_instruction(text, cap, slice_plan=_slice_plan(hint, self.settings), declared=declared)
        self.instruction_report = rep
        self.dropped_context = {n: t for n, t, r in rep.dropped if r != "text-chat only"}
        try:
            _log.info("voice live instruction (%s): %d -> %d tokens (cap %d, floor %d%s); dropped: %s",
                      self.mode, rep.tokens_before, rep.tokens_after, cap, rep.floor_tokens,
                      ", FLOOR OVER CAP" if rep.floor_over_cap else "",
                      ", ".join("%s(%d)" % (n, t) for n, t, _r in rep.dropped) or "nothing")
        except Exception:
            pass
        if self.mode != "on":
            self._write({"event": "instruction", "mode": self.mode, "cap": cap,
                         "tokens_now": rep.tokens_before, "tokens_if_on": rep.tokens_after,
                         "floor": rep.floor_tokens, "floor_over_cap": bool(rep.floor_over_cap),
                         "would_drop": [[n, t, r] for n, t, r in rep.dropped]})
            return text
        return out

    # -- LV-2 -----------------------------------------------------------------
    def would_declare(self) -> List[str]:
        """What mode "on" declares right now (the kit, widened); used by the shadow report."""
        return self._kit_names()

    def declared_names(self) -> List[str]:
        """What THIS leg declares. Off and shadow: everything (as today)."""
        if self.mode != "on":
            return [n for n in self.available if n != "more_tools"]
        return self._kit_names()

    def _kit_names(self) -> List[str]:
        pool = [n for n in self.available if n != "more_tools"] + ["more_tools"]
        if self.everything:
            return pool
        names = list(self.kit)
        for g in self.extra_groups:
            for n in tools_of_group(g, self.available):
                if n not in names:
                    names.append(n)
        return [n for n in self.available if n in set(names)]

    def kit_arg(self) -> Optional[dict]:
        """For voice_engine._build_voice_live_tools(kit=...). None = full, as before."""
        if self.mode != "on" or self.everything:
            return None
        return {"names": set(self.declared_names()), "compact": True}

    def would_drop(self) -> List[str]:
        keep = set(self.kit)
        return [n for n in self.available if n not in keep]

    def widen(self, group: str) -> str:
        """more_tools(group). Takes effect on the next leg. Returns what to tell the model."""
        g = str(group or "").strip().lower().replace(" ", "_")
        if self.mode != "on":
            return ("Every tool is already available on this call; nothing to add. "
                    "Call the tool you need.")
        if g in ("everything", "all"):
            self.everything = True
        elif g in ("fewer", "less", "fewer_tools"):
            self.everything = False
            self.extra_groups = []
        elif g in group_ids():
            if g not in self.extra_groups:
                self.extra_groups.append(g)
        else:
            return ("No tool group called '%s'. Groups: %s, or 'everything'." % (g, ", ".join(group_ids())))
        self.relegs_pending = True
        if g in ("fewer", "less", "fewer_tools"):
            return "Okay: back to the short toolset when the call reconnects, in a moment."
        added = (self.available if self.everything else tools_of_group(g, self.available))
        added = [n for n in added if n not in self.kit]
        return ("Widening: %d more tools (%s) arrive when the call reconnects, in a moment. "
                "Tell the user plainly that you are adding them and that you will say when "
                "they are ready. Do not call them before then."
                % (len(added), ", ".join(added[:12]) + (" ..." if len(added) > 12 else "")))

    def instruction_for_leg(self) -> str:
        """The (ungated) instruction for a leg after the tool list changed.

        Re-run from the same assembled text so the surface note names exactly
        what this leg declares. The caller gates it again before sending."""
        return self.fit(self._prefit, hint=self._hint)

    def tools_for_leg(self, types, nonblocking: bool = False):
        """New Tool list for a leg whose declared set changed, else None.

        Tools change only here, at a leg boundary."""
        self.relegs_pending = False
        sig = tuple(self.declared_names())
        if sig == self._sig:
            return None
        self._sig = sig
        from agent_friday.services.voice_engine import _build_voice_live_tools
        return _build_voice_live_tools(types, behavior="NON_BLOCKING" if nonblocking else None,
                                       kit=self.kit_arg())

    def reconnect_due(self, have_handle: bool) -> bool:
        """True when a widened set is waiting and the call can be renewed without losing it."""
        return bool(self.relegs_pending and have_handle)

    def leg_started(self) -> None:
        self.relegs_pending = False

    def note_call(self, name: str) -> None:
        """A tool Gemini actually called: usage (for the next kit) and the shadow miss log."""
        try:
            self.usage.record(name)
        except Exception:
            pass
        if self.mode == "off" or name in ("more_tools", "call_cost"):
            return
        miss = name not in set(self.kit) and name not in set(self._extra_names())
        ctx_miss = name in CONTEXT_TOOLS and bool(self.dropped_context)
        self._write({"event": "call", "tool": name, "miss": bool(miss),
                     "context_miss": bool(ctx_miss), "mode": self.mode})

    def _extra_names(self) -> List[str]:
        out: List[str] = []
        for g in self.extra_groups:
            out.extend(tools_of_group(g, self.available))
        return out

    def _log_plan(self) -> None:
        if self.mode == "off":
            return
        dropped = self.would_drop()
        self._write({"event": "plan", "mode": self.mode, "full": len(self.available),
                     "kit": len(self.kit), "would_drop": len(dropped), "would_drop_tools": dropped})
        _log.info("voice live tool kit (%s): %d of %d tools%s", self.mode, len(self.kit),
                  len(self.available), "" if self.mode == "on" else " (shadow: all declared)")

    def _write(self, rec: dict) -> None:
        p = self._shadow_path or _shadow_path()
        if p is None:
            return
        rec = {"ts": round(time.time(), 1), "session": self.session_id, **rec}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    # -- LV-1: compression -----------------------------------------------------
    def compression_config(self, types):
        """ContextWindowCompressionConfig; explicit trigger and target tokens only when trim is "on"."""
        trig = _num(self.settings, "voice_live_compression_trigger_tokens")
        targ = _num(self.settings, "voice_live_compression_target_tokens")
        if self.mode == "on" and trig > 0 and targ > 0 and targ < trig:
            try:
                return types.ContextWindowCompressionConfig(
                    trigger_tokens=trig, sliding_window=types.SlidingWindow(target_tokens=targ))
            except Exception as e:  # noqa: BLE001
                _log.warning("explicit compression targets not accepted by this google-genai (%s); "
                             "using the default window", e)
        return types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow())

    # -- LV-6 -----------------------------------------------------------------
    def on_input_text(self, text: str) -> None:
        try:
            self.prefetch.on_text(text)
        except Exception:
            pass

    def on_usage(self, chunk) -> None:
        um = getattr(chunk, "usage_metadata", None)
        if um is None:
            return
        try:
            p = int(getattr(um, "prompt_token_count", 0) or 0)
            r = int(getattr(um, "response_token_count", 0) or 0)
            self._last_usage = (p, r)
        except Exception:
            pass

    def on_turn_complete(self, model: str = "") -> dict:
        """Commit this turn's usage. Returns {"note": str|None, "frame": dict|None}."""
        out = {"note": None, "frame": None}
        if self._last_usage is None:
            return out
        p, r = self._last_usage
        self._last_usage = None
        self.turns += 1
        self.session_in += p
        self.session_out += r
        usd = None
        try:
            from agent_friday.services import cost_meter as cm
            usd = cm.cost_for(model or self.model, p, r)
        except Exception:
            usd = None
        self.session_usd += float(usd or 0.0)
        soft = _num(self.settings, "voice_live_soft_budget_usd", float)
        if soft > 0 and not self.heads_up_given and self.session_usd >= soft:
            self.heads_up_given = True
            out["note"] = HEADS_UP_NOTE
        out["frame"] = self.panel_frame()
        return out

    def day_total_usd(self) -> float:
        try:
            from agent_friday.services import cost_meter as cm
            return float(((cm.summary("today").get("by_kind") or {}).get("voice") or {}).get("usd") or 0.0)
        except Exception:
            return 0.0

    def panel_line(self) -> str:
        return "this call about $%.2f · voice today $%.2f" % (self.session_usd, self.day_total_usd())

    def panel_frame(self) -> dict:
        return {"type": "voice_budget", "session_usd": round(self.session_usd, 4),
                "day_usd": round(self.day_total_usd(), 4), "turns": self.turns,
                "soft_budget_usd": _num(self.settings, "voice_live_soft_budget_usd", float),
                "line": self.panel_line()}

    def cost_report(self) -> str:
        soft = _num(self.settings, "voice_live_soft_budget_usd", float)
        return ("This call so far: %d turns, about %d prompt tokens and %d spoken tokens, roughly $%.2f. "
                "Voice today: $%.2f.%s" % (
                    self.turns, self.session_in, self.session_out, self.session_usd,
                    self.day_total_usd(),
                    (" A heads-up is set for $%.2f." % soft) if soft > 0 else ""))


# ── Entry points the hooks call ─────────────────────────────────────────────

def plan_session(sys_text: str, settings: dict, available, *, workspace: str = "",
                 opening: str = "", now: Optional[float] = None,
                 usage: Optional[UsageStore] = None, shadow_path=None) -> Tuple[str, SessionPlan]:
    """Build the plan and the capped instruction. Never raises: on error the
    caller gets its text back and a plan with every tool declared.

    `available` is the list of declarable tool names, or a callable returning it."""
    try:
        if callable(available):
            available = available()
        available = list(available or [])
    except Exception:  # noqa: BLE001
        available = []
    try:
        plan = SessionPlan(settings, available, workspace=workspace, opening=opening,
                           now=now, usage=usage, shadow_path=shadow_path)
        return plan.fit(sys_text, hint=" ".join(x for x in (workspace, opening) if x)), plan
    except Exception as e:  # noqa: BLE001
        _log.error("live session plan failed (%s); sending the session as before", e)
        safe = dict(settings or {}, voice_live_trim="off")
        return sys_text, SessionPlan(safe, available, shadow_path=shadow_path)


def tool_more_tools(args: dict, session: Optional[dict]) -> str:
    plan = (session or {}).get("lsp")
    if plan is None:
        return "Every tool is already available on this call."
    return plan.widen((args or {}).get("group"))


def tool_call_cost(_args: dict, session: Optional[dict]) -> str:
    plan = (session or {}).get("lsp")
    if plan is None:
        return "I can't see this call's cost yet."
    return plan.cost_report()


def note_call(session: Optional[dict], name: str) -> None:
    plan = (session or {}).get("lsp") if isinstance(session, dict) else None
    if plan is not None:
        plan.note_call(name)
