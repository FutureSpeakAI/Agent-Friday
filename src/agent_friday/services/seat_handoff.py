"""Switch this chat's seat, or hand one task to the local model: always on the owner's card.

Two things the owner can ask for in chat or by voice, through one tool (`switch_seat`):

  * **switch** -- this conversation's seat moves between the local model and a cloud model. It is the
    same binding the chat's model picker writes (`conversations.patch(seat=...)`), so the next turn
    runs on it and the sidebar shows it.
  * **handoff** -- ONE task runs on the local model, on this computer, as a pinned background task
    (`_spawn_task(..., pin_to_seat=True)`, the path private voice already uses). The chat keeps its seat.

NOTHING HAPPENS ON THE MODEL'S WORD. The tool raises one approval card and returns; the card's
decision hook (`on_decision`) does the switch or the handoff after the owner approves it, and posts
the outcome in the conversation. Voice uses the same card: the voice reads it back, the owner
decides it.

THE OFFER AFTER A REFUSAL. When the vault gate refuses a tool call because a cloud seat may not see
its data, `denial_result` turns the refusal into an offer: one card, "Run this on the local model",
carrying the refused step and the owner's request. Further refusals in the same conversation join
that card while it is pending, so the owner sees one card, not one per refused call. The text the
model receives says the card exists and asks for one sentence, not an argument about the rule.

WHERE THE DATA GOES. A handoff's input goes TO an on-device model, so the vault check exempts this
tool only when the target resolves to a model that really runs here (privacy/vault_access
LOCAL_HANDOFF_TOOLS); a cloud-tagged Ollama model is never a local target. The target comes from the
owner's seat bindings (services/tiers), never from the tool's arguments.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

_log = logging.getLogger(__name__)

#: Parallel tool calls in one turn can be refused at the same moment; the find-or-create of the
#: conversation's card runs under this lock so they join ONE card.
_RAISE_LOCK = threading.RLock()

KIND = "seat_handoff"
TOOL = "switch_seat"

#: Refused steps one offer card names; the rest are counted.
MAX_STEPS = 8

#: How long an approved switch waits for the local-seat survey to answer.
SURVEY_WAIT_S = 5.0


# ── Where a handoff or a switch would go ────────────────────────────────────

def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _local_only() -> bool:
    mode = str(((_settings().get("model_routing") or {}).get("mode") or "")).strip().lower()
    return mode == "local_only"


def _inventory():
    """The model names a daemon on this computer can serve (the Ollama daemon's installed tags and
    the local descriptors' models), or None when that cannot be verified."""
    try:
        from agent_friday.services.model_seat_gate import _installed_local_models
        return _installed_local_models()
    except Exception:
        return None


def _serving_url(model: str) -> str:
    """The address `model` is served from: its local descriptor's base_url, else the Ollama URL."""
    try:
        from agent_friday.services.model_seat_gate import _local_openai_descriptor
        desc = _local_openai_descriptor(model)
        if desc:
            return str(desc.get("base_url") or "")
    except Exception:
        return ""
    try:
        from agent_friday.services.local_call import ollama_url
        return ollama_url()
    except Exception:
        return ""


def _loopback(url: str) -> bool:
    """True only for an http(s) address on this machine (localhost, 127.0.0.0/8, ::1)."""
    try:
        import ipaddress
        from urllib.parse import urlparse
        u = urlparse(str(url or ""))
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        host = u.hostname.strip("[]").lower()
        if host == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except Exception:
        return False


def _runs_here(model: str) -> str:
    """Empty when `model` really runs on this computer, else why not. By name (not a cloud id or a
    cloud-tagged Ollama model), by the local daemon's inventory, and by the address it is served
    from (loopback only): a "local" model relayed to another host is not local."""
    from agent_friday.privacy.vault_access import is_on_device
    if not is_on_device(model):
        return "%s does not run on this computer" % model
    names = _inventory()
    if names is None:
        return "this computer's local models could not be listed"
    if model not in names:
        return "%s is not installed on this computer" % model
    if not _loopback(_serving_url(model)):
        return "%s is served from another computer" % model
    return ""


def local_target() -> tuple:
    """(model, reason): the on-device model a handoff runs on, or ("", why there is none).

    The large local seat, as the owner bound it. Never substitutes across the local/cloud line: a seat
    bound to a cloud model, to a cloud-tagged Ollama model, to a model the local daemon does not have,
    or to a daemon that is not on this computer, is no local target. Checked when the card is raised
    and again when it is decided.
    """
    try:
        from agent_friday.services import tiers
        res = tiers.resolve(tiers.LARGE_LOCAL)
    except Exception as e:
        return "", "the local seat could not be read (%s)" % e
    if not res.ok:
        return "", res.reason or "no local model is available"
    if not res.is_local:
        return "", "%s does not run on this computer" % res.model
    why = _runs_here(res.model)
    if why:
        return "", why
    return res.model, ""


def _cloud_target() -> tuple:
    """(model, reason) for moving a chat to the cloud."""
    if _local_only():
        return "", "local-only mode is on, so no chat runs on a cloud model"
    try:
        from agent_friday.services import tiers
        res = tiers.resolve(tiers.CLOUD_FRONTIER)
    except Exception as e:
        return "", "the cloud seat could not be read (%s)" % e
    if not res.ok:
        return "", res.reason or "no cloud model is configured"
    return res.model, ""


def local_target_for(tool_name, tool_input) -> str:
    """The on-device model this call would hand its input to, or "".

    For the vault check only: the target is resolved here, from the owner's bindings, so a tool's
    arguments can never name their own exemption.
    """
    if tool_name != TOOL:
        return ""
    inp = tool_input if isinstance(tool_input, dict) else {}
    if _action(inp) == "switch" and str(inp.get("to") or "local").strip().lower() != "local":
        return ""
    return local_target()[0]


def _provider_of(model: str) -> str:
    """The provider the chat's model picker would record for this model id."""
    try:
        from agent_friday.services.model_catalog import build_catalog
        for m in (build_catalog().get("models") or []):
            if m.get("id") == model:
                provs = m.get("providers") or ([m.get("provider")] if m.get("provider") else [])
                if provs:
                    return str(provs[0])
    except Exception:
        pass
    from agent_friday.privacy.vault_access import is_on_device
    if is_on_device(model):
        return "ollama"
    try:
        from agent_friday.routing.model_router import provider_family
        return provider_family(model) or "cloud"
    except Exception:
        return "cloud"


def _action(inp: dict) -> str:
    """A task is a handoff; otherwise `to` names where this chat moves."""
    return "handoff" if str((inp or {}).get("task") or "").strip() else "switch"


# ── The card ────────────────────────────────────────────────────────────────

def _ctx():
    """(conversation_id, owner_text) for the call being made."""
    try:
        from agent_friday.services import agent as _ag
        return (_ag._CURRENT_CONVERSATION.get() or "", _ag._CURRENT_OWNER_TEXT.get() or "")
    except Exception:
        return "", ""


def _pending(cid: str, action: str):
    """The pending card of this kind for this conversation and action, or None. A card with no
    conversation is never joined: two unrelated requests must not share one."""
    if not cid:
        return None
    try:
        from agent_friday.services import approvals as ap
        for rec in ap.list_approvals(status="pending", kind=KIND):
            p = rec.get("payload") or {}
            if p.get("conversation_id", "") == (cid or "") and p.get("action") == action:
                return rec
    except Exception:
        pass
    return None


#: How much of one step's input the card shows.
STEP_INPUT_CHARS = 300


def _handoff_text(model: str, payload: dict) -> tuple:
    """(title, description) for a handoff card. The description shows what will run: the task's
    text, the owner's request and every refused step with its input. It is for the card on the
    screen only; notices and text messages name only the tool (approvals.notice_title)."""
    name = payload.get("title") or "this task"
    steps = payload.get("steps") or []
    lines = ["Friday hands this to %s, the model on this computer. The result is posted in this chat "
             "and kept on this PC: the chat's cloud model is not sent it. This chat stays on its "
             "current model." % model, "", "Task: " + name]
    task = str(payload.get("task") or "").strip()
    if task and task != name:
        lines.append(task[:900])
    said = str(payload.get("owner_text") or "").strip()
    if said and said not in (name, task):
        lines += ["", "Your request: " + said[:400]]
    if steps:
        lines += ["", "Steps it will run (refused on the cloud seat):"]
        for st in steps[:MAX_STEPS]:
            inp = json.dumps(st.get("input") or {}, default=str, ensure_ascii=False)
            if len(inp) > STEP_INPUT_CHARS:
                inp = inp[:STEP_INPUT_CHARS] + "..."
            lines.append("- %s %s" % (st.get("tool"), inp))
        if len(steps) > MAX_STEPS:
            lines.append("- and %d more" % (len(steps) - MAX_STEPS))
    lines += ["", "Anything in it that sends, books, deletes or changes something outside this computer "
              "still asks you on its own card.",
              "", "To move the whole chat to the local model instead, say \"switch this chat to local\"."]
    return ("Run this on the local model (%s)?" % model)[:200], "\n".join(lines)[:3800]


def _raise(payload: dict) -> dict:
    """Create the card for `payload`, or fold it into this conversation's pending card of the same
    action. Returns the card."""
    with _RAISE_LOCK:
        return _raise_locked(payload)


def _raise_locked(payload: dict) -> dict:
    from agent_friday.services import approvals as ap
    cid = payload.get("conversation_id") or ""
    action = payload["action"]
    model = payload["model"]
    have = _pending(cid, action)
    if have is not None:
        if action != "handoff":
            return have
        old = dict(have.get("payload") or {})
        merged = dict(old)
        steps = list(old.get("steps") or [])
        for st in payload.get("steps") or []:
            if st not in steps:
                steps.append(st)
        merged["steps"] = steps
        if payload.get("task") and payload.get("task") not in str(old.get("task") or ""):
            merged["task"] = (str(old.get("task") or "") + "\n\n" + payload["task"]).strip()
        if not old.get("owner_text") and payload.get("owner_text"):
            merged["owner_text"] = payload["owner_text"]
        if merged == old:
            return have
        # The card on the screen is never changed under the owner: what they approve is what they
        # saw. The joined request goes on a NEW card and the old one is retired; if the owner
        # decided the old one meanwhile, only the new part goes on the new card.
        retired = ap.expire_matching(
            lambda r, _aid=have["approval_id"]: r.get("approval_id") == _aid,
            "system:seat_handoff", "replaced by a card that also carries a later refused step")
        payload = merged if retired else payload
        model = payload.get("model") or model
    if action == "handoff":
        title, desc = _handoff_text(model, payload)
        what = "hand one task to the local model %s on this computer" % model
    else:
        where = "the local model, on this computer" if payload.get("to") == "local" else "a cloud model"
        title = ("Switch this chat to %s?" % model)[:200]
        desc = ("This chat would answer on %s (%s) from your next message. Now: %s. Pick a model in the chat "
                "header at any time to change it back." % (model, where, payload.get("from") or "the default"))
        if payload.get("to") != "local":
            desc += (" From your next message, the conversation so far is sent to %s, off this computer, "
                     "as each turn's history." % model)
        what = "switch this conversation's model to %s" % model
    return ap.create_approval(
        kind=KIND, subject_type="conversation",
        subject_id="%s:%s:%s" % (cid or "main", action, uuid.uuid4().hex[:8]),
        title=title, description=desc, action_description=what,
        payload=payload, requested_by="friday", force_gate=True)


def _said(card: dict, line: str) -> str:
    return ("SEAT_NEEDS_YES:%s %s Nothing has changed: it happens only when the owner approves the card. "
            "Say so in one sentence (read the card's question back if this is a voice call), then stop."
            % (card.get("approval_id"), line))


# ── The tool ────────────────────────────────────────────────────────────────

def tool(inp) -> str:
    """Tool handler for `switch_seat`: raise the card, change nothing."""
    inp = inp if isinstance(inp, dict) else {}
    action = _action(inp)
    cid, owner_text = _ctx()
    if action == "handoff":
        task = str(inp.get("task") or "").strip()
        model, why = local_target()
        if not model:
            return "SEAT_FAIL: no local model can take it: %s. Say so plainly." % why
        title = task.splitlines()[0][:80]
        card = _raise({"action": "handoff", "conversation_id": cid, "model": model, "title": title,
                       "task": task, "steps": [], "owner_text": owner_text[:2000], "origin": "asked"})
        # The task is not echoed: the reply goes back to the seat that asked, and the task can be the
        # private part.
        return _said(card, "A card asks the owner to run this task on %s, the local model." % model)
    to = str(inp.get("to") or "local").strip().lower()
    if to not in ("local", "cloud"):
        return "SEAT_FAIL: to is local or cloud."
    if not cid:
        return "SEAT_FAIL: there is no chat in scope to switch."
    model, why = local_target() if to == "local" else _cloud_target()
    if not model:
        return "SEAT_FAIL: cannot switch this chat to %s: %s." % (to, why)
    current = ""
    try:
        from agent_friday.services import conversations as _cv
        current = str(((_cv.effective_seat(cid) or {}).get("model")) or "")
    except Exception:
        pass
    if current == model:
        return "SEAT_UNCHANGED: this chat already runs on %s." % model
    card = _raise({"action": "switch", "conversation_id": cid, "model": model, "to": to,
                   "provider": _provider_of(model), "from": current or "the default model"})
    return _said(card, "A card asks the owner to switch this chat to %s." % model)


# ── After a vault refusal ───────────────────────────────────────────────────

def denial_result(tool_name, tool_input, detail, session_ctx=None, head=None) -> str:
    """The tool_result a cloud seat receives when the vault gate refuses a call.

    Starts with the `[VAULT ACCESS DENIED]` sentinel the receipts read. `head` is the gate's own
    refusal text (vault_access.refusal_text) when the caller has it; the offer follows it. When a
    local model can take the work, ONE card offering it is raised (or joined) and the text says so.
    A never-send floor refusal carries no offer: that material leaves this PC by no route, whichever
    model would send it.
    """
    if not head:
        head = ("[VAULT ACCESS DENIED] This tool call references %s data, which this cloud seat may not "
                "see, so it was not run." % detail)
    head = str(head).rstrip()
    try:
        from agent_friday.privacy.vault_access import denial_component
        if denial_component(detail)[0] == "privacy.never_send_floor":
            return head
    except Exception:
        return head
    if tool_name == TOOL:
        return head + " No on-device model was verified for it. Say so plainly."
    try:
        model, why = local_target()
        if not model:
            return head + " No local model can take it right now (%s). Say so plainly." % why
        sc = session_ctx or {}
        cid, owner_text = _ctx()
        cid = cid or str(sc.get("conversation_id") or "")
        owner_text = owner_text or str(sc.get("owner_text") or "")
        inp = tool_input if isinstance(tool_input, dict) else {"input": tool_input}
        if tool_name == "spawn_task" and str(inp.get("prompt") or "").strip():
            title, task, steps = (str(inp.get("name") or "")[:80] or "Background task",
                                  str(inp["prompt"]).strip(), [])
        else:
            title = (owner_text.strip().splitlines() or ["Finish what the cloud seat could not"])[0][:80]
            task, steps = "", [{"tool": str(tool_name), "input": inp}]
        card = _raise({"action": "handoff", "conversation_id": cid, "model": model, "title": title,
                       "task": task, "steps": steps, "owner_text": owner_text[:2000],
                       "origin": "vault_denial"})
    except Exception as e:
        _log.warning("seat handoff: could not raise the offer card (%s)", e)
        return head + " Switching this chat to the local model would let it run."
    return head + (" ONE card (%s) now offers the owner to run it on %s, the local model on this "
                   "computer. Tell them in one sentence and stop: do not retry this call, do not raise "
                   "another card, and do not argue the refusal is intended. To move the whole chat "
                   "instead, they can ask to switch it to local (switch_seat)."
                   % (card.get("approval_id"), model))


# ── On the Yes ──────────────────────────────────────────────────────────────

_HANDOFF_PROMPT = ("The owner approved handing this to you, the local model on this computer, from a chat "
                   "whose cloud model could not do it. Do it fully, with whatever tools it needs, then report "
                   "the real outcome in a few plain sentences. Never claim a step you did not complete.\n\n")


def _handoff_prompt(p: dict) -> str:
    parts = [_HANDOFF_PROMPT]
    if p.get("owner_text"):
        parts.append("The owner's request: " + str(p["owner_text"]) + "\n\n")
    if p.get("task"):
        parts.append("Task: " + str(p["task"]) + "\n\n")
    steps = p.get("steps") or []
    if steps:
        parts.append("Steps the cloud model was refused (do them if they still serve the request):\n")
        for s in steps:
            parts.append("- %s %s\n" % (s.get("tool"), json.dumps(s.get("input") or {}, default=str)[:1500]))
    return "".join(parts).strip()


def _do_handoff(p: dict) -> tuple:
    model, why = local_target()
    if not model:
        return False, "The local model could not take it: %s. Nothing was run." % why
    from agent_friday.services import agent as _ag
    # The card the owner approved says the result stays on this PC: the task
    # carries that promise (agent._spawn_task local_only_result).
    tid = _ag._spawn_task(str(p.get("title") or "Local handoff")[:120], _handoff_prompt(p),
                          description="Handed to the local model from a chat", model=model, tools=None,
                          conversation_id=p.get("conversation_id") or None, orb_icon="🖥", pin_to_seat=True,
                          local_only_result=True)
    if not tid:
        return False, "The local task could not be started. Nothing was run."
    moved = "" if model == p.get("model") else " (the local seat is now %s)" % model
    return True, "Handed to %s on this computer%s as task %s. The result will be posted here." % (model, moved, tid)


def _do_switch(p: dict) -> tuple:
    cid = p.get("conversation_id") or ""
    want = p.get("model") or ""
    if p.get("to") == "local":
        model, why = local_target()
        if not model:
            return False, "The chat was not switched: %s." % why
        want = model
    elif _local_only():
        return False, "The chat was not switched: local-only mode is on."
    seat = {"model": want, "provider": p.get("provider") or _provider_of(want)}
    note = None
    try:
        # The same refusal the chat's model picker gets (one local model at a
        # time). A survey still running is waited for briefly, not read as no.
        from agent_friday.routes.conversations import (_SeatSurveyPending,
                                                       _why_this_seat_cannot_be_bound)
        deadline = time.monotonic() + SURVEY_WAIT_S
        note = _why_this_seat_cannot_be_bound(seat)
        while isinstance(note, _SeatSurveyPending) and time.monotonic() < deadline:
            time.sleep(0.25)
            note = _why_this_seat_cannot_be_bound(seat)
    except Exception:
        note = None
    if note:
        return False, "The chat was not switched: %s" % note
    from agent_friday.services import conversations as _cv
    if _cv.patch(cid, seat=seat) is None:
        return False, "The chat was not switched: it is no longer there."
    now = str(((_cv.effective_seat(cid) or {}).get("model")) or "")
    if now != want:
        return False, "The chat was not switched: it still reads %s." % (now or "the default")
    return True, "This chat now runs on %s. It takes effect on your next message." % want


def on_decision(card: dict) -> None:
    """Carry out an approved card: exactly once, and say how it went in the conversation."""
    if (card or {}).get("kind") != KIND or card.get("status") != "approved":
        return
    from agent_friday.services import approvals as ap
    aid = card.get("approval_id")
    if not ap.claim_for_execution(aid):
        return
    p = (ap.get_approval(aid) or card).get("payload") or {}
    try:
        ok, text = _do_switch(p) if p.get("action") == "switch" else _do_handoff(p)
    except Exception as e:
        ok, text = False, "It could not be done: %s: %s" % (type(e).__name__, e)
    try:
        ap.mark_used(aid, "seat_handoff", {"ok": ok, "summary": text[:300]})
    except Exception:
        pass
    try:
        from agent_friday.services import item_actions
        item_actions._post_back(p.get("conversation_id"), text, aid)
    except Exception:
        pass


_REGISTERED = False


def register() -> None:
    """Idempotent. Called when the tool registry loads, so a card approved after a restart still runs."""
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook(KIND, on_decision)
        _REGISTERED = True
    except Exception as e:
        _log.warning("seat handoff: could not register the decision hook (%s)", e)
