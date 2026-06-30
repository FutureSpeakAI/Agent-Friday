"""Workspace Studio — Friday as a per-workspace customization agent.

Every workspace window has a 💬 chat button (next to the 🎤 mic). It opens a
contextual chat scoped to THAT workspace. The user can give feedback, request
features, ask Friday to apply changes live, and roll them back.

The mechanics, kept deliberately simple and safe for a local single-user OS:

  * Each workspace owns a JSON doc at ~/.friday/workspace_studio/<ws>.json with
    its chat history, the *current* customization, and a stack of versioned
    snapshots.
  * A "customization" is a small, declarative, whitelisted patch (scoped CSS, a
    pinned note, an accent colour, density, hidden sections, quick-action
    buttons). The frontend applies it live to the workspace window — no React
    recompile, no server restart. That's the "hot-reload".
  * Before any change is applied the *current* state is snapshotted as a new
    version, so every change is revertible. Revert is itself snapshotted, so it
    too can be undone.

Friday decides — from the user's message — whether to just talk or to emit a
customization patch. She returns the patch in a fenced ```friday-customize
{json}``` block which this module parses, sanitizes, applies, and versions.
"""

import json
import logging
import re
import uuid
from datetime import datetime

from agent_friday.core import FRIDAY_DIR

_log = logging.getLogger("friday.workspace_studio")

WS_STUDIO_DIR = FRIDAY_DIR / "workspace_studio"
WS_STUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Cap stored history so the docs never grow without bound.
_MAX_CHAT = 200
_MAX_VERSIONS = 40

# Keys a customization patch may contain. Anything else is dropped.
_ALLOWED_KEYS = {"css", "note", "accent", "density", "hidden", "actions", "summary"}
_ALLOWED_DENSITY = {"comfortable", "compact"}


# ── persistence ────────────────────────────────────────────────────────────

def _ws_path(ws_id):
    safe = re.sub(r"[^a-z0-9_-]", "", str(ws_id or "").lower())[:48] or "unknown"
    return WS_STUDIO_DIR / f"{safe}.json"


def _blank_doc(ws_id):
    return {
        "workspace": ws_id,
        "chat": [],
        "customization": {},
        "versions": [],
        "updated": datetime.now().isoformat(),
    }


def load_ws_doc(ws_id):
    p = _ws_path(ws_id)
    if not p.exists():
        return _blank_doc(ws_id)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc.setdefault("workspace", ws_id)
        doc.setdefault("chat", [])
        doc.setdefault("customization", {})
        doc.setdefault("versions", [])
        return doc
    except Exception:
        return _blank_doc(ws_id)


def save_ws_doc(ws_id, doc):
    doc["updated"] = datetime.now().isoformat()
    doc["chat"] = doc.get("chat", [])[-_MAX_CHAT:]
    doc["versions"] = doc.get("versions", [])[-_MAX_VERSIONS:]
    try:
        _ws_path(ws_id).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except Exception as e:  # pragma: no cover - disk failure
        _log.warning("workspace_studio save failed (%s): %s", ws_id, e)
    return doc


# ── customization sanitation ───────────────────────────────────────────────

def _sanitize_css(css):
    """Strip anything that could break out of a <style> or run script.

    The frontend additionally scopes every rule to the workspace root, so this
    only has to neutralise injection, not enforce scoping.
    """
    if not isinstance(css, str):
        return ""
    css = css[:8000]
    # Kill style/script breakouts and js: urls / expression() hacks.
    css = re.sub(r"</\s*style", "", css, flags=re.I)
    css = re.sub(r"<\s*script", "", css, flags=re.I)
    css = re.sub(r"javascript\s*:", "", css, flags=re.I)
    css = re.sub(r"expression\s*\(", "(", css, flags=re.I)
    css = re.sub(r"@import[^;]+;?", "", css, flags=re.I)
    return css.strip()


def _sanitize_patch(patch):
    """Coerce a raw model patch into the whitelisted, typed shape."""
    if not isinstance(patch, dict):
        return {}
    out = {}
    for k, v in patch.items():
        if k not in _ALLOWED_KEYS:
            continue
        if k == "css":
            out["css"] = _sanitize_css(v)
        elif k in ("note", "summary"):
            out[k] = (str(v)[:1500]).strip() if v is not None else None
        elif k == "accent":
            if v is None:
                out["accent"] = None
            elif isinstance(v, str) and re.match(r"^#?[0-9a-fA-F]{3,8}$", v.strip()):
                a = v.strip()
                out["accent"] = a if a.startswith("#") else "#" + a
        elif k == "density":
            out["density"] = v if v in _ALLOWED_DENSITY else None
        elif k == "hidden":
            if isinstance(v, list):
                out["hidden"] = [str(s)[:200] for s in v][:40]
            elif v is None:
                out["hidden"] = None
        elif k == "actions":
            if isinstance(v, list):
                acts = []
                for a in v[:8]:
                    if isinstance(a, dict) and a.get("label") and a.get("prompt"):
                        acts.append({
                            "label": str(a["label"])[:40],
                            "prompt": str(a["prompt"])[:400],
                        })
                out["actions"] = acts
            elif v is None:
                out["actions"] = None
    return out


def _merge_customization(current, patch):
    """Patch semantics: present keys override; explicit None clears a key."""
    merged = dict(current or {})
    for k, v in patch.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    merged.pop("summary", None)  # summary is per-change, never part of state
    return merged


# ── versioning + apply / revert ────────────────────────────────────────────

def _snapshot(doc, label):
    """Push the CURRENT customization onto the version stack."""
    ver = {
        "id": "v" + uuid.uuid4().hex[:8],
        "ts": datetime.now().isoformat(),
        "label": (label or "change")[:120],
        "customization": json.loads(json.dumps(doc.get("customization", {}))),
    }
    doc.setdefault("versions", []).append(ver)
    return ver


def _apply_to_doc(doc, patch, label=None):
    """Snapshot the doc's CURRENT customization, then merge the sanitized patch
    in, mutating `doc` in place. Returns the snapshot version (revert TO it to
    undo) or None if the patch was empty. Does not persist — caller saves."""
    clean = _sanitize_patch(patch)
    if not clean:
        return None
    ver = _snapshot(doc, label or clean.get("summary") or "change")
    doc["customization"] = _merge_customization(doc.get("customization", {}), clean)
    return ver


def apply_customization(ws_id, patch, label=None):
    """Load → snapshot → merge → save. Returns (doc, version)."""
    doc = load_ws_doc(ws_id)
    ver = _apply_to_doc(doc, patch, label)
    if ver is None:
        return doc, None
    save_ws_doc(ws_id, doc)
    return doc, ver


def revert_customization(ws_id, version_id):
    """Restore the customization captured in `version_id`. The pre-revert state
    is itself snapshotted first, so reverts are undoable."""
    doc = load_ws_doc(ws_id)
    target = next((v for v in doc.get("versions", []) if v["id"] == version_id), None)
    if not target:
        return None
    _snapshot(doc, "before revert")
    doc["customization"] = json.loads(json.dumps(target.get("customization", {})))
    save_ws_doc(ws_id, doc)
    return doc


def reset_customization(ws_id):
    """Snapshot current, then clear all customization (back to baseline)."""
    doc = load_ws_doc(ws_id)
    if doc.get("customization"):
        _snapshot(doc, "before reset")
    doc["customization"] = {}
    save_ws_doc(ws_id, doc)
    return doc


def clear_chat(ws_id):
    doc = load_ws_doc(ws_id)
    doc["chat"] = []
    save_ws_doc(ws_id, doc)
    return doc


def all_customizations():
    """Map of ws_id -> current customization for every studio doc, so the UI can
    apply everything on first paint."""
    out = {}
    for p in WS_STUDIO_DIR.glob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            cust = doc.get("customization") or {}
            if cust:
                out[doc.get("workspace") or p.stem] = cust
        except Exception:
            pass
    return out


# ── the agentic chat turn ──────────────────────────────────────────────────

_PATCH_RE = re.compile(r"```friday-customize\s*(\{.*?\})\s*```", re.S)


def _strip_patch_block(text):
    return _PATCH_RE.sub("", text or "").strip()


def _extract_patch(text):
    m = _PATCH_RE.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _system_prompt(ws_id, ws_label, current, base_system):
    cur = json.dumps(current or {}, indent=2)
    guide = f"""

═══ WORKSPACE STUDIO MODE ═══
You are talking to the user *inside* the "{ws_label}" workspace ({ws_id}) of
their Friday desktop OS. This is a contextual chat scoped to THIS workspace. The
user may: give feedback on it, request new features, ask you to apply changes
live, or roll changes back. Keep replies short, warm, and concrete.

You can reshape this workspace by emitting a customization patch. When (and only
when) the user actually wants a visible change, end your reply with a fenced
block:

```friday-customize
{{"summary":"short human description","css":"...","note":"...","accent":"#00d4ff","density":"compact","hidden":["selector"],"actions":[{{"label":"Refresh","prompt":"refresh the data"}}]}}
```

Rules for the patch:
- It is a PATCH. Include only the keys you are changing. Use null to clear a key
  (e.g. {{"accent":null}} removes a custom accent).
- "css": plain CSS. EVERY selector MUST start with `.ws-custom-root` — that is
  the wrapper around this workspace's content. e.g.
  `.ws-custom-root .card{{border-radius:14px}}`. No @import, no <style>, no JS.
- "note": a short pinned note/banner shown at the top of the workspace (markdown
  ok). Good for reminders or summarising what you changed.
- "accent": a hex colour that tints this workspace's highlights.
- "density": "compact" or "comfortable".
- "hidden": array of CSS selectors (within `.ws-custom-root`) to hide.
- "actions": up to a few quick-action buttons; each {{label, prompt}} sends its
  prompt back into this same workspace chat when clicked.
- For genuinely new data/features that need backend work you cannot express as a
  patch, say so plainly and offer to spin up a background task — do NOT fake it
  with CSS.
- If the user is just chatting or asking a question, reply normally with NO
  patch block.

Current customization for this workspace:
{cur}
"""
    return (base_system or "") + guide


def workspace_chat_turn(ws_id, ws_label, message, system=None, generate=None):
    """Run one workspace-studio chat turn.

    `generate(messages, system, orb_label)` -> reply text. Injected so the
    route can wire the model router (and so tests can stub it). If omitted we
    import the router lazily.
    """
    ws_label = ws_label or ws_id
    doc = load_ws_doc(ws_id)
    doc["chat"].append({
        "role": "user", "text": message,
        "time": datetime.now().isoformat(),
    })

    history = [
        {"role": "user" if m["role"] == "user" else "assistant", "content": m["text"]}
        for m in doc["chat"][-16:]
    ]
    sys_prompt = _system_prompt(ws_id, ws_label, doc.get("customization", {}), system)

    if generate is None:
        from agent_friday.services.model_router import _generate_text

        def generate(messages, system, orb_label):
            return _generate_text(messages, system=system, max_tokens=1800,
                                  orb_label=orb_label, workspace=ws_id)

    reply = ""
    try:
        reply = generate(history, sys_prompt, f"🛠️ {ws_label} Studio") or ""
    except Exception as e:
        _log.warning("workspace_chat_turn generate error (%s): %s", ws_id, e)
        reply = "I hit an error reaching the model. Try again in a moment."

    patch = _extract_patch(reply)
    visible_reply = _strip_patch_block(reply) or reply
    applied_version = None
    if patch:
        label = (patch.get("summary") if isinstance(patch, dict) else None) or "change"
        # Mutate the in-memory doc (which already holds the pending user message)
        # so nothing is lost; we persist once at the end of the turn.
        applied_version = _apply_to_doc(doc, patch, label)

    entry = {
        "role": "friday", "text": visible_reply,
        "time": datetime.now().isoformat(),
    }
    if applied_version:
        # The snapshot we just pushed is the PRE-change state; reverting to it
        # undoes this change. Store its id so the UI can offer a Revert button.
        entry["applied"] = True
        entry["revert_to"] = applied_version["id"]
        entry["change"] = applied_version["label"]
    doc["chat"].append(entry)
    save_ws_doc(ws_id, doc)

    return {
        "status": "ok",
        "response": visible_reply,
        "applied": bool(applied_version),
        "revert_to": applied_version["id"] if applied_version else None,
        "change": applied_version["label"] if applied_version else None,
        "customization": doc.get("customization", {}),
        "versions": doc.get("versions", []),
    }
