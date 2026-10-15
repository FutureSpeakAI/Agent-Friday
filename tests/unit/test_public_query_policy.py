"""Owner decision (a), 2026-10-15: public query text is not private by topic.

SENSITIVE SUBSYSTEM (AGENTS.md): exercises privacy/vault_access.check_action,
privacy/public_query and the search/fetch gates (web_search, firecrawl).

The failure (install report item 17): a cloud seat's ``search_web`` calls for
"disability OR Medicaid OR Social Security" and "transgender rights Texas
court ruling" were keyword-tiered TIER_3 by check_action and refused as
``cloud_denied_tier_TIER_3``; the search layer's own gate tier-classified the
same query again. The refusal reached friday.log nowhere (print() under
pythonw).

The rule now: public query text a CLOUD seat authored (search_web.query,
search_news.query, deep_research.question/sub_questions) is checked ONLY
against the never-send floor (credentials and keys, ID/account numbers, the
owner's never-send list) and the owner's personal identifiers, with or without
cloud consent. Every refusal names its component and reason, is logged to the
"friday.privacy" logger tree, and never carries the value.

The relaxation applies ONLY to the exact strings the cloud seat wrote, pinned
below: a query whose [PII:...] tag was put back as a real value, a query a
local seat or a background job wrote, a replayed query the receiving provider
did not write (or that was refused), and browse_web URLs all keep the
ordinary topic gate. Result rows pass only as the exact rendered row.

All fixtures are synthetic.
"""
from __future__ import annotations

import inspect
import json
import logging
from urllib.parse import quote_plus

import pytest

MEDICAID_Q = "disability OR Medicaid OR Social Security"
CIVIL_Q = "transgender rights Texas court ruling"
NEVER_TOKEN = "Zebra Quokka Ledger 7731"
OWNER_EMAIL = "owner.synthetic@example.invalid"
FAKE_KEY = "sk-ant-api03-" + "A" * 40  # pragma: allowlist secret
SSN_Q = "who owns 123-45-6789"  # pragma: allowlist secret
DOTTED_SSN_Q = "who owns 123.45.6789"  # pragma: allowlist secret
# Fullwidth digits AND fullwidth hyphen-minus (U+FF0D): NFKC folds it to the
# ASCII SSN_Q number; the ASCII-hyphen SSN pattern does not see it unfolded.
FULLWIDTH_SSN_Q = "who owns \uff11\uff12\uff13\uff0d\uff14\uff15\uff0d\uff16\uff17\uff18\uff19"  # pragma: allowlist secret
ROW_TITLE = "Medicaid disability benefits after the Social Security court ruling"
ROW_SNIPPET = ("Disability recipients on Medicaid and Social Security keep "
               "their benefits while the court ruling is appealed.")
ROW_URL = "https://example.org/news/medicaid-disability-ruling"
ROW2_TITLE = "Medicaid disability appeals and the hospital diagnosis backlog"
ROW2_SNIPPET = "Appeals for disability benefits are waiting on medical records."
ROW2_URL = "https://example.org/news/medicaid-appeals"
PII_TAG = "[PII:name:0a1b2c3d]"
SYNTH_NAME = "Pat Synthetic"
HEALTH_TAIL = "diabetes insulin dose and HIV diagnosis"


def _cloud(*texts, provider="anthropic"):
    """The context a cloud seat's search tool call runs in (agent loops):
    the exact strings that seat sent."""
    from agent_friday.privacy import public_query
    return public_query.cloud_authored(provider, texts)


@pytest.fixture(autouse=True)
def _clean_public_registry():
    from agent_friday.privacy import public_query as pq
    from agent_friday.services import egress_gate as eg
    with eg._TRUSTED_LOCK:
        saved_echo = {k: set(v) for k, v in eg._PROVIDER_ECHO.items()}
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
    pq.clear_cloud_queries()
    yield
    with eg._TRUSTED_LOCK:
        eg._PUBLIC_PARAS.clear()
        eg._PUBLIC_ORIGINS.clear()
        eg._PROVIDER_ECHO.clear()
        eg._PROVIDER_ECHO.update(saved_echo)
    pq.clear_cloud_queries()


@pytest.fixture(autouse=True)
def floor(monkeypatch):
    """No owner never-send tokens, identifiers or consent unless a test adds
    them; the floor reads them through its real loaders."""
    from agent_friday import core
    from agent_friday.services import egress_gate, judgment_gate
    state = {"never": [], "settings": {}, "watch": []}
    monkeypatch.setattr(judgment_gate, "never_send_tokens",
                        lambda: list(state["never"]))
    monkeypatch.setattr(core, "_load_settings", lambda: dict(state["settings"]))
    monkeypatch.setattr(core, "_load_privacy_watchlist",
                        lambda: list(state["watch"]))
    try:
        from agent_friday.phone import config as phone_config
        monkeypatch.setattr(phone_config, "load", lambda: {"owner_cell": ""})
    except ImportError:
        pass
    monkeypatch.setattr(egress_gate, "is_unrestricted_cloud", lambda: False)
    return state


def _check(tmp_path, action, args, provider="anthropic"):
    from agent_friday.privacy.vault_access import VaultAccessControl
    return VaultAccessControl().check_action(
        provider, action, json.dumps(args),
        access_log_path=str(tmp_path / "access-log.jsonl"))


# ── the reported refusals ───────────────────────────────────────────────────

@pytest.mark.parametrize("query", [MEDICAID_Q, CIVIL_Q])
def test_public_health_and_civil_rights_queries_reach_a_cloud_seat(tmp_path, query):
    allowed, detail, _tier = _check(tmp_path, "search_web", {"query": query})
    assert allowed, f"{query!r} was refused by topic: {detail}"
    assert detail == "public_query_ok", detail


@pytest.mark.parametrize("query", [MEDICAID_Q, CIVIL_Q])
def test_the_search_layer_sends_a_cloud_seats_query_too(query):
    from agent_friday.services import web_search as ws
    with _cloud(query):
        gated, refusal = ws._gate_search_query(query, "brave")
    assert refusal == "", f"the search gate refused a public topic: {refusal}"
    assert gated == query


def test_firecrawl_sends_a_cloud_seats_public_health_query(monkeypatch):
    import requests
    from agent_friday.services import firecrawl as fc
    sent = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"success": True, "data": [
                {"url": "https://example.com/a", "title": "t", "description": "d"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return _Resp()

    monkeypatch.setattr(fc, "api_key", lambda: "fake-firecrawl-key")  # pragma: allowlist secret
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(fc, "_meter", lambda *a, **k: None)
    with _cloud(MEDICAID_Q):
        out = fc.search(MEDICAID_Q, count=1)
    assert out["ok"] is True, out
    assert sent and sent[-1]["query"] == MEDICAID_Q


# ── A1: the author decides, not the search backend ──────────────────────────

def test_a_query_no_cloud_seat_authored_keeps_the_topic_gate(monkeypatch):
    """A local seat's query, the research harness's, setup research's: the
    `provider` the search gate is handed is the BACKEND, so without the
    cloud-authored mark the old topic-classifying gate must run, unchanged."""
    from agent_friday.services import egress_gate
    from agent_friday.services import web_search as ws
    seen = []

    def topic_gate(text, provider, field, log_path=None):
        seen.append(field)
        return ""                       # the topic classifier withholds it

    monkeypatch.setattr(egress_gate, "_gate_text", topic_gate)
    gated, refusal = ws._gate_search_query(MEDICAID_Q, "brave")
    assert seen == ["web_search.query"], "the topic gate was skipped"
    assert gated == "" and "stays on this device" in refusal

    seen.clear()
    with _cloud(MEDICAID_Q):
        gated, refusal = ws._gate_search_query(MEDICAID_Q, "brave")
    assert seen == [] and refusal == "" and gated == MEDICAID_Q


def test_firecrawl_search_without_a_cloud_author_keeps_the_topic_gate(monkeypatch):
    from agent_friday.services import egress_gate
    from agent_friday.services import firecrawl as fc
    seen = []
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda t, p, f, log_path=None: seen.append(f) or "")
    _, refusal = fc._gate_outbound(MEDICAID_Q, "firecrawl.search.query")
    assert seen == ["firecrawl.search.query"] and refusal


def test_for_tool_call_marks_only_a_cloud_seats_public_query_strings():
    from agent_friday.privacy import public_query as pq
    with pq.for_tool_call("anthropic", "claude-x", "search_web", {"query": MEDICAID_Q}):
        assert pq.authored_by_cloud() == "anthropic"
        assert pq.cloud_author_of(MEDICAID_Q) == "anthropic"
        assert pq.cloud_author_of(f"  {MEDICAID_Q}\n") == "anthropic"   # strip only
        assert pq.cloud_author_of(MEDICAID_Q + " 2026") == ""         # exact
        assert pq.cloud_author_of(CIVIL_Q) == ""
    assert pq.authored_by_cloud() == "" and pq.cloud_author_of(MEDICAID_Q) == ""
    with pq.for_tool_call("anthropic", "", "deep_research",
                          {"question": MEDICAID_Q, "sub_questions": [CIVIL_Q],
                           "context": "my own notes"}):
        assert pq.cloud_author_of(CIVIL_Q) == "anthropic"
        assert pq.cloud_author_of("my own notes") == ""   # not a public field
    # A cloud-tagged model served through the local daemon is a cloud seat.
    with pq.for_tool_call("ollama", "glm-4.6:cloud", "search_web", {"query": MEDICAID_Q}):
        assert pq.cloud_author_of(MEDICAID_Q) == "ollama"
    with pq.for_tool_call("ollama", "llama3.1:8b", "search_web", {"query": MEDICAID_Q}):
        assert pq.cloud_author_of(MEDICAID_Q) == ""
    with pq.for_tool_call("local", "", "deep_research", {"question": MEDICAID_Q}):
        assert pq.cloud_author_of(MEDICAID_Q) == ""
    with pq.for_tool_call("anthropic", "", "read_file", {"path": MEDICAID_Q}):
        assert pq.cloud_author_of(MEDICAID_Q) == ""
    with pq.for_tool_call("anthropic", "", "browse_web", {"url": MEDICAID_Q}):
        assert pq.cloud_author_of(MEDICAID_Q) == ""


def test_a_rehydrated_pii_tag_keeps_the_topic_gate(monkeypatch):
    """HIGH-1. A cloud seat writes a contact's name as a [PII:...] tag;
    _execute_tool puts the real name back (_restore_placeholders) BEFORE the
    search gate runs. That name + health query is not what the cloud seat
    wrote, so it must take the ordinary topic gate (refused), while the
    identical query typed out by the cloud seat itself is floor-only.

    Red on 73ad3bcfa: the mark covered the whole tool call, so the rehydrated
    query was floor-only (and for_tool_call took no args)."""
    from agent_friday.privacy import public_query as pq
    from agent_friday.services import agent as A
    from agent_friday.services import egress_gate
    from agent_friday.services import web_search as ws
    seen = []
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda t, p, f, log_path=None: seen.append(t) or "")
    tagged = {"query": f"{PII_TAG} {HEALTH_TAIL}"}
    plain_q = f"{SYNTH_NAME} {HEALTH_TAIL}"

    with pq.for_tool_call("anthropic", "claude-x", "search_web", tagged):
        restored = A._restore_placeholders(dict(tagged), {PII_TAG: SYNTH_NAME})["query"]
        assert restored == plain_q
        gated, refusal = ws._gate_search_query(restored, "brave")
    assert seen == [plain_q], "the rehydrated query skipped the topic gate"
    assert gated == "" and refusal

    seen.clear()
    with pq.for_tool_call("anthropic", "claude-x", "search_web", {"query": plain_q}):
        gated, refusal = ws._gate_search_query(plain_q, "brave")
    assert seen == [] and refusal == "" and gated == plain_q


def test_both_agent_loops_and_voice_mark_the_input_as_sent():
    """The two agent-loop dispatch sites and the live-voice dispatcher are the
    only places a cloud seat's tool call executes; each wraps it, handing the
    mark the input as the seat sent it (before _restore_placeholders), and
    each chat loop records the replay provenance under its loop provider."""
    from agent_friday.services import agent as A
    from agent_friday.services import voice_engine as V
    claude_src = inspect.getsource(A._call_claude_agent_run)
    oai_src = inspect.getsource(A._oai_agentic_loop_run)
    assert "for_tool_call as _pq_call" in claude_src
    assert "tu.name, tu.input):" in claude_src
    assert "for_tool_call as _pq_call" in oai_src
    assert "model or \"\", tname, targs):" in oai_src
    for src in (claude_src, oai_src):
        assert "author_provider=_LOOP_PROVIDER.get()" in src
    assert "_pq_call(c.get(\"provider\", \"cloud\"), \"\", tool, a)" in inspect.getsource(V)


# ── A2: a URL is not search text ────────────────────────────────────────────

def test_browse_web_url_keeps_the_topic_check_as_on_main(tmp_path):
    """A cloud seat could otherwise carry context out in a URL query string."""
    from agent_friday.privacy import public_query as pq
    assert "browse_web" not in pq.PUBLIC_QUERY_TOOLS
    allowed, detail, _ = _check(
        tmp_path, "browse_web",
        {"url": "https://example.org/x?q=my medical diagnosis and prescription history"})
    assert not allowed, "a URL query string escaped the topic check"
    assert detail.startswith("cloud_denied_tier_"), detail


def test_a_firecrawl_scrape_url_keeps_the_topic_gate_even_for_a_cloud_seat(monkeypatch):
    from agent_friday.services import egress_gate
    from agent_friday.services import firecrawl as fc
    seen = []
    monkeypatch.setattr(egress_gate, "_gate_text",
                        lambda t, p, f, log_path=None: seen.append(f) or "")
    url = "https://example.org/?q=private"
    with _cloud(url):
        _, refusal = fc._gate_outbound(url, "firecrawl.scrape.url")
    assert seen == ["firecrawl.scrape.url"] and refusal


def test_deep_research_question_is_public_but_its_context_is_not(tmp_path):
    ok, detail, _ = _check(tmp_path, "deep_research", {"question": MEDICAID_Q})
    assert ok, detail
    ok, detail, _ = _check(
        tmp_path, "deep_research",
        {"question": MEDICAID_Q,
         "context": "my medical diagnosis and prescription history"})
    assert not ok, "the non-public context argument lost its topic check"
    assert detail.startswith("cloud_denied_tier_"), detail


# ── what still blocks: the floor and the owner's identifiers ────────────────

def test_a_never_send_token_is_refused(tmp_path, floor):
    floor["never"] = [NEVER_TOKEN]
    allowed, detail, _ = _check(tmp_path, "search_web",
                                {"query": f"{NEVER_TOKEN} court records"})
    assert not allowed
    assert detail == "public_query_refused:never_send_list", detail


def test_a_credential_is_refused(tmp_path):
    allowed, detail, _ = _check(tmp_path, "search_web",
                                {"query": f"is {FAKE_KEY} valid"})
    assert not allowed
    assert detail == "public_query_refused:credential", detail


def test_an_ssn_shaped_number_is_refused(tmp_path):
    allowed, detail, _ = _check(tmp_path, "search_web", {"query": SSN_Q})
    assert not allowed
    assert detail == "public_query_refused:id_or_account_number", detail


def test_the_owners_email_is_refused(tmp_path, floor):
    floor["settings"] = {"user_email": OWNER_EMAIL}
    allowed, detail, _ = _check(tmp_path, "search_web",
                                {"query": f"{OWNER_EMAIL} data breach"})
    assert not allowed
    assert detail == "public_query_refused:owner_identifier", detail


def test_the_owners_email_url_encoded_in_a_query_is_refused(tmp_path, floor):
    floor["settings"] = {"owner_identities": [OWNER_EMAIL]}
    allowed, detail, _ = _check(
        tmp_path, "search_web",
        {"query": "lookup owner.synthetic%40example.invalid"})
    assert not allowed
    assert detail == "public_query_refused:owner_identifier", detail


# ── A5: every form the text could take on the wire ──────────────────────────

def test_a_double_encoded_never_send_token_is_refused(tmp_path, floor):
    floor["never"] = [NEVER_TOKEN]
    twice = quote_plus(quote_plus(NEVER_TOKEN))      # "Zebra%2BQuokka..."
    assert NEVER_TOKEN not in twice
    allowed, detail, _ = _check(tmp_path, "search_web",
                                {"query": f"records {twice}"})
    assert not allowed, "a double-encoded never-send token got through"
    assert detail == "public_query_refused:never_send_list", detail


def test_a_dotted_ssn_is_refused(tmp_path):
    allowed, detail, _ = _check(tmp_path, "search_web", {"query": DOTTED_SSN_Q})
    assert not allowed, "a dotted SSN is the same SSN as the hyphenated one"
    assert detail == "public_query_refused:id_or_account_number", detail


def test_a_fullwidth_ssn_is_refused(tmp_path):
    """LOW. Red on 73ad3bcfa: no NFKC fold, so the fullwidth hyphens hid the
    SSN shape from the floor."""
    allowed, detail, _ = _check(tmp_path, "search_web", {"query": FULLWIDTH_SSN_Q})
    assert not allowed, "a fullwidth SSN is the same SSN"
    assert detail == "public_query_refused:id_or_account_number", detail


@pytest.mark.parametrize("zw", ["\u200b", "\u200d", "\u2060", "\u00ad"])
def test_a_zero_width_split_never_send_token_is_refused(tmp_path, floor, zw):
    """LOW. Red on 73ad3bcfa: a format character (category Cf) inside the
    token split it, so the never-send match missed."""
    floor["never"] = [NEVER_TOKEN]
    split = NEVER_TOKEN[:3] + zw + NEVER_TOKEN[3:]
    assert NEVER_TOKEN not in split
    allowed, detail, _ = _check(tmp_path, "search_web",
                                {"query": f"{split} court records"})
    assert not allowed, "a zero-width-split never-send token got through"
    assert detail == "public_query_refused:never_send_list", detail


@pytest.mark.parametrize("query", [
    f"{MEDICAID_Q} 2026-10-15",          # a date is not an ID
    "court ruling 10/15/2026 version 1.2.3",
    "top 100 of 2024 health stories",
    "\uff08Medicaid\uff09 disability 2024",   # fullwidth brackets fold harmlessly
])
def test_dates_and_versions_are_not_invented_ids(tmp_path, query):
    allowed, detail, _ = _check(tmp_path, "search_web", {"query": query})
    assert allowed, detail


def test_the_floor_is_the_same_under_unrestricted_consent(monkeypatch):
    from agent_friday.services import egress_gate
    from agent_friday.services import web_search as ws
    monkeypatch.setattr(egress_gate, "is_unrestricted_cloud", lambda: True)
    with _cloud(SSN_Q, MEDICAID_Q):
        _, refusal = ws._gate_search_query(SSN_Q, "brave")
        assert refusal and "privacy.public_query" in refusal
        gated, refusal = ws._gate_search_query(MEDICAID_Q, "brave")
    assert refusal == "" and gated == MEDICAID_Q


# ── A3: the history replay and the result rows ──────────────────────────────

def _rendered_search(monkeypatch, query, rows=None):
    """search_web's tool result for `query`, from the renderer the model is
    actually handed, over a stubbed backend. The outbound query gate is
    stubbed open (it is pinned by the tests above), so this runs the same on
    main and shows what the REPLAY does with the result."""
    from agent_friday.services import agent as A
    from agent_friday.services import web_search as W
    monkeypatch.setattr(W, "_gate_search_query", lambda q, p: (q, ""))
    monkeypatch.setattr(W, "_wigolo_ready", lambda: True)
    monkeypatch.setattr(W, "_firecrawl_ready", lambda: False)
    monkeypatch.setattr(W, "brave_key", lambda: "")
    rows = rows or [{"title": ROW_TITLE, "url": ROW_URL, "description": ROW_SNIPPET}]
    monkeypatch.setattr(W, "_wigolo_search", lambda q, n: {
        "status": W.SearchStatus.OK, "detail": "", "results": list(rows)})
    return A._tool_search_web({"query": query, "count": len(rows)})


def _replay(query, rendered, provider="anthropic"):
    return [
        {"role": "user", "content": "what changed for disability benefits?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "search_web",
             "input": {"query": query}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": rendered}]},
    ]


def test_a_replayed_cloud_search_and_its_rows_reach_the_cloud_seat(monkeypatch, tmp_path):
    """Red on main: the replayed search_web.query was topic-classified and
    withheld from the very seat that wrote it, and the result rows (health and
    legal words, published by a search engine) were withheld too. Here the
    seat's query passed check_action (provenance recorded) and the tool ran
    under the seat's mark, as in the agent loop."""
    from agent_friday.services import egress_gate as eg
    ok, detail, _ = _check(tmp_path, "search_web", {"query": MEDICAID_Q})
    assert ok, detail
    with _cloud(MEDICAID_Q):
        rendered = _rendered_search(monkeypatch, MEDICAID_Q)
    assert ROW_TITLE in rendered
    out = eg._gate_messages(_replay(MEDICAID_Q, rendered), "anthropic",
                            log_path=tmp_path / "egress.jsonl")
    assert out[1]["content"][0]["input"]["query"] == MEDICAID_Q
    result = out[2]["content"][0]["content"]
    for s in (ROW_TITLE, ROW_SNIPPET, ROW_URL, f"Search results for '{MEDICAID_Q}'"):
        assert s in result, f"published/own text was withheld: {s!r}"


def test_an_openai_shape_replay_keeps_the_cloud_query(tmp_path):
    from agent_friday.services import egress_gate as eg
    ok, detail, _ = _check(tmp_path, "search_web", {"query": CIVIL_Q}, provider="openai")
    assert ok, detail
    history = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "search_web",
                      "arguments": json.dumps({"query": CIVIL_Q})}}]}]
    out = eg._gate_messages(history, "openai", log_path=tmp_path / "egress.jsonl")
    args = json.loads(out[0]["tool_calls"][0]["function"]["arguments"])
    assert args["query"] == CIVIL_Q


def _replayed_query(history, provider, tmp_path):
    from agent_friday.services import egress_gate as eg
    out = eg._gate_messages(history, provider, log_path=tmp_path / "egress.jsonl")
    msg = out[0]
    if msg.get("tool_calls"):
        return json.loads(msg["tool_calls"][0]["function"]["arguments"])["query"]
    return msg["content"][0]["input"]["query"]


def _search_call(shape, query):
    if shape == "anthropic":
        return [{"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "search_web",
             "input": {"query": query}}]}]
    return [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "search_web", "arguments": json.dumps({"query": query})}}]}]


@pytest.mark.parametrize("shape", ["anthropic", "openai"])
def test_a_local_authored_replayed_health_query_is_still_withheld(tmp_path, shape):
    """HIGH-2. A search_web call a LOCAL seat wrote (no cloud provenance) and
    that later replays to a cloud seat keeps the ordinary topic gate.

    Red on 73ad3bcfa: every replayed search_web query was floor-only by tool
    name, so the local seat's health query went to the cloud verbatim."""
    got = _replayed_query(_search_call(shape, MEDICAID_Q), "anthropic", tmp_path)
    assert got != MEDICAID_Q, "a local seat's health query replayed to the cloud"


def test_another_providers_or_a_refused_query_is_not_this_seats_text(tmp_path):
    """HIGH-2. Provenance is per provider and only for a call that passed."""
    ok, _, _ = _check(tmp_path, "search_web", {"query": MEDICAID_Q}, provider="openai")
    assert ok
    got = _replayed_query(_search_call("anthropic", MEDICAID_Q), "anthropic", tmp_path)
    assert got != MEDICAID_Q, "openai's query was replayed to anthropic as its own"

    # deep_research refused for its private context: the question is NOT
    # recorded, so its replay keeps the ordinary gate.
    ok, _, _ = _check(tmp_path, "deep_research",
                      {"question": CIVIL_Q,
                       "context": "my medical diagnosis and prescription history"})
    assert not ok
    from agent_friday.privacy import public_query as pq
    assert not pq.is_cloud_query("anthropic", CIVIL_Q)


def test_the_cloud_seats_own_registered_query_replays_under_the_floor(tmp_path):
    """HIGH-2, the other half: the seat's own passed query replays to it."""
    ok, _, _ = _check(tmp_path, "search_web", {"query": MEDICAID_Q})
    assert ok
    for shape in ("anthropic", "openai"):
        assert _replayed_query(_search_call(shape, MEDICAID_Q), "anthropic",
                               tmp_path) == MEDICAID_Q


def test_a_never_send_token_in_a_replayed_query_is_still_withheld(floor, tmp_path):
    from agent_friday.services import egress_gate as eg
    from agent_friday.privacy import public_query as pq
    floor["never"] = [NEVER_TOKEN]
    # Even a recorded string meets the floor again on replay.
    pq.record_cloud_query("anthropic", [f"{NEVER_TOKEN} court records"])
    history = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "search_web",
             "input": {"query": f"{NEVER_TOKEN} court records"}}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "search_web", "arguments": json.dumps(
                 {"query": f"{NEVER_TOKEN} court records"})}}]},
    ]
    for msg in history:
        # Either outcome keeps the floor: the value withheld in place (this
        # branch) or the whole call stopped (NeverSendBlocked, as on main).
        try:
            out = eg._gate_messages([msg], "anthropic",
                                    log_path=tmp_path / "egress.jsonl")
        except eg.NeverSendBlocked:
            continue
        assert NEVER_TOKEN not in json.dumps(out), "a never-send token was replayed"


def test_a_replayed_non_query_tool_keeps_the_topic_gate(tmp_path):
    from agent_friday.services import egress_gate as eg
    path = "C:/Users/someone/Documents/medical/diagnosis-and-prescription.txt"
    history = [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu_1", "name": "read_file",
         "input": {"path": path}}]}]
    out = eg._gate_messages(history, "anthropic", log_path=tmp_path / "egress.jsonl")
    assert out[0]["content"][0]["input"]["path"] != path


def test_search_rows_are_registered_as_the_exact_rendered_paragraph(monkeypatch):
    """Each rendered row is its own paragraph and that exact paragraph is the
    registered published string; its separate lines are not."""
    from agent_friday.services import egress_gate as eg
    rendered = _rendered_search(monkeypatch, MEDICAID_Q)
    paras = rendered.split("\n\n")
    row = next(p for p in paras if ROW_TITLE in p)
    assert row.strip() == f"1. {ROW_TITLE}\n   {ROW_SNIPPET}\n   {ROW_URL}"
    assert eg.public_origin_of(row).startswith("web-search")
    for s in (ROW_TITLE, ROW_SNIPPET, ROW_URL):
        assert eg.public_origin_of(s) == "", f"a lone row line was registered: {s!r}"


def test_the_header_is_echoed_only_for_a_cloud_authored_query(monkeypatch):
    from agent_friday.services import egress_gate as eg
    rendered = _rendered_search(monkeypatch, MEDICAID_Q)      # no cloud mark
    header = rendered.split("\n\n")[0].strip()
    assert MEDICAID_Q in header
    assert "anthropic" not in eg._PROVIDER_ECHO.get(header, ())
    with _cloud(MEDICAID_Q):
        rendered = _rendered_search(monkeypatch, MEDICAID_Q)
    assert "anthropic" in eg._PROVIDER_ECHO.get(rendered.split("\n\n")[0].strip(), ())
    with _cloud("something else entirely"):                   # not the exact query
        rendered = _rendered_search(monkeypatch, CIVIL_Q)
    assert "anthropic" not in eg._PROVIDER_ECHO.get(rendered.split("\n\n")[0].strip(), ())


@pytest.mark.parametrize("private", [
    f"{ROW_TITLE}\n{ROW2_TITLE}",
    f"1. {ROW_TITLE}\n2. {ROW2_TITLE}",
    f"Things I am dealing with:\n\n1. {ROW_TITLE}\n2. {ROW2_TITLE}",
])
def test_a_private_list_of_registered_titles_is_still_gated(monkeypatch, tmp_path, private):
    """MEDIUM. A multi-line list whose every line matches a published title is
    not itself published: nothing exempts a combination of independently
    registered lines.

    Red on 73ad3bcfa: raw titles were registered and _all_lines_published
    passed a paragraph made only of registered lines (list markers allowed)."""
    from agent_friday.services import egress_gate as eg
    _rendered_search(monkeypatch, MEDICAID_Q, rows=[
        {"title": ROW_TITLE, "url": ROW_URL, "description": ROW_SNIPPET},
        {"title": ROW2_TITLE, "url": ROW2_URL, "description": ROW2_SNIPPET}])
    out = eg._gate_text(private, "anthropic", "messages[0].content",
                        log_path=tmp_path / "egress.jsonl")
    assert ROW_TITLE not in out or ROW2_TITLE not in out, \
        "a private list made of registered titles reached the cloud"


def test_a_floor_failure_refuses(tmp_path, monkeypatch):
    from agent_friday.privacy import public_query

    def boom(_text):
        raise RuntimeError("floor down")

    monkeypatch.setattr(public_query, "_hits", boom)
    allowed, detail, _ = _check(tmp_path, "search_web", {"query": MEDICAID_Q})
    assert not allowed
    assert detail == "public_query_refused:floor_unavailable", detail


# ── other tools keep today's behaviour ──────────────────────────────────────

def test_a_non_query_tool_keeps_the_topic_deny(tmp_path):
    allowed, detail, _ = _check(
        tmp_path, "read_file",
        {"path": "C:/Users/someone/Documents/medical/diagnosis-and-prescription.txt"})
    assert not allowed
    assert detail.startswith("cloud_denied_tier_"), detail


def test_a_local_provider_is_unaffected(tmp_path, floor):
    floor["never"] = [NEVER_TOKEN]
    allowed, _, _ = _check(tmp_path, "search_web", {"query": NEVER_TOKEN},
                           provider="ollama")
    assert allowed
    from agent_friday.privacy import public_query as pq
    assert not pq.is_cloud_query("ollama", NEVER_TOKEN), \
        "a local seat's query was recorded as cloud-authored"


# ── transparency ────────────────────────────────────────────────────────────

def test_a_refusal_is_logged_with_component_and_reason_not_value(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="friday.privacy"):
        allowed, _, _ = _check(tmp_path, "search_web", {"query": SSN_Q})
    assert not allowed
    recs = [r for r in caplog.records if r.name.startswith("friday.privacy")
            and r.levelno >= logging.WARNING]
    assert recs, "the refusal never reached the friday.privacy logger"
    text = " ".join(r.getMessage() for r in recs)
    assert "privacy.public_query" in text
    assert "id_or_account_number" in text
    assert "search_web" in text
    assert "123-45-6789" not in text  # pragma: allowlist secret


def test_a_topic_denial_is_logged_too(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="friday.privacy"):
        allowed, _, _ = _check(
            tmp_path, "read_file",
            {"path": "C:/Users/someone/Documents/medical/diagnosis.txt"})
    assert not allowed
    text = " ".join(r.getMessage() for r in caplog.records
                    if r.name.startswith("friday.privacy"))
    assert "privacy.vault_access" in text and "cloud_denied_tier_" in text
    assert "diagnosis.txt" not in text


def test_the_search_gate_refusal_is_logged_without_the_value(caplog):
    from agent_friday.services import web_search as ws
    q = f"is {FAKE_KEY} valid"
    with caplog.at_level(logging.WARNING, logger="friday.privacy"), _cloud(q):
        _, refusal = ws._gate_search_query(q, "brave")
    assert refusal
    assert FAKE_KEY not in refusal
    text = " ".join(r.getMessage() for r in caplog.records
                    if r.name.startswith("friday.privacy"))
    assert "privacy.public_query" in text and "credential" in text
    assert FAKE_KEY not in text


def test_the_model_is_told_the_component_reason_and_to_report_it():
    from agent_friday.privacy.vault_access import refusal_text
    msg = refusal_text("search_web", "public_query_refused:owner_identifier")
    assert msg.startswith("[VAULT ACCESS DENIED]")
    assert "privacy.public_query" in msg and "owner_identifier" in msg
    assert "tell the user" in msg
    msg = refusal_text("read_file", "cloud_denied_tier_TIER_3")
    assert "privacy.vault_access" in msg and "cloud_denied_tier_TIER_3" in msg
    assert "tell the user" in msg
