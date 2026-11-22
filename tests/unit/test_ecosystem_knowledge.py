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
    ("a renamed action verb", lambda f: f["capabilities"]["actions"]["verbs"].append("door.unlock")),
    ("a new bound service", lambda f: f["bound_services"].append("BIND_REMOTEVIEWS")),
    ("a release key", lambda f: f.update(release_signing="release_key")),
    ("a new minimum Android", lambda f: f.update(min_sdk=33)),
    ("version reporting added", lambda f: f["pairing"].update(reports_app_version=True)),
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
    (lambda f: f["capabilities"]["actions"]["verbs"].remove("phone.call"), "verb:actions/phone.call"),
    (lambda f: f["pairing"].update(gateway=False), "pairing:gateway"),
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
    ("service:BIND_QUICK_SETTINGS_TILE", True), ("service:BIND_APPWIDGET", False),
    ("capability:talk", True), ("capability:widget", False),
    ("verb:actions/timer.start", True), ("verb:actions/door.unlock", False), ("verb:talk/timer.start", False),
    ("pairing:gateway", True), ("pairing:reports_app_version", False), ("pairing:declares_kind", False),
    ("signing:none", True), ("signing:release_key", False),
    ("made_up:thing", False), ("", False), (None, False)])
def test_evidence_keys_against_the_phone_snapshot(key, holds):
    assert eco.read_facts("phone").holds(key) is holds


@pytest.mark.parametrize("broken", [
    "", "not json", "[]", "{}", '{"reviewed": "2026-11-22"}', '{"reviewed": "soon", "apps": []}',
    '{"reviewed": "2026-11-22", "apps": [{"id": "phone"}]}',
    '{"reviewed": "2026-11-22", "apps": [1, 2, 3, 4]}'])
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
    lambda a: a.update(platform=None), lambda a: a.update(status="amazing"), lambda a: a.update(noun=7)])
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


def test_what_a_device_sees_follows_its_pairing_role_and_permissions():
    phone, tv, watch = (" ".join(eco.sees(eco.app(i))) for i in ("phone", "tv", "watch"))
    assert "it is your phone" in phone and "grants all of it" in phone
    assert "the microphone" in phone and "the camera" in phone and "contacts" in phone
    assert "household device" in tv and "one permission" in tv and "it is your TV" not in tv
    assert "camera" not in tv and "contacts" not in tv
    assert "I grant it nothing" in watch


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
    (re.compile(r"add a device|/pairing", re.I), "a pairing control this build does not have"),
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
    "Tell me the six digits.", "Switch off kids mode first.", r"Copy it to C:\Users\someone.",
    'Type "Friday, add a device" here.'])
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


def test_pairing_says_the_pc_side_is_off_with_no_switch_before_any_other_step():
    """True of this build: nothing in the product switches the device gateway on."""
    from agent_friday.services import device_pairing
    assert not hasattr(device_pairing, "open_pairing_window")
    for app_id in ("phone", "tablet", "tv"):
        first = eco.app(app_id).pair.steps[0].text
        assert "device gateway on this PC" in first and "no switch for it in the app yet" in first
        assert "no switch for it in the app yet" in eco.info(app_id, "limits")


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
    for word in ("phone", "tablet", "TV", "watch", "ecosystem_info", "no QR code", "through ask_friday"):
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
