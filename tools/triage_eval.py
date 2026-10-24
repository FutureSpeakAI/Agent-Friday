"""Score the four background-triage heads against the incumbents they sit beside.

    python tools/triage_eval.py [--fake] [--encoder-from DIR] [--json out.json] [--gate]

Each head (services/triage_heads) is scored on its labelled set in
tests/fixtures/triage_eval_*.json, n/N against the code it augments:

  mail           message_triage.classify with the set's own rules, then the same
                 with the mail head allowed to file unmatched mail ("fallback")
  news           news_seen.news_value, the head alone, and the blended reading
  notifications  notification_policy.tier_for, then with the head escalating
  memory         the regex patterns in memory_dreaming._mine_facts, then the head

and the invariants the heads exist under, which are counted, not sampled:
rule hits changed, important mail moved to a quieter lane, NEEDS_YOU notices
lowered. All three must be 0. CPU time per item is measured with the real clock.

The synthetic sets hold no one's words. The run uses a scratch FRIDAY_HOME, so
it never touches the owner's files; the pinned encoder is copied in from
--encoder-from (default: the one installed for Friday) when it is not already
there. --fake swaps the encoder for a hashed bag-of-words stand-in, which
exercises the plumbing but says nothing about the model.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
ENCODER_DIRNAME = "laya2-encoder"
ENCODER_FILES = ("model.onnx", "tokenizer.json")


def _fixture(name: str) -> dict:
    with (FIXTURES / ("triage_eval_%s.json" % name)).open("r", encoding="utf-8") as f:
        return json.load(f)


def nN(hits: int, total: int) -> str:
    return "%d/%d" % (hits, total)


# ---------------------------------------------------------------------------
#  per-head evaluation (pure given an importable agent_friday)
# ---------------------------------------------------------------------------

def eval_mail(th, mt) -> dict:
    data = _fixture("mail")
    rules, items = data["rules"], data["items"]
    signals = {"senders": {}}
    actionable = {l["id"] for l in mt.lanes() if l.get("actionable")}
    rows = []
    for it in items:
        msg = {"sender": it["sender"], "subject": it["subject"], "snippet": it["snippet"]}
        th._mode_override["mail"] = "off"
        base = mt.classify(msg, rules, signals)[0]
        th._mode_override["mail"] = "fallback"
        with_head = mt.classify(msg, rules, signals)[0]
        head_lane, head_p = th.mail_label(it["subject"], it["snippet"], [l["id"] for l in mt.lanes()])
        rows.append((it, base, with_head, head_lane))
    th._mode_override.pop("mail", None)
    n = len(rows)
    free = [r for r in rows if not r[0]["rule_hit"]]
    return {
        "head": "mail", "N": n,
        "incumbent": nN(sum(r[1] == r[0]["label"] for r in rows), n),
        "with_head": nN(sum(r[2] == r[0]["label"] for r in rows), n),
        "unmatched_N": len(free),
        "unmatched_incumbent": nN(sum(r[1] == r[0]["label"] for r in free), len(free)),
        "unmatched_with_head": nN(sum(r[2] == r[0]["label"] for r in free), len(free)),
        "head_alone_unmatched": nN(sum(r[3] == r[0]["label"] for r in free), len(free)),
        "moved": sum(r[1] != r[2] for r in rows),
        "moved_wrong": sum(r[1] != r[2] and r[2] != r[0]["label"] for r in rows),
        "rule_hits_changed": sum(r[0]["rule_hit"] and r[1] != r[2] for r in rows),
        "demotions": sum(r[1] in actionable and r[2] not in actionable for r in rows),
    }


def eval_news(th, ns) -> dict:
    items = _fixture("news")["items"]
    pool = [{"title": i["title"], "source": i["source"]} for i in items]
    inc = [ns.news_value(p) for p in pool]
    scores = th.head("news").score([th.news_text(p) for p in pool], block=True) or [{} for _ in pool]
    head = [th.decide("news", s)[0] for s in scores]
    blend = [th.news_blend(i, s)[0] if s else i for i, s in zip(inc, scores)]
    n = len(items)
    order_inc = ns.rank(list(pool))
    order = th.blended_order(pool, inc, scores)
    moved = sum(1 for pos, idx in enumerate(order) if pool[idx] is not order_inc[pos])
    return {
        "head": "news", "N": n,
        "incumbent": nN(sum(a == i["label"] for a, i in zip(inc, items)), n),
        "head_alone": nN(sum(a == i["label"] for a, i in zip(head, items)), n),
        "blended": nN(sum(a == i["label"] for a, i in zip(blend, items)), n),
        "positions_that_differ": moved,
    }


def eval_notifications(th, pol) -> dict:
    items = _fixture("notifications")["items"]
    rows = []
    for it in items:
        th._mode_override["notify"] = "off"
        base = pol.tier_for(it["kind"], it["source"], it["priority"], None, it["text"])
        th._mode_override["notify"] = "escalate"
        with_head = pol.tier_for(it["kind"], it["source"], it["priority"], None, it["text"])
        rows.append((it, base, with_head))
    th._mode_override.pop("notify", None)
    n = len(rows)
    unknown = [r for r in rows if r[0]["kind"] not in pol._named_kinds()]
    return {
        "head": "notifications", "N": n,
        "incumbent": nN(sum(r[1] == r[0]["label"] for r in rows), n),
        "with_head": nN(sum(r[2] == r[0]["label"] for r in rows), n),
        "unknown_kind_N": len(unknown),
        "unknown_incumbent": nN(sum(r[1] == r[0]["label"] for r in unknown), len(unknown)),
        "unknown_with_head": nN(sum(r[2] == r[0]["label"] for r in unknown), len(unknown)),
        "escalated": sum(r[1] != r[2] for r in rows),
        "false_escalations": sum(r[1] != r[2] and r[0]["label"] != "needs_you" for r in rows),
        "needs_you_demoted": sum(r[1] == pol.NEEDS_YOU and r[2] != pol.NEEDS_YOU for r in rows),
    }


_REGEX_CLASS = {"preference": "preference", "workflow": "decision", "bio": "bio"}


def eval_memory(th, md) -> dict:
    items = _fixture("memory")["items"]
    texts = [i["text"] for i in items]
    got = th.classify_sentences(texts) or [("none", 0.0)] * len(texts)
    n = len(items)
    inc = []
    for t in texts:
        facts = md._mine_facts([{"role": "user", "text": t}])
        inc.append(_REGEX_CLASS.get(facts[0]["category"], "none") if facts else "none")

    def head_label(g):
        lab, p = g
        return lab if lab != "none" and p >= th.threshold("memory") else "none"
    hd = [head_label(g) for g in got]

    def inc_ok(pred, true):
        # the regex cannot tell a deadline from a bio fact ("my deadline" is a
        # bio pattern), so a bio reading counts for a deadline sentence
        return pred == true or (true == "deadline" and pred == "bio")
    pos = [i for i in items if i["label"] != "none"]
    return {
        "head": "memory", "N": n,
        "incumbent_class": nN(sum(inc_ok(p, i["label"]) for p, i in zip(inc, items)), n),
        "head_class": nN(sum(p == i["label"] for p, i in zip(hd, items)), n),
        "incumbent_found": nN(sum(p != "none" for p, i in zip(inc, items) if i["label"] != "none"), len(pos)),
        "head_found": nN(sum(p != "none" for p, i in zip(hd, items) if i["label"] != "none"), len(pos)),
        "incumbent_false_alarms": sum(p != "none" for p, i in zip(inc, items) if i["label"] == "none"),
        "head_false_alarms": sum(p != "none" for p, i in zip(hd, items) if i["label"] == "none"),
    }


def eval_speed(th) -> dict:
    out = {}
    pools = {
        "mail": [th.mail_text(i["subject"], i["snippet"]) for i in _fixture("mail")["items"]],
        "news": [i["title"] for i in _fixture("news")["items"]],
        "notify": [i["text"] for i in _fixture("notifications")["items"]],
        "memory": [i["text"] for i in _fixture("memory")["items"]],
    }
    for name, texts in pools.items():
        out[name] = th.measure_ms_per_item(name, texts, batch=16, repeats=3)
        single = th.measure_ms_per_item(name, texts, batch=1, repeats=1)
        out[name]["single_item_p95_ms"] = single.get("p95_ms")
    return out


# ---------------------------------------------------------------------------
#  environment: scratch FRIDAY_HOME + the pinned encoder
# ---------------------------------------------------------------------------

def _prepare_home(encoder_from: Path | None, fake: bool) -> Path:
    default_src = Path(encoder_from) if encoder_from else Path.home() / ".friday" / "models" / ENCODER_DIRNAME
    home = Path(tempfile.mkdtemp(prefix="triage_eval_home_"))
    for var in ("FRIDAY_HOME", "HOME", "USERPROFILE"):
        os.environ[var] = str(home)
    if not fake:
        dest = home / "models" / ENCODER_DIRNAME
        dest.mkdir(parents=True, exist_ok=True)
        for name in ENCODER_FILES:
            if (default_src / name).is_file():
                shutil.copyfile(default_src / name, dest / name)
    return home


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fake", action="store_true", help="use the hashed bag-of-words stand-in, not the encoder")
    ap.add_argument("--encoder-from", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--gate", action="store_true", help="exit 1 if an invariant is broken")
    args = ap.parse_args(argv)

    home = _prepare_home(args.encoder_from, args.fake)
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("USE_TF", "0")
    os.environ["FRIDAY_NO_ENCODER_SHADOW"] = "1"
    try:
        from agent_friday.services import (memory_dreaming as md, message_triage as mt,
                                           news_seen as ns, notification_policy as pol,
                                           triage_heads as th)
        if args.fake:
            th.set_embedder(th.hashed_embedder())
        elif not th.available():
            print("No encoder at %s; rerun with --fake or --encoder-from DIR." % (home / "models"))
            return 2
        th.RUN_INLINE = True
        report = {"encoder": "hashed-bow (fake)" if args.fake else "all-MiniLM-L6-v2 onnx (cpu)",
                  "seed_set": th.SEED_SET_ID, "seeds_sha256": th.content_hash()[:16]}
        if not th.warm():
            print("Prototypes could not be built.")
            return 2
        report["mail"] = eval_mail(th, mt)
        report["news"] = eval_news(th, ns)
        report["notifications"] = eval_notifications(th, pol)
        report["memory"] = eval_memory(th, md)
        report["speed"] = eval_speed(th)
        invariants = {"rule_hits_changed": report["mail"]["rule_hits_changed"],
                      "important_mail_demoted": report["mail"]["demotions"],
                      "needs_you_demoted": report["notifications"]["needs_you_demoted"]}
        report["invariants"] = invariants
        report["invariants_ok"] = not any(invariants.values())
        text = json.dumps(report, indent=1)
        print(text)
        if args.json:
            args.json.write_text(text + "\n", encoding="utf-8", newline="\n")
        return 0 if (report["invariants_ok"] or not args.gate) else 1
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
