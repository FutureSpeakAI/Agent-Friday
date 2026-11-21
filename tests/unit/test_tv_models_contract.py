"""The model-slate contract the phone, the tablet and the TV clients share (Stephen, 2026-11-14).

He picked Opus on the tablet, sent a voice chat and got nothing: ``GET /tv/v1/models`` had been a 404 (no slate in
the selector, no OpenRouter, no local models) and the PC never saw a turn. The contract guarded here, for every
device kind:

* GET /tv/v1/models lists the PC's whole chat slate (curated seats AND OpenRouter's discovered models), each with
  a stable id, display name, local/cloud, provider, whether it is usable now and why not, warm or cold, a rough
  speed and voice suitability;
* an owner-bound phone/tablet/watch may select what is available; a TV or family device sees the same slate but
  selects nothing, and is told why;
* a pick is honoured or refused loudly, before anything runs, with a stable ``code`` and a reason in words: an
  unknown id is 400 ``unknown_model`` (with the nearest ids), an unavailable one is 409 ``model_unavailable``
  ("... needs an Anthropic key on your PC"); a family word ("opus") means one listed model; never a quiet other model;
* switching inside ONE conversation (Opus, then an OpenRouter model, then Bonsai) is served by the named model each
  time, and the receipts say so.
"""
import json

import pytest

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import app as gateway
from agent_friday.tv_gateway import household, models, owner_turn
from tests.unit.test_owner_turn_model_truth import Desk
from tests.unit.test_tv_gateway import FakeTv, auth, open_session

CATALOG = {
    "roles": {"orchestrator": [
        {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "provider": "anthropic", "provider_label": "Anthropic",
         "local": False, "available": True, "needs_key": "ANTHROPIC_API_KEY", "roles": ["orchestrator"], "supports_tools": True},
        {"id": "claude-opus-5", "label": "Claude Opus 5", "provider": "anthropic", "provider_label": "Anthropic",
         "local": False, "available": True, "needs_key": "ANTHROPIC_API_KEY", "roles": ["orchestrator"]},
        {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "provider": "anthropic", "provider_label": "Anthropic",
         "local": False, "available": True, "needs_key": "ANTHROPIC_API_KEY", "roles": ["orchestrator"]},
        {"id": "gpt-4o", "label": "GPT-4o", "provider": "openai", "provider_label": "OpenAI",
         "local": False, "available": False, "needs_key": "OPENAI_API_KEY", "roles": ["orchestrator"]},
        {"id": "bonsai2:27b", "label": "Ternary Bonsai 2 27B", "provider": "bonsai2-local", "provider_label": "Bonsai 2",
         "local": True, "available": True, "needs_key": None, "roles": ["orchestrator"], "resident": True},
    ]},
    "models": [
        {"id": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B Instruct", "provider": "openrouter",
         "provider_label": "OpenRouter", "local": False, "available": True, "needs_key": "OPENROUTER_API_KEY",
         "roles": [], "modalities": ["text"]},
        {"id": "google/gemini-flash-1.5", "label": "Gemini Flash 1.5", "provider": "openrouter",
         "provider_label": "OpenRouter", "local": False, "available": True, "needs_key": "OPENROUTER_API_KEY",
         "roles": [], "modalities": ["text"]},
        {"id": "black-forest/flux", "label": "Flux", "provider": "openrouter", "provider_label": "OpenRouter",
         "local": False, "available": True, "roles": ["creative"], "modalities": ["image"]},
    ],
    "providers": [{"name": "anthropic", "label": "Anthropic", "available": True},
                  {"name": "openai", "label": "OpenAI", "available": False},
                  {"name": "openrouter", "label": "OpenRouter", "available": True},
                  {"name": "bonsai2-local", "label": "Bonsai 2", "available": True}],
}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(household, "_path", lambda: tmp_path / "household.json")
    monkeypatch.setattr(gateway, "_BUCKET", gateway._Bucket())
    monkeypatch.setattr(models, "raw_catalog", lambda: CATALOG)
    monkeypatch.setattr(models, "_selected", lambda settings=None: "bonsai2:27b")
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household.ensure_default_members()
    yield
    device_pairing._reset_for_tests()


@pytest.fixture
def client():
    return gateway.create_app().test_client()


def paired(kind, member):
    dev = FakeTv()
    device_pairing.add_device(dev.spki, name="A device", kind=kind)
    if member:
        household.bind_device(dev.id, member)
    return dev


def slate(client, dev, **q):
    r = client.get("/tv/v1/models", query_string=q, headers=auth(open_session(client, dev)))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


OWNER_KINDS = ["phone", "tablet", "watch"]


# ── the slate ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", OWNER_KINDS)
def test_an_owner_device_gets_the_whole_chat_slate_including_openrouter(client, kind):
    body = slate(client, paired(kind, "stephen"))
    ids = {m["id"] for m in body["models"]}
    assert {"claude-opus-5-5", "claude-haiku-5-5", "gpt-4o", "bonsai2:27b", "meta-llama/llama-3.3-70b-instruct",
            "google/gemini-flash-1.5"} <= ids
    assert "black-forest/flux" not in ids                          # an image model is not a chat model
    assert body["scope"] == "owner" and body["selected"] == "bonsai2:27b"
    assert {p["name"] for p in body["providers"]} >= {"anthropic", "openrouter", "openai"}


@pytest.mark.parametrize("kind", OWNER_KINDS)
def test_every_model_carries_the_contract_fields(client, kind):
    for m in slate(client, paired(kind, "stephen"))["models"]:
        assert {"id", "name", "provider", "provider_name", "kind", "available", "reason", "key_present", "warm",
                "speed", "speed_basis", "voice", "tools", "curated", "selected", "selectable"} <= set(m)
        assert m["kind"] in ("local", "cloud") and m["speed"] in ("fast", "medium", "slow")
        assert set(m["voice"]) == {"suitable", "why"}


def test_local_is_local_cloud_is_cloud_and_a_resident_local_model_is_warm(client):
    by = {m["id"]: m for m in slate(client, paired("tablet", "stephen"))["models"]}
    assert by["bonsai2:27b"]["kind"] == "local" and by["bonsai2:27b"]["warm"] is True
    assert by["claude-opus-5-5"]["kind"] == "cloud" and by["claude-opus-5-5"]["warm"] is None
    assert by["meta-llama/llama-3.3-70b-instruct"]["provider"] == "openrouter"


def test_an_unavailable_model_says_why_in_words(client):
    by = {m["id"]: m for m in slate(client, paired("tablet", "stephen"))["models"]}
    g = by["gpt-4o"]
    assert g["available"] is False and g["selectable"] is False and g["key_present"] is False
    assert g["reason"] == "GPT-4o needs an OpenAI key on your PC (add it in Settings > Keys)"


def test_speed_and_voice_suitability_are_a_labelled_guide(client):
    by = {m["id"]: m for m in slate(client, paired("tablet", "stephen"))["models"]}
    assert by["claude-haiku-5-5"]["speed"] == "fast" and by["claude-haiku-5-5"]["voice"]["suitable"] is True
    assert by["claude-opus-5-5"]["speed"] == "slow" and by["claude-opus-5-5"]["voice"]["suitable"] is False
    assert "guide" in by["claude-opus-5-5"]["speed_basis"]


def test_the_selected_model_is_first_and_the_list_can_be_searched(client):
    dev = paired("phone", "stephen")
    assert slate(client, dev)["models"][0]["id"] == "bonsai2:27b"
    only = slate(client, dev, provider="openrouter")
    assert only["count"] == 2 and {m["provider"] for m in only["models"]} == {"openrouter"}
    assert [m["id"] for m in slate(client, dev, q="gemini")["models"]] == ["google/gemini-flash-1.5"]


@pytest.mark.parametrize("kind,member", [("tv", "family"), ("phone", "family"), ("tablet", None)])
def test_a_tv_or_family_device_sees_the_slate_but_selects_nothing_and_is_told_why(client, kind, member):
    body = slate(client, paired(kind, member))
    assert body["scope"] == "household" and body["total"] >= 6
    assert not any(m["selectable"] for m in body["models"])
    assert "household" in next(m for m in body["models"] if m["available"])["reason"]


def test_no_session_is_a_401_that_says_open_a_session(client):
    r = client.get("/tv/v1/models")
    assert r.status_code == 401 and r.get_json()["code"] == "session_required"


def test_a_catalog_that_cannot_be_read_is_a_loud_503_not_an_empty_list(client, monkeypatch):
    def boom():
        raise RuntimeError("cache")
    monkeypatch.setattr(models, "raw_catalog", boom)
    r = client.get("/tv/v1/models", headers=auth(open_session(client, paired("tablet", "stephen"))))
    assert r.status_code == 503 and r.get_json()["code"] == "slate_unavailable"


# ── a pick is honoured or refused loudly ─────────────────────────────────────

@pytest.fixture
def desk(monkeypatch):
    d = Desk()
    monkeypatch.setattr(owner_turn, "_api", d.api, raising=False)
    monkeypatch.setattr(owner_turn, "_post", d.post)
    return d


def ask(client, dev, **extra):
    body = dict({"messages": [{"role": "user", "content": "Say only the word ok."}]}, **extra)
    return client.post("/tv/v1/messages", json=body, headers=auth(open_session(client, dev)))


def events(r):
    return [json.loads(line[6:]) for part in r.get_data(as_text=True).split("\n\n")
            for line in part.split("\n") if line.startswith("data: ")]


def block(r):
    return next(e for e in events(r) if e.get("type") == "message_delta")["friday"]


@pytest.mark.parametrize("kind", ["phone", "tablet"])
def test_an_unknown_model_is_400_with_the_nearest_ids_and_nothing_runs(client, desk, kind):
    r = ask(client, paired(kind, "stephen"), model="gemini-flash")
    b = r.get_json()
    assert r.status_code == 400 and b["code"] == "unknown_model" and b["ok"] is False
    assert "gemini-flash" in b["error"] and b["did_you_mean"] == ["google/gemini-flash-1.5"]
    assert desk.turns == [] and desk.api_calls == []


def test_an_unavailable_model_is_409_naming_the_reason_and_nothing_runs(client, desk):
    r = ask(client, paired("tablet", "stephen"), model="gpt-4o")
    b = r.get_json()
    assert r.status_code == 409 and b["code"] == "model_unavailable" and b["model"] == "gpt-4o"
    assert "needs an OpenAI key on your PC" in b["error"]
    assert desk.turns == []


def test_opus_with_no_key_says_opus_needs_a_key(client, desk, monkeypatch):
    cat = json.loads(json.dumps(CATALOG))
    for m in cat["roles"]["orchestrator"]:
        if m["provider"] == "anthropic":
            m["available"] = False
    monkeypatch.setattr(models, "raw_catalog", lambda: cat)
    r = ask(client, paired("tablet", "stephen"), model="opus")
    assert r.status_code == 409 and "needs an Anthropic key on your PC" in r.get_json()["error"]
    assert "Opus" in r.get_json()["error"] and desk.turns == []


def test_a_family_word_means_one_listed_model_and_the_reply_says_which(client, desk):
    desk.answer = {"model": "claude-opus-5-5", "seat": "cloud", "provider": "anthropic"}
    r = ask(client, paired("tablet", "stephen"), model="opus")
    assert r.status_code == 200
    b = block(r)
    assert b["requested"] == "claude-opus-5-5" and b["asked"] == "opus" and b["model"] == "claude-opus-5-5"
    assert b["substituted"] is False
    assert desk.api_calls[-1][:2] == ("PATCH", "/api/conversations/conv-new")      # created without a seat, then bound
    assert desk.api_calls[-1][2]["seat"] == {"model": "claude-opus-5-5"}


def test_a_display_name_is_accepted_for_its_id(client, desk):
    r = ask(client, paired("tablet", "stephen"), model="Claude Haiku 5.5")
    assert r.status_code == 200 and desk.api_calls[-1][2]["seat"] == {"model": "claude-haiku-5-5"}


def test_a_tv_that_names_a_model_is_still_answered_by_the_household_and_never_by_the_owners_seat(client, desk, monkeypatch):
    monkeypatch.setattr(owner_turn, "is_owner_device", lambda dev: False)
    from agent_friday.tv_gateway import turn
    monkeypatch.setattr(turn, "run", lambda dev, body, principal: iter([turn._event({"type": "message_stop"})]))
    r = ask(client, paired("tv", "family"), model="claude-opus-5-5")
    assert r.status_code == 200 and desk.api_calls == [] and desk.turns == []


# ── switching inside one conversation, each answer served by the named model ──

class Switch(Desk):
    """A desktop that answers with the model its conversation is bound to, and says so in served_by."""

    PROVIDER = {"claude-opus-5-5": ("anthropic", "cloud"), "meta-llama/llama-3.3-70b-instruct": ("openrouter", "cloud"),
                "bonsai2:27b": ("arbiter-local", "local")}

    def post(self, text, conversation_id):
        model = (self.convs[conversation_id].get("seat") or {}).get("model")
        prov, seat = self.PROVIDER[model]
        self.answer = {"model": model, "seat": seat, "provider": prov}
        return super().post(text, conversation_id)


def test_opus_then_an_openrouter_model_then_bonsai_in_one_conversation_each_served_by_the_named_model(client, monkeypatch):
    from tests.ledger_support import rows
    d = Switch()
    monkeypatch.setattr(owner_turn, "_api", d.api, raising=False)
    monkeypatch.setattr(owner_turn, "_post", d.post)
    dev = paired("tablet", "stephen")
    served = []
    for pick in ("opus", "meta-llama/llama-3.3-70b-instruct", "bonsai2:27b"):
        r = ask(client, dev, model=pick, conversation_id="conv-old")
        assert r.status_code == 200, r.get_data(as_text=True)
        b = block(r)
        served.append((b["model"], b["provider"], b["local"], b["substituted"], b["conversation_id"]))
    assert served == [("claude-opus-5-5", "anthropic", False, False, "conv-old"),
                      ("meta-llama/llama-3.3-70b-instruct", "openrouter", False, False, "conv-old"),
                      ("bonsai2:27b", "arbiter-local", True, False, "conv-old")]
    assert [t["conversation_id"] for t in d.turns] == ["conv-old"] * 3         # one conversation throughout
    receipts = [(r["model"], r["provider"], r["device_kind"]) for r in rows("device_turn_served")][-3:]
    assert receipts == [("claude-opus-5-5", "anthropic", "tablet"),
                        ("meta-llama/llama-3.3-70b-instruct", "openrouter", "tablet"),
                        ("bonsai2:27b", "arbiter-local", "tablet")]


def test_every_refusal_is_receipted_so_the_pc_can_show_what_was_asked_and_why_it_ran_nothing(client, desk):
    from tests.ledger_support import rows
    ask(client, paired("tablet", "stephen"), model="gpt-4o")
    last = rows("device_turn_refused")[-1]
    assert last["requested"] == "gpt-4o" and last["code"] == "model_unavailable" and "OpenAI key" in last["reason"]
