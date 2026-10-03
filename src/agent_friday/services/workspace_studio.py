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

#: A workspace id is one plain lowercase name. Anything else is refused, not
#: rewritten: stripping characters made `my.workspace` and `myworkspace`
#: resolve to one file, so a request for one silently read the other.
_WS_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


def check_ws_id(ws_id) -> str:
    s = str(ws_id or "")
    if not _WS_ID.match(s):
        raise ValueError("invalid workspace id")
    return s


def _ws_path(ws_id):
    return WS_STUDIO_DIR / f"{check_ws_id(ws_id)}.json"


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

_DATA_IMAGE_URL = re.compile(r"^data:image/[a-z0-9.+-]+[;,][a-z0-9+/=%;,._-]*$", re.I)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_CSS_REMOTE = re.compile(r"(?:[a-z][a-z0-9+.-]*:)?//[^\s)'\";,]*", re.I)
_CSS_FETCHERS = re.compile(r"(?:-webkit-)?(?:image-set|cross-fade)\s*\(|\bsrc\s*\(", re.I)
_KEPT_MARK = "\x01"


def _sanitize_css(css):
    """Reduce model- or file-supplied CSS to something inert inside a <style>
    element of Friday's own page.

    The page additionally scopes every rule to the workspace root, so this only
    neutralises injection: no `<` (so no tag and no `</style`, however it is
    spelled or nested), no backslash (so no escaped `url(`), no @import, no
    script or expression URLs, and no request to anywhere but an inline image.
    Removals repeat until nothing more can be removed, so a payload cannot be
    assembled from the pieces a single pass leaves behind.
    """
    if not isinstance(css, str):
        return ""
    css = css[:8000].replace("\x00", "").replace(_KEPT_MARK, "")
    css = css.replace("<", "").replace("\\", "")
    kept = []

    def _url(m):
        target = m.group(2).strip()
        if _DATA_IMAGE_URL.match(target):
            kept.append("url(" + target + ")")
            return "%s%d%s" % (_KEPT_MARK, len(kept) - 1, _KEPT_MARK)
        return "url()"

    prev = None
    while prev != css:
        prev = css
        css = _CSS_URL.sub(_url, css)
        css = re.sub(r"javascript\s*:|vbscript\s*:", "", css, flags=re.I)
        css = re.sub(r"expression\s*\(", "(", css, flags=re.I)
        css = re.sub(r"@import[^;]*;?", "", css, flags=re.I)
        css = _CSS_FETCHERS.sub("(", css)
        css = _CSS_REMOTE.sub("", css)
    css = re.sub(_KEPT_MARK + r"(\d+)" + _KEPT_MARK, lambda m: kept[int(m.group(1))], css)
    return css.strip()


def _sanitize_selector(sel):
    """A `hidden` entry is one selector: it cannot open a rule, end a
    declaration, start an at-rule or fetch anything."""
    sel = re.sub(r"[{};@]", "", _sanitize_css(str(sel)))
    return sel.strip()[:200]


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
            elif isinstance(v, str) and re.fullmatch(r"#?[0-9a-fA-F]{3,8}", v.strip()):
                a = v.strip()
                out["accent"] = a if a.startswith("#") else "#" + a
        elif k == "density":
            out["density"] = v if v in _ALLOWED_DENSITY else None
        elif k == "hidden":
            if isinstance(v, list):
                out["hidden"] = [c for c in (_sanitize_selector(x) for x in v[:40]) if c]
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

def _snapshot(doc, label, kind="change"):
    """Push the CURRENT customization onto the version stack.

    `kind` is "change" for the state before a change, "undo_point" for the
    state before an undo (kept so an undo is itself reversible, but skipped
    when the next undo looks for where to go)."""
    ver = {
        "id": "v" + uuid.uuid4().hex[:8],
        "ts": datetime.now().isoformat(),
        "label": (label or "change")[:120],
        "kind": kind,
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
    # A new change restarts the undo walk: the next undo removes THIS change.
    doc.pop("undo_cursor", None)
    return ver


def apply_customization(ws_id, patch, label=None):
    """Load → snapshot → merge → save. Returns (doc, version).

    BLAST RADIUS: a workspace or liquid-UI change may not reach model routing,
    the egress gate, the vault boundary or the safety rules. Those live outside
    what a UI edit can touch on purpose — a self-edit that quietly widened the
    vault boundary would be the worst outcome available in this system, and it
    would be invisible in a diff nobody reads. Refused here, before the patch is
    sanitized, so the refusal cannot be bypassed by a sanitizer that learns a
    new key later.
    """
    try:
        from agent_friday.services.boot_guard import check_blast_radius, safe_mode
        if safe_mode():
            return load_ws_doc(ws_id), None
        ok, why = check_blast_radius(patch)
        if not ok:
            raise PermissionError(why)
    except PermissionError:
        raise
    except Exception:
        pass
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
    _snapshot(doc, "before revert", kind="undo_point")
    doc["customization"] = json.loads(json.dumps(target.get("customization", {})))
    # The walk continues from here: the next undo goes before this version.
    doc["undo_cursor"] = target["id"]
    save_ws_doc(ws_id, doc)
    return doc


def undo_last(ws_id):
    """Undo the most recent change — the "roll that back" case.

    The snapshot stack already made this possible and nothing could reach it:
    `revert_customization` had no route, no tool and no button anywhere in the
    tree, so a mechanism that worked was unusable. This is the one-step form,
    and it is what both the spoken undo and the UI control call.
    """
    doc = load_ws_doc(ws_id)
    # Only the states before CHANGES are steps to walk back through. The
    # snapshots an undo itself takes ("undo_point") are kept so an undo can be
    # reversed by an explicit revert, but they are not steps: treating them as
    # steps is what made two undos in a row go round in a circle.
    steps = [v for v in (doc.get("versions") or []) if v.get("kind", "change") == "change"]
    if not steps:
        return None, "there is nothing to undo for this workspace"
    cursor = doc.get("undo_cursor")
    if cursor:
        idx = next((i for i, v in enumerate(steps) if v["id"] == cursor), None)
        if idx is None:
            target = steps[-1]
        elif idx == 0:
            return None, "there is nothing further to undo for this workspace"
        else:
            target = steps[idx - 1]
    else:
        target = steps[-1]
    _snapshot(doc, "before undo", kind="undo_point")
    doc["customization"] = json.loads(json.dumps(target.get("customization", {})))
    doc["undo_cursor"] = target["id"]
    save_ws_doc(ws_id, doc)
    return doc, None


def restore_as_of(ws_id, when):
    """Restore the state the workspace had AT a moment in time.

    "Put my workspace back to how it was this morning" is the request that
    version ids cannot answer — the user will not remember which of six changes broke
    it. `when` is an ISO timestamp or a datetime; the newest snapshot taken at
    or before it wins, because that snapshot holds the state as it was BEFORE
    the change that followed.
    """
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except Exception:
            return None, "could not read %r as a time" % when
    doc = load_ws_doc(ws_id)
    vers = doc.get("versions") or []
    if not vers:
        return None, "this workspace has no history to restore from"
    candidates = []
    for v in vers:
        try:
            ts = datetime.fromisoformat(v["ts"])
        except Exception:
            continue
        if ts <= when:
            candidates.append((ts, v))
    if not candidates:
        oldest = vers[0]
        return None, ("no snapshot exists at or before %s — the oldest is %s"
                      % (when.isoformat(timespec="minutes"), oldest.get("ts")))
    candidates.sort(key=lambda t: t[0])
    target = candidates[-1][1]
    out = revert_customization(ws_id, target["id"])
    if out is None:
        return None, "the chosen snapshot could not be restored"
    return out, None


def _changed_keys(before, after) -> list:
    """Which customization keys differ between two states.

    Snapshots hold the state BEFORE a change, so comparing snapshot N with
    whatever came next (snapshot N+1, or the live customization for the newest)
    is what turns "a change" into "a change that touched css and accent". The
    label alone is whatever the model called it at the time; this is derived
    from the states themselves and cannot be wrong about what moved.
    """
    before = before or {}
    after = after or {}
    return sorted(k for k in set(before) | set(after)
                  if before.get(k) != after.get(k))


def history(ws_id):
    """The audit trail, in the user's language: what changed, when, how to undo it."""
    doc = load_ws_doc(ws_id)
    vers = doc.get("versions") or []
    current = doc.get("customization") or {}
    out = []
    # Oldest → newest, so each snapshot can be compared with what came after it;
    # the list is reversed at the end because a timeline reads newest-first.
    for i, v in enumerate(vers):
        nxt = vers[i + 1].get("customization") if i + 1 < len(vers) else current
        changed = _changed_keys(v.get("customization"), nxt)
        out.append({
            "version_id": v.get("id"),
            "when": v.get("ts"),
            "label": v.get("label") or "a change",
            "kind": v.get("kind", "change"),
            "describes": ("state BEFORE: %s" % (v.get("label") or "a change")),
            "undo_hint": ("restoring this version undoes '%s' and everything "
                          "after it" % (v.get("label") or "that change")),
            # What this snapshot would restore, and what the change after it
            # actually moved. Both are key names only — never the CSS itself,
            # which can be 8000 characters and has no business in a list view.
            "keys": sorted(v.get("customization") or {}),
            "changed": changed,
            "changed_label": (", ".join(changed) if changed else "nothing"),
        })
    out.reverse()
    return {"workspace": ws_id, "current": current,
            "current_keys": sorted(current), "entries": out}


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
            cust = _sanitize_patch(doc.get("customization") or {})
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
    refused = None
    if patch:
        # BLAST RADIUS, on the live path. `apply_customization` had the gate and
        # no caller; this turn applied the model's patch directly and skipped
        # it. The same two checks, before anything is merged: safe mode from
        # outside the app, and a patch that names model routing, the egress
        # gate, the vault boundary or the safety rules.
        try:
            from agent_friday.services.boot_guard import check_blast_radius, safe_mode
            if safe_mode():
                refused = "Friday is in safe mode, so workspace changes are off until it is lifted"
            else:
                ok, why = check_blast_radius(patch)
                if not ok:
                    refused = why
        except ImportError:
            pass
    if patch and not refused:
        label = (patch.get("summary") if isinstance(patch, dict) else None) or "change"
        # Mutate the in-memory doc (which already holds the pending user message)
        # so nothing is lost; we persist once at the end of the turn.
        applied_version = _apply_to_doc(doc, patch, label)
    elif patch and refused:
        visible_reply = (visible_reply.rstrip() + "\n\nI did not apply that change: %s." % refused).strip()

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
        "refused": refused,
        "revert_to": applied_version["id"] if applied_version else None,
        "change": applied_version["label"] if applied_version else None,
        "customization": doc.get("customization", {}),
        "versions": doc.get("versions", []),
    }
