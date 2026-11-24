"""Friday's knowledge of her companion apps is true, tied to the apps' real facts, and reaches her.

The knowledge file (resources/ecosystem/ecosystem.json) may only say what the
facts snapshots beside it support, and what she reads is rendered from it.
"""
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from agent_friday.services import ecosystem as eco

REPO = Path(__file__).resolve().parents[2]


def _facts_script():
    spec = importlib.util.spec_from_file_location("ecosystem_facts", REPO / "scripts" / "ecosystem_facts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _doc():
    return json.loads(eco.DOC_PATH.read_text(encoding="utf-8"))


def _raw(app_id):
    return copy.deepcopy(next(a for a in _doc()["apps"] if a["id"] == app_id))


def _snapshot(app_id):
    return json.loads((eco.FACTS_DIR / f"{app_id}.json").read_text(encoding="utf-8"))


def _facts_with(app_id, change):
    """The app's facts after `change(facts_dict)`, re-fingerprinted as the extractor would."""
    snapshot = _snapshot(app_id)
    change(snapshot["facts"])
    snapshot["fingerprint"] = _facts_script().fingerprint(snapshot["facts"])
    return eco.parse_facts(snapshot)


def _everything_rendered():
    texts = [eco.prompt_block(), eco.info()]
    for app_id in eco.APP_IDS:
        texts += [eco.info(app_id, topic) for topic in eco.TOPICS]
    return texts


# ── The file is tied to the facts ───────────────────────────────────────────

def test_the_file_describes_exactly_the_four_device_kinds_the_gateway_knows():
    from agent_friday.tv_gateway import household
    assert {a.id for a in eco.apps()} == set(household.DEVICE_KINDS) == set(eco.APP_IDS)


@pytest.mark.parametrize("app_id", eco.APP_IDS)
def test_each_entry_carries_the_fingerprint_and_version_of_its_snapshot(app_id):
    snapshot, raw, entry = _snapshot(app_id), _raw(app_id), eco.app(app_id)
    assert raw["facts_fingerprint"] == snapshot["fingerprint"] == entry.facts.fingerprint
    assert (entry.version_name, entry.version_code) == (
        snapshot["facts"]["version_name"], snapshot["facts"]["version_code"])
    # The snapshot's own fingerprint is the hash of its facts, so neither half
    # can be edited by hand without the other.
    assert snapshot["fingerprint"] == _facts_script().fingerprint(snapshot["facts"])


@pytest.mark.parametrize("what,change", [
    ("a new version", lambda f: f.update(version_code=61, version_name="0.13.0")),
    ("a new permission", lambda f: f["permissions"].append("android.permission.READ_SMS")),
    ("a dropped permission", lambda f: f["permissions"].remove("android.permission.CAMERA")),
    ("a new action verb", lambda f: f["capabilities"]["actions"]["verbs"].update({"door.unlock": "A2"})),
    ("a verb's gate changed", lambda f: f["capabilities"]["actions"]["verbs"].update({"timer.start": "A2"})),
    ("a new bound service", lambda f: f["bound_services"].append("BIND_REMOTEVIEWS")),
    ("a release key", lambda f: f.update(release_signing="release_key")),
    ("a new minimum Android", lambda f: f.update(min_sdk=33)),
    ("version reporting added", lambda f: f["pairing"].update(reports_app_version=True)),
    ("a new gateway surface", lambda f: f["routes"]["gateway"].append("payments")),
])
def test_an_app_change_without_a_reviewed_entry_does_not_load(what, change):
    with pytest.raises(eco.EcosystemError, match="facts changed"):
        eco.parse_app(_raw("phone"), _facts_with("phone", change))


def test_an_entry_whose_version_was_edited_alone_does_not_load():
    raw = _raw("tv")
    raw["version"] = {"name": "9.9", "code": 99}
    with pytest.raises(eco.EcosystemError, match="version"):
        eco.parse_app(raw, eco.read_facts("tv"))


def test_shipped_needs_evidence_that_is_in_the_snapshot():
    raw = _raw("watch")
    raw["capabilities"].append({"id": "x", "text": "pair with this PC directly", "status": "shipped",
                                "evidence": ["pairing:gateway"]})
    with pytest.raises(eco.EcosystemError, match="evidence not in the facts"):
        eco.parse_app(raw, eco.read_facts("watch"))
    raw = _raw("watch")
    raw["capabilities"].append({"id": "x", "text": "do anything at all", "status": "shipped", "evidence": []})
    with pytest.raises(eco.EcosystemError, match="no evidence"):
        eco.parse_app(raw, eco.read_facts("watch"))


@pytest.mark.parametrize("gone,named", [
    (lambda f: f["permissions"].remove("android.permission.USE_BIOMETRIC"), "USE_BIOMETRIC"),
    (lambda f: f["capabilities"]["actions"]["verbs"].pop("phone.call"), "verb:actions/phone.call"),
    (lambda f: f["capabilities"]["actions"]["verbs"].update({"calendar.add": "N"}), "verb:actions/calendar.add"),
    (lambda f: f["pairing"].update(gateway=False), "pairing:gateway"),
    (lambda f: f["routes"]["gateway"].remove("workspaces"), "route:gateway/workspaces"),
])
def test_a_claim_loses_its_footing_when_the_app_drops_what_it_rested_on(gone, named):
    """Even with the fingerprint refreshed, the entry cannot keep saying it."""
    facts = _facts_with("phone", gone)
    raw = _raw("phone")
    raw["facts_fingerprint"] = facts.fingerprint
    with pytest.raises(eco.EcosystemError, match=re.escape(named)):
        eco.parse_app(raw, facts)


def test_built_but_off_must_say_until_when():
    raw = _raw("tv")
    next(c for c in raw["capabilities"] if c["status"] == "built_off").pop("until")
    with pytest.raises(eco.EcosystemError, match="until"):
        eco.parse_app(raw, eco.read_facts("tv"))


@pytest.mark.parametrize("key,holds", [
    ("permission:android.permission.RECORD_AUDIO", True), ("permission:android.permission.READ_SMS", False),
    ("service:BIND_QUICK_SETTINGS_TILE", True), ("service:BIND_APPWIDGET", True),
    ("service:BIND_REMOTEVIEWS", False),
    ("route:gateway/workspaces", True), ("route:gateway/payments", False), ("route:phone_link/chats", False),
    ("capability:talk", True), ("capability:widget", False),
    ("verb:actions/timer.start", True), ("verb:actions/door.unlock", False), ("verb:talk/timer.start", False),
    ("verb:actions/forbidden", False),
    ("pairing:gateway", True), ("pairing:reports_app_version", False), ("pairing:declares_kind", False),
    ("signing:none", True), ("signing:release_key", False),
    ("made_up:thing", False), ("", False), (None, False)])
def test_evidence_keys_against_the_phone_snapshot(key, holds):
    assert eco.read_facts("phone").holds(key) is holds


@pytest.mark.parametrize("broken", [
    "", "not json", "[]", "{}", '{"reviewed": "2026-11-22"}', '{"reviewed": "soon", "apps": []}',
    '{"reviewed": "2026-11-22", "apps": [{"id": "phone"}]}',
    '{"reviewed": "2026-11-22", "apps": [1, 2, 3, 4]}',
    '{"reviewed": "2026-11-22", "apps": [{"id": ["phone"]}, {"id": {}}, {"id": null}, {"id": 4}]}'])
def test_a_broken_file_does_not_load_and_she_says_so_instead_of_guessing(tmp_path, monkeypatch, broken):
    bad = tmp_path / "ecosystem.json"
    bad.write_text(broken, encoding="utf-8")
    with pytest.raises(eco.EcosystemError):
        eco.load(bad)
    monkeypatch.setattr(eco, "apps", lambda: eco.load(bad))
    assert eco.tool_ecosystem_info({"app": "phone", "topic": "pair"}) == eco.UNREADABLE
    assert eco.prompt_section() == ""


@pytest.mark.parametrize("damage", [
    lambda a: a.update(capabilities="talk"), lambda a: a.update(install=[1]), lambda a: a.update(pair=[]),
    lambda a: a.update(limits="none"), lambda a: a["pair"].update(steps=[]),
    lambda a: a["pair"]["steps"][0].update(surprise=True), lambda a: a["capabilities"][0].update(evidence="x"),
    lambda a: a.update(platform=None), lambda a: a.update(status="amazing"), lambda a: a.update(noun=7),
    lambda a: a.update(id=["phone"])])
def test_a_malformed_entry_is_refused_with_a_reason_not_a_crash(damage):
    raw = _raw("phone")
    damage(raw)
    with pytest.raises(eco.EcosystemError):
        eco.parse_app(raw, eco.read_facts("phone"))


@pytest.mark.parametrize("snapshot", [None, {}, {"fingerprint": "x"}, {"fingerprint": "x", "facts": {}},
                                      {"fingerprint": "x", "facts": {"capabilities": []}}])
def test_a_malformed_snapshot_is_refused_with_a_reason(snapshot):
    with pytest.raises(eco.EcosystemError):
        eco.parse_facts(snapshot)


# ── Only what is shipped is stated as working ───────────────────────────────

@pytest.mark.parametrize("status,starts,never", [
    ("shipped", "The phone app can ", "isn't built"),
    ("built_off", "Thing is built but switched off until ", "app can"),
    ("planned", "Thing isn't built yet", "app can")])
def test_sentence_shape_by_status(status, starts, never):
    text = "do the thing" if status == "shipped" else "Thing"
    cap = eco.Capability("x", text, status, "you allow it" if status == "built_off" else None, ())
    sentence = eco.capability_sentence("phone", cap)
    assert sentence.startswith(starts) and never not in sentence and sentence.endswith(".")


def test_nothing_planned_or_switched_off_is_stated_in_the_present_tense():
    seen = set()
    for a in eco.apps():
        overview = eco.info(a.id, "overview")
        for cap in a.capabilities:
            sentence = eco.capability_sentence(a.noun, cap)
            assert sentence in overview
            seen.add(cap.status)
            if cap.status != "shipped":
                assert f"The {a.noun} app can {cap.text}" not in overview
                assert ("isn't built yet" in sentence) or ("switched off until" in sentence)
    assert seen == set(eco.ITEM_STATUSES)


def test_the_watch_is_introduced_as_a_developer_preview_on_every_topic():
    assert eco.app("watch").status == "developer_preview"
    for topic in eco.TOPICS:
        assert eco.info("watch", topic).startswith("The watch app is a developer preview")
    assert "developer preview" in eco.prompt_block()


# ── What a device can see ───────────────────────────────────────────────────

@pytest.mark.parametrize("app_id", eco.APP_IDS)
def test_every_permission_in_a_snapshot_has_a_note(app_id):
    facts = eco.read_facts(app_id)
    assert set(facts.permissions) <= set(eco.PERMISSION_NOTES), \
        sorted(set(facts.permissions) - set(eco.PERMISSION_NOTES))
    assert set(facts.bound_services) <= set(eco.SERVICE_NOTES), \
        sorted(set(facts.bound_services) - set(eco.SERVICE_NOTES))


def test_every_right_has_a_note():
    from agent_friday.services import device_pairing
    assert set(eco.RIGHT_NOTES) == set(device_pairing.ALL_RIGHTS)


@pytest.mark.parametrize("app_id", ["phone", "tablet", "tv"])
def test_what_the_pairing_press_grants_is_said_as_the_pairing_code_grants_it(app_id):
    """The first sentence lists exactly the rights the press gives; the second, exactly the ones
    that each need their own press. Neither list borrows from the other."""
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import household, pairing
    owner = pairing.owner_grant(app_id, household.member_for_kind(app_id))
    at_once = set(device_pairing.RIGHTS) if owner else {device_pairing.BASE_RIGHT}
    later = set(device_pairing.ALL_RIGHTS) - at_once if owner else set(device_pairing.RIGHTS) - at_once
    lines = eco.sees(eco.app(app_id))
    first, turn, second = lines[0], lines[1], lines[2]
    assert first.startswith("Once you approve the pairing it is signed in as you") is owner
    assert turn == (eco.OWNER_TURN if owner else eco.HOUSEHOLD_TURN)
    assert "anything that would need your approval at this PC still does" in eco.OWNER_TURN
    assert "off until you allow it" in second
    for right, note in eco.RIGHT_NOTES.items():
        assert (note in first) is (right in at_once), right
        assert (note in second) is (right in later), right
    assert owner is (app_id in ("phone", "tablet"))


def test_a_paired_tv_is_said_to_talk_with_this_pcs_models_because_the_gateway_answers_it(monkeypatch):
    """tv_gateway/turn.run answers a household device's turn on the base right, with no owner context."""
    from agent_friday.services import device_pairing, global_stop
    from agent_friday.tv_gateway import owner_turn, turn
    assert device_pairing.BASE_RIGHT == "chat_read"
    tv_device = {"id": "d", "kind": "tv", "rights": ["chat_read"]}
    assert turn.tools_for(tv_device, "family", [{"name": "tv_volume"}]) == []
    # A turn from a TV holding only the base right is routed to a model and answered, with no
    # tool offered. If run() ever asks for a further right first, the sentence below is false.
    answered = []
    monkeypatch.setattr(owner_turn, "is_owner_device", lambda dev: False)
    monkeypatch.setattr(global_stop, "raise_if_stopped", lambda what: None)
    monkeypatch.setattr(turn, "_route", lambda principal, plain, has_tools: {"provider": "local", "model": "m"})
    monkeypatch.setattr(turn, "_row", lambda *a, **k: None)
    monkeypatch.setattr(turn, "_local_round",
                        lambda provider, model, name, system, messages, tools, max_tokens:
                        answered.append(tools) or iter(["answer"]))
    body = {"messages": [{"role": "user", "content": "hello"}], "tools": [{"name": "tv_volume"}]}
    assert list(turn.run(tv_device, body, "family")) == ["answer"]
    assert answered == [[]]
    tv = "\n".join(eco.sees(eco.app("tv")))
    assert "talk with me, answered by this PC's models" in tv
    assert "none of your memory or files" in tv and "no tool of this PC is run for it" in tv
    assert "nothing else" not in tv and "read the chat" not in tv
    cap = next(c for c in eco.app("tv").capabilities if c.id == "pc_answers")
    assert cap.status == "shipped"
    assert "built but switched off" not in eco.capability_sentence("TV", cap)


def test_what_a_device_sees_follows_its_permissions():
    phone, tv, watch = (" ".join(eco.sees(eco.app(i))) for i in ("phone", "tv", "watch"))
    assert "the microphone" in phone and "the camera" in phone and "contacts" in phone
    assert "household device, never signed in as you" in tv
    assert "camera" not in tv and "contacts" not in tv
    assert "I grant it nothing" in watch and not any(n in watch for n in eco.RIGHT_NOTES.values())


def test_approving_from_a_phone_is_said_to_need_its_own_permission():
    for app_id in ("phone", "tablet"):
        cap = next(c for c in eco.app(app_id).capabilities if c.id == "approve")
        assert "and, separately, to approve, each on this PC's screen" in cap.text


def test_approving_from_a_device_is_said_to_cover_most_requests_not_all():
    """With both presses given, the gateway still refuses a device's approval for the screen-only card
    kinds and for any card that gives a device a right (tv_gateway/approval_verify.device_may_not_decide)."""
    from agent_friday.services import approvals
    from agent_friday.tv_gateway import approval_verify, pairing
    assert approvals.SCREEN_ONLY_KINDS
    for kind in list(approvals.SCREEN_ONLY_KINDS) + [pairing.RIGHT_KIND, pairing.KIND]:
        assert approval_verify.device_may_not_decide({"kind": kind, "approval_id": "a-1"}, "aaaa000000000001")
    only_here = "only ever approved on this PC's own screen"
    for app_id in ("phone", "tablet"):
        a = eco.app(app_id)
        cap = next(c for c in a.capabilities if c.id == "approve")
        assert cap.text.startswith("approve most of what I ask") and only_here in cap.text
        for line in a.adds:
            if "approve" in line:
                assert "most of what I ask" in line and only_here in line
    assert "most of my requests" in eco.RIGHT_NOTES["pc_approve"] and only_here in eco.RIGHT_NOTES["pc_approve"]
    for text in _everything_rendered():
        assert "can approve what I ask" not in text and "approve my requests, one at a time" not in text


# ── Nothing false, secret or personal is rendered ───────────────────────────

FORBIDDEN = (
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "a network address"),
    (re.compile(r"[A-Za-z]:\\Users|/home/|/Users/"), "a home path"),
    (re.compile(r"\.ts\.net|tailnet-", re.I), "a tailnet name"),
    (re.compile(r"scan (?:the|a) (?:qr|code)", re.I), "an instruction to scan a code"),
    (re.compile(r"(?:download|get|install) (?:it|the app) (?:from|in|on) (?:the )?(?:Google )?Play", re.I),
     "a store that does not carry it"),
    (re.compile(r"(?:tell|give|say|read) (?:me|friday) (?:the|your) (?:pin|six digits|code)", re.I),
     "asking for a PIN or code"),
    (re.compile(r"(?:turn|switch) (?:off|on) kids mode|(?:leave|exit|enter) kids mode", re.I),
     "an instruction to change kids mode"),
)


def test_nothing_rendered_or_stored_carries_an_address_a_false_claim_or_a_request_for_a_code():
    stored = [eco.DOC_PATH.read_text(encoding="utf-8")]
    stored += [p.read_text(encoding="utf-8") for p in sorted(eco.FACTS_DIR.glob("*.json"))]
    for text in _everything_rendered() + stored:
        for pattern, what in FORBIDDEN:
            hit = pattern.search(text)
            assert not hit, f"{what}: {hit.group(0)!r}"


@pytest.mark.parametrize("bad", [
    "Connect to 192.168.1.20 first.", "Scan the QR on the PC.", "Get it from the Play Store.",
    "Tell me the six digits.", "Switch off kids mode first.", r"Copy it to C:\Users\someone."])
def test_the_forbidden_patterns_catch_what_they_exist_for(bad):
    assert any(pattern.search(bad) for pattern, _ in FORBIDDEN)


def test_every_store_mention_says_it_is_not_there():
    mentions = 0
    for text in _everything_rendered():
        for line in text.splitlines():
            if "app store" in line.lower() or "Play Store" in line:
                mentions += 1
                assert re.search(r"\bnot in\b|isn't built yet|None (?:of them )?is in", line), line
    assert mentions > 8


GATEWAY_APPS = ("phone", "tablet", "tv")


def _package_sources(*folders):
    for folder in folders:
        for file in sorted(folder.rglob("*")):
            if file.suffix in (".py", ".html", ".js") and file.is_file():
                yield file, file.read_text(encoding="utf-8", errors="replace")


def _served_by_the_gateway() -> str:
    from agent_friday import tv_gateway
    return "\n".join(text for _, text in _package_sources(Path(tv_gateway.__file__).parent))


def test_pairing_says_the_pc_side_is_off_with_no_switch_before_any_other_step():
    """True of this build: nothing in the product switches the device gateway on. A change that adds a
    caller of set_gateway makes the first pairing step false, so it fails here until that step is rewritten
    in resources/ecosystem/ecosystem.json."""
    package = Path(eco.__file__).resolve().parents[1]
    callers = [file.name for file, text in _package_sources(package, REPO / "static")
               if "set_gateway(" in text.replace("def set_gateway(", "")]
    assert callers == [], f"the gateway has a switch now; rewrite the first pairing step: {callers}"
    for app_id in GATEWAY_APPS:
        first = eco.app(app_id).pair.steps[0].text
        assert "device gateway on this PC" in first and "no switch for it in the app yet" in first


@pytest.mark.parametrize("app_id", GATEWAY_APPS)
def test_pairing_is_opened_by_the_owner_at_this_pc_before_the_device_asks(app_id):
    """tv_gateway/pairing refuses pair/start outside a window the owner opened (device_pairing.pairing_window)."""
    from agent_friday.routes import control
    from agent_friday.services import add_device_command
    texts = [s.text for s in eco.app(app_id).pair.steps]
    opened = next(i for i, t in enumerate(texts) if "Open pairing on this PC" in t)
    asked = next(i for i, t in enumerate(texts) if "six digits" in t)
    assert 0 < opened < asked
    step = texts[opened]
    said = re.search(r'type "([^"]+)"', step).group(1)
    assert add_device_command.is_add_device(said)
    assert add_device_command.WINDOW_S == 300 and "five minutes" in step
    assert "/pairing" in step and "'/pairing'" in Path(control.__file__).read_text(encoding="utf-8")
    assert "only be opened from this PC itself" in step
    assert not re.search(r"\bsay\b", step), "the spoken command is not built; the steps name typing only"


@pytest.mark.parametrize("app_id", eco.APP_IDS)
def test_a_gateway_surface_a_shipped_claim_rests_on_is_one_this_pc_serves(app_id):
    """The app asking for a surface is half of it; the claim also needs this PC to answer there."""
    served = _served_by_the_gateway()
    raw = _raw(app_id)
    for claim in raw["capabilities"] + raw["install"] + [raw["pair"]]:
        for key in claim.get("evidence", []):
            if claim.get("status") == "shipped" and key.startswith("route:gateway/"):
                name = key.split("/", 1)[1]
                assert f'"/tv/v1/{name}' in served, f"{app_id}: {key} is not served by tv_gateway"


def test_the_served_check_tells_a_surface_this_pc_has_from_one_it_lacks():
    served = _served_by_the_gateway()
    assert '"/tv/v1/workspaces' in served and '"/tv/v1/chats' in served
    assert '"/tv/v1/payments' not in served


@pytest.mark.parametrize("app_id", ("phone", "tablet"))
def test_the_phone_and_tablet_are_said_to_open_chats_workspaces_and_a_widget(app_id):
    caps = {c.id: c for c in eco.app(app_id).capabilities}
    assert {caps[i].status for i in ("chats", "workspaces", "widget")} == {"shipped"}
    overview = eco.info(app_id)
    assert "open the chats you have had with me on this PC" in overview
    assert "open workspaces from this PC" in overview and "home-screen widget" in overview
    # A live call in the PC's voice is asked for by no installed build, so it is not said as working.
    assert not eco.app(app_id).facts.holds("route:gateway/voice")
    assert caps["pc_voice"].status == "planned"


def _read_guarded_surfaces() -> set:
    """The first path segment of every gateway route whose handler asks pc_read._guard, read from the
    gateway's own sources: the surfaces a device is refused on until the owner's separate read press."""
    from agent_friday import tv_gateway
    guarded = set()
    for file, text in _package_sources(Path(tv_gateway.__file__).parent):
        call = "_guard(" if file.name == "pc_read.py" else "pc_read._guard("
        marks = list(re.finditer(r'@app\.(?:get|post|put|delete|route)\("/tv/v1/([a-z][a-z0-9_-]*)', text))
        pending = []
        for i, mark in enumerate(marks):
            chunk = text[mark.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            pending.append(mark.group(1))
            if "def " in chunk:
                if call in chunk:
                    guarded.update(pending)
                pending = []
    return guarded


#: How a claim says it waits for the read press.
READ_SAID = re.compile(r"allow(?: it|ed) to read this PC")
#: Read-guarded surfaces an app asks for that no sentence in the file talks about. A new one fails
#: test_every_read_guarded_surface_an_app_uses_is_claimed_with_the_press_or_listed until it is decided.
UNCLAIMED_READS = {"jobs", "media", "orbs", "evolution", "connectors", "providers", "graph", "events"}
NEEDS_ROUTE = {"chats": "route:gateway/chats", "workspaces": "route:gateway/workspaces",
               "approve": "route:gateway/approvals", "pc_update": "route:gateway/phone"}


def _shipped_claims(raw):
    """(what, text, evidence) for every shipped capability and install channel of one raw entry."""
    for cap in raw["capabilities"]:
        if cap["status"] == "shipped":
            yield f"capability {cap['id']}", cap["text"], cap["evidence"]
    for channel in raw["install"]:
        if channel["status"] == "shipped":
            yield f"install {channel['id']}", f"{channel['title']} {channel['for']}", channel["evidence"]


def test_the_read_guarded_surfaces_are_found_from_the_gateways_own_code():
    guarded = _read_guarded_surfaces()
    assert {"chats", "workspaces", "approvals", "phone", "jobs"} <= guarded
    assert not guarded & {"messages", "pair", "hello", "inherit", "session"}


@pytest.mark.parametrize("app_id", eco.APP_IDS)
def test_every_claim_behind_the_read_press_says_so(app_id):
    """tv_gateway refuses a read-guarded surface with needs_right until the owner allows the device to
    read this PC, a press of its own that pairing does not include (device_pairing.PC_RIGHTS). A shipped
    sentence resting on such a surface and silent about the press promises a refusal."""
    from agent_friday.services import device_pairing
    assert "pc_read" in device_pairing.PC_RIGHTS and "pc_read" not in device_pairing.RIGHTS
    guarded = _read_guarded_surfaces()
    for what, text, evidence in _shipped_claims(_raw(app_id)):
        behind = [k for k in evidence if k.startswith("route:gateway/") and k.split("/", 1)[1] in guarded]
        if behind:
            assert READ_SAID.search(text), f"{app_id} {what} rests on {behind} and does not say it needs the read press"


@pytest.mark.parametrize("app_id", ("phone", "tablet"))
def test_claims_that_use_a_gateway_surface_cite_it(app_id):
    """The check above only sees a surface a claim cites, so the claims known to use one must cite it."""
    raw = _raw(app_id)
    caps = {c["id"]: c for c in raw["capabilities"]}
    for cap_id, key in NEEDS_ROUTE.items():
        assert key in caps[cap_id]["evidence"], (cap_id, key)
    channel = next(c for c in raw["install"] if c["id"] == "pc_update")
    assert NEEDS_ROUTE["pc_update"] in channel["evidence"]


@pytest.mark.parametrize("app_id", ("phone", "tablet", "tv"))
def test_every_read_guarded_surface_an_app_uses_is_claimed_with_the_press_or_listed(app_id):
    guarded = _read_guarded_surfaces()
    used = {r.split("/", 1)[1] for r in eco.app(app_id).facts.routes if r.startswith("gateway/")} & guarded
    cited = {k.split("/", 1)[1] for _w, _t, evidence in _shipped_claims(_raw(app_id))
             for k in evidence if k.startswith("route:gateway/")}
    assert used <= cited | UNCLAIMED_READS, sorted(used - cited - UNCLAIMED_READS)
    assert not cited & UNCLAIMED_READS, "a surface with a claim is no longer unclaimed"


def test_the_lines_about_what_a_device_adds_carry_the_same_condition():
    for app_id in ("phone", "tablet"):
        for line in eco.app(app_id).adds:
            if re.search(r"chats|workspaces|approve", line):
                assert READ_SAID.search(line), line
    assert "read your chats, workspaces, jobs, media and approval cards on this PC" in eco.RIGHT_NOTES["pc_read"]


def test_the_watch_chat_list_is_said_to_wait_on_the_phones_read_press():
    """The phone serves the watch's chat list from /tv/v1/chats, which is read-guarded (friday-phone
    watch/WatchInboxProvider -> chats/ChatSession), so the watch sees no chats until the phone may read."""
    watch = _raw("watch")
    for what, text, evidence in _shipped_claims(watch):
        if "route:phone_link/chats" in evidence:
            assert READ_SAID.search(text), what
    assert any("route:phone_link/chats" in e for _w, _t, e in _shipped_claims(watch))
    for line in eco.app("watch").adds:
        if "chats" in line:
            assert READ_SAID.search(line), line
    assert "no more than your phone can" in eco.PHONE_LINK_TURN


def test_the_watch_is_said_to_reach_me_through_the_phone_and_its_older_helper_is_named():
    watch = eco.app("watch")
    assert watch.pair.method == "phone_link"
    assert not watch.facts.holds("pairing:gateway")
    assert not [r for r in watch.facts.routes if r.startswith("gateway/")]
    assert watch.facts.holds("route:phone_link/item") and watch.facts.holds("route:phone_link/chats")
    assert "pair the phone with this PC first" in watch.pair.steps[0].text
    assert eco.sees(watch)[0] == eco.PHONE_LINK_TURN
    everything = "\n".join(eco.info("watch", topic) for topic in eco.TOPICS)
    assert "It cannot approve anything through the phone." in everything
    assert "through your phone" in everything
    # The app still carries its own pairing with a helper program on the PC; while it does, that is said,
    # and nothing claims the watch has no path to this PC at all.
    assert watch.facts.holds("pairing:relay")
    assert eco.WATCH_HELPER in watch.limits
    assert "never signs in to this PC" not in everything and "does not pair with this PC" not in everything
    assert "never signs in to me" in everything


def test_the_watch_buzz_is_not_said_as_working_while_only_the_helper_feeds_it():
    """The buzz for something finishing or needing him is driven by the helper's events; the phone link
    carries no events (it buzzes only for a reply it brings back)."""
    watch = eco.app("watch")
    assert "events" not in {r.split("/", 1)[1] for r in watch.facts.routes if r.startswith("phone_link/")}
    buzz = next(c for c in watch.capabilities if c.id == "buzz")
    assert buzz.status == "built_off" and "helper" in buzz.until
    assert "can buzz" not in eco.info("watch")


@pytest.mark.parametrize("app_id,noun", [("phone", "phone"), ("tablet", "tablet"), ("tv", "TV")])
def test_the_keep_paired_rule_is_stated_as_the_gateway_applies_it(app_id, noun):
    """device_pairing.reconfirm_state: asked from RECONFIRM_WARN_DAYS before the date, when the device
    connects; it keeps working until the date and is refused after it until the owner approves."""
    from agent_friday.services import device_pairing
    days = device_pairing.reconfirm_days(app_id)
    assert (device_pairing.RECONFIRM_DAYS, device_pairing.RECONFIRM_DAYS_TV) == (30, 90)
    assert device_pairing.RECONFIRM_WARN_DAYS == 7
    import time
    day, now = 86400, time.time()
    state = lambda left: device_pairing.reconfirm_state({"kind": app_id, "reconfirm_by": now + left})  # noqa: E731
    assert (state(8 * day), state(6 * day), state(-60)) == ("ok", "due", "overdue")
    limits = eco.info(app_id, "limits")
    assert (f"A paired {noun} is kept for {days} days at a time. In the last week, when it next connects, "
            f"I ask you on this PC whether to keep it. It goes on working until the {days} days are up; "
            "after that it is refused until you approve.") in limits
    assert "Until you answer, it is refused" not in limits
    assert "is kept for" not in eco.info("watch", "limits")


def test_the_tv_entry_states_the_kids_mode_rule_and_pairs_behind_the_pin():
    limits = eco.info("tv", "limits")
    assert "Kids mode on the TV starts and ends only on the TV itself" in limits
    assert "I cannot change it from here, and neither can a voice" in limits
    assert "grown-up PIN with the remote" in eco.info("tv", "pair")


def test_the_advanced_route_is_named_as_advanced_and_ends_by_switching_it_off():
    for a in eco.apps():
        text = eco.info(a.id, "install")
        advanced = [i for i in a.install if i.advanced and i.status == "shipped"]
        assert advanced, a.id
        assert text.count(eco.ADVANCED_NOTICE) == len(advanced)
        assert text.count(eco.ADVANCED_CLOSE) == len(advanced)
        assert text.index(eco.ADVANCED_NOTICE) < text.index("Command: adb")
        assert text.rstrip().endswith(eco.ADVANCED_CLOSE)
    assert "read data from it" in eco.ADVANCED_NOTICE and "developer options" in eco.ADVANCED_NOTICE


def test_install_and_pair_steps_are_given_as_written():
    for a in eco.apps():
        install, pair = eco.info(a.id, "install"), eco.info(a.id, "pair")
        for channel in a.install:
            for step in channel.steps:
                assert step.text in install
                assert step.command is None or f"Command: {step.command}" in install
        for step in a.pair.steps:
            assert step.text in pair
    commands = [s.command for a in eco.apps() for i in a.install for s in i.steps if s.command]
    assert commands and all(c.startswith("adb ") and "<" in c for c in commands)


# ── It reaches her, and the tool only reads ─────────────────────────────────

def test_the_companion_apps_reach_the_chat_prompt():
    from agent_friday.services import model_router
    prompt = model_router._get_friday_system_prompt(provider="local", vault_control=None)
    block = eco.prompt_block()
    assert f"== {eco.SECTION_TITLE} ==\n{block}" in prompt
    assert len(block) <= eco.PROMPT_BLOCK_MAX_CHARS
    for word in ("phone", "tablet", "TV", "watch", "ecosystem_info", "there is no QR code", "through ask_friday"):
        assert word in block
    assert eco.prompt_block() == block


def test_the_voice_front_keeps_the_companion_apps_when_the_budget_is_tight():
    from agent_friday.services import voice_context_digest as digest
    section = f"== {eco.SECTION_TITLE} ==\n{eco.prompt_block()}"
    filler = "== PROFESSIONAL INDEX ==\n" + "Filler line.\n" * 4000
    context = "\n\n".join(["Preamble.", "== CORE IDENTITY ==\nShe is Friday.", filler, section])
    out = digest.front_digest(context, 600)
    assert section in out
    assert out.count("Filler line.") < 4000
    assert eco.SECTION_TITLE in digest.FRONT_PRIORITY_SECTIONS


def test_the_voice_front_is_pointed_at_a_tool_it_has():
    """The front has no ecosystem tool of its own; the block sends it through ask_friday."""
    from agent_friday.services import voice_engine
    names = {t[0] for t in voice_engine._VOICE_LIVE_TOOLS} | set(voice_engine._VOICE_SHARED_TOOLS)
    assert "ask_friday" in names
    assert "in voice, through ask_friday" in eco.prompt_block()


def test_the_tool_is_registered_read_only_and_grouped():
    from agent_friday.services import agent, tool_catalogue
    assert "ecosystem_info" in agent.CLAUDE_TOOL_HANDLERS
    assert agent.TOOL_RINGS["ecosystem_info"] == 0
    assert tool_catalogue.groups_of("ecosystem_info")
    spec = next(t for t in agent.CLAUDE_TOOLS if t["name"] == "ecosystem_info")
    assert set(spec["input_schema"]["properties"]["app"]["enum"]) == set(eco.APP_IDS)
    assert set(spec["input_schema"]["properties"]["topic"]["enum"]) == set(eco.TOPICS)
    assert agent.CLAUDE_TOOL_HANDLERS["ecosystem_info"]({"app": "phone", "topic": "pair"}) == eco.info("phone", "pair")


def test_an_app_is_found_whatever_the_case_and_an_unknown_one_is_named_as_unknown():
    assert eco.info("TV", "limits") == eco.info("tv", "limits") == eco.info(" Tv ", "limits")
    assert eco.info("fridge") == "I have no companion app called that. I know of: phone, tablet, tv, watch."
    assert eco.info("phone", "secrets") == eco.info("phone", "overview")


@pytest.mark.parametrize("inp", [
    None, "phone", {}, {"app": 7, "topic": []}, {"app": "fridge"}, {"app": "phone", "topic": "secrets"},
    {"app": "phone", "topic": "pair", "approve": True, "open_window": True, "grant": "tv_brain"}])
def test_the_tool_answers_any_input_in_words_and_changes_nothing(monkeypatch, inp):
    from agent_friday.services import device_pairing

    def refuse(*a, **k):
        pytest.fail("a read-only tool reached the device store")
    for name in ("set_gateway", "add_device", "set_right", "remove_device", "stop_all", "open_session", "_save"):
        monkeypatch.setattr(device_pairing, name, refuse)
    out = eco.tool_ecosystem_info(inp)
    assert isinstance(out, str) and out.strip() and out != eco.UNREADABLE
    assert "Traceback" not in out


def test_pairing_text_says_who_presses_and_that_the_digits_are_not_spoken():
    text = eco.info("phone", "pair")
    assert "I cannot press Approve for you" in text
    assert "Never read the six digits out to me" in text
    assert "nothing to type and nothing to scan" in text.lower()


def test_the_tool_has_a_decided_class_at_the_action_gate():
    """Reading shipped notes is internal: decided, not left to the outward default."""
    from agent_friday.governance import action_gate
    assert action_gate.known("ecosystem_info")
    assert "ecosystem_info" in action_gate.INTERNAL_TOOLS
