"""The organize tools by voice, per docs/reference/voice-tool-contract.md.

  * Each is declared for the live session with its required arguments, routes
    to its handler, and carries the call's conversation (§1).
  * Its description says what to say while it runs, names the fallback and
    gives the three ways out of a card: yes, no, or change it (§2, §4).
  * A spoken answer decides a card only through local_context.decide_by_voice,
    the one way a card is decided by voice (§3).
  * What reaches the cloud voice model names no subject, sender, account, file
    or page Friday found. The seam tests read the text _voice_tool_run hands
    back, which is what the live call receives (§5).
"""
from __future__ import annotations

import os

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import action_journal as journal
from agent_friday.services import agent
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import voice_engine as ve

TOOLS = {"organize_email": ["action"], "organize_files": ["action"],
         "organize_wiki": ["action"], "undo_action": [],
         "answer_card": ["card_id", "decision"]}


def _spec(name):
    return next(t for t in ve._VOICE_LIVE_TOOLS if t[0] == name)


def _quiet_call(fn, *a, **k):
    tok = ia.QUIET.set(True)
    try:
        return fn(*a, **k)
    finally:
        ia.QUIET.reset(tok)


def _say(*_a, **_k):
    return None


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    import agent_friday.privacy.vault_crypto as vc
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(vc, "sign_entry", lambda e, *a, **k: e, raising=False)
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    monkeypatch.setattr(journal, "_path", lambda: tmp_path / "receipts.jsonl")
    monkeypatch.setattr(journal, "home_trash", lambda rid: tmp_path / "friday-trash" / rid)
    journal.reset()
    ia._CHOICES.clear()
    yield
    journal.reset()
    ia._CHOICES.clear()


# ── §1, §2, §4: declared, routed, and told how to behave ────────────────────

@pytest.mark.parametrize("name,required", sorted(TOOLS.items()))
def test_each_is_a_voice_tool_with_its_required_args(name, required):
    assert _spec(name)[3] == required
    assert name not in ve._VOICE_SHARED_TOOLS, "one name, one declaration"
    assert ve._voice_tool_names().count(name) == 1


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_each_routes_to_its_handler_and_carries_the_conversation(name, monkeypatch):
    """A declared tool with no route answers 'unknown tool' out loud; one that
    loses the conversation reports an approved batch where nobody listens."""
    seen = []
    monkeypatch.setattr(agent, "_tool_" + name,
                        lambda inp: seen.append(agent._CURRENT_CONVERSATION.get()) or "ok")
    monkeypatch.setattr(agent, "_execute_tool",
                        lambda t, a, handler=None, session_ctx=None: handler(a))
    out = ve._voice_tool_run(name, {"action": "archive"}, _say, {"conversation_id": "conv-call"})
    assert out == "ok" and seen == ["conv-call"], out


def test_lists_reach_gemini_as_lists_of_strings():
    types = pytest.importorskip("google.genai.types", reason="google-genai not installed")
    decls = {d.name: d for t in ve._build_voice_live_tools(types)
             for d in (t.function_declarations or [])}
    for tool, arg in (("organize_files", "items"), ("organize_wiki", "pages"),
                      ("organize_email", "thread_ids")):
        prop = decls[tool].parameters.properties[arg]
        assert prop.type == types.Type.ARRAY and prop.items.type == types.Type.STRING, tool


def test_the_descriptions_carry_the_manners():
    mail = _spec("organize_email")[1]
    assert "yes, no, or change it" in mail and "replaces" in mail
    assert "ask_local_for_context" in mail, "hearing what private mail says goes to the local model"
    assert "Settings" in mail and "do NOT say you cannot reach" in mail, "name the fallback"
    assert "yes, no, or change it" in _spec("organize_files")[1]
    assert "#1" in _spec("organize_wiki")[1]
    answer = _spec("answer_card")[1]
    assert "NOT RECORDED" in answer and "never say it was done" in answer
    assert "RUNNING" in answer, "say what to do while it is still working"


def test_a_card_result_gives_the_three_ways_out():
    text = agent._organize_result({"status": "pending_approval", "approval_id": "appr_9",
                                   "readback": "I found 3 conversations. Shall I archive them?"})
    assert "yes, no, or change it" in text and "replaces" in text and "card_id=appr_9" in text


# ── Mail ────────────────────────────────────────────────────────────────────

ACCOUNTS = [
    {"id": "acct_work", "label": "Work", "email": "me@work.example", "services": {"gmail": True},
     "mail": {"read": True, "modify": True}, "health": {"healthy": True}},
    {"id": "acct_home", "label": "Home", "email": "me@home.example", "services": {"gmail": True},
     "mail": {"read": True, "modify": False}, "health": {"healthy": True}},
]
PRIVATE_MAIL = ("Quarterly invoice", "Harbor Legal Billing", "Home", "me@home.example")


@pytest.fixture
def gmail(monkeypatch):
    from agent_friday.services import gmail_mailbox as gm
    from agent_friday.services import google_accounts as G
    calls = []
    monkeypatch.setattr(G, "list_accounts", lambda: [dict(a) for a in ACCOUNTS])
    monkeypatch.setattr(gm, "can_modify", lambda acct: acct == "acct_work")
    monkeypatch.setattr(ia, "_search_threads", lambda acct, q, limit: (
        {"acct_work": ["t1", "t2", "t3"], "acct_home": ["h1"]}.get(acct, [])[:limit], False))
    monkeypatch.setattr(ia, "_thread_heads", lambda acct, tids: {
        t: {"subject": "Quarterly invoice %s" % t, "sender": "Harbor Legal Billing", "date": ""}
        for t in tids})
    monkeypatch.setattr(gm, "apply_action", lambda acct, tids, action: calls.append(
        (acct, tuple(tids), action)) or {"changed": {t: {"added": [], "removed": ["INBOX"]}
                                                    for t in tids}, "failed": {}})
    return calls


def test_a_spoken_mail_request_hands_the_cloud_no_names(gmail):
    out = ve._voice_tool_run("organize_email", {"action": "archive", "query": "from:billing"},
                             _say, {"conversation_id": "c-mail",
                                    "owner_text": "archive the billing mail"})
    assert out.startswith("CARD_RAISED"), out
    assert not [w for w in PRIVATE_MAIL if w in out], out
    assert "3 conversations" in out and "on your screen" in out and "one account" in out
    typed = agent._execute_tool("organize_email", {"action": "trash", "query": "from:billing"},
                                session_ctx=agent.prepare_confirmation_ctx(
                                    "s-typed", "trash the billing mail", {"authenticated": True}))
    assert "Quarterly invoice" in typed and "Home" in typed, "chat keeps the names"


def test_a_spoken_answer_is_decided_by_the_voice_path(gmail, monkeypatch):
    """§3: reuse decide_by_voice; do not add a second way to say yes."""
    from agent_friday.services import local_context as lc
    calls, real = [], lc.decide_by_voice
    monkeypatch.setattr(lc, "decide_by_voice", lambda *a: calls.append(a) or real(*a))
    out = ia.propose_email("archive", query="from:billing", owner_words="archive the billing mail")
    words = "yeah go ahead and archive them"
    res = ia.answer_card(out["approval_id"], "approve", words, surface="voice-live")
    assert calls == [(out["approval_id"], words, False, "approve")]
    assert res["ok"], res
    assert ap.get_approval(out["approval_id"])["decided_by"] == "owner:/voice/"


def test_words_the_voice_path_does_not_hear_as_yes_decide_nothing(gmail):
    out = ia.propose_email("archive", query="from:billing", owner_words="archive the billing mail")
    res = ia.answer_card(out["approval_id"], "approve", "hmm, maybe later", surface="voice-live")
    assert res["text"].startswith("NOT RECORDED")
    assert ap.get_approval(out["approval_id"])["status"] == "pending"


def test_a_spoken_yes_to_the_cloud_is_answered_in_counts(gmail):
    out = ia.propose_email("archive", query="from:billing", owner_words="archive the billing mail")
    res = _quiet_call(ia.answer_card, out["approval_id"], "approve", "yes, go ahead",
                      surface="voice-live")
    assert res["text"] == "Approved and done: Archived 3 conversations.", res


def test_change_it_withdraws_the_card_it_replaces(gmail):
    first = ia.propose_email("archive", query="from:billing", conversation_id="c1",
                             owner_words="archive the billing mail")
    second = ia.propose_email("archive", query="from:billing older_than:1y", conversation_id="c1",
                              owner_words="only the old ones", replaces=first["approval_id"])
    old = ap.get_approval(first["approval_id"])
    assert old["status"] == "denied" and old["decided_by"] == "friday:withdrawn"
    assert ap.get_approval(second["approval_id"])["status"] == "pending"
    assert any("withdrawn" in n for n in second["notes"])
    elsewhere = ia.propose_email("trash", query="is:unread", conversation_id="c-other")
    ia.propose_email("spam", query="from:billing", conversation_id="c1",
                     replaces=elsewhere["approval_id"])
    assert ap.get_approval(elsewhere["approval_id"])["status"] == "pending", (
        "a card from another conversation is not this request's to withdraw")


def test_a_finished_batch_tells_the_live_call_in_counts(gmail):
    from agent_friday.services import voice_live_channel as vlc
    heard = []

    def hear(text, kind):
        heard.append((kind, text))
    vlc.register("c-live", hear)
    try:
        out = ia.propose_email("archive", query="from:billing", conversation_id="c-live",
                               owner_words="archive the billing mail")
        ap.decide(out["approval_id"], "approve")
        assert ia.wait_for(out["approval_id"], 10)
    finally:
        vlc.unregister("c-live", hear)
    assert heard and heard[0][0] == "result", heard
    assert "Archived 3 conversations" in heard[0][1]
    assert not [w for w in PRIVATE_MAIL if w in heard[0][1]], heard


# ── Wiki ────────────────────────────────────────────────────────────────────

@pytest.fixture
def wiki(tmp_path, monkeypatch):
    from agent_friday.services import wiki_engine as we
    root = tmp_path / "wiki"
    (root / "people").mkdir(parents=True)
    (root / "family").mkdir()
    monkeypatch.setattr(we, "WIKI_DIR", root)
    monkeypatch.setattr(we, "_wiki_mirror_dir", lambda: None)
    monkeypatch.setattr(we, "_wiki_encrypted_sections", lambda: set())
    monkeypatch.setattr(agent, "_get_vault_key", lambda: os.urandom(32))
    (root / "people" / "Dana Reyes.md").write_text("# Dana Reyes\n", encoding="utf-8")
    (root / "family" / "Dana birthday ideas.md").write_text("# Ideas\n", encoding="utf-8")
    return root


def test_an_ambiguous_page_is_numbered_on_screen_not_read_to_the_cloud(wiki, monkeypatch):
    shown = []
    monkeypatch.setattr(ia, "_notify", lambda title, body, kind="info", priority="":
                        shown.append(body))
    call = {"conversation_id": "c-wiki"}
    out = ve._voice_tool_run("organize_wiki", {"action": "archive", "pages": ["Dana"]}, _say, call)
    assert out.startswith("NOT DONE") and "2 pages" in out and "#1" in out, out
    assert "Reyes" not in out and "birthday" not in out, out
    listed = shown[0].splitlines()
    assert len(listed) == 2 and listed[1].startswith("2. ")
    second = listed[1][3:]
    out = ve._voice_tool_run("organize_wiki", {"action": "archive", "pages": ["#2"]}, _say, call)
    assert out.startswith("DONE: Archived 1 page."), out
    assert "Reyes" not in out and "birthday" not in out, out
    assert (wiki / "_archived" / second).is_file(), "#2 is the second page on the screen"


def test_typed_choices_are_named_and_numbered(wiki):
    with pytest.raises(ia.Refused) as e:
        ia.organize_wiki("archive", pages=["Dana"])
    assert "1. " in e.value.user_message and "Dana Reyes" in e.value.user_message


def test_a_number_with_no_choices_is_refused(wiki):
    with pytest.raises(ia.Refused):
        ia.wiki_ref("#1")
