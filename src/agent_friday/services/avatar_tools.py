"""Friday's look, by voice and chat: the avatar_evolution tool.

docs/design/active/avatar-visual-genome.md §8.4. One tool, declared once in
the text registry and shared into voice (voice_engine._VOICE_SHARED_TOOLS),
so a spoken "evolve now", "undo that look", "go back to last month's look",
"turn evolution off" or "what changed?" runs the same code the screen does,
and what Friday says comes from the same step record the history shows. It
also picks the structure shown ("switch to the wormhole"): kept like the
picker's choice and pushed to the open page.

Replies are written for the ear: sentences, numbers as words, no gene names.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

from agent_friday.services import avatar_genome as g
from agent_friday.services import avatar_growth as gr

ACTIONS = ("status", "describe", "evolve_now", "undo", "rollback", "set_enabled",
           "set_author", "use_local", "show")

_NUM = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()
_WORDNUM = {w: i for i, w in enumerate(_NUM)} | {"a": 1, "an": 1}

STRUCTURE_NAMES = {
    "CUBES": "lattice", "ICOSAHEDRON": "Dyson sphere", "NETWORK": "network", "DOME": "cathedral",
    "ASTROLABE": "astrolabe", "TESSERACT": "tesseract", "QUANTUM": "probability cloud",
    "MANDELBROT": "Mandelbrot set", "MOBIUS": "Mobius strip", "GRID": "ocean",
    "CABLES": "nerve", "NONE": "fibration", "EDEN": "Giga Earth",
    "WORMHOLE": "wormhole", "BLACKHOLE": "black hole",
}


def _now() -> float:
    return time.time()


def _say_num(n) -> str:
    n = int(round(abs(float(n))))
    return _NUM[n] if n < len(_NUM) else str(n)


def _phrase(d) -> str | None:
    gene, a, b = d.get("gene", ""), d.get("from"), d.get("to")
    up = (isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a)
    if gene == "palette/base_offset":
        return "a little more %s, about %s degrees" % ("violet" if up else "teal", _say_num(b - a))
    if gene == "palette/saturation":
        return "a touch richer" if up else "a touch softer"
    if gene == "palette/scheme":
        return "a new accent colour"
    if gene == "palette/accent_share":
        return "a little more accent colour" if up else "a little less accent colour"
    if gene == "luma/bloom":
        return "I glow a little more" if up else "I glow a little less"
    if gene == "luma/grain":
        return "a grainier texture" if up else "a finer texture"
    if gene == "form/density":
        return "a little fuller" if up else "a little sparer"
    if gene == "form/coherence":
        return "more orderly" if up else "a little freer"
    if gene == "form/symmetry":
        return "a different symmetry"
    if gene.startswith("speech/"):
        return "I move a little differently when I talk"
    if gene.startswith("gesture/"):
        return "my working gestures have a new rhythm"
    if gene == "facets":
        return "a new ring for something I learned to do"
    if gene.startswith("structures/") and gene.split("/")[1] in g.TRACKS:
        sid = gene.split("/")[1]
        tr = g.TRACKS[sid]
        if isinstance(b, int) and 0 <= b < len(tr["forms"]):
            return "%s %s to its %s form" % (tr["label"], "moved on" if up else "went back",
                                             tr["forms"][b].lower())
        return "%s changed form" % tr["label"]
    if gene.startswith("structures/"):
        parts = gene.split("/")
        where = STRUCTURE_NAMES.get(parts[1], "shape")
        if parts[2] == "spacing":
            return "my %s pulled in tighter" % where if not up else "my %s spread out a little" % where
        return "my %s changed shape a little" % where
    return None


def _credit(step) -> str:
    a = step.get("author") or {}
    if a.get("path") == "track":
        return "It follows Giga Earth's set track, so no model was asked"
    if a.get("path") == "seeded" or not a.get("model"):
        return "I made it myself, on this computer"
    who = a["model"]
    if a.get("path") == "local":
        return "%s made it, on this computer" % who
    s = "%s made it" % who
    if a.get("why_this_model"):
        s += ". " + a["why_this_model"]
    return s


def describe(step=None) -> str:
    step = step if step is not None else g.active_step()
    if step is None:
        return "I still look the way I started. No changes yet."
    bits = [p for p in (_phrase(d) for d in step.get("diff") or []) if p]
    what = ", and ".join(bits) if bits else "a small change"
    name = step.get("name")
    head = ("This look is %s: " % name) if name else "This look: "
    return head + what[0].upper() + what[1:] + ". " + _credit(step) + "."


def _waiting_text(res) -> str:
    fixes = res.get("fixes") or []
    local = next((f.split(":", 1)[1] for f in fixes if f.startswith("use_local:")), None)
    s = "I'm waiting for a cloud model: %s." % res.get("reason", "none is connected")
    if "connect_cloud" in fixes and local:
        s += " I can open Connections, or use %s instead. Which?" % local
    elif "connect_cloud" in fixes:
        s += " I can open Connections for you."
    return s


def _ago(phrase):
    p = phrase.strip().lower()
    if p in ("yesterday",):
        return 86400
    if p in ("last week", "a week ago"):
        return 7 * 86400
    if p in ("last month", "a month ago"):
        return 30 * 86400
    m = re.match(r"^(\d+|[a-z]+)\s+(day|week|month)s?\s+ago$", p)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _WORDNUM.get(m.group(1))
        if n is not None:
            return n * {"day": 1, "week": 7, "month": 30}[m.group(2)] * 86400
    return None


def _resolve(when):
    """(step or None for the first look, error text or None, ambiguous list)."""
    p = str(when or "").strip()
    low = p.lower()
    if not p:
        return None, "Which look? You can say a name, or something like 'last month'.", []
    if low in ("the first look", "the original", "the first one", "how you started",
               "the start", "original", "first look"):
        return None, None, []
    steps = [s for s in g.history() if s.get("verification") != "tampered"]
    ago = _ago(low)
    target = None
    if ago is not None:
        target = _now() - ago
    else:
        try:
            target = datetime.fromisoformat(p).timestamp() + 86399
        except ValueError:
            target = None
    if target is not None:
        before = [s for s in steps if float(s.get("created_at", 0)) <= target]
        return (before[-1] if before else None), None, []
    m = re.match(r"^the one (.+) made$", low)
    if m:
        who = m.group(1).strip()
        hits = [s for s in steps if who in str((s.get("author") or {}).get("model") or "").lower()]
        if not hits:
            return None, "I couldn't find a look %s made." % who, []
        return hits[-1], None, []
    hits = [s for s in steps if low in str(s.get("name") or "").lower()]
    if not hits:
        return None, "I couldn't find a look called %s." % p, []
    exact = [s for s in hits if str(s.get("name") or "").lower() == low]
    if len(exact) == 1:
        return exact[0], None, []
    if len(hits) > 1:
        return None, None, hits
    return hits[0], None, []


def push(action: dict) -> dict:
    """Send one action to the open desktop page."""
    try:
        from agent_friday.services import desktop_bus
        return desktop_bus.send([action], timeout=4.0)
    except Exception as e:  # the page is a nicety; the choice is already kept
        return {"delivered": False, "reason": str(e)}


def _said(scene_name: str) -> str:
    """A structure's name as said: "Ocean of Light", "Giga Earth"."""
    return scene_name.title().replace(" Of ", " of ").replace(" (Rez)", "")


def show(name) -> str:
    """Show the structure `name` means, kept like the picker's choice."""
    from agent_friday.routes.insights import SCENE_NAMES, pin_scene, scene_index_for
    i = scene_index_for(name)
    if i < 0:
        return "I don't have a structure called %s. I can show %s." % (
            str(name or "that").strip() or "that",
            ", ".join(_said(n) for n in SCENE_NAMES[:-1]) + " or " + _said(SCENE_NAMES[-1]))
    try:
        pin_scene(i)
    except OSError:
        return "I couldn't keep that choice just now."
    sent = push({"type": "scene", "target": SCENE_NAMES[i]})
    if not sent.get("delivered"):
        return "I'll show %s when the Friday window is open." % _said(SCENE_NAMES[i])
    return "Now showing %s." % _said(SCENE_NAMES[i])


def handle(inp: dict) -> str:
    inp = inp or {}
    action = inp.get("action")
    if action not in ACTIONS:
        return ("I can describe my look, evolve now, undo, go back to an earlier look, turn "
                "evolution on or off, or show another structure.")
    if action == "show":
        return show(inp.get("structure"))
    if action == "status":
        st = gr.status()
        if not st.get("enabled"):
            return "Evolution is off. I'm keeping this look."
        if st.get("waiting"):
            return _waiting_text({"reason": st["waiting"].get("reason"), "fixes": st["waiting"].get("fixes")})
        days = max(0, int((float(st.get("next_due") or 0) - _now()) // 86400))
        who = {"frontier": "a frontier model I pick", "seeded": "me, with no model"}.get(
            st.get("author"), str(st.get("author")).split(":")[-1])
        return "Evolution is on. My next change is in about %s days, made by %s." % (_say_num(days), who)
    if action == "describe":
        return describe()
    if action == "evolve_now":
        res = gr.evolve_now()
        s = res.get("status")
        if s == "stepped":
            return "Done. " + describe()
        if s == "pending":
            return "I've made a new look. It's waiting for your OK on screen."
        if s == "waiting":
            return _waiting_text(res)
        if s == "skipped":
            return "My look didn't change this time: %s." % res.get("reason")
        return "I couldn't change my look right now."
    if action == "undo":
        res = gr.undo()
        if res.get("status") != "undone":
            return "There's nothing to undo. I still look the way I started."
        cur = g.active_step()
        return ("Done. I'm back to %s." % (cur.get("name") or "my previous look")) if cur \
            else "Done. I'm back to how I first looked."
    if action == "rollback":
        step, err, many = _resolve(inp.get("when"))
        if err:
            return err
        if many:
            names = [s.get("name") or "an unnamed look" for s in many[-2:]]
            return "Which one: %s or %s?" % (names[0], names[1])
        if step is None:
            g.reset()
            return "Done. I'm back to how I first looked."
        g.rollback(step["content_hash"])
        return "Done. I'm back to %s. %s." % (step.get("name") or "that look", _credit(step))
    if action == "set_enabled":
        on = bool(inp.get("enabled"))
        gr.set_settings(enabled=on)
        return "Evolution is on. I'll change a little each week." if on else \
            "Evolution is off. I'll keep this look."
    if action == "set_author":
        try:
            gr.set_settings(author=str(inp.get("author") or ""))
        except ValueError as e:
            return "I can't use that: %s." % e
        a = gr.settings()["author"]
        who = {"frontier": "a frontier model I pick", "seeded": "I myself, with no model,"}.get(
            a, a.split(":")[-1])
        return "From now on, %s will make my looks." % who
    if action == "use_local":
        try:
            res = gr.use_local_instead(str(inp.get("model") or gr._local_seat() or ""))
        except ValueError as e:
            return "I can't use that model: %s." % e
        return ("Done. " + describe()) if res.get("status") == "stepped" else \
            "I've switched to the local model, but the change didn't happen yet: %s." % res.get("reason", res.get("status"))
    return ""


def _tool_avatar_evolution(inp):
    return handle(inp if isinstance(inp, dict) else {})


TOOLS = [
    {"name": "avatar_evolution",
     "description": (
         "Friday's own look on the holographic desktop, which changes a little each week. "
         "action=describe says what the current look changed and which model made it; "
         "evolve_now makes the next change now; undo goes back to the previous look; "
         "rollback goes to an earlier look named by 'when' (a look's name, 'last month', "
         "'two weeks ago', 'the one <model> made', or 'the first look'); set_enabled turns "
         "weekly evolution on or off; set_author picks who makes the changes ('frontier', "
         "'seeded', 'local:<model>', or 'cloud:<provider>:<model>'); use_local switches to the "
         "local model when a cloud model is missing; status says whether it is on and when "
         "the next change is; show switches the structure on screen to the one named by "
         "'structure' (any of its names: 'the wormhole', 'Hawking Radiation', 'the Dyson "
         "sphere', 'Giga Earth'). Say the result in one or two plain sentences; if it asks "
         "'Which one', ask the owner that question."),
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(ACTIONS)},
         "when": {"type": "string"},
         "enabled": {"type": "boolean"},
         "author": {"type": "string"},
         "model": {"type": "string"},
         "structure": {"type": "string"}},
         "required": ["action"]}},
]
RINGS = {"avatar_evolution": 1}
HANDLERS = {"avatar_evolution": _tool_avatar_evolution}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
