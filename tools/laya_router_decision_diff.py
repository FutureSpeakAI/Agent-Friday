"""Decision diff for services/laya_router: the chain's voice route before and after the router generation.

The invariant: for every phrase the chain already handled, the voice route makes exactly the same decision,
tool, arguments, question, LAYER and handoff after the port as before. Surfaces compared, each over the same
cases:

  route / route_turn      untrusted, at the generous budget (1000 ms) and the live one (50 ms)
  route / route_turn      trusted (the owner's voice and the verified page) with a fixture stage
  route (held filter)     a short held-tools list
  rules_route             the degraded path

The trusted passes run with the rung budgets widened (route(rung_budget_ms=...), a parameter only the oracle
passes); ``dump`` exits 4 if any trusted rung still timed out or was skipped, or a trusted turn ran out of
time, because such a run is not deterministic.

The only difference the port may make is NARROWER: a phrase that holds a change anywhere in the turn becomes
a ``defer`` that names no tool (reason "an imperative that changes something", or a point-and-act defer for
a change). That class is reported on its own per surface. On the trusted surfaces, the listed FEATURE probes
(words that point at the screen) may change to a point-and-act or resolver decision; they are listed too.
Anything else, including a change of layer alone, is NOT ALLOWED and exits 1.

The oracle refuses to run unless the reading model is loaded in BOTH processes (tier 1 would otherwise be a
silent ``degraded`` on both sides and the diff would compare tier 0 only): ``dump`` exits 3 when the encoder
files are absent or an embedding fails. Point it at the real encoder directory, read-only, while FRIDAY_HOME,
HOME and USERPROFILE stay on a scratch directory.

    python tools/laya_router_decision_diff.py run  --base <chain tip worktree> --new <this worktree> \
        --scratch <dir> --encoder-dir <~/.friday/models/laya2-encoder> [--hf-home <HF_HOME>]
    python tools/laya_router_decision_diff.py dump --src <worktree> --cases <cases.json> --out <out.json>
    python tools/laya_router_decision_diff.py compare <base.json> <new.json>

The NEW tree is dumped twice: with Laya's safe writes OFF (the shipped default) and ON (``--safe-writes on``, the
mode forced in the dump process; the base tree has no such setting and is dumped once). OFF must differ from the base
only by the classes above. ON may additionally turn a WRITE verb with a referential object, on the trusted surfaces, into
a safe-write route (layer ``safe_write``: a card call, a question, or a row-less defer); every other difference,
including any read, select or open taken by that layer, is NOT ALLOWED.

CPU only (CUDA_VISIBLE_DEVICES=-1); nothing is sent anywhere; no real home is written.
"""
from __future__ import annotations

import argparse
import inspect
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_SETS = ("laya_router_eval.json", "laya_router_holdout2.json", "laya_router_holdout3.json")
WRITE_REASON = "an imperative that changes something"
HELD = ["query_calendar", "check_email", "search_email", "answer_card"]
BUDGETS = (1000, 50)

#: Probes the eval sets do not carry: (text, kwargs). A waiting card, follow-ups, the owner-mark phrase and the
#: compounds the write guard exists for.
PROBES = [
    ("yes", {"pending_card": "ap_probe"}), ("no", {"pending_card": "ap_probe"}),
    ("yes please", {"pending_card": "ap_probe"}), ("sure go ahead", {"pending_card": "ap_probe"}),
    ("what about tomorrow", {"last_route": {"tool": "query_calendar", "args": {}, "age": 0}}),
    ("any from Sarah", {"last_route": {"tool": "check_email", "args": {}, "age": 0}}),
    ("what about the bank", {"last_route": {"tool": "search_email", "args": {"query": "x"}, "age": 0}}),
    ("quiet until morning", {}), ("be quiet until morning", {}), ("stop", {}),
    ("cancel the background task", {}), ("undo that", {}), ("pause the podcast", {}),
    ("play some jazz and delete my old mail", {}),
    ("look up the dentist's hours and put them on my calendar", {}),
    ("email my sister that the party moved", {}),
    # a read that point-and-act used to take on the trusted path (must keep its read rule)
    ("show me the email from Sarah", {}), ("pull up the email about the invoice", {}),
    ("show me my unread emails", {}), ("show me the news about Ukraine", {}), ("pick up where we left off", {}),
    # quantified reads and selects: Laya's safe-write scope check (setting laya_safe_writes) must never take them
    ("show me everything on my calendar today", {}), ("show me only the unread emails", {}),
    ("select all of them", {}),
]

#: Words that point AT the screen: on the trusted surfaces these may take the new point-and-act / resolver
#: decision. They are the feature, listed on their own, never counted as a regression.
FEATURE_PROBES = [
    "open the second one", "open the first one", "select these", "tick the first three", "all of them",
    "archive these", "archive all of them", "trash all of them", "star all of them", "mark all of them as read",
    "open the budget spreadsheet", "open the go-live checklist", "show me the first one", "clear the selection",
]

#: HAND-LABELLED: the phrases that are a WRITE verb with a referential object. The ON pass takes
#: ``write_referential`` from this list, never from the parser under test.
WRITE_REFERENTIAL = frozenset({
    "archive these", "archive all of them", "trash all of them", "star all of them", "mark all of them as read",
    "star these", "unstar these", "mark these as read", "mark these as unread", "label these as receipts",
    "move these to receipts", "archive the first two", "archive the second one"})

#: What a safe-write route may carry: the one tool, the allowed actions, and only these argument keys.
SAFE_WRITE_TOOL = "organize_email"
SAFE_WRITE_ACTIONS = frozenset({"archive", "read", "unread", "star", "unstar", "label", "move"})
SAFE_WRITE_ARGS = frozenset({"action", "laya_write"})

#: Every regex the port changed gets cases generated from it.
OPENERS = ["also", "just", "then", "now", "kindly", "quickly", "i want to", "i need to", "i have to", "i'd like to",
           "i would like to", "i gotta"]
RULE_BASES = ["be quiet until morning", "quiet until morning", "go quiet until morning", "undo that", "pause the podcast",
              "stop the task", "cancel the background task", "play some jazz", "open the calendar",
              "turn the volume up", "skip this episode", "what's on my calendar tomorrow", "check my email"]
TEXT_RULE = ["text messages from Mom", "show me my text messages", "read my text messages", "any text messages today",
             "text messages about the party", "text my wife that I'm late", "text me when it's done", "texting is hard"]
NEW_ACT_VERBS = ["email", "e-mail", "tell", "let", "notify", "ping", "dm", "label", "snooze", "flag", "tag", "pin", "unpin",
                 "mute", "unmute", "block", "dismiss", "reserve", "rsvp", "sign up"]
READS = ["what's on my calendar", "check my email", "what's the news", "search my files for the lease"]
WRITES = ["put it on my calendar", "reply to the landlord", "email Sam that I'm late", "delete my old mail",
          "tell my brother I'm on my way", "label them as taxes"]
JOINERS = [" and ", ", then ", " and then ", ", also ", " after that ", "; "]


#: A pointing word INSIDE a description, and "all the X": reads that must keep their read rule on the trusted path.
DESC_VERBS = ["show me", "pull up", "open", "view", "bring up"]
DESC_OBJECTS = ["the email about this week's invoice", "the email from Sarah about that meeting", "all the emails",
                "the emails from this morning", "the invoice from this week", "the email about that meeting",
                "all the news about Ukraine", "the newsletter from this month", "the email that Sarah sent this week",
                "my unread emails", "the email from Sarah", "the messages from those people last week"]
ORACLE_RUNG_BUDGET_MS = 3000


def description_probes() -> list:
    return ["%s %s" % (v, o) for v, o in itertools.product(DESC_VERBS, DESC_OBJECTS)]
def regex_probes() -> list:
    out = []
    for o, b in itertools.product(OPENERS, RULE_BASES):
        out.append("%s %s" % (o, b))
    out.extend(TEXT_RULE)
    for v in NEW_ACT_VERBS:
        out.append("%s the team about the outage" % v)
        out.append("%s this" % v)
        out.append("%s my brother that the party moved" % v)
    for r, j, w in itertools.product(READS, JOINERS, WRITES):
        out.append(r + j + w)
    return out


def build_cases(root: Path) -> list:
    cases, seen = [], set()

    def add(text, kw=None, feature=False):
        key = (text, json.dumps(kw or {}, sort_keys=True))
        if text and key not in seen:
            seen.add(key)
            cases.append({"text": text, "kw": kw or {}, "feature": feature})
    for name in EVAL_SETS:
        p = root / "tests" / "fixtures" / name
        if p.is_file():
            for c in json.loads(p.read_text(encoding="utf-8"))["cases"]:
                add(c["text"])
    for p in sorted((root / "src" / "agent_friday" / "resources" / "laya_manifests").glob("*.json")):
        for v in json.loads(p.read_text(encoding="utf-8")).get("verbs", []):
            if v.get("laya_tier") in ("router_rule", "router_seed"):
                for ph in v.get("phrasings", []):
                    add(ph)
    for text, kw in PROBES:
        add(text, kw)
    for text in regex_probes():
        add(text)
    for text in description_probes():
        add(text)
    for text in FEATURE_PROBES:
        add(text, feature=True)
    for text in sorted(WRITE_REFERENTIAL):
        add(text, feature=True)
    for c in cases:
        c["write_referential"] = c["text"] in WRITE_REFERENTIAL
    return cases


def _row(r) -> dict:
    return {"decision": r.decision, "tool": r.tool, "args": r.args, "layer": r.layer, "reason": r.reason,
            "question": r.question, "handoff": getattr(r, "handoff", ""), "chosen": getattr(r, "chosen", {})}


def _stage():
    from agent_friday.services import screen_stage as ss
    return ss.bound_stage({"workspace": "messages", "rev": 1, "items": [
        {"ref": "mail:a@x:t1", "n": 1, "title": "Invoice from Sarah", "facets": {"from": "Sarah"}},
        {"ref": "mail:a@x:t2", "n": 2, "title": "Re: the invoice", "facets": {"from": "Mara"}},
        {"ref": "mail:a@x:t3", "n": 3, "title": "Weekly newsletter", "facets": {"from": "News"}}]}, now=time.time())


def nondeterministic(stats: dict, slow_turns: list) -> bool:
    """A trusted rung timed out or was skipped, or a trusted laya.ask turn ran out of time: the run's results
    depend on timing, so the oracle refuses to write them."""
    return bool(stats.get("timeout") or stats.get("skipped") or slow_turns)


def dump(src: Path, cases_path: Path, out: Path, safe_writes: str = "off") -> int:
    sys.path.insert(0, str(src / "src"))
    from agent_friday.services import laya, laya2_encoder, laya_router as lr
    if safe_writes == "on":
        try:        # the setting forced on in this process; the base tree has no such module and stays as it is
            from agent_friday.services import laya_safe_writes as _sw
            _sw.mode = lambda settings=None: "on"
        except ImportError:
            pass
    if not laya2_encoder.is_available():
        print("REFUSED: the Laya reading model is not on disk (%s); tier 1 would be a silent 'degraded' on both "
              "sides. Pass --encoder-dir." % laya2_encoder.artifacts_dir(), file=sys.stderr)
        return 3
    try:
        vec = laya2_encoder.embed(["probe"])
        lr._prototypes()
        if not vec or len(vec[0]) != laya2_encoder.EMBED_DIM:
            raise RuntimeError("bad embedding")
    except Exception as e:      # noqa: BLE001
        print("REFUSED: the reading model did not load: %s" % e, file=sys.stderr)
        return 3
    takes_trust = "trusted" in inspect.signature(lr.route).parameters
    takes_budget = "rung_budget_ms" in inspect.signature(lr.route).parameters
    if hasattr(lr, "rung_stats"):
        lr.rung_stats.update(timeout=0, skipped=0)
    stage = _stage() if takes_trust else None
    slow_turns = []
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    rows, tier1 = [], 0
    for c in cases:
        t, kw = c["text"], dict(c["kw"])
        row = {"text": t, "kw": kw, "feature": c.get("feature", False), "trust_supported": takes_trust}
        row["write_referential"] = bool(c.get("write_referential"))        # the hand label in the case file
        for b in BUDGETS:
            row["route@%d" % b] = _row(lr.route(t, budget_ms=b, log=False, **kw))
            row["route_turn@%d" % b] = _row(laya.route_turn(t, caller="eval.diff", budget_ms=b, log=False, **kw))
            if takes_trust:
                tk = dict(kw, trusted=True, quiet=False, stage=stage, pointed=None)
                if safe_writes == "on":
                    tk["conversation_id"] = "oracle-conversation"       # a ticket binds to a conversation
                if takes_budget:          # the oracle alone widens the rung budgets, so a run is deterministic
                    tk["rung_budget_ms"] = ORACLE_RUNG_BUDGET_MS
                row["route_trusted@%d" % b] = _row(lr.route(t, budget_ms=b, log=False, **tk))
                row["route_turn_trusted@%d" % b] = _row(laya.route_turn(t, caller="eval.diff", budget_ms=b, log=False, **tk))
            else:           # this tree has no trusted rungs: the trusted voice route IS the plain route
                row["route_trusted@%d" % b] = row["route@%d" % b]
                row["route_turn_trusted@%d" % b] = row["route_turn@%d" % b]
        row["route_held"] = _row(lr.route(t, budget_ms=1000, log=False, tools=HELD, **kw))
        row["rules_route"] = _row(lr.rules_route(t, pending_card=kw.get("pending_card"), last_route=kw.get("last_route")))
        tier1 += row["route@1000"]["layer"] == "laya2"
        for k in ("route_turn_trusted@1000", "route_turn_trusted@50"):
            if takes_trust and "over budget or unavailable" in (row[k]["reason"] or ""):
                slow_turns.append(t)
        rows.append(row)
    if tier1 == 0:
        print("REFUSED: no case was decided by tier 1: the reading model is not in the loop.", file=sys.stderr)
        return 3
    stats = dict(getattr(lr, "rung_stats", {}))
    if nondeterministic(stats, slow_turns):
        print("REFUSED: a trusted rung timed out or was skipped (%s), or a trusted laya.ask turn ran out of time "
              "(%d): the dump would not be deterministic. Close other work and run again." % (stats, len(slow_turns)),
              file=sys.stderr)
        return 4
    out.write_text(json.dumps({"src": str(src), "tier1_decided": tier1, "rows": rows}, indent=1), encoding="utf-8")
    print("dumped %d cases from %s (%d decided by tier 1)" % (len(rows), src, tier1))
    return 0


def _same(a: dict, b: dict) -> bool:
    keys = ("decision", "tool", "args", "question", "layer", "handoff", "chosen")
    return all(a.get(k) == b.get(k) for k in keys)


def _handoff_added(a: dict, b: dict) -> bool:
    """Everything the same except that the new tree names a hand-off on a request the base already deferred
    (the ``handoff`` field is new: a send, reply or write phrase now says a writer composes it)."""
    keys = ("decision", "tool", "args", "question", "layer", "chosen")
    return (a["decision"] == "defer" and not a.get("handoff") and bool(b.get("handoff"))
            and all(a.get(k) == b.get(k) for k in keys))


def _narrower(a: dict, b: dict) -> bool:
    return (b["decision"] == "defer" and not b["tool"] and a["decision"] != "defer"
            and (WRITE_REASON in (b["reason"] or "") or b["layer"] == "point_act"))


def _feature(row: dict, surface: str, b: dict) -> bool:
    return bool(row.get("feature")) and "trusted" in surface and b["layer"] in ("point_act", "resolver")


def _safe_write_feature(row: dict, surface: str, b: dict, on: bool) -> bool:
    """ON pass only: a WRITE verb with a referential object, on a trusted surface, may become Laya's safe-write
    route (a card call, a question, a row-less defer). Nothing else may take the layer."""
    if not (on and "trusted" in surface and b["layer"] == "safe_write" and row.get("write_referential")):
        return False
    args = b.get("args") or {}
    if b["decision"] == "tool":
        return (b["tool"] == SAFE_WRITE_TOOL and set(args) <= SAFE_WRITE_ARGS
                and args.get("action") in SAFE_WRITE_ACTIONS and isinstance(args.get("laya_write"), dict))
    if b["decision"] in ("ask", "defer"):
        return not b["tool"] and not args           # a question or a deferral that carries no tool and no rows
    return False


def compare(base: Path, new: Path, on: bool = False) -> int:
    A = json.loads(base.read_text(encoding="utf-8"))
    B = json.loads(new.read_text(encoding="utf-8"))
    ra = {(r["text"], json.dumps(r["kw"], sort_keys=True)): r for r in A["rows"]}
    rb = {(r["text"], json.dumps(r["kw"], sort_keys=True)): r for r in B["rows"]}
    if ra.keys() != rb.keys():
        print("the two dumps are not over the same cases", file=sys.stderr)
        return 2
    surfaces = [k for k in next(iter(rb.values())) if k.startswith(("route", "rules_route"))]
    allowed = {s: [] for s in surfaces}
    feature = {s: [] for s in surfaces}
    handoff = {s: [] for s in surfaces}
    safe = {s: [] for s in surfaces}
    bad, same = [], 0
    for k in sorted(ra):
        for s in surfaces:
            a, b = ra[k][s], rb[k][s]
            if _same(a, b):
                same += 1
            elif _handoff_added(a, b):
                handoff[s].append((k[0], a, b))
            elif _safe_write_feature(rb[k], s, b, on):
                safe[s].append((k[0], a, b))
            elif _feature(rb[k], s, b):
                feature[s].append((k[0], a, b))
            elif _narrower(a, b):
                allowed[s].append((k[0], a, b))
            else:
                bad.append((s, k[0], a, b))
    print("compared %d cases x %d surfaces: %d identical (tier-1 decided: base %s, new %s)" % (
        len(ra), len(surfaces), same, A.get("tier1_decided"), B.get("tier1_decided")))
    for title, bucket in (("ALLOWED (narrower: a change anywhere in the turn is a defer)", allowed),
                          ("ALLOWED (a hand-off named on a request the base already deferred)", handoff),
                          ("FEATURE (safe writes ON: a write to pointed rows becomes a safe-write route)", safe),
                          ("FEATURE (words that point at the screen, trusted surfaces only)", feature)):
        for s, rows in bucket.items():
            if rows:
                print("\n%s on %s: %d" % (title, s, len(rows)))
                for text, a, b in rows[:60]:
                    print("   %-62s %s %s -> %s %s" % (text[:62], a["decision"], a["tool"] or "", b["decision"], b["tool"] or ""))
    print("\nNOT ALLOWED: %d" % len(bad))
    for s, text, a, b in bad:
        print("   [%s] %-58s %s %s %s %s  ->  %s %s %s %s" % (s, text[:58], a["decision"], a["tool"], a["layer"], a["args"],
                                                         b["decision"], b["tool"], b["layer"], b["args"]))
    return 1 if bad else 0


def run(base: Path, new: Path, scratch: Path, encoder_dir: Path, hf_home: str) -> int:
    if not encoder_dir.is_dir():
        print("REFUSED: --encoder-dir %s is not a directory" % encoder_dir, file=sys.stderr)
        return 3
    scratch.mkdir(parents=True, exist_ok=True)
    cases = scratch / "cases.json"
    cases.write_text(json.dumps(build_cases(new), indent=1), encoding="utf-8")
    env = dict(os.environ, FRIDAY_HOME=str(scratch), HOME=str(scratch), USERPROFILE=str(scratch),
               CUDA_VISIBLE_DEVICES="-1", PYTHONDONTWRITEBYTECODE="1", FRIDAY_ENCODER_DIR=str(encoder_dir),
               HF_HUB_OFFLINE="1")
    if hf_home:
        env["HF_HOME"] = hf_home
    outs = {}
    for name, tree, mode in (("base", base, "off"), ("new", new, "off"), ("new_on", new, "on")):
        out = scratch / ("decisions_%s.json" % name)
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "dump", "--src", str(tree),
                            "--cases", str(cases), "--out", str(out), "--safe-writes", mode], env=env)
        if r.returncode:
            return r.returncode
        outs[name] = out
    print("== safe writes OFF ==")
    off = compare(outs["base"], outs["new"])
    print("\n== safe writes ON ==")
    on = compare(outs["base"], outs["new_on"], on=True)
    return off or on


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", required=True)
    r.add_argument("--new", required=True)
    r.add_argument("--scratch", required=True)
    r.add_argument("--encoder-dir", required=True)
    r.add_argument("--hf-home", default="")
    d = sub.add_parser("dump")
    d.add_argument("--src", required=True)
    d.add_argument("--cases", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--safe-writes", choices=("off", "on"), default="off")
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("new")
    c.add_argument("--on", action="store_true", help="the new dump was made with safe writes on")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(Path(a.base), Path(a.new), Path(a.scratch), Path(a.encoder_dir), a.hf_home)
    if a.cmd == "dump":
        return dump(Path(a.src), Path(a.cases), Path(a.out), a.safe_writes)
    return compare(Path(a.base), Path(a.new), on=a.on)


if __name__ == "__main__":
    sys.exit(main())
