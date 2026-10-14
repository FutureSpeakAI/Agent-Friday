"""Local, no-network estimate of what a Gemini Live session sends at start.

It builds the system instruction and the tool declarations through the same
functions ``/ws/live`` calls, then counts tokens with a local tokenizer
approximation. Nothing is sent anywhere: no Gemini key is read, no socket is
opened, no model is loaded.

Counted:
  * system instruction, split into the pieces the handler concatenates
    (voice prefix and rules, personality, public product spec, ask-first policy,
    the context prompt, the tool-surface note, persona wrap);
  * tool declarations (the exact ``FunctionDeclaration`` list ``/ws/live``
    puts in the config, serialised to JSON);
  * "the rest" is everything that is not in either: the audio, the running
    conversation and Google's own overhead. It is not knowable locally, so
    this tool reports the stored per-turn prompt size (when a ledger is
    given) next to the static parts and leaves the difference as the
    remainder.

Tokenizer: tiktoken ``o200k_base`` (a public BPE; Gemini's own tokenizer is not
installed, so counts are an approximation), shown beside the ``chars // 4``
rule the repo uses for its voice contract budget. Network is blocked for the
tokenizer: if the BPE file is not already in the local cache it falls back to
``chars // 4`` and says so.

The instruction is assembled here by mirroring ``ws_live`` (it is built inline
in that closure, not in a callable). The context prompt is built from whatever
the scratch ``FRIDAY_HOME`` holds, which is nothing: it is the empty-install
floor, and a real install adds its memory, wiki and vault slices on top.

Refuses to run unless ``FRIDAY_HOME``, ``HOME`` and ``USERPROFILE`` all point
at one scratch directory under the temp folder.

    python tools/voice_session_estimate.py [--ledger ~/.friday/costs.db] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def _scratch_guard():
    vals = {k: os.environ.get(k) for k in ("FRIDAY_HOME", "HOME", "USERPROFILE")}
    if not all(vals.values()) or len({str(Path(v).resolve()) for v in vals.values()}) != 1:
        raise SystemExit("set FRIDAY_HOME, HOME and USERPROFILE to one scratch folder first")
    home = Path(vals["FRIDAY_HOME"]).resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    if tmp not in home.parents or home.name == ".friday":
        raise SystemExit("FRIDAY_HOME must be a scratch folder under the temp directory")


def _counter():
    """(name, fn). No network: the proxy is dead and a cache miss falls back."""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[k] = "http://127.0.0.1:9"
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
        return "tiktoken o200k_base (local BPE, approximation of Gemini's)", \
            lambda s: len(enc.encode(s, disallowed_special=()))
    except Exception:
        return "chars // 4 (tiktoken BPE not cached locally)", lambda s: len(s) // 4


def _assemble(vr):
    """Mirror of the instruction assembly in ws_live. Returns {piece: text}."""
    settings = vr._load_settings() or {}
    vault_open = not vr._vault_local_only()
    full_ctx = vr._get_friday_system_prompt(provider="gemini", vault_control=None,
                                            vault_fallback=vr._vault_cloud_fallback())
    try:
        from agent_friday.services.conversation_recall import recent_voice_pin
        full_ctx += "\n" + recent_voice_pin()
    except Exception:
        pass
    full_ctx += vr._build_emotional_tone_block()
    live_language, language_name = vr._live_language(vr._get_voice_language())
    live_style = vr._get_voice_style_prompt()
    full_ctx = vr.strip_text_chat_hints(full_ctx, keep_tone=not live_style)
    try:
        cloud_self = ""
        cm = vr._vm.get_manifest()
        cm.refresh_selection(settings)
        if cm.mode == "gemini":
            cloud_self = cm.describe_for_model(vault_open=vault_open)
    except Exception:
        cloud_self = ""
    prefix = (
        "You are Agent Friday, a sovereign personal AI assistant.\n"
        + (cloud_self + "\n" if cloud_self else "")
        + "You are having a LIVE VOICE conversation — be natural and speak like a person.\n"
        + vr.VOICE_LENGTH_RULE
        + (vr.VOICE_STATE_NOTE_RULE if settings.get("voice_tools", True) else "")
        + "NEVER use markdown formatting — no asterisks, headers, or bullet points. Speak naturally.\n"
        "Use contractions. When it fits, ask a follow-up question to keep the conversation flowing.\n"
        "Never state that an action succeeded unless the tool result in this turn says so. "
        "A withheld, failed, or missing result is reported as exactly that — say what "
        "happened, not what you expected to happen. This complements, not replaces, your "
        "anti-fabrication directive.\n"
        + vr.vault_rule(vault_open, False) + vr.VOICE_CROSSTALK_RULE
        + vr.VOICE_ENGLISH_RULE.format(language=language_name) + "\n"
        + vr.VOICE_ANCHOR_RULES + "\n" + vr.VOICE_TOOL_CHOREOGRAPHY)
    personality = ""
    try:
        p = vr._load_agent_personality()
        personality = f"=== YOUR PERSONALITY ===\n{p}\n\n" if p else ""
    except Exception:
        pass
    demo = vr._load_voice_demo()
    spec = ("=== ABOUT AGENT FRIDAY (PUBLIC / ALWAYS SHAREABLE) ===\n"
            "The following is public product knowledge. You may speak any of "
            "it aloud to anyone — it is never private vault data, so never "
            "deflect these topics to the Sovereign Vault.\n\n" + demo + "\n\n") if demo else ""
    surface = vr._voice_tool_surface_note()
    body = prefix + personality + spec + full_ctx + surface
    try:
        from agent_friday.voice_personality import get_voice_personality
        vp = get_voice_personality()
        body = vp.build_system_instruction(body, affective_dialog=False, persona=bool(live_style))
    except Exception:
        pass
    sealed = vr.compose_live_instruction(live_style, body, settings)
    return {
        "voice prefix and rules": prefix,
        "personality": personality,
        "public product spec": spec,
        "context prompt (empty-install floor)": full_ctx,
        "tool-surface note": surface,
        "whole instruction as composed (before seal and gate)": sealed,
    }


def _tool_text(types, vr):
    tools = vr._build_voice_live_tools(types)
    decls = [d for t in tools for d in (t.function_declarations or [])]
    payload = [d.model_dump(exclude_none=True, mode="json") for d in decls]
    return len(decls), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _ledger_turn_mean(path):
    try:
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            n, mean = conn.execute(
                "SELECT COUNT(*), AVG(input_tokens) FROM cost_calls "
                "WHERE kind='voice' AND provider='gemini'").fetchone()
        finally:
            conn.close()
        return int(n or 0), (round(mean) if mean else None)
    except Exception:
        return 0, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=None,
                    help="costs.db (read-only) for the stored mean prompt per turn")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    _scratch_guard()
    os.environ.setdefault("FRIDAY_TESTING", "1")
    os.environ.setdefault("FRIDAY_NO_ARBITER", "1")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    name, count = _counter()
    import agent_friday.routes.voice as vr
    from google.genai import types

    pieces = _assemble(vr)
    n_decls, tool_json = _tool_text(types, vr)
    instr = pieces["whole instruction as composed (before seal and gate)"]
    rows = {k: {"chars": len(v), "tokens": count(v)} for k, v in pieces.items()}
    report = {
        "tokenizer": name,
        "system_instruction_tokens": rows["whole instruction as composed (before seal and gate)"]["tokens"],
        "system_instruction_chars_div_4": len(instr) // 4,
        "tool_declarations": n_decls,
        "tool_declaration_tokens": count(tool_json),
        "tool_declaration_chars_div_4": len(tool_json) // 4,
        "instruction_pieces": {k: v for k, v in rows.items()
                               if not k.startswith("whole instruction")},
    }
    static = report["system_instruction_tokens"] + report["tool_declaration_tokens"]
    report["static_session_start_tokens"] = static
    if args.ledger:
        n, mean = _ledger_turn_mean(args.ledger)
        report["stored_mean_prompt_tokens_per_voice_turn"] = mean
        report["voice_turns_in_ledger"] = n
        if mean:
            report["remainder_tokens_per_turn"] = mean - static
            report["remainder_note"] = (
                "audio, the running conversation, the real context slices and "
                "Google's framing; the empty-install context above is a floor")
    print(json.dumps(report, indent=2) if args.json else _render(report))
    return 0


def _render(r):
    out = ["Gemini Live session-start estimate (local, nothing sent)"]
    for k, v in r.items():
        if isinstance(v, dict):
            out.append(f"  {k}:")
            out += [f"      {a}: {b}" for a, b in v.items()]
        else:
            out.append(f"  {k}: {v}")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
