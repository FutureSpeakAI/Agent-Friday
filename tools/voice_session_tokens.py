"""Gemini Live session-start payload, before and after CV-A, counted locally.

What it measures: the two things a Live call sends at the start of EVERY turn
that Friday controls, the system instruction and the tool declarations, built
through the same functions /ws/live calls, for a set of synthetic owner states.

  before   the pre-1.1 payload: the full text-chat prompt + voice rules, every
           tool declared with full descriptions (cap 0, kits off).
  shadow   what 1.1 ships by default: the capped voice-only instruction, every
           tool still declared in full ("shadow" logs what a kit would drop).
  on       the capped instruction + a Laya-chosen kit of compact tools.

What it cannot measure offline, and says so: the per-turn audio, the running
conversation, Google's own framing and the effect of explicit compression
targets. Those are reported by the stored per-turn mean (--stored-mean) and by
LV-0's live component estimate, not by this tool.

Tokenizer: tiktoken o200k_base, a public BPE counted locally. Gemini's own
tokenizer is not installed, so these are approximations (the same convention
as tools/voice_session_estimate.py); chars/4 is shown beside it. The runtime
cap uses the more conservative chars/3.5 (live_session_plan.estimate_tokens).

Money: services/cost_meter.PRICING rows for the Live model, input side, times
turns. Static tokens only; history and audio are on top.

No network: the proxy variables point at a dead port and socket.connect is
replaced by one that records the attempt and refuses; the report prints the
count (it must be 0). No model is loaded (slice_head's encoder is stubbed out
and said so), no key is read.

Default mode refuses to run unless FRIDAY_HOME, HOME and USERPROFILE are one
scratch folder under the temp directory.

    python tools/voice_session_tokens.py [--json] [--runs 100] [--stored-mean 47573]
    python tools/voice_session_tokens.py --owner-local [--owner-home PATH]

--owner-local builds the instruction from the owner's REAL ~/.friday and
prints token COUNTS only, never text, never a block title (a title can carry
the owner's words). It is read-only by construction: an audit hook refuses every
write, rename, delete and mkdir under that folder and the report counts how
many it blocked. The owner (or the lead) runs it; builders do not.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── guards ──────────────────────────────────────────────────────────────────

NETWORK_ATTEMPTS = []     # connects to anything that is not this machine
LOOPBACK_PROBES = []      # "is the local daemon port open?" checks; refused, not counted as network


class NetworkBlocked(ConnectionRefusedError):
    """Every connect is refused. An OSError, so a caller reads it as 'closed port'."""


def _is_loopback(address) -> bool:
    try:
        host = address[0] if isinstance(address, tuple) else str(address)
    except Exception:
        return False
    return str(host) in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "")


def _note(address):
    (LOOPBACK_PROBES if _is_loopback(address) else NETWORK_ATTEMPTS).append(str(address)[:80])


def _scratch_guard():
    vals = {k: os.environ.get(k) for k in ("FRIDAY_HOME", "HOME", "USERPROFILE")}
    if not all(vals.values()) or len({str(Path(v).resolve()) for v in vals.values()}) != 1:
        raise SystemExit("set FRIDAY_HOME, HOME and USERPROFILE to one scratch folder first")
    home = Path(vals["FRIDAY_HOME"]).resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    if tmp not in home.parents or home.name == ".friday":
        raise SystemExit("FRIDAY_HOME must be a scratch folder under the temp directory")


def _block_network():
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[k] = "http://127.0.0.1:9"

    def refuse(self, address, *a, **k):
        _note(address)
        raise NetworkBlocked("voice_session_tokens: no network")
    socket.socket.connect = refuse
    socket.socket.connect_ex = lambda self, address, *a, **k: (_note(address) or 10061)


WRITES_BLOCKED = []


def _read_only_audit(home: Path):
    """Refuse every mutation under `home` for the rest of this process."""
    prefix = str(home.resolve()).lower()

    def under(p):
        try:
            return str(Path(os.fsdecode(p)).resolve()).lower().startswith(prefix)
        except Exception:
            return False

    def hook(event, args):
        try:
            if event == "open":
                path, mode = args[0], (args[1] or "r")
                if isinstance(mode, str) and any(c in mode for c in "wax+") and under(path):
                    WRITES_BLOCKED.append(event)
                    raise PermissionError("owner-local mode is read-only")
            elif event in ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.truncate",
                           "shutil.rmtree", "shutil.move", "os.replace", "os.symlink",
                           "os.link", "os.chmod", "os.utime"):
                if any(under(a) for a in args if isinstance(a, (str, bytes, os.PathLike))):
                    WRITES_BLOCKED.append(event)
                    raise PermissionError("owner-local mode is read-only")
        except PermissionError:
            raise
        except Exception:
            pass
    sys.addaudithook(hook)


def _counter():
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
        return ("tiktoken o200k_base (local BPE, approximation of Gemini's)",
                lambda s: len(enc.encode(s, disallowed_special=())))
    except BaseException:
        return "chars // 4 (tiktoken BPE not cached locally)", lambda s: len(s) // 4


# ── synthetic owner states ───────────────────────────────────────────────────

_WORDS = ("garden neighbour invoice ferry lunch dentist quarterly roadmap recipe flight museum "
          "bicycle parcel council photo album tax return workshop kitchen router warranty "
          "podcast chapter budget library cabinet orchard greenhouse permit lecture").split()


def _synthetic_lines(n: int, seed: int, prefix: str) -> str:
    out = []
    x = seed
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        w = [_WORDS[(x >> k) % len(_WORDS)] for k in (3, 7, 11, 15, 19, 23)]
        out.append("- %s %02d: %s about the %s and the %s, follow up on the %s before %s." %
                   (prefix, i + 1, w[0].capitalize(), w[1], w[2], w[3], w[4]))
    return "\n".join(out) + "\n"


def synthetic_context(size: str) -> str:
    """Invented 'populated install' sections. Nothing here is anyone's data."""
    if size == "empty":
        return ""
    k = 1 if size == "typical" else 4
    return ("\n== RECENT MEMORIES ==\n" + _synthetic_lines(14 * k, 7, "Memory")
            + "\n== ACTIVE TASKS ==\n" + _synthetic_lines(8 * k, 11, "Task")
            + "\n== PERSONAL CONTEXT (smart-loaded for this turn) ==\n" + _synthetic_lines(16 * k, 13, "Note")
            + "\n== TRUST NETWORK ==\n" + _synthetic_lines(6 * k, 17, "Person")
            + "\n== WIKI BRIEFING DATA ==\n" + _synthetic_lines(12 * k, 19, "Page") + "\n")


STATES = (
    # name, context size, workspace, local hour, opening utterance
    ("empty install, home, morning", "empty", "home", 9, ""),
    ("typical, mail, morning", "typical", "mail", 9, ""),
    ("typical, news, evening", "typical", "news", 19, ""),
    ("typical, media, night", "typical", "media", 21, ""),
    ("heavy, code, afternoon", "heavy", "code", 14, ""),
    ("heavy, home, early morning", "heavy", "home", 8, ""),
)

# Synthetic replay for the offline miss estimate: (workspace, hour, tool the call needed).
REPLAY = (
    ("home", 8, "query_calendar"), ("home", 8, "check_email"), ("home", 8, "get_briefing"),
    ("home", 9, "search_news"), ("home", 9, "search_web"), ("home", 13, "open_url"),
    ("home", 20, "media_play"), ("mail", 9, "check_email"), ("mail", 10, "search_email"),
    ("mail", 10, "organize_email"), ("mail", 14, "query_calendar"), ("news", 19, "search_news"),
    ("news", 19, "discuss_story"), ("news", 20, "get_article_deep_dive"), ("news", 8, "get_briefing"),
    ("media", 21, "media_play"), ("media", 21, "podcast_play"), ("media", 22, "media_turn"),
    ("code", 14, "codebase_run"), ("code", 14, "open_project"), ("code", 15, "read_file"),
    ("code", 16, "write_file"), ("files", 11, "search_files"), ("files", 11, "open_path"),
    ("library", 15, "search_library"), ("library", 15, "search_wiki"), ("settings", 12, "set_setting"),
    ("home", 12, "spawn_task"), ("home", 12, "run_workflow"), ("home", 18, "show_my_day"),
)

#: What a typical owner has called most (seeds the recent-use term).
SEED_USAGE = ("query_calendar", "check_email", "search_news", "get_briefing", "media_play",
              "search_web", "navigate_to", "search_wiki")


# ── assembly (a mirror of ws_live; see tests for the drift guard) ───────────

#: The ask-first policy literal in routes/voice.py ws_live (a local there).
ACTIONS_POLICY_HEAD = "=== TAKING ACTIONS (ASK FIRST) ==="


def _actions_policy(vr) -> str:
    src = Path(vr.__file__).read_text(encoding="utf-8")
    at = src.index('"=== TAKING ACTIONS (ASK FIRST) ===\\n"')
    end = src.index("\n        )\n", at)
    block = src[at:end]
    # evaluate the adjacent string literals exactly as Python would
    return eval("(" + block + ")", {})


def assemble(vr, settings: dict, synth: str):
    """(sealed_text, pieces) for one owner state, as ws_live builds them up to the gate."""
    vault_open = not vr._vault_local_only()
    full_ctx = vr._get_friday_system_prompt(provider="gemini", vault_control=None,
                                            vault_fallback=vr._vault_cloud_fallback())
    try:
        from agent_friday.services.action_policy import ACTION_PERMISSION_POLICY as pol
        full_ctx = full_ctx.replace(pol, synth + pol) if synth and pol in full_ctx else full_ctx + synth
    except Exception:
        full_ctx += synth
    try:
        from agent_friday.services.conversation_recall import recent_voice_pin
        full_ctx += "\n" + recent_voice_pin()
    except Exception:
        pass
    full_ctx += vr._build_emotional_tone_block()
    _code, language_name = vr._live_language(vr._get_voice_language())
    live_style = vr._get_voice_style_prompt()
    full_ctx = vr.strip_text_chat_hints(full_ctx, keep_tone=not live_style)
    prefix = (
        "You are Agent Friday, a sovereign personal AI assistant.\n"
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
    try:
        p = vr._load_agent_personality()
        personality = f"=== YOUR PERSONALITY ===\n{p}\n\n" if p else ""
    except Exception:
        personality = ""
    demo = vr._load_voice_demo()
    spec = ("=== ABOUT AGENT FRIDAY (PUBLIC / ALWAYS SHAREABLE) ===\n"
            "The following is public product knowledge. You may speak any of "
            "it aloud to anyone — it is never private vault data, so never "
            "deflect these topics to the Sovereign Vault.\n\n" + demo + "\n\n") if demo else ""
    policy = _actions_policy(vr)
    voice_prefix = prefix + personality + spec + policy
    surface = vr._voice_tool_surface_note()
    body = voice_prefix + full_ctx + surface
    try:
        from agent_friday.voice_personality import get_voice_personality
        body = get_voice_personality().build_system_instruction(
            body, affective_dialog=False, persona=bool(live_style))
    except Exception:
        pass
    composed = vr.compose_live_instruction(live_style, body, settings)
    from agent_friday.services.action_policy import seal_system_prompt
    sealed = seal_system_prompt(composed, "Gemini Live prompt")
    pieces = {"voice prefix and rules": prefix, "personality": personality,
              "public product spec": spec, "ask-first policy": policy,
              "context prompt": full_ctx, "tool-surface note": surface}
    return sealed, pieces


def tools_json(types, vr, kit=None) -> tuple:
    tools = vr._build_voice_live_tools(types, kit=kit)
    decls = [d for t in tools for d in (t.function_declarations or [])]
    payload = [d.model_dump(exclude_none=True, mode="json") for d in decls]
    return len(decls), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ── the run ──────────────────────────────────────────────────────────────────

def _stub_encoder():
    """No model loads: slice_head's encoder reading is 'not ready' (never blocks by design)."""
    from agent_friday.services import slice_head
    slice_head.scores_for = lambda text, block=False: None


def _plan(vr, lsp, sealed, base_settings, mode, cap, state, usage, now_hour):
    s = dict(base_settings, voice_live_trim=mode, voice_live_instruction_max_tokens=cap)
    now = time.mktime((2026, 10, 12, now_hour, 0, 0, 0, 0, -1))
    t0 = time.perf_counter()
    text, plan = lsp.plan_session(sealed, s, vr._voice_tool_names, workspace=state[2],
                                  opening=state[4], now=now, usage=usage)
    return text, plan, (time.perf_counter() - t0) * 1000.0


def run(args) -> dict:
    os.environ.setdefault("FRIDAY_TESTING", "1")
    os.environ.setdefault("FRIDAY_NO_ARBITER", "1")
    sys.path.insert(0, str(ROOT / "src"))
    name, count = _counter()
    import agent_friday.routes.voice as vr
    from agent_friday.services import cost_meter, live_session_plan as lsp
    from google.genai import types
    _stub_encoder()

    settings = dict(vr._load_settings() or {})
    row = cost_meter.price_for("gemini-3.8-live") or {"in": 0.003, "out": 0.012}
    scratch = Path(tempfile.mkdtemp(prefix="vst_"))
    usage = lsp.UsageStore(scratch / "usage.json")
    for n in SEED_USAGE:
        for _ in range(3):
            usage.record(n)

    results = []
    lat = {"before": [], "shadow": [], "on": []}
    cap_default = lsp.DEFAULTS["voice_live_instruction_max_tokens"]
    for st in STATES:
        sealed, pieces = assemble(vr, settings, synthetic_context(st[1]))
        n_full, full_json = tools_json(types, vr)
        row_out = {"state": st[0], "pieces": {k: count(v) for k, v in pieces.items()}}
        configs = (("before", "off", 0), ("shadow", "shadow", cap_default), ("on", "on", cap_default))
        for label, mode, cap in configs:
            runs = []
            for _ in range(max(1, args.runs)):
                text, plan, ms = _plan(vr, lsp, sealed, settings, mode, cap, st, usage, st[3])
                runs.append(ms)
            lat[label].extend(runs)
            kit = plan.kit_arg()
            n_decl, tj = tools_json(types, vr, kit=kit) if kit else (n_full, full_json)
            instr_t, tools_t = count(text), count(tj)
            row_out[label] = {
                "instruction_tokens": instr_t, "instruction_chars_div_4": len(text) // 4,
                "tool_declarations": n_decl, "tool_tokens": tools_t,
                "static_tokens": instr_t + tools_t,
                "report": plan.instruction_report.as_dict() if label != "before" else None,
                "safety_ok": lsp.safety_ok(sealed, text),
                "usd_per_turn": round((instr_t + tools_t) / 1000.0 * row["in"], 5),
            }
            if label == "on":
                row_out[label]["kit"] = list(plan.kit)
        b, a = row_out["before"]["static_tokens"], row_out["on"]["static_tokens"]
        row_out["static_saved_pct_on"] = round(100.0 * (b - a) / b, 1)
        row_out["static_saved_pct_shadow"] = round(
            100.0 * (b - row_out["shadow"]["static_tokens"]) / b, 1)
        results.append(row_out)

    # Offline miss estimate for the kit: did the kit contain the tool the call needed?
    hits = misses = 0
    for ws, hour, tool in REPLAY:
        now = time.mktime((2026, 10, 12, hour, 0, 0, 0, 0, -1))
        plan = lsp.SessionPlan(dict(settings, voice_live_trim="on"), vr._voice_tool_names(),
                               workspace=ws, now=now, usage=usage)
        if tool in set(plan.declared_names()):
            hits += 1
        else:
            misses += 1

    def p(vals, q):
        v = sorted(vals)
        return round(v[min(len(v) - 1, int(q * len(v)))], 2) if v else None
    stored = args.stored_mean
    mid = results[1]
    report = {
        "tokenizer": name, "price_row_per_1k_input_usd": row["in"],
        "network_attempts": len(NETWORK_ATTEMPTS), "loopback_probes_refused": len(LOOPBACK_PROBES),
        "states": results,
        "plan_latency_ms": {k: {"p50": p(v, .5), "p95": p(v, .95), "max": round(max(v), 2)}
                            for k, v in lat.items() if v},
        "offline_kit_replay": {"intents": len(REPLAY), "kit_hits": hits, "kit_misses": misses,
                               "miss_rate": round(misses / len(REPLAY), 3),
                               "note": "synthetic, no opening utterance, seeded usage; a floor on what "
                                       "shadow mode will measure for real"},
        "stored_mean_prompt_tokens_per_turn": stored,
        "projected_per_turn_prompt_tokens_on": None,
    }
    saved = mid["before"]["static_tokens"] - mid["on"]["static_tokens"]
    report["projected_per_turn_prompt_tokens_on"] = stored - saved
    report["projected_saving_pct_vs_stored_mean_typical_state"] = round(100.0 * saved / stored, 1)
    report["turns_per_call"] = args.turns
    report["usd_static_per_call"] = {
        k: round(mid[k]["usd_per_turn"] * args.turns, 3) for k in ("before", "shadow", "on")}
    return report


def run_owner_local(args) -> dict:
    home = Path(args.owner_home or (Path.home() / ".friday")).resolve()
    os.environ["FRIDAY_HOME"] = str(home)
    os.environ.setdefault("FRIDAY_TESTING", "1")
    os.environ.setdefault("FRIDAY_NO_ARBITER", "1")
    _read_only_audit(home)
    sys.path.insert(0, str(ROOT / "src"))
    name, count = _counter()
    import agent_friday.routes.voice as vr
    from agent_friday.services import live_session_plan as lsp
    from google.genai import types
    _stub_encoder()
    settings = dict(vr._load_settings() or {})
    sealed, pieces = assemble(vr, settings, "")
    n_full, full_json = tools_json(types, vr)
    out = {"tokenizer": name, "counts_only": True,
           "instruction_components": {k: count(v) for k, v in pieces.items()},
           "instruction_total_before": count(sealed),
           "tools_before": {"declarations": n_full, "tokens": count(full_json)}}
    usage = lsp.UsageStore(Path(tempfile.mkdtemp(prefix="vst_")) / "u.json")
    for label, mode in (("shadow", "shadow"), ("on", "on")):
        s = dict(settings, voice_live_trim=mode)
        text, plan = lsp.plan_session(sealed, s, vr._voice_tool_names, usage=usage)
        kit = plan.kit_arg()
        nd, tj = tools_json(types, vr, kit=kit) if kit else (n_full, full_json)
        rep = plan.instruction_report
        blocks = lsp.split_blocks(sealed)
        kinds = {"mandatory": 0, "chat_only": 0, "optional": 0}
        for b in blocks:
            kinds[b.kind] += count(b.text)
        out[label] = {
            "instruction_tokens": count(text), "tool_declarations": nd, "tool_tokens": count(tj),
            "static_tokens": count(text) + count(tj),
            "cap": rep.cap, "floor_over_cap": rep.floor_over_cap, "blocks": len(blocks),
            "block_tokens_by_class": kinds,
            "dropped_blocks": len(rep.dropped), "dropped_tokens": sum(t for _n, t, _r in rep.dropped),
            "safety_ok": lsp.safety_ok(sealed, text)}
    out["writes_blocked_by_read_only_guard"] = len(WRITES_BLOCKED)
    out["network_attempts"] = len(NETWORK_ATTEMPTS)
    out["loopback_probes_refused"] = len(LOOPBACK_PROBES)
    return out


def _render(r: dict) -> str:
    L = ["Gemini Live session-start payload, before vs CV-A (local, nothing sent)",
         "  tokenizer: " + r["tokenizer"]]
    if r.get("counts_only"):
        L.append(json.dumps(r, indent=2))
        return "\n".join(L)
    L.append("  network attempts: %d (loopback port probes refused: %d)    input price: $%.4f / 1K tokens" % (
        r["network_attempts"], r["loopback_probes_refused"], r["price_row_per_1k_input_usd"]))
    L.append("")
    L.append("  %-30s %9s %9s %9s %9s  %8s %8s" % (
        "state", "before", "shadow", "on", "tools on", "save%sh", "save%on"))
    for s in r["states"]:
        L.append("  %-30s %9d %9d %9d %9d  %7.1f%% %7.1f%%" % (
            s["state"], s["before"]["static_tokens"], s["shadow"]["static_tokens"],
            s["on"]["static_tokens"], s["on"]["tool_declarations"],
            s["static_saved_pct_shadow"], s["static_saved_pct_on"]))
    L.append("  (static tokens = system instruction + tool declarations, per turn)")
    L.append("")
    for s in r["states"]:
        L.append("  %s" % s["state"])
        for k in ("before", "shadow", "on"):
            c = s[k]
            L.append("      %-7s instruction %6d  tools %6d (%d decl)  $%.4f/turn  safety_ok=%s" % (
                k, c["instruction_tokens"], c["tool_tokens"], c["tool_declarations"],
                c["usd_per_turn"], c["safety_ok"]))
        rep = s["on"]["report"] or {}
        L.append("      on: floor %s tokens, cap %s, floor_over_cap=%s, dropped %d blocks" % (
            rep.get("floor_tokens"), rep.get("cap"), rep.get("floor_over_cap"), len(rep.get("dropped", []))))
    L.append("")
    L.append("  plan latency (ms, built before connect): " + json.dumps(r["plan_latency_ms"]))
    L.append("  offline kit replay: " + json.dumps(r["offline_kit_replay"]))
    L.append("  stored mean prompt/turn: %s -> projected %s with kits on (%s%% saving)" % (
        r["stored_mean_prompt_tokens_per_turn"], r["projected_per_turn_prompt_tokens_on"],
        r["projected_saving_pct_vs_stored_mean_typical_state"]))
    L.append("  static $/call (%d turns): %s" % (r["turns_per_call"], json.dumps(r["usd_static_per_call"])))
    L.append("  Not measurable offline: per-turn audio, running history, Google framing, compression effect.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--runs", type=int, default=100, help="plan_session repetitions per state/config (latency)")
    ap.add_argument("--stored-mean", type=int, default=47573,
                    help="stored mean prompt tokens per voice turn (LV-0 ledger)")
    ap.add_argument("--turns", type=int, default=37, help="turns per call for the $ line (LV-0: ~37)")
    ap.add_argument("--owner-local", action="store_true",
                    help="build from the owner's real home, read-only, counts only")
    ap.add_argument("--owner-home", default=None, help="override ~/.friday (tests)")
    args = ap.parse_args(argv)
    _block_network()
    if args.owner_local:
        rep = run_owner_local(args)
    else:
        _scratch_guard()
        rep = run(args)
    print(json.dumps(rep, indent=2) if args.json else _render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
