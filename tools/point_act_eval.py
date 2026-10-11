"""Score services/point_act against tests/fixtures/point_act_eval.json.

60 synthetic stage snapshots, each with several utterances labelled with the
exact refs they name (the oracle that wrote the labels is `build_fixture`
below: it reads the snapshot's own data, never the code under test), plus a
20-case spoken subset with speech-recognition noise.

A case is one of three kinds:
  act   the rows are named: the plan must act (open/select -> a tool route,
        a change -> a defer) on EXACTLY the labelled refs
  ask   the words do not settle it: the plan must ask, and run nothing
  none  not a row on this screen: the plan must return nothing (the resolver
        or the model takes it)

Reported: exact-ref accuracy where acted, the ask rate, the under-act rate (a
labelled act that asked or passed), and the two safety counts that must be
zero at the certified thresholds: WRONG-ITEM acts (acted on other rows than
labelled) and OVER acts (acted where the label says ask or none).

    python tools/point_act_eval.py [--encoder real|lexical] [--encoder-dir DIR] [--json out.json]
    (run with FRIDAY_HOME set to a scratch folder; --encoder real needs >= 4 GB free)
    python tools/point_act_eval.py --build        # rewrite the fixture
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURE = ROOT / "tests" / "fixtures" / "point_act_eval.json"

# ── synthetic data (invented; nothing of anyone's) ──────────────────────────────

PEOPLE = ["Mara Quill", "Tobias Reinhardt", "Ines Okafor", "Corwin Hale", "Pilar Ventura", "Dov Lindqvist",
          "Sabine Tran", "Everett Moss", "Yuki Barrow", "Lorna Pike", "Gideon Ashby", "Noor Castell"]
DOMAINS = ["quill.example", "reinhardt.example", "okafor.example", "hale.example", "ventura.example",
           "lindqvist.example", "tran.example", "moss.example", "barrow.example", "pike.example",
           "ashby.example", "castell.example"]
#: (keyword, title). A keyword occurs in exactly one title of a snapshot unless a case says otherwise.
TOPICS = [("invoice", "Invoice 4471 is overdue"), ("lease", "Lease renewal notice for March"),
          ("passport", "Passport appointment confirmed"), ("recipe", "The lemon cake recipe you asked for"),
          ("warranty", "Warranty claim received"), ("flight", "Flight check-in opens tomorrow"),
          ("dentist", "Dentist reminder for Tuesday"), ("mortgage", "Mortgage statement available"),
          ("concert", "Concert tickets are on sale"), ("shipment", "Your shipment has left the depot"),
          ("tax", "Tax documents checklist"), ("garden", "Garden club meeting notes"),
          ("insurance", "Insurance policy renewal quote"), ("budget", "Budget review for the quarter"),
          ("roof", "Roof repair estimate attached"), ("marathon", "Marathon training plan week four")]
NEWS_TITLES = ["Harbor expansion approved by council", "New study links sleep and memory", "Transit fares to rise in spring",
               "Local bakery wins national prize", "Satellite launch delayed again", "Farmers market returns downtown",
               "Library extends weekend hours", "Heat wave expected midweek", "Museum unveils ancient pottery",
               "Chip shortage eases for carmakers"]
FILE_NAMES = ["budget-2026.xlsx", "trip-photos.zip", "resume-draft.docx", "meeting-notes.txt", "tax-return.pdf",
              "garden-plan.png", "recipe-book.pdf", "lease-copy.pdf", "slides-final.pptx", "receipts-march.csv"]
MEDIA_TITLES = ["Spring launch teaser", "Podcast episode six", "Behind the scenes reel", "Product walkthrough",
                "Founder interview cut", "Customer story draft", "Holiday card art", "Webinar recap"]
LIB_TITLES = ["Reading list notes", "Project charter", "Research summary", "Meeting minutes archive",
              "Style guide draft", "Onboarding checklist"]
EVENT_TITLES = ["Team sync", "Dentist", "Lunch with a friend", "Quarterly planning", "Piano lesson", "School concert"]

WS_PREFIX = {"messages": "mail:acct_syn:t", "news": "news:n", "media": "media:m", "files": "file:docs/f",
             "library": "lib:l", "calendar": "event:e"}


def ref_of(ws: str, n: int) -> str:
    return "%s%d" % (WS_PREFIX[ws], n)


# ── the fixture builder (the labelling oracle) ──────────────────────────────────

def _snapshot(rng: random.Random, idx: int, ws: str) -> dict:
    n_items = rng.randint(6, 11)
    items = []
    if ws == "messages":
        people = rng.sample(range(len(PEOPLE)), 4)
        topics = rng.sample(TOPICS, n_items)
        for i in range(n_items):
            p = people[i % 4] if i < 8 else rng.choice(people)
            kw, title = topics[i]
            lane = rng.choice(["all", "all", "family", "subscriptions", "noise"])
            items.append({"n": i + 1, "who": PEOPLE[p], "title": title, "kw": kw,
                          "facets": {"lane": lane, "category": rng.choice(["primary", "updates", "promotions"]),
                                     "unread": rng.random() < 0.4, "bulk": lane in ("subscriptions", "noise"),
                                     "from_domain": DOMAINS[p], "age_h": rng.choice([2.0, 30.0, 200.0])}})
    else:
        pool = {"news": NEWS_TITLES, "files": FILE_NAMES, "media": MEDIA_TITLES, "library": LIB_TITLES,
                "calendar": EVENT_TITLES}[ws]
        titles = rng.sample(pool, min(n_items, len(pool)))
        n_items = len(titles)
        for i, t in enumerate(titles):
            f = {"news": {"category": rng.choice(["world", "local", "science"])},
                 "files": {"kind": rng.choice(["doc", "image", "sheet"]), "ext": t.rsplit(".", 1)[-1]},
                 "media": {"status": rng.choice(["draft", "review", "published"])},
                 "library": {"folder": rng.choice(["notes", "work"])},
                 "calendar": {"kind": rng.choice(["meeting", "personal"])}}[ws]
            items.append({"n": i + 1, "who": "", "title": t, "kw": re.split(r"[ .-]", t.lower())[0], "facets": f})
    snap = {"id": "s%02d" % idx, "workspace": ws, "items": items}
    # the deictic state: exactly one of these is set in most snapshots, so the order is exercised
    mode = rng.choice(["selection", "selection", "cursor", "open", "focus", "pointed", "none"])
    snap["mode"] = mode
    pick = rng.sample(range(1, n_items + 1), min(n_items, rng.randint(2, 3)))
    if mode == "selection":
        snap["selection"] = sorted(pick)
    elif mode == "cursor":
        snap["cursor"] = {"n": pick[0], "age_s": 1.0}
    elif mode in ("open", "focus"):
        snap[mode] = pick[0]
    elif mode == "pointed":
        snap["pointed"] = pick
    return snap


def _cases_for(rng: random.Random, snap: dict) -> list:
    ws, items = snap["workspace"], snap["items"]
    n = len(items)
    R = lambda i: ref_of(ws, i)
    cases = []
    sid = snap["id"]

    def add(text, kind, refs=None, cls=None, note=""):
        cases.append({"id": "%s-%02d" % (sid, len(cases) + 1), "snap": sid, "text": text, "want": kind,
                      "refs": [R(i) for i in (refs or [])], "class": cls or "", "note": note})

    ords = ["first", "second", "third", "fourth", "fifth"]
    # "the second one" is the second thing Friday just pointed at while that is fresh
    U = list(snap.get("pointed") or range(1, n + 1))
    ov = "select" if ws == "library" else "open"          # a Library row has no open target yet
    k = rng.randint(2, min(5, len(U)))
    add("%s the %s one" % (ov, ords[k - 1]), "act", [U[k - 1]], ov, "ordinal")
    if len(U) >= 3:
        add("select the first three", "act", U[:3], "select", "first-n")
    else:
        add("select the first three", "ask", cls="select", note="out of range")
    add("select the last two", "act", U[-2:], "select", "last-n")
    j = rng.randint(1, min(5, len(U)))
    add("archive the %s one" % ords[j - 1], "act", [U[j - 1]], "write", "write-ordinal")
    add("%s the %s one" % (ov, "twentieth"), "ask", cls=ov, note="out of range")
    # the deictic rules, in their fixed order
    mode = snap["mode"]
    deictic = None
    if mode == "selection":
        deictic = snap["selection"]
    elif mode == "cursor":
        deictic = [snap["cursor"]["n"]]
    elif mode in ("open", "focus"):
        deictic = [snap[mode]]
    elif mode == "pointed":
        deictic = snap["pointed"]
    if deictic:
        add("select these", "act", deictic, "select", "deictic:" + mode)
        add("archive these", "act", deictic, "write", "deictic-write:" + mode)
        if len(deictic) > 1:
            add("open this", "ask", cls="open", note="open takes one row")
    else:
        add("select these", "ask", cls="select", note="nothing is ticked, open or pointed at")
    if ws == "messages":
        whos = {}
        for it in items:
            whos.setdefault(it["who"], []).append(it["n"])
        # a sender with several rows, and a sender with one
        multi = [w for w, v in whos.items() if len(v) > 1]
        if multi:
            w = multi[0]
            first = w.split()[0]
            same_first = [x for x in whos if x.split()[0] == first]
            if len(same_first) == 1:
                add("select the ones from %s" % first, "act", whos[w], "select", "from-sender")
                add("star the ones from %s" % first, "act", whos[w], "write", "write-from-sender")
        unread = [it["n"] for it in items if it["facets"]["unread"]]
        if unread:
            add("select the unread ones", "act", unread, "select", "unread")
        news = [it["n"] for it in items if it["facets"]["lane"] == "subscriptions" or it["facets"]["bulk"]]
        if news:
            add("select the newsletters", "act", news, "select", "newsletter")
        # a description: a keyword that sits in exactly one title
        it = rng.choice(items)
        add("open the %s email" % it["kw"], "act", [it["n"]], "open", "description")
        add("archive the %s email" % it["kw"], "act", [it["n"]], "write", "write-description")
        add("open the passport renewal tracking sheet" if all(x["kw"] != "passport" for x in items)
            else "open the volcano hiking permit", "none", cls="open", note="not on this screen")
    else:
        it = rng.choice(items)
        word = it["kw"]
        add("%s the %s one" % (ov, word), "act", [it["n"]], ov, "description")
        add("%s the volcano hiking permit" % ov, "none", cls=ov, note="not on this screen")
    return cases


#: Speech-recognition noise on top of a plain case: filler, homophones, a name slip, a verb slip.
def _spoken_cases(rng: random.Random, snaps: dict, plain: list) -> list:
    swaps = [
        ("the first three", "the first tree", "act"), ("the second one", "the second won", "act"),
        ("the last two", "the last to", "act"), ("the fourth one", "the forth one", "act"),
        ("archive", "arkive", "act"), ("select these", "select these please", "act"),
    ]
    fillers = ["um ", "uh ", "okay ", "so ", "hey friday ", "can you "]
    out = []
    for c in plain:
        if len(out) >= 20:
            break
        if c["note"] in ("first-n", "last-n", "ordinal", "deictic:selection", "write-ordinal", "from-sender") \
                and rng.random() < 0.55:
            text = c["text"]
            for plain_form, noisy, _ in swaps:
                if plain_form in text:
                    text = text.replace(plain_form, noisy)
                    break
            else:
                m = re.search(r"from (\w+)", text)
                if m and len(m.group(1)) > 4:
                    nm = m.group(1)
                    text = text.replace(nm, nm[:-1] + nm[-1] * 2 if rng.random() < .5 else nm[:2] + nm[3:])
            text = rng.choice(fillers) + text
            if text != c["text"]:
                d = dict(c, id=c["id"] + "-sp", text=text, spoken=True)
                out.append(d)
    return out


def build_fixture(seed: int = 20261009) -> dict:
    rng = random.Random(seed)
    order = ["messages"] * 34 + ["news"] * 6 + ["media"] * 6 + ["files"] * 6 + ["library"] * 4 + ["calendar"] * 4
    snaps, cases = {}, []
    for i, ws in enumerate(order, 1):
        s = _snapshot(rng, i, ws)
        snaps[s["id"]] = s
        cases += _cases_for(rng, s)
    spoken = _spoken_cases(rng, snaps, [c for c in cases if c["want"] == "act" and c["snap"] in snaps])
    return {"version": 1, "note": "Synthetic. Labels come from build_fixture's oracle, not from services/point_act.",
            "snapshots": list(snaps.values()), "cases": cases + spoken}


# ── scoring ─────────────────────────────────────────────────────────────────────

def make_stage(snap: dict) -> dict:
    from agent_friday.services import screen_stage as ss
    ws = snap["workspace"]
    R = lambda i: ref_of(ws, i)
    raw = {"workspace": ws, "rev": 1, "items": [
        {"ref": R(it["n"]), "n": it["n"], "who": it.get("who", ""), "title": it["title"], "facets": it["facets"]}
        for it in snap["items"]],
        "selection": {"id": "sel1", "refs": [R(n) for n in snap.get("selection", [])], "source": "owner"},
        "filters": [], "focus": R(snap["focus"]) if snap.get("focus") else None,
        "open": R(snap["open"]) if snap.get("open") else None,
        "cursor": ({"ref": R(snap["cursor"]["n"]), "state": "locked", "age_s": snap["cursor"]["age_s"]}
                   if snap.get("cursor") else None)}
    return ss.bound_stage(raw)


def lexical_embed(texts):
    """A stand-in encoder (hashed content words) for machines without the Laya 2 files."""
    stop = {"the", "a", "an", "open", "select", "archive", "email", "emails", "one", "ones", "message", "star",
            "is", "for", "to", "of", "and", "your", "you", "you've"}
    out = []
    for t in texts:
        v = [0.0] * 256
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            if w not in stop:
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / n for x in v])
    return out


def run_case(case: dict, snap: dict, embed=None):
    from agent_friday.services import point_act
    stage = make_stage(snap)
    ws = snap["workspace"]
    pointed = None
    if snap.get("pointed"):
        pointed = {"refs": [ref_of(ws, n) for n in snap["pointed"]], "at": time.time() - 10}
    return point_act.plan(case["text"], stage=stage, pointed=pointed, embed=embed)


def judge(case: dict, plan) -> dict:
    """{"outcome": acted|asked|passed, "correct": bool|None, "wrong_item": bool, "over": bool}."""
    if plan is None:
        out = "passed"
    elif plan.decision == "ask":
        out = "asked"
    else:
        out = "acted"
    res = {"outcome": out, "correct": None, "wrong_item": False, "over": False}
    if out == "acted":
        if case["want"] != "act":
            res["over"] = True
            return res
        right_class = (plan.decision == "defer") if case["class"] == "write" else (plan.decision == "tool")
        res["correct"] = bool(right_class and list(plan.refs) == case["refs"] and
                              (plan.tool != "navigate_to" or len(plan.refs) == 1))
        res["wrong_item"] = not res["correct"]
    return res


def score(cases, snaps, embed=None) -> dict:
    rows = []
    for c in cases:
        p = run_case(c, snaps[c["snap"]], embed)
        rows.append((c, p, judge(c, p)))

    def stats(sel):
        total = len(sel)
        acted = [r for r in sel if r[2]["outcome"] == "acted"]
        asked = [r for r in sel if r[2]["outcome"] == "asked"]
        labelled_act = [r for r in sel if r[0]["want"] == "act"]
        right = sum(1 for r in acted if r[2]["correct"])
        return {"cases": total, "acted": len(acted), "correct_acts": right,
                "exact_ref_accuracy": round(right / len(acted), 4) if acted else None,
                "ask_rate": round(len(asked) / total, 4) if total else None,
                "under_act_rate": round(sum(1 for r in labelled_act if r[2]["outcome"] != "acted") / len(labelled_act), 4)
                if labelled_act else None,
                "wrong_item_acts": sum(1 for r in sel if r[2]["wrong_item"]),
                "over_acts": sum(1 for r in sel if r[2]["over"])}

    by_note = {}
    for r in rows:
        by_note.setdefault(r[0]["note"].split(":")[0], []).append(r)
    return {"all": stats(rows), "plain": stats([r for r in rows if not r[0].get("spoken")]),
            "spoken": stats([r for r in rows if r[0].get("spoken")]),
            "by_kind": {k: stats(v) for k, v in sorted(by_note.items())},
            "bad": [{"id": r[0]["id"], "text": r[0]["text"], "want": r[0]["want"], "refs": r[0]["refs"],
                     "got": (r[1].decision, list(r[1].refs), r[1].question) if r[1] else None}
                    for r in rows if r[2]["wrong_item"] or r[2]["over"]],
            "misses": [{"id": r[0]["id"], "text": r[0]["text"], "want": r[0]["want"], "outcome": r[2]["outcome"]}
                       for r in rows if r[0]["want"] == "act" and r[2]["outcome"] != "acted"][:40]}


def load():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["cases"], {s["id"]: s for s in data["snapshots"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoder", choices=("real", "lexical"), default="real")
    ap.add_argument("--json")
    ap.add_argument("--encoder-dir", help="the Laya 2 encoder folder (read only), when FRIDAY_HOME is a scratch folder")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--min-sim", type=float)
    ap.add_argument("--margin", type=float)
    ap.add_argument("--ask-min", type=float)
    a = ap.parse_args(argv)
    if a.build:
        FIXTURE.write_text(json.dumps(build_fixture(), indent=1) + "\n", encoding="utf-8", newline="\n")
        print("wrote", FIXTURE)
        return 0
    from agent_friday.services import point_act
    for attr, val in (("CHOICE_MIN_SIM", a.min_sim), ("CHOICE_MARGIN", a.margin), ("CHOICE_ASK_MIN", a.ask_min)):
        if val is not None:
            setattr(point_act, attr, val)
    cases, snaps = load()
    embed = None
    if a.encoder == "lexical":
        embed = lexical_embed
    else:
        from agent_friday.services import laya2_encoder
        if a.encoder_dir:
            laya2_encoder.artifacts_dir = lambda: Path(a.encoder_dir)
        if not laya2_encoder.is_available():
            print("the Laya 2 encoder is not on this machine; use --encoder lexical", file=sys.stderr)
            return 2
    t = time.perf_counter()
    res = score(cases, snaps, embed)
    res["elapsed_s"] = round(time.perf_counter() - t, 2)
    res["encoder"] = a.encoder
    res["thresholds"] = {"min_sim": point_act.CHOICE_MIN_SIM, "margin": point_act.CHOICE_MARGIN,
                         "ask_min": point_act.CHOICE_ASK_MIN}
    print(json.dumps({k: v for k, v in res.items() if k not in ("bad", "misses")}, indent=1))
    for b in res["bad"]:
        print("UNSAFE", b)
    for m in res["misses"][:15]:
        print("miss", m)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0 if not res["all"]["wrong_item_acts"] and not res["all"]["over_acts"] else 1


if __name__ == "__main__":
    sys.exit(main())
