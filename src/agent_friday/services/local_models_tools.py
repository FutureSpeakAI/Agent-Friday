"""The typed tool behind "what's the biggest model I can run?", for chat and
for voice: one read-only answer from the same fit arithmetic the Models
screen shows. It never downloads; installing raises the usual card.

Questions it answers, each in a sentence a voice can say:
  * biggest: the largest file that runs well as the brain, then the largest
    that runs at all and how;
  * could I run X: the verdict for X's best file, with the one-line reason;
  * what would I need for X: the graphics memory, RAM and disk X wants;
  * pretend I had N GB: the biggest answer on a pretend card;
  * what's using my graphics memory: the stack bar read aloud.
"""
from __future__ import annotations

import json
import re

TOOL_NAME = "local_models_advise"


def _profile(inp: dict) -> dict:
    from agent_friday.services import hardware_profile as hwp
    from agent_friday.services import model_fit as mf
    prof = hwp.get()
    v = inp.get("pretend_vram_gb")
    r = inp.get("pretend_ram_gb")
    if v or r:
        prof = mf.what_if(prof, vram_total_mib=int(float(v) * 1024) if v else None,
                          ram_total_mib=int(float(r) * 1024) if r else None,
                          gpu_name=("a pretend %s GB card" % v) if v else None)
    return prof


def _about(sp: dict | None) -> str:
    if not sp or sp.get("tok_s") is None:
        return "at a speed I have not measured"
    if sp.get("basis") == "measured":
        return "at %s tokens a second, measured here" % round(sp["tok_s"])
    if sp.get("basis") == "published":
        return "at %s tokens a second by the maker's figures" % round(sp["tok_s"])
    return "at about %s tokens a second" % round(sp["tok_s"])


def _ctx(n) -> str:
    return "%dK of context" % round((n or 0) / 1024) if n else "no measured context"


def _rank(v: str) -> int:
    return ["wont_fit", "partial", "tight", "runs_well"].index(v)


def _find(models: list, needle: str) -> dict | None:
    n = (needle or "").strip().lower()
    if not n:
        return None
    for m in models:
        if m["id"].lower() == n or (m.get("label") or "").lower() == n:
            return m
    for m in models:
        hay = " ".join([m["id"], m.get("label") or "", m.get("publisher") or ""]).lower()
        if all(tok in hay for tok in re.split(r"[\s\-_:]+", n) if tok):
            return m
    return None


def advise(inp: dict | None) -> dict:
    """The answer, as `{spoken, kind, data}`. Never raises."""
    inp = dict(inp or {})
    q = str(inp.get("question") or "").strip()
    ql = q.lower()
    model_name = str(inp.get("model") or "").strip()
    try:
        from agent_friday.services import model_catalog_rows as rows
        prof = _profile(inp)
        sim = bool(prof.get("simulated"))
        cat = rows.catalog_payload(prof, beside_resident=not sim)
        models = cat["models"]
        pre = "On a pretend card with %s GB, " % inp.get("pretend_vram_gb") if sim else ""

        if "using" in ql and ("memory" in ql or "card" in ql or "gpu" in ql) and not sim:
            from agent_friday.services import model_stack as ms
            picks = []
            try:
                from agent_friday.services.residency_arbiter import get_arbiter
                arb = get_arbiter()
                seats = ((arb.plan if arb else {}) or {}).get("seats") or {}
                for role, s in seats.items():
                    if isinstance(s, dict) and s.get("status") == "pinned" and s.get("model_id"):
                        picks.append({"id": s["model_id"], "label": s["model_id"], "kind": "text", "role": role,
                                      "file_bytes": 0, "peak_vram_mib": s.get("vram_mib")})
            except Exception:
                pass
            m = cat["machine"]
            loaded = ", ".join(sorted({p["label"] for p in picks})) or "nothing of mine"
            spoken = "Your %s has %s GB usable for models; %s is loaded right now%s." % (
                (m.get("gpu") or {}).get("name", "card"), round((m.get("gpu") or {}).get("available_mib", 0) / 1024, 1),
                loaded, (", using about %s GB" % round(cat.get("pinned_vram_mib", 0) / 1024, 1)) if cat.get("pinned_vram_mib") else "")
            return {"spoken": spoken, "kind": "using", "data": {"machine": m, "loaded": picks}}

        asks_biggest = bool(re.search(r"\b(biggest|largest|most capable|best)\b", ql))
        name = model_name
        if not name and not asks_biggest and (re.search(r"\b(could|can|would|will)\b.*\brun\b", ql) or "need for" in ql):
            name = re.sub(r"^.*?\brun\b|^.*?\bneed for\b|\?|\bon this\b.*$|\bhere\b.*$", "", ql)
            name = re.sub(r"^\s*(the|a|an)\s+", "", name.strip()).strip(" .,!")
        if name:
            m = _find(models, name)
            if m is None and "/" in name:
                chk = rows.check_hf_repo(name, prof)
                m = chk.get("model") if chk.get("status") == "ok" else None
            if m is None:
                return {"spoken": "I don't know a model called %s. Paste its Hugging Face id in Settings, Models, and I'll check it." % (name or "that"),
                        "kind": "unknown", "data": {"asked": name}}
            best = max(m["files"], key=lambda f: (_rank(f["verdict"]), f.get("context") or 0))
            if "need" in ql:
                from agent_friday.services import model_fit as mf
                need = mf.what_would_i_need(int(best["bytes"] or 0), rows._layout_for(m), best.get("context") or 32768,
                                            os_family=(prof.get("os") or {}).get("family", "windows"))
                spoken = "%s would want about %s GB of graphics memory, %s GB of RAM and %s GB of disk to run well at %s." % (
                    m["label"], round(need["vram_total_mib"] / 1024), round(need["ram_total_mib"] / 1024),
                    round(need["disk_mib"] / 1024), _ctx(best.get("context") or 32768))
                return {"spoken": spoken, "kind": "need", "data": {"model": m["id"], "need": need}}
            v = best["verdict"]
            if v == "runs_well":
                spoken = "%sYes. %s runs well here, %s with %s." % (pre, m["label"], _about(best["speed"]), _ctx(best["context"]))
            elif v == "tight":
                spoken = "%sJust about. %s fits with little to spare, %s with %s." % (pre, m["label"], _about(best["speed"]), _ctx(best["context"]))
            elif v == "partial":
                spoken = "%sPartly. %s would run with some of it on the processor, %s." % (pre, m["label"], _about(best["speed"]))
            else:
                spoken = "%sNo. %s needs about %s GB more than this computer has, even taking turns." % (
                    pre, m["label"], round((best.get("shortfall_mib") or 0) / 1024, 1))
            if m.get("installed"):
                spoken += " It's already installed."
            return {"spoken": spoken, "kind": "could", "data": {"model": m["id"], "file": best["file"], "verdict": v,
                                                                 "why": best.get("why"), "installed": m.get("installed")}}

        # biggest, the default
        cands = [(m, f) for m in models for f in m["files"] if "brain" in (m.get("roles") or ["brain"]) or True]
        well = [(m, f) for m, f in cands if f["verdict"] == "runs_well"]
        runs = [(m, f) for m, f in cands if f["verdict"] in ("tight", "partial")]
        if well:
            m, f = max(well, key=lambda mf_: int(mf_[1]["bytes"] or 0))
            spoken = "%sThe biggest that runs well is %s, the %s file at %s GB, %s with %s." % (
                pre, m["label"], f["packing"], f["gib"], _about(f["speed"]), _ctx(f["context"]))
            if m.get("friday_standard"):
                spoken += " That's my standard model."
            if runs:
                m2, f2 = max(runs, key=lambda mf_: int(mf_[1]["bytes"] or 0))
                if int(f2["bytes"] or 0) > int(f["bytes"] or 0):
                    spoken += " %s would also run, but %s." % (m2["label"], "tight" if f2["verdict"] == "tight" else "partly on the processor")
            return {"spoken": spoken, "kind": "biggest", "data": {"model": m["id"], "file": f["file"], "verdict": "runs_well"}}
        if runs:
            m, f = max(runs, key=lambda mf_: int(mf_[1]["bytes"] or 0))
            how = "tight" if f["verdict"] == "tight" else "partly on the processor"
            return {"spoken": "%sNothing runs well here, but %s would run %s, %s." % (pre, m["label"], how, _about(f["speed"])),
                    "kind": "biggest", "data": {"model": m["id"], "file": f["file"], "verdict": f["verdict"]}}
        return {"spoken": "%sNone of the models I know fit this computer; the cloud is the way to run one." % pre,
                "kind": "biggest", "data": {"model": None}}
    except Exception as e:
        return {"spoken": "I couldn't work that out: %s." % e, "kind": "error", "data": {"error": str(e)}}


def _tool_local_models_advise(inp):
    out = advise(inp)
    return json.dumps(out)


TOOLS = [
    {"name": TOOL_NAME,
     "description": (
         "What local models this computer can run and how: 'what's the biggest model I can run', "
         "'could I run X', 'what would I need for X', 'pretend I had 24 GB', 'what's using my graphics "
         "memory'. Reads the machine and the catalogue; downloads nothing. Say the `spoken` sentence "
         "aloud as it is, numbers included; it already says 'about' where a figure is an estimate. "
         "To install, offer Settings → Models or the Get button; that raises the usual card."),
     "input_schema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "The user's words."},
         "model": {"type": "string", "description": "A model name or Hugging Face id, when the question names one."},
         "pretend_vram_gb": {"type": "number", "description": "A pretend graphics-memory size in GB, for 'pretend I had…'."},
         "pretend_ram_gb": {"type": "number", "description": "A pretend RAM size in GB."}},
         "required": ["question"]}},
]

RINGS = {TOOL_NAME: 0}
HANDLERS = {TOOL_NAME: _tool_local_models_advise}


def register(claude_tools, handlers, rings):
    known = {t["name"] for t in claude_tools}
    for t in TOOLS:
        if t["name"] not in known:
            claude_tools.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
