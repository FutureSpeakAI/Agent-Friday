"""Clean-up help for the Media library: Friday spots near-duplicate renders
and stale drafts and OFFERS a tidy-up as one batched card. Nothing is
removed without the owner's approval, and what is removed goes to a
recoverable trash inside Friday's home, never a hard delete.

- Near-duplicate images and video posters: the preview pass's difference hash
  (services/media_previews.py); two cards are a pair when the hashes differ in
  at most DHASH_BITS bits. In a group the keeper is the favourite, else the
  larger file, else the newer one.
- Near-duplicate documents: word shingles; a pair when the Jaccard similarity
  is at least DOC_SIMILARITY.
- Stale drafts: Media's own drafts and ideas untouched for STALE_DAYS with
  little or no text.

The offer is one approval card (the governed-action gate, like publishing);
on approval the files move to <home>/media/trash/<stamp>/ with a manifest and
the index is refreshed. Restore moves them back. Posts are never touched here:
a post is taken down on its platform, not tidied.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import agent_friday.core as core

HANDLER = "media_tidy"
TIDY_ACTION = "media: tidy"
DHASH_BITS = 6
DOC_SIMILARITY = 0.85
STALE_DAYS = 30
STALE_WORDS = 40
#: What tidy may move: things that are files on this PC and Media's own records.
MOVABLE = ("creation", "document", "daily_file", "comfy", "media", "draft_html")


def trash_dir() -> Path:
    return Path(core.FRIDAY_DIR) / "media" / "trash"


def proposals_dir() -> Path:
    return Path(core.FRIDAY_DIR) / "media" / "tidy"


# ── finding what to tidy ─────────────────────────────────────────────────────

def _shingles(text: str, n: int = 4) -> set:
    """Word shingles of the body; a leading heading is not the body (a copy
    with a new title is still a copy)."""
    body = re.sub(r"^\s*#.*\n", "", text or "", count=1)
    words = re.findall(r"\w+", body.lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _keeper(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The favourite, else the larger file, else the newer card."""
    return sorted(group, key=lambda c: (1 if c.get("favorite") else 0, (c.get("details") or {}).get("bytes") or 0, c.get("when_ts") or 0), reverse=True)[0]


def _groups(pairs: List[Tuple[str, str]]) -> List[List[str]]:
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    out: Dict[str, List[str]] = {}
    for x in list(parent):
        out.setdefault(find(x), []).append(x)
    return [g for g in out.values() if len(g) > 1]


def report(cards: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What a tidy-up would do, as groups with a keeper and the rest, plus stale drafts."""
    from agent_friday.services import media_index as mi, media_previews as mp
    if cards is None:
        cards = mi.query(view="all", limit=100000)["cards"]
    by_id = {c["id"]: c for c in cards}
    movable = [c for c in cards if c.get("source_kind") in MOVABLE and c.get("status") != "published"]
    # images and video posters, by difference hash
    hashed: List[Tuple[str, int]] = []
    for c in movable:
        if c.get("kind") not in ("image", "imageset", "chart", "video"):
            continue
        d = mp.details(c)
        if d.get("dhash"):
            try:
                hashed.append((c["id"], int(d["dhash"], 16)))
            except ValueError:
                continue
    pairs: List[Tuple[str, str]] = []
    for i in range(len(hashed)):
        for j in range(i + 1, len(hashed)):
            if bin(hashed[i][1] ^ hashed[j][1]).count("1") <= DHASH_BITS and by_id[hashed[i][0]]["kind"] == by_id[hashed[j][0]]["kind"]:
                pairs.append((hashed[i][0], hashed[j][0]))
    # documents, by word shingles
    texts: List[Tuple[str, set]] = []
    for c in movable:
        if c.get("kind") in ("draft", "article", "doc", "page", "deck"):
            full = mi.get(c["id"]) or {}
            t = full.get("body") or (mp.details(c) or {}).get("text") or ""
            sh = _shingles(t)
            if len(sh) >= 3:
                texts.append((c["id"], sh))
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if _jaccard(texts[i][1], texts[j][1]) >= DOC_SIMILARITY:
                pairs.append((texts[i][0], texts[j][0]))
    groups = []
    for ids in _groups(pairs):
        g = [by_id[i] for i in ids]
        keep = _keeper(g)
        groups.append({"keep": _brief(keep), "remove": [_brief(c) for c in g if c["id"] != keep["id"]],
                       "why": "near-duplicate " + ("pictures" if keep["kind"] in ("image", "imageset", "chart") else "video" if keep["kind"] == "video" else "documents")})
    # stale drafts
    stale = []
    cutoff = time.time() - STALE_DAYS * 86400
    for c in movable:
        if c.get("source_kind") in ("media", "draft_html") and c.get("status") in ("draft", "idea") and not c.get("favorite"):
            m = mi._parse_when(c.get("modified")) if c.get("modified") else None
            if m and m < cutoff and (c.get("words") or 0) < STALE_WORDS:
                stale.append(_brief(c))
    remove_ids = [r["id"] for g in groups for r in g["remove"]] + [s["id"] for s in stale]
    total_bytes = sum(int((by_id[i].get("details") or {}).get("bytes") or 0) for i in remove_ids if i in by_id)
    return {"groups": groups, "stale": stale, "count": len(remove_ids), "bytes": total_bytes, "ids": remove_ids}


def _brief(c: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": c["id"], "title": c["title"], "kind": c["kind"], "thumb": c.get("thumb"), "when": c.get("when"),
            "bytes": (c.get("details") or {}).get("bytes"), "favorite": bool(c.get("favorite")), "path": c.get("path")}


# ── the offer: one card, nothing moves before it is approved ────────────────

def propose(requested_by: str = "user", ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Raise one approval card for the whole tidy-up (or the given subset).
    Returns pending with the proposal id; apply() runs from the decision hook."""
    rep = report()
    chosen = [i for i in rep["ids"] if (ids is None or i in ids)]
    if not chosen:
        return {"status": "nothing", "message": "Nothing to tidy: no near-duplicates and no stale drafts.", "report": rep}
    pid = "tidy_" + uuid.uuid4().hex[:8]
    proposals_dir().mkdir(parents=True, exist_ok=True)
    (proposals_dir() / (pid + ".json")).write_text(json.dumps({"id": pid, "ids": chosen, "report": rep, "created": time.time(), "status": "proposed"}, ensure_ascii=False), encoding="utf-8")
    n_dup = sum(len(g["remove"]) for g in rep["groups"])
    n_stale = len(rep["stale"])
    mb = rep["bytes"] / 1048576.0
    detail = {"handler": HANDLER, "proposal": pid, "count": len(chosen), "duplicates": n_dup, "stale": n_stale, "bytes": rep["bytes"]}
    parts = []
    if n_dup:
        parts.append(f"{n_dup} near-duplicate render{'s' if n_dup != 1 else ''} in {len(rep['groups'])} group{'s' if len(rep['groups']) != 1 else ''} (the favourite or the larger file is kept)")
    if n_stale:
        parts.append(f"{n_stale} stale draft{'s' if n_stale != 1 else ''} untouched for {STALE_DAYS} days")
    description = "Move to Friday's trash: " + "; ".join(parts) + f". About {mb:.1f} MB. Everything can be restored from the Trash in Media; nothing is deleted for good."
    try:
        from agent_friday.governance import action_gate
        v = action_gate.authorize_external(
            TIDY_ACTION, detail, requested_by=requested_by,
            title=f"Tidy up {len(chosen)} item{'s' if len(chosen) != 1 else ''} in Media",
            description=description,
            action_description="Move them to Friday's trash. Restore any of them from Media's Trash.",
        )
    except Exception as e:
        return {"status": "error", "message": f"The approval gate is unavailable: {e}"}
    act = getattr(v, "action", "deny")
    if act == "allow":
        return apply(pid, approval_id=None)
    if act == "deny":
        return {"status": "denied", "message": getattr(v, "reason", "") or "Refused."}
    return {"status": "pending", "proposal": pid, "count": len(chosen), "message": "A card is asking you first.", "report": rep}


def _load_proposal(pid: str) -> Optional[Dict[str, Any]]:
    p = proposals_dir() / (pid + ".json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def apply(pid: str, approval_id: Optional[str] = None) -> Dict[str, Any]:
    """Run an approved proposal: every item to the trash, then the index refreshed."""
    prop = _load_proposal(pid)
    if prop is None:
        return {"status": "not_found"}
    if prop.get("status") == "applied":
        return {"status": "ok", "message": "Already tidied.", "moved": prop.get("moved", [])}
    moved, failed = [], []
    for cid in prop.get("ids") or []:
        r = trash_card(cid, reason="tidy " + pid)
        (moved if r.get("status") == "ok" else failed).append({"id": cid, **({k: r[k] for k in ("entry",) if k in r})} if r.get("status") == "ok" else {"id": cid, "message": r.get("message")})
    prop.update({"status": "applied", "applied": time.time(), "approval_id": approval_id, "moved": moved, "failed": failed})
    (proposals_dir() / (pid + ".json")).write_text(json.dumps(prop, ensure_ascii=False), encoding="utf-8")
    try:
        from agent_friday.services import media_index as mi
        mi.reindex("tidy")
    except Exception:
        pass
    return {"status": "ok", "moved": moved, "failed": failed}


def _on_decision(record: Dict[str, Any]) -> None:
    payload = record.get("payload") or {}
    if payload.get("handler") != HANDLER or record.get("status") != "approved":
        return
    try:
        from agent_friday.services import approvals as ap
        aid = record.get("approval_id")
        if not ap.claim_for_execution(aid):
            return
        res = apply(payload.get("proposal"), approval_id=aid)
        ap.mark_used(aid, "media", {"ok": res.get("status") == "ok", "moved": len(res.get("moved") or [])})
    except Exception:
        pass


_HOOKED = False


def register_hooks() -> None:
    global _HOOKED
    if _HOOKED:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook("governed_action", _on_decision)
        _HOOKED = True
    except Exception:
        pass


# ── the trash: recoverable, never a hard delete ──────────────────────────────

def _files_of(c: Dict[str, Any]) -> List[Path]:
    """The files a card owns: the file itself, a creation's sidecar, a Media record's json and md."""
    from agent_friday.services import media_index as mi
    out: List[Path] = []
    sk = c.get("source_kind")
    p = Path(c["path"]) if c.get("path") else None
    if sk == "media":
        root = mi.cards_dir()
        for suf in (".json", ".md"):
            q = root / (c["source_ref"] + suf)
            if q.exists():
                out.append(q)
        if p and p.is_file() and p not in out:
            out.append(p)
    elif p and p.is_file():
        out.append(p)
        if sk == "creation":
            try:
                from agent_friday.services import creative_engine as ce
                side = Path(ce.CREATIVE_META_DIR) / (p.name + ".json")
                if side.exists():
                    out.append(side)
            except Exception:
                pass
    return out


def trash_card(card_id: str, reason: str = "deleted") -> Dict[str, Any]:
    """Move a card's files into Friday's trash with a manifest. Posts are refused."""
    from agent_friday.services import media_index as mi
    c = mi.get(card_id)
    if c is None:
        return {"status": "not_found"}
    if c.get("source_kind") == "post":
        return {"status": "denied", "message": "A post that went out is taken down on the platform, not here."}
    if c.get("source_kind") not in MOVABLE:
        return {"status": "denied", "message": "That is not a file on this PC; its card goes when its source does."}
    files = _files_of(c)
    if not files:
        return {"status": "denied", "message": "There is no file to move."}
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    entry = trash_dir() / stamp
    entry.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in files:
        dest = entry / f.name
        shutil.move(str(f), str(dest))
        moved.append({"from": str(f), "name": f.name})
    manifest = {"entry": stamp, "card": {k: c.get(k) for k in ("id", "kind", "title", "source_kind", "source_ref", "project", "when")},
                "files": moved, "reason": reason, "moved_at": time.time()}
    (entry / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        from agent_friday.services import media_previews as mp
        mp.forget(c)
    except Exception:
        pass
    return {"status": "ok", "entry": stamp, "files": len(moved)}


def trash_list() -> List[Dict[str, Any]]:
    root = trash_dir()
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        m = d / "manifest.json"
        if d.is_dir() and m.exists():
            try:
                rec = json.loads(m.read_text(encoding="utf-8"))
                rec["bytes"] = sum((d / f["name"]).stat().st_size for f in rec.get("files") or [] if (d / f["name"]).exists())
                out.append(rec)
            except Exception:
                continue
    return out


def restore(entry: str) -> Dict[str, Any]:
    """Move an entry's files back where they came from (a taken name gets a suffix)."""
    d = trash_dir() / entry
    m = d / "manifest.json"
    if not d.is_dir() or not m.exists():
        return {"status": "not_found"}
    rec = json.loads(m.read_text(encoding="utf-8"))
    back = []
    for f in rec.get("files") or []:
        src = d / f["name"]
        if not src.exists():
            continue
        dest = Path(f["from"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(dest.stem + "-restored" + dest.suffix)
        shutil.move(str(src), str(dest))
        back.append(str(dest))
    try:
        m.unlink()
        d.rmdir()
    except OSError:
        pass
    try:
        from agent_friday.services import media_index as mi
        mi.reindex("restore")
    except Exception:
        pass
    return {"status": "ok", "restored": back}


def empty_trash_never() -> Dict[str, Any]:
    """There is no emptying here: the trash is the owner's folder, cleared by them, by hand."""
    return {"status": "denied", "message": "Friday does not delete for good. The trash folder is " + str(trash_dir()) + "."}
