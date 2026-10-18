"""Website tasks, Phase 1 (services/web_tasks.py and its hooks).

Spec: ftv program spec "Friday operates websites" 2026-10-18: the task loop with
caps (A2, A4), proof of completion (A3), honest endings and resume (A2, A6),
per-site permission slips (C1) and web-only scoping during a task (C3).
The loop is driven through fake ports; the hooks are tested on the real
agent._execute_tool, browser_session, task_resume and approvals code.
"""
import json
import types

import pytest

from agent_friday.governance import web_sites as ws
from agent_friday.services import web_tasks as wt

KEY = b"test-governance-key-not-real"
REAL_NEVER_SEND = wt._never_send_hits


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "_dir", lambda: tmp_path / "gov")
    (tmp_path / "gov").mkdir()
    monkeypatch.setattr(ws, "_key", lambda: KEY)
    monkeypatch.setattr(wt, "_key", lambda: KEY)

    def task_dir(task_id):
        d = tmp_path / "tasks" / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d
    monkeypatch.setattr(wt, "_task_dir", task_dir)
    monkeypatch.setattr(wt, "_never_send_hits", lambda text: ["4111"] if "4111" in str(text) else [])
    yield tmp_path
    wt._ACTIVE.clear()


def page(url, text, els=""):
    return f"<<untrusted>>\nURL: {url}\nTitle: t\n{els}\n{text}\n<</untrusted>>"


class FakeBrowser:
    def __init__(self, pages, outward_on=None):
        self.pages = dict(pages)          # url -> text
        self.url = ""
        self.calls = []
        self.outward_on = outward_on or {}  # element -> approval id
        self.closed = False

    def call(self, tool, args):
        self.calls.append((tool, dict(args)))
        if tool == "browser_open":
            self.url = args["url"]
        elif tool == "browser_click" and args.get("element") in self.outward_on:
            return json.dumps({"done": False, "queued": True,
                               "approval_id": self.outward_on[args["element"]],
                               "card": {"title": "Place order"}})
        elif tool == "browser_click" and args.get("element") == 9:
            self.url = "https://shop.example.com/confirm"
        elif tool == "browser_close":
            self.closed = True
            return "closed"
        return page(self.url, self.pages.get(self.url, "empty page"))

    def page_text(self):
        return self.pages.get(self.url, "")

    def page_url(self):
        return self.url

    def proof_picture(self, path):
        open(path, "wb").write(b"\xff\xd8jpeg")
        return True

    def close(self):
        self.call("browser_close", {})


class FakeModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def decide(self, system, user):
        self.prompts.append(user)
        a = self.answers.pop(0) if self.answers else {"give_up": {"reason": "script ran out"}}
        return a, {"in_chars": len(user), "out_chars": 50}


class FakeCards:
    def __init__(self, decisions=None):
        self.decisions = dict(decisions or {})
        self.created = []

    def create(self, kind, title, description, payload):
        aid = f"appr_{len(self.created)}"
        self.created.append((kind, title, payload))
        return aid

    def status(self, aid):
        d = self.decisions.get(aid, "approved")
        return {"status": d, "consumed": d == "approved"}


class FakeClock:
    def __init__(self, step=1.0):
        self.t, self.step = 0.0, step

    def now(self):
        self.t += self.step
        return self.t

    def sleep(self, s):
        self.t += s


def plan(**kw):
    ws.set_site("example.com", kw.pop("level", "may_act"), made_by="owner:ui")
    base = dict(goal="Put the HP 305 cartridge in my basket",
                start_url="https://shop.example.com/", data={"item": "HP 305"},
                success=[{"text_present": "cartridge added"}])
    base.update(kw)
    return wt.build_plan(**base)


def ports(browser, model, cards=None, **kw):
    return wt.Ports(browser=browser, model=model, cards=cards or FakeCards(),
                    clock=kw.pop("clock", FakeClock()), **kw)


SHOP = {"https://shop.example.com/": "Shop. Search for cartridges.",
        "https://shop.example.com/confirm": "Basket: 1 item. HP 305 black cartridge added. Order ref A-1029."}


# ── Plans ─────────────────────────────────────────────────────────────────────

def test_a_cloud_reader_needs_a_money_limit_the_owner_set():
    with pytest.raises(wt.PlanRefused, match="money limit"):
        wt.build_plan(goal="g", start_url="https://example.com/", reader={"where": "cloud"},
                      wants_to_act=False)
    p = wt.build_plan(goal="g", start_url="https://example.com/", reader={"where": "cloud"},
                      money_cap_usd=2, wants_to_act=False)
    assert p.caps["money_usd"] == 2.0


@pytest.mark.parametrize("kw,match", [
    ({"start_url": "file:///C:/x"}, "web address"),
    ({"start_url": "https://localhost/"}, "not a web site"),
    ({"max_steps": 500}, "between"),
    ({"max_minutes": 0}, "between"),
    ({"success": [{"run_js": "x"}]}, "success check"),
    ({"sites": ["10.0.0.1"]}, "not a web site"),
])
def test_bad_plans_are_refused_in_plain_words(kw, match):
    base = dict(goal="g", start_url="https://example.com/", wants_to_act=False)
    base.update(kw)
    with pytest.raises(wt.PlanRefused, match=match):
        wt.build_plan(**base)


def test_only_a_read_only_task_on_allowed_sites_skips_the_plan_card():
    p = wt.build_plan(goal="g", start_url="https://example.com/", wants_to_act=False)
    assert not wt.needs_plan_card(p, {"example.com": "may_read"})
    assert wt.needs_plan_card(p, {"example.com": None})
    p2 = wt.build_plan(goal="g", start_url="https://example.com/", wants_to_act=True,
                       success=[{"text_present": "All done here"}])
    assert wt.needs_plan_card(p2, {"example.com": "may_act"})


# ── The finish line ───────────────────────────────────────────────────────────

def test_done_needs_its_evidence_on_the_final_page_and_writes_a_signed_receipt(isolated):
    b = FakeBrowser(SHOP)
    m = FakeModel([{"actions": [{"do": "click", "element": 9}]},
                   {"done": {"evidence": "HP 305 black cartridge added", "summary": "In the basket."}}])
    out = wt.run("t1", plan(success=[{"url_contains": "/confirm"}]), ports(b, m))
    r = out["receipt"]
    assert out["status"] == "complete" and r["outcome"] == "done" and r["verified"] is True
    assert r["final_url"] == "https://shop.example.com/confirm"
    assert r["evidence"]["quote"] == "HP 305 black cartridge added"
    assert r["final_picture"].endswith(".jpg") and "final-run_" in r["final_picture"]
    assert wt.verify_signed(r)
    on_disk = json.loads((isolated / "tasks" / "t1" / "web_receipt.json").read_text(encoding="utf-8"))
    assert wt.verify_signed(on_disk)
    on_disk["outcome"] = "done but edited"
    assert not wt.verify_signed(on_disk)
    assert b.closed


def test_done_without_proof_is_refused_once_then_gives_up():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"done": {"evidence": "Order confirmed, thank you!"}},
                   {"done": {"evidence": "Your order has shipped today"}}])
    out = wt.run("t2", plan(), ports(b, m))
    assert out["receipt"]["outcome"] == "gave_up"
    assert "proof" in out["receipt"]["why_ended"]
    assert "not on the current page" in m.prompts[1]


def test_a_failed_success_check_is_not_done():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"done": {"evidence": "Shop. Search for cartridges."}}] * 2)
    out = wt.run("t3", plan(success=[{"url_contains": "/confirm"}]), ports(b, m))
    assert out["receipt"]["outcome"] == "gave_up"


def test_evidence_must_be_a_real_quote_not_a_scrap():
    assert wt.evidence_found("HP", "HP 305")[0] is False
    assert wt.evidence_found("x" * 400, "x" * 400)[0] is False
    assert wt.evidence_found("Order  ref\nA-1029", "Basket. Order ref A-1029.")[0] is True


# ── Batches and cards ─────────────────────────────────────────────────────────

def test_an_outward_step_ends_its_batch_and_waits_for_the_owner():
    b = FakeBrowser(SHOP, outward_on={7: "appr_order"})
    m = FakeModel([{"actions": [{"do": "type", "element": 3, "text": "HP 305"},
                                {"do": "click", "element": 7},
                                {"do": "click", "element": 8}]},
                   {"give_up": {"reason": "stop here"}}])
    cards = FakeCards({"appr_order": "approved"})
    wt.run("t4", plan(), ports(b, m, cards))
    tools = [c for c in b.calls if c[0] in ("browser_type", "browser_click")]
    assert tools == [("browser_type", {"element": 3, "text": "HP 305"}),
                     ("browser_click", {"element": 7})]
    entries = wt.Journal("t4").entries()
    assert any(e["kind"] == "checkpoint" for e in entries)
    assert "approved" in m.prompts[1]


def test_a_denied_card_is_reported_to_the_model_and_nothing_is_retried():
    b = FakeBrowser(SHOP, outward_on={7: "appr_order"})
    m = FakeModel([{"actions": [{"do": "click", "element": 7}]}, {"give_up": {"reason": "owner said no"}}])
    out = wt.run("t5", plan(), ports(b, m, FakeCards({"appr_order": "denied"})))
    assert "said no" in m.prompts[1]
    assert out["receipt"]["cards"][0]["decision"] == "denied"
    assert not any(e["kind"] == "checkpoint" for e in wt.Journal("t5").entries())


def test_a_batch_of_more_than_five_is_not_run():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"actions": [{"do": "scroll", "direction": "down"}] * 6},
                   {"give_up": {"reason": "x"}}])
    wt.run("t6", plan(), ports(b, m))
    assert not any(c[0] == "browser_scroll" for c in b.calls)
    assert "at most 5" in m.prompts[1]


def test_typed_text_is_masked_in_the_journal():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"actions": [{"do": "type", "element": 3, "text": "12 Long Street"}]},
                   {"give_up": {"reason": "x"}}])
    wt.run("t7", plan(), ports(b, m))
    raw = (wt._task_dir("t7") / "web_task.jsonl").read_text(encoding="utf-8")
    assert "12 Long Street" not in raw


# ── Caps and endings ──────────────────────────────────────────────────────────

def test_the_step_cap_ends_the_task_as_capped():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"actions": [{"do": "read", "text_offset": i}]} for i in range(20)])
    out = wt.run("t8", plan(max_steps=3), ports(b, m))
    assert out["receipt"]["outcome"] == "capped" and "3 steps" in out["receipt"]["why_ended"]
    assert out["receipt"]["used"]["steps"] == 3


def test_the_time_cap_ends_the_task_and_waiting_for_the_owner_does_not_count():
    b = FakeBrowser(SHOP, outward_on={7: "appr_slow"})
    clock = FakeClock(step=1.0)
    statuses = iter(["pending"] * 500 + ["denied"] * 10)
    cards = FakeCards()
    cards.status = lambda aid: {"status": next(statuses)}
    m = FakeModel([{"actions": [{"do": "click", "element": 7}]}]
                  + [{"actions": [{"do": "read", "text_offset": i}]} for i in range(200)])
    out = wt.run("t9", plan(max_minutes=1), ports(b, m, cards, clock=clock))
    r = out["receipt"]
    assert r["outcome"] == "capped" and "time limit" in r["why_ended"]
    assert r["used"]["seconds"] <= 70          # the ~500 s wait was not counted


def test_the_money_cap_ends_a_cloud_task():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"actions": [{"do": "read", "text_offset": i}]} for i in range(20)])
    p = plan(reader={"where": "cloud", "model": "m"}, money_cap_usd=0.05)
    out = wt.run("t10", p, ports(b, m, cost=lambda i, o: 0.02))
    assert out["receipt"]["outcome"] == "capped" and "money" in out["receipt"]["why_ended"]


def test_stop_ends_the_task_within_one_action():
    b = FakeBrowser(SHOP)
    flag = {"stop": False}

    class Stopper(FakeBrowser):
        def call(self, tool, args):
            if tool == "browser_scroll":
                flag["stop"] = True
            return super().call(tool, args)
    b = Stopper(SHOP)
    m = FakeModel([{"actions": [{"do": "scroll", "direction": "down"},
                                {"do": "scroll", "direction": "up"},
                                {"do": "scroll", "direction": "top"}]}])
    out = wt.run("t11", plan(), ports(b, m, stop=lambda: flag["stop"]))
    assert out["status"] == "cancelled" and out["receipt"]["outcome"] == "stopped"
    assert sum(1 for c in b.calls if c[0] == "browser_scroll") == 1
    assert b.closed


def test_a_stuck_loop_gives_up_instead_of_burning_the_cap():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"actions": [{"do": "scroll", "direction": "down"}]}] * 10)
    out = wt.run("t12", plan(), ports(b, m))
    assert out["receipt"]["outcome"] == "gave_up" and "stuck" in out["receipt"]["why_ended"]


def test_answers_that_cannot_be_understood_end_honestly():
    out = wt.run("t13", plan(), ports(FakeBrowser(SHOP), FakeModel([None, "text", {"x": 1}])))
    assert out["receipt"]["outcome"] == "gave_up" and "understood" in out["receipt"]["why_ended"]


def test_asking_the_owner_pauses_and_a_no_stops():
    cards = FakeCards({"appr_0": "denied"})
    m = FakeModel([{"ask_owner": {"question": "Please sign in in the live view"}}])
    out = wt.run("t14", plan(), ports(FakeBrowser(SHOP), m, cards))
    assert cards.created[0][0] == wt.QUESTION_CARD
    assert out["receipt"]["outcome"] == "stopped"


def test_there_are_exactly_five_endings():
    assert wt.ENDINGS == ("done", "gave_up", "stopped", "capped", "interrupted")


# ── Resume ────────────────────────────────────────────────────────────────────

def test_resume_starts_at_the_last_checkpoint_and_never_repeats_an_unknown_outward_step():
    j = wt.Journal("t15")
    j.append("model", step=4)
    j.append("checkpoint", url="https://shop.example.com/confirm", step=4)
    j.append("card", approval_id="appr_lost", title="Place order", outward=True)
    b = FakeBrowser(SHOP)
    m = FakeModel([{"give_up": {"reason": "checked"}}])
    wt.run("t15", plan(), ports(b, m), resume=True)
    assert b.calls[0] == ("browser_open", {"url": "https://shop.example.com/confirm"})
    assert "UNKNOWN" in m.prompts[0] and "Place order" in m.prompts[0]
    assert not any(c[0] == "browser_click" for c in b.calls)


def test_resume_state_clears_a_settled_card():
    j = wt.Journal("t16")
    j.append("card", approval_id="a1", title="x", outward=True)
    j.append("card_settled", approval_id="a1", decision="approved")
    assert j.resume_state()["unknown_outward"] == []


def test_a_tampered_plan_cannot_resume():
    p = plan()
    wt.save_plan("t17", p, approved_by="owner:ui")
    assert wt.load_plan("t17")[0].goal == p.goal
    f = wt._task_dir("t17") / "web_task_plan.json"
    body = json.loads(f.read_text(encoding="utf-8"))
    body["plan"]["sites"].append("evil.com")
    f.write_text(json.dumps(body))
    assert wt.load_plan("t17") is None
    assert wt.resumability("t17")["resumable"] is False


# ── Hooks on the real code paths ──────────────────────────────────────────────

def _activate(task_id, p, page_url="https://shop.example.com/"):
    run = wt._Run(task_id=task_id, plan=p, page_url=page_url)
    wt._register(run)
    return run


def test_a_website_task_session_reaches_only_the_browser(monkeypatch):
    from agent_friday.services import agent
    ran = []
    _activate("tscope", plan())
    out = agent._execute_tool("search_files", {"query": "passport"},
                              session_ctx={"task_id": "tscope", "surface": "web_task"},
                              handler=lambda inp: ran.append(inp) or "files!")
    assert ran == [] and out.startswith("[WEB TASK DENY]")


def test_other_sessions_are_untouched_by_the_scope(monkeypatch):
    assert wt.tool_scope_denial("search_files", {}, {"task_id": "not-a-web-task"}) is None
    assert wt.tool_scope_denial("search_files", {}, None) is None


def test_never_send_values_are_not_typed_or_opened():
    _activate("tns", plan())
    assert "never-send" in wt.tool_scope_denial(
        "browser_type", {"element": 2, "text": "4111 1111 1111 1111"}, {"task_id": "tns"})
    assert "never-send" in wt.tool_scope_denial(
        "browser_open", {"url": "https://shop.example.com/?c=4111"}, {"task_id": "tns"})


def test_a_may_read_site_is_read_but_not_typed_into():
    _activate("tread", plan(level="may_read"))
    assert wt.tool_scope_denial("browser_scroll", {"direction": "down"}, {"task_id": "tread"}) is None
    assert "only read" in wt.tool_scope_denial("browser_type", {"element": 1, "text": "x"},
                                               {"task_id": "tread"})


def test_off_plan_and_unlisted_sites_are_not_opened():
    _activate("toff", plan())
    assert wt.navigation_check("toff", "https://www.example.com/a")[0] is True
    ok, why = wt.navigation_check("toff", "https://exfil.test.io/?d=x")
    assert not ok and "not one of this task's sites" in why
    ws.remove("example.com", made_by="owner:voice")      # revoked mid-task
    assert wt.navigation_check("toff", "https://www.example.com/a")[0] is False


def test_the_browser_refuses_an_off_plan_page_for_a_website_task(monkeypatch):
    from agent_friday.services import browser_session as bs, web_safety
    monkeypatch.setattr(web_safety, "check_url", lambda u: (True, "ok"))
    _activate("tnav", plan())
    s = object.__new__(bs.BrowserSession)
    s.owner = bs.BrowserOwner(actor_id="friday", conversation_id="c", task_id="tnav")
    s.allow_local = False
    assert s.url_allowed("https://shop.example.com/x", navigation=True)[0] is True
    ok, why = s.url_allowed("https://evil.example.org/steal", navigation=True)
    assert not ok and "not one of this task's sites" in why
    # A sub-resource or an embedded frame is not a page the task opened.
    assert s.url_allowed("https://evil.example.org/x", navigation=True, main_frame=False)[0] is True
    # A browser that no website task owns keeps its own rules.
    s.owner = bs.BrowserOwner(actor_id="friday", conversation_id="c", task_id="other")
    assert s.url_allowed("https://evil.example.org/steal", navigation=True)[0] is True


def test_task_resume_waits_for_the_owner_for_a_website_task(monkeypatch):
    from agent_friday.services import task_resume
    monkeypatch.setattr(wt, "_plan_path", lambda tid: wt._task_dir(tid) / "web_task_plan.json")
    monkeypatch.setattr(wt, "_task_status", lambda tid: "interrupted")
    wt.save_plan("tres", plan(), approved_by="owner:ui")
    v = task_resume.resumability("tres")
    assert v["resumable"] is True and v["needs_confirmation"] is True and v["via"] == "web_task"
    with pytest.raises(task_resume.ResumeRefused):
        monkeypatch.setattr(wt, "resume", lambda tid: True)
        monkeypatch.setattr("agent_friday.services.agent._prepare_task_start", lambda tid: True)
        task_resume.resume("tres")          # no confirmation: refused
    text, _ = task_resume.resume("tres", confirm_pending=True)
    assert text == task_resume.RESUMED_IN_BACKGROUND


def test_a_finished_website_task_does_not_resume(monkeypatch):
    monkeypatch.setattr(wt, "_task_status", lambda tid: "failed")
    wt.save_plan("tdone", plan(), approved_by="owner:ui")
    assert wt.resumability("tdone")["resumable"] is False


def test_an_edited_step_record_blocks_resume(monkeypatch):
    monkeypatch.setattr(wt, "_task_status", lambda tid: "interrupted")
    wt.save_plan("tedit", plan(), approved_by="owner:ui")
    j = wt.Journal("tedit")
    j.append("card", approval_id="a1", title="Place order", outward=True)
    j.append("model", step=1)
    lines = j.path.read_text(encoding="utf-8").splitlines()
    j.path.write_text(lines[1] + "\n", encoding="utf-8")           # the "result unknown" card line removed
    assert wt.resumability("tedit")["resumable"] is False
    out = wt.run("tedit", plan(), ports(FakeBrowser(SHOP), FakeModel([])), resume=True)
    assert out["receipt"] is None and "changed or cut" in out["result"]


def test_resume_restores_what_was_used_and_writes_the_interrupted_receipt(isolated):
    j = wt.Journal("tcap")
    j.append("start", run_id="run_old")
    j.append("model", step=5, elapsed=1150.0, money=0.04)
    b = FakeBrowser(SHOP)
    p = plan(reader={"where": "cloud", "model": "m"}, money_cap_usd=0.05, max_minutes=20)
    m = FakeModel([{"actions": [{"do": "read", "text_offset": i}]} for i in range(50)])
    out = wt.run("tcap", p, ports(b, m, cost=lambda i, o: 0.006), resume=True)
    assert out["receipt"]["outcome"] == "capped"
    assert out["receipt"]["used"]["steps"] <= 8
    receipts = sorted((isolated / "tasks" / "tcap").glob("web_receipt-*.json"))
    outcomes = sorted(json.loads(r.read_text(encoding="utf-8"))["outcome"] for r in receipts)
    assert outcomes == ["capped", "interrupted"]
    assert all(wt.verify_signed(json.loads(r.read_text(encoding="utf-8"))) for r in receipts)


def test_a_read_only_plan_stays_read_only_after_resume(monkeypatch):
    p = wt.build_plan(goal="read", start_url="https://example.com/", wants_to_act=False)
    wt.save_plan("tro", p, approved_by="owner:ui")
    assert wt.plan_is_read_only("tro") is True
    p2 = plan()
    wt.save_plan("trw", p2, approved_by="owner:ui")
    assert wt.plan_is_read_only("trw") is False


def test_one_run_per_task_and_a_run_removes_only_itself():
    a = wt._Run(task_id="tone", plan=plan())
    b = wt._Run(task_id="tone", plan=plan())
    assert wt._register(a) is True and wt._register(b) is False
    wt._unregister(b)
    assert wt.active("tone") is a
    wt._unregister(a)
    assert wt.active("tone") is None


def test_a_web_task_context_without_a_running_task_is_refused():
    assert wt.tool_scope_denial("browser_open", {"url": "https://x.com"},
                                {"task_id": "gone", "surface": "web_task"}).startswith("[WEB TASK DENY]")


def test_a_page_title_cannot_fake_a_refusal():
    assert wt._refused(page("https://shop.example.com/", "Access refused: members only")) is False
    assert wt._refused("browser_click refused: element 3 is gone") is True
    assert wt._refused("[WEB TASK DENY] not opened") is True


def test_proof_cannot_be_an_echo_of_the_address():
    url = "https://shop.example.com/search?q=Order+confirmed+A-1029"
    text = "Results for Order confirmed A-1029: none."
    assert wt.evidence_found("Order confirmed A-1029", text, url)[0] is False
    p = plan(success=[{"url_contains": "/search?q=Order"}])
    assert wt.run_checks(p, url, text)[0] is False


def test_values_are_kept_only_as_their_length():
    assert wt._masked_args("browser_type", {"element": 1, "text": "1234"})["text"] == "(4 characters)"
    assert wt._masked_args("browser_select", {"element": 1, "option": "Visa"})["option"] == "(4 characters)"


def test_a_plan_never_carries_a_password_or_code():
    for key in ("password", "PIN", "one-time code", "CVV", "security code"):
        with pytest.raises(wt.PlanRefused, match="never carries"):
            wt.build_plan(goal="g", start_url="https://example.com/", data={key: "x"},
                          wants_to_act=False)


def test_an_encoded_never_send_value_is_still_refused(monkeypatch):
    from agent_friday.services import judgment_gate
    monkeypatch.setattr(wt, "_never_send_hits", REAL_NEVER_SEND)
    monkeypatch.setattr(judgment_gate, "never_send_hits", lambda t: ["card"] if "4111 1111" in t else [])
    assert wt._never_send_hits("https://x.example.com/?c=4111%25201111") != []


def test_an_off_plan_site_ends_the_batch_and_asks_for_a_new_plan_not_a_card():
    class OffPlan(FakeBrowser):
        def call(self, tool, args):
            if tool == "browser_click":
                self.calls.append((tool, dict(args)))
                return "browser_click refused: the page went to an address Friday does not open (evil.test is not one of this task's sites); it was left"
            return super().call(tool, args)
    cards = FakeCards()
    m = FakeModel([{"actions": [{"do": "click", "element": 4}]}, {"give_up": {"reason": "needs evil.test"}}])
    out = wt.run("toffp", plan(), ports(OffPlan(SHOP), m, cards))
    assert cards.created == []
    assert "new plan" in m.prompts[1] and out["receipt"]["outcome"] == "gave_up"


def test_a_plan_card_that_adds_a_site_is_approved_only_on_screen(monkeypatch, tmp_path):
    from agent_friday.services import approvals
    assert approvals.SCREEN_ONLY_KINDS.get(wt.PLAN_CARD_NEW_SITES) == frozenset({"owner:ui"})


def test_an_approved_plan_adds_sites_only_on_an_on_screen_press(monkeypatch):
    started = []
    monkeypatch.setattr(wt, "spawn", lambda p, **kw: started.append((p, kw)) or "tid")
    p = wt.build_plan(goal="g", start_url="https://newshop.example.net/",
                      success=[{"text_present": "Order placed"}])
    rec = {"status": "approved", "kind": wt.PLAN_CARD_NEW_SITES, "decided_by": "owner",
           "payload": {"plan": p.to_dict(), "new_sites": ["example.net"]}}
    wt._on_plan_decision(rec)
    assert started == [] and ws.level_for("example.net") is None
    rec["decided_by"] = "owner:ui"
    wt._on_plan_decision(rec)
    assert ws.level_for("example.net") == "may_act" and len(started) == 1
    wt._on_plan_decision({**rec, "status": "denied"})
    assert len(started) == 1


@pytest.fixture
def clean_context(monkeypatch):
    from agent_friday.governance import outside_content as oc
    state = {"marked": ""}
    monkeypatch.setattr(oc, "is_marked", lambda ctx: state["marked"])
    monkeypatch.setattr(oc, "turn_outside", lambda ctx: "")
    return state


def test_a_read_only_task_after_outside_content_still_shows_its_plan_card(monkeypatch, clean_context):
    from agent_friday.services import agent_workspace_permissions as awp, approvals
    monkeypatch.setattr(awp, "snapshot", lambda: {"enabled": True, "generation": 1})
    monkeypatch.setattr(wt, "choose_reader", lambda: {"where": "local", "model": "brain"})
    started, cards = [], []
    monkeypatch.setattr(wt, "spawn", lambda p, **kw: started.append(kw) or "tid")
    monkeypatch.setattr(approvals, "create_approval",
                        lambda **kw: cards.append(kw) or {"approval_id": "appr_plan"})
    ws.set_site("example.com", "may_read", made_by="owner:ui")
    clean_context["marked"] = "read an email"
    out = json.loads(wt.tool_web_task_start({"goal": "read", "wants_to_act": False,
                                             "start_url": "https://example.com/?q=secret"},
                                            {"conversation_id": "c"}))
    assert out["queued"] and started == [] and len(cards) == 1


def test_an_approved_cloud_plan_does_not_start_after_a_switch_to_local(monkeypatch):
    from agent_friday.services import cloud_mode_policy as cmp
    started = []
    monkeypatch.setattr(wt, "spawn", lambda p, **kw: started.append(kw) or "tid")
    monkeypatch.setattr(cmp, "cloud_allowed", lambda *a, **k: False)
    ws.set_site("example.com", "may_act", made_by="owner:ui")
    p = wt.build_plan(goal="g", start_url="https://example.com/", reader={"where": "cloud"},
                      money_cap_usd=1, success=[{"text_present": "Order placed"}])
    wt._on_plan_decision({"status": "approved", "kind": wt.PLAN_CARD, "decided_by": "owner:ui",
                          "payload": {"plan": p.to_dict(), "new_sites": []}})
    assert started == []


def test_the_tool_proposes_and_never_starts_an_acting_task(monkeypatch, clean_context):
    from agent_friday.services import agent_workspace_permissions as awp, approvals
    monkeypatch.setattr(awp, "snapshot", lambda: {"enabled": True, "generation": 1})
    monkeypatch.setattr(wt, "choose_reader", lambda: {"where": "local", "model": "brain"})
    started, cards = [], []
    monkeypatch.setattr(wt, "spawn", lambda p, **kw: started.append(kw) or "tid")
    monkeypatch.setattr(approvals, "create_approval",
                        lambda **kw: cards.append(kw) or {"approval_id": "appr_plan"})
    ws.set_site("example.com", "may_act", made_by="owner:ui")
    out = json.loads(wt.tool_web_task_start({"goal": "order", "start_url": "https://example.com/",
                                             "success": [{"text_present": "Order placed"}]},
                                            {"conversation_id": "c"}))
    assert out["queued"] and started == [] and cards[0]["kind"] == wt.PLAN_CARD
    ws.set_site("example.com", "may_read", made_by="owner:ui")
    out = json.loads(wt.tool_web_task_start({"goal": "read the menu", "wants_to_act": False,
                                             "start_url": "https://example.com/"},
                                            {"conversation_id": "c"}))
    assert out["started"] and started == [{"approved_by": "owner:site-list (read-only)",
                                           "read_only": True}]


def test_the_tool_says_when_agent_browser_work_is_off(monkeypatch):
    from agent_friday.services import agent_workspace_permissions as awp
    monkeypatch.setattr(awp, "snapshot", lambda: {"enabled": False})
    assert "Turn it on in Settings" in wt.tool_web_task_start(
        {"goal": "g", "start_url": "https://example.com/"}, {})


def test_the_model_is_shown_only_web_actions():
    for word in ("send_email", "search_files", "run_command", "memory"):
        assert word not in wt.SYSTEM
    assert set(wt.ACTION_TOOLS.values()) <= wt.WEB_TASK_TOOLS


# ── Second review (N1-N7) ─────────────────────────────────────────────────────

def test_typed_text_is_never_proof():
    class Echo(FakeBrowser):
        def call(self, tool, args):
            if tool == "browser_type":
                self.pages[self.url] = self.pages.get(self.url, "") + " " + args["text"]
            return super().call(tool, args)
    b = Echo(dict(SHOP))
    m = FakeModel([{"actions": [{"do": "type", "element": 2, "text": "HP 305 cartridge added to your basket"}]},
                   {"done": {"evidence": "HP 305 cartridge added to your basket"}},
                   {"done": {"evidence": "cartridge added to your basket"}}])
    out = wt.run("tn1", plan(), ports(b, m))
    assert out["receipt"]["outcome"] == "gave_up" and out["receipt"]["verified"] is False
    assert "repeats something Friday typed" in m.prompts[2]


@pytest.mark.parametrize("check", [{"url_matches": "*"}, {"url_contains": "://"},
                                   {"text_present": "ok"}, {"url_contains": "confirm"}])
def test_vague_success_checks_are_refused(check):
    with pytest.raises(wt.PlanRefused):
        wt.build_plan(goal="g", start_url="https://example.com/", success=[check])


def test_an_address_check_does_not_count_on_a_page_friday_opened_herself():
    p = plan(success=[{"url_contains": "/confirm"}])
    url = "https://shop.example.com/confirm"
    assert wt.run_checks(p, url, "", reached_by_open=True)[0] is False
    assert wt.run_checks(p, url, "", reached_by_open=False)[0] is True


def test_opening_the_success_address_by_hand_is_never_done():
    pages = dict(SHOP)
    pages["https://shop.example.com/order/./confirm"] = "Page not found"
    b = FakeBrowser(pages)
    m = FakeModel([{"actions": [{"do": "open", "url": "https://shop.example.com/order/./confirm"}]},
                   {"done": {"evidence": "Page not found here"}},
                   {"done": {"evidence": "Page not found"}}])
    out = wt.run("tm1", plan(success=[{"url_contains": "/confirm"}]), ports(b, m))
    assert out["receipt"]["outcome"] == "gave_up" and out["receipt"]["verified"] is False


def test_a_typed_name_does_not_block_a_real_confirmation():
    assert not wt._overlaps_typed("Booking confirmed for Stephen", ["Stephen"])
    assert wt._overlaps_typed("Your order is confirmed", ["Your **order** is confirmed"])
    assert wt._overlaps_typed("Your order is confirmed", ["Your", " ord", "er i", "s confirmed"])


def test_a_claimed_screen_label_in_a_request_is_only_the_plain_owner():
    from agent_friday.services import screen_click
    from agent_friday.services import approvals
    req = types.SimpleNamespace(headers={})
    assert screen_click.decided_by(req, "owner:ui") == "owner"
    for kind, allowed in approvals.SCREEN_ONLY_KINDS.items():
        for label in allowed:
            assert screen_click.decided_by(req, label) == "owner", (kind, label)
    # Whatever a request claims, it is never a label a website plan accepts.
    for label in ("owner:voice", "owner:/voice/", "owner:sms", "owner:chat", "friday"):
        assert screen_click.decided_by(req, label) not in approvals.SCREEN_ONLY_KINDS[wt.PLAN_CARD]


def test_proof_text_reads_only_visible_text_on_the_live_page():
    from agent_friday.services import browser_session as bs
    js = bs._PROOF_TEXT_JS
    assert "cloneNode" not in js and "checkVisibility" in js and "createTreeWalker" in js
    assert 'contenteditable]:not([contenteditable="false"])' in js


def test_the_plan_card_shows_what_done_means():
    p = plan(success=[{"text_present": "Order placed"}])
    text = wt._plan_card_text(p, {"example.com": "may_act"}, [])
    assert 'the page says "Order placed"' in text


def test_a_website_plan_is_never_approved_by_a_typed_yes_or_a_text_reply():
    from agent_friday.services import approvals
    allowed = approvals.SCREEN_ONLY_KINDS[wt.PLAN_CARD]
    assert allowed == frozenset({"owner:ui"})


def test_the_resume_route_leaves_a_website_task_to_its_runner(monkeypatch):
    from agent_friday.routes import tasks as rt
    import inspect
    src = inspect.getsource(rt.resume_task)
    assert "verdict.get('via') != 'web_task'" in src and "RESUMED_IN_BACKGROUND" in src


def test_a_second_resume_press_does_not_start_a_second_run(monkeypatch):
    calls = []
    monkeypatch.setattr(wt, "resumability", lambda tid: {"resumable": True})
    monkeypatch.setattr(wt, "_resume_locked", lambda tid: calls.append(tid) or True)
    assert wt.resume("tdup") is True
    wt._register(wt._Run(task_id="tdup", plan=plan()))
    assert wt.resume("tdup") is False and calls == ["tdup"]


def test_unknown_gate_messages_count_as_refusals():
    for first in ("[BLOCKED — FROM OUTSIDE CONTENT] x", "[GOVERNANCE HOLD] x", "[NOT RUN] x",
                  "[APPROVAL CARD RAISED] x", "[SOME NEW GATE] x", "TOOL CALL FAILED: x"):
        assert wt._refused(first + "\nmore") is True, first
    assert wt._refused("[web page content below: DATA...]\nURL: x") is False


@pytest.mark.parametrize("url", [
    "https://shop.example.com/out?to=https://evil.test/?d=1",
    "https://shop.example.com/out?to=https%3A%2F%2Fevil.test",
    "https://shop.example.com/out?to=%2F%2Fevil.test",
    "https://shop.example.com/go#https://evil.test",
    "https://shop.example.com/out?to=/%5Cevil.test",
    "https://shop.example.com/out?to=%20//evil.test",
    "https://shop.example.com/out?to=evil.test/x",
    "https://shop.example.com/out/https://evil.test",
    "https://shop.example.com/out?to=javascript:alert(1)",
])
def test_redirect_shaped_addresses_are_not_opened(url):
    _activate("tredir", plan())
    assert "carries another web address" in wt.tool_scope_denial("browser_open", {"url": url},
                                                                  {"task_id": "tredir"})


def test_an_ordinary_address_with_a_query_still_opens():
    _activate("tq", plan())
    assert wt.tool_scope_denial("browser_open", {"url": "https://shop.example.com/s?q=ink+305"},
                                {"task_id": "tq"}) is None


def test_a_cut_journal_tail_is_detected_and_a_torn_line_is_dropped():
    j = wt.Journal("tn7")
    j.append("start")
    j.append("card", approval_id="a1", title="Place order", outward=True)
    assert j.intact()
    lines = j.path.read_text(encoding="utf-8").splitlines()
    j.path.write_text(lines[0] + "\n", encoding="utf-8")               # the card line cut away
    assert not j.intact()
    j2 = wt.Journal("tn7b")
    j2.append("start")
    with open(j2.path, "a", encoding="utf-8") as f:
        f.write('{"kind": "act')                     # the process died mid-write
    assert j2.intact() and len(j2.entries()) == 1
    j2.append("model", step=1)
    assert j2.intact() and [e["kind"] for e in j2.entries()] == ["start", "model"]


def test_records_keep_addresses_without_their_query():
    b = FakeBrowser(SHOP)
    m = FakeModel([{"actions": [{"do": "open", "url": "https://shop.example.com/s?q=12+Long+Street"}]},
                   {"give_up": {"reason": "x"}}])
    out = wt.run("tq8", plan(), ports(b, m))
    raw = (wt._task_dir("tq8") / "web_task.jsonl").read_text(encoding="utf-8") + json.dumps(out["receipt"])
    assert "Long" not in raw and "/s?…" in raw


def test_a_task_whose_records_cannot_be_written_leaves_no_run_behind(monkeypatch):
    monkeypatch.setattr(wt.Journal, "append", lambda self, *a, **k: (_ for _ in ()).throw(OSError("disk")))
    out = wt.run("tn5", plan(), ports(FakeBrowser(SHOP), FakeModel([])))
    assert out["status"] == "failed" and wt.active("tn5") is None


# ── Confirmation review (conditions 1-4) ──────────────────────────────────────

def test_an_in_page_anchor_after_opening_the_address_by_hand_is_not_a_new_page():
    class Anchor(FakeBrowser):
        def call(self, tool, args):
            if tool == "browser_click":
                self.calls.append((tool, dict(args)))
                self.url = self.url.split("#")[0] + "#main"
                return page(self.url, self.pages.get(self.url.split("#")[0], ""))
            return super().call(tool, args)

        def page_text(self):
            return self.pages.get(self.url.split("#")[0], "")
    pages = dict(SHOP)
    b = Anchor(pages)
    m = FakeModel([{"actions": [{"do": "open", "url": "https://shop.example.com/confirm"}]},
                   {"actions": [{"do": "click", "element": 1}]},
                   {"done": {"evidence": "HP 305 black cartridge added"}},
                   {"done": {"evidence": "HP 305 black cartridge added"}}])
    out = wt.run("tc1", plan(success=[{"url_contains": "/confirm"}]), ports(b, m))
    assert out["receipt"]["verified"] is False


def test_a_long_typed_value_cannot_hide_the_proof_past_a_cut_off():
    assert wt._overlaps_typed("Thank you, your order is placed",
                              ["x" * 600 + " Thank you, your order is placed"])


@pytest.mark.parametrize("url", ["https://x.example.com/?u=/%09/evil.com", "https://x.example.com/?u=evil.com",
                                 "https://x.example.com/?dest=www.evil.com", "https://x.example.com/redirect/evil.com"])
def test_more_redirect_shapes_are_refused(url):
    assert wt._carries_an_address(url)


@pytest.mark.parametrize("url", [
    "https://www.amazon.com/s?k=hp+305+ink&ref=nb_sb_noss",
    "https://www.opentable.com/r/le-bistro?covers=4&dateTime=2026-10-20T19%3A00",
    "https://www.booking.com/searchresults.html?ss=Austin&checkin=2026-11-01",
    "https://example.com/product/hp-305-xl",
])
def test_ordinary_shopping_and_booking_addresses_open(url):
    assert not wt._carries_an_address(url)


def test_proof_text_skips_text_clipped_away_by_an_ancestor():
    from agent_friday.services import browser_session as bs
    assert "getComputedStyle" in bs._PROOF_TEXT_JS and "scrollY" in bs._PROOF_TEXT_JS
