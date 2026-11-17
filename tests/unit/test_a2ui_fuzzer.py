"""The Liquid UI escalation fuzzer E-L (NS-41.2-10; Salon/Crews spec section 10, E-L, amended 2026-11-11).

10,000 seeded hostile A2UI documents, made by mutating a good view with attacks from the hostile set and
beyond: basic-catalog components, other catalog ids, inputs, Modal and Card, openUrl and other functions,
URLs in every text field, confusable and zero-width labels, banned verbs, extra props, extra or hidden
approval slots, data model injection, node, depth and text blow-ups, broken ids and cycles, and plain type
garbage. Each document goes through the real validator and the real reference renderer.

The pass condition is 0 escalations in 10,000, judged by an oracle that does NOT call the profile's own
functions: a document that renders (is accepted) must produce no input, link, image, frame or form, no
button for a verb outside the allowed set, no approval marker the shell did not supply, and no button or
heading text with an approval, payment or secret word. A document that is rejected must name a rule.
Nothing may raise. A deliberately weakened validator must produce escalations, so the oracle can fail
(test_the_oracle_can_fail). Pure and offline."""
import copy
import random
import re
import unicodedata

from tests.unit import test_a2ui_profile as base

prof, rend = base.prof, base.rend

N = 10_000
ALLOWED_VERBS = {"open_run", "pause_crew"}
RULES = {f"V{n}" for n in range(1, 15)}

# the oracle's own small folding: not the profile's
LOOKALIKES = str.maketrans({"А": "A", "а": "a", "Е": "E", "е": "e", "О": "O", "о": "o",
                            "Р": "P", "р": "p", "С": "C", "с": "c", "Х": "X", "х": "x",
                            "і": "i", "І": "I", "ο": "o", "Α": "A", "Β": "B", "Ε": "E"})
WORDS = ("approve", "grant", "authorize", "authorise", "confirm", "purchase", "checkout", "password", "passcode",
         "unlock", "sign in", "log in", "top up", "send money")
FORMAT = dict.fromkeys(range(0x200B, 0x2010), None) | dict.fromkeys([0x2060, 0xFEFF, 0x00AD], None)


def oracle_fold(text):
    text = unicodedata.normalize("NFKC", text).translate(FORMAT).translate(LOOKALIKES)
    return re.sub(r"\s+", " ", text).lower()


BAD_TAGS = re.compile(r"<(input|a[ >]|img|iframe|form|textarea|select|video|audio|object|embed|script|link|svg)", re.I)
LABELS = re.compile(r"<(?:button|h[1-6]|a|summary|label)\b[^>]*>(.*?)</(?:button|h[1-6]|a|summary|label)>", re.S | re.I)

ATTACK_LABELS = ["Approve", "Аpprove", "Grant access", "Au​thorize", "ｐｕｒｃｈａｓｅ", "Confirm", "Sign in",
                 "Top up now", "Send money", "Checkout", "Enter your password", "Unlock", "Log in",
                 "Рaѕѕword", "Allow", "Pay", "Buy"]
ATTACK_TEXTS = ["http://evil.example/x", "https://a.b", "javascript:alert(1)", "data:text/html,<b>x</b>", "www.evil.com",
                "evil.com/path", "mailto:a@b.c", "file:///etc/passwd", "intent://x#Intent;end", "<script>alert(1)</script>",
                "<img src=x onerror=alert(1)>", "[click](http://x.y)", "Needs you", "Waiting for you", "Friday wants to"]
HOSTILE_COMPONENTS = ["Button", "TextField", "CheckBox", "ChoicePicker", "Slider", "DateTimeInput", "Modal", "Tabs", "Card",
                      "List", "Image", "Icon", "Video", "AudioPlayer", "McpApp", "iframe", "html", "script", "input"]
BANNED_FUNCTIONS = ["openUrl", "required", "regex", "length", "numeric", "email", "and", "or", "not", "callAgent"]
BANNED_VERBS = ["approval_decide", "connection_grant", "cap_raise", "purchase_credits", "key_write", "set_setting",
                "send_mail", "publish_site", "device_set_right", "owner_toggle", "by_design_toggle", "loud_thing",
                "not_a_real_verb", ""]
GARBAGE = [None, 0, 1, -1, 2 ** 70, 1.5, True, [], {}, "", "x" * 6000, [None], {"a": {"b": [1, {"c": None}]}}]


def surface(d):
    return d[0]["createSurface"]


def comps(d):
    return surface(d)["components"]


def some_comp(rng, d):
    return rng.choice(comps(d))


def m_label(rng, d):
    for c in comps(d):
        if c.get("component") == "verb_button":
            c["label"] = rng.choice(ATTACK_LABELS)


def m_heading(rng, d):
    comps(d).append({"id": "h" + str(rng.randrange(99)), "component": "text_block",
                     "text": rng.choice(ATTACK_LABELS + ATTACK_TEXTS), "style": "heading"})
    comps(d)[0]["children"] = comps(d)[0].get("children", []) + [comps(d)[-1]["id"]]


def m_text(rng, d):
    for c in comps(d):
        if c.get("component") == "text_block":
            c["text"] = rng.choice(ATTACK_TEXTS)


def m_status_title(rng, d):
    comps(d).append({"id": "sb", "component": "status_block", "title": rng.choice(ATTACK_LABELS), "children": []})
    comps(d)[0]["children"].append("sb")


def m_hostile_component(rng, d):
    comps(d).append({"id": "x" + str(rng.randrange(99)), "component": rng.choice(HOSTILE_COMPONENTS),
                     "child": "t1", "action": {"event": {"name": "approve"}}, "url": "http://evil.example"})
    comps(d)[0]["children"].append(comps(d)[-1]["id"])


def m_catalog(rng, d):
    surface(d)["catalogId"] = rng.choice(["https://a2ui.org/specification/v1_0/basic_catalog.json",
                                          "https://evil.example/catalog", "", None, 7,
                                          "https://agentfriday.ai/a2ui/catalogs/core/v2"])


def m_version(rng, d):
    d[0]["version"] = rng.choice(["0.9", "2.0", "", None, 1])


def m_message_key(rng, d):
    d.append(rng.choice([{"version": "1.0", "callAgentFunction": {"name": "x"}},
                         {"version": "1.0", "callRendererFunction": {"name": "openUrl"}},
                         {"version": "1.0", "updateDataModel": {"surfaceId": "x", "dataModel": {"a": 1}}},
                         {"version": "1.0", "agentFunctionResponse": {}}, {"version": "1.0"},
                         {"version": "1.0", "deleteSurface": {"surfaceId": "s"}, "createSurface": {}}]))


def m_data_model(rng, d):
    s = surface(d)
    s[rng.choice(["dataModel", "sendDataModel", "dataModel"])] = rng.choice([{"x": 1}, True, "all", {"/a": "http://x.y"}])


def m_function(rng, d):
    for c in comps(d):
        if c.get("component") == "text_block":
            c["text"] = {"call": rng.choice(BANNED_FUNCTIONS), "args": {"value": "x"}}


def m_verb(rng, d):
    verb = rng.choice(BANNED_VERBS)
    for c in comps(d):
        if c.get("component") == "verb_button":
            c["verb"] = verb
            c["action"] = {"event": {"name": rng.choice([verb, "approve", "other"]), "context": rng.choice([{}, {"id": "x"}])}}


def m_extra_prop(rng, d):
    some_comp(rng, d)[rng.choice(["style", "onClick", "html", "className", "href", "src", "dangerouslySetInnerHTML"])] = \
        rng.choice(ATTACK_TEXTS + GARBAGE if False else ATTACK_TEXTS)


def m_slot(rng, d):
    kind = rng.randrange(7)
    base_slot = {"id": "s" + str(rng.randrange(9, 99)), "component": "approval_slot", "filter": {"subject": base.SUBJECT}}
    if kind == 0:
        base_slot["text"] = "Approve all"
    elif kind == 1:
        base_slot["filter"] = {"subject": "run:other"}
    elif kind == 2:
        base_slot["filter"]["kind"] = rng.choice(["x", 5, None, "send_email"])
    elif kind == 3:
        base_slot["children"] = ["t1"]
    elif kind == 4:
        base_slot["accessibility"] = {"label": "Approve", "hidden": True}
    elif kind == 5:
        base_slot["action"] = {"event": {"name": "approval_decide"}}
    comps(d).append(base_slot)
    comps(d)[0]["children"].append(base_slot["id"])


def m_slot_in_template(rng, d):
    comps(d).append({"id": "tpl", "component": "row", "children": {"componentId": "s1", "path": "/rows"}})
    comps(d)[0]["children"].append("tpl")


def m_accessibility(rng, d):
    some_comp(rng, d)["accessibility"] = rng.choice([{"label": rng.choice(ATTACK_LABELS)}, {"hidden": True},
                                                      {"live": "assertive"}, {"live": "polite"}, {"live": "off"},
                                                      {"label": "Ok", "hidden": rng.choice([True, False])}])


def m_blowup(rng, d):
    kind = rng.randrange(4)
    if kind == 0:
        for i in range(rng.randrange(55, 200)):
            comps(d).append({"id": f"n{i}", "component": "divider"})
            comps(d)[0]["children"].append(f"n{i}")
    elif kind == 1:
        parent = "root"
        for i in range(rng.randrange(6, 40)):
            comps(d).append({"id": f"d{i}", "component": "column", "children": []})
            by = {c["id"]: c for c in comps(d)}
            by[parent]["children"].append(f"d{i}")
            parent = f"d{i}"
    elif kind == 2:
        comps(d).append({"id": "big", "component": "text_block", "text": "x" * rng.randrange(4000, 9000)})
        comps(d)[0]["children"].append("big")
    else:
        for i in range(rng.randrange(3, 10)):
            comps(d).append({"id": f"t{i}", "component": "text_block", "text": "y" * 900})
            comps(d)[0]["children"].append(f"t{i}")


def m_ids(rng, d):
    kind = rng.randrange(4)
    if kind == 0:
        comps(d).append(dict(comps(d)[1]))
    elif kind == 1:
        comps(d)[0]["children"].append("root")
    elif kind == 2:
        comps(d)[0]["children"].append("missing")
    else:
        comps(d)[0]["children"] = comps(d)[0]["children"] * 3


def m_garbage(rng, d):
    target = rng.choice([some_comp(rng, d), surface(d), d[0]])
    key = rng.choice(list(target) or ["x"])
    target[key] = rng.choice(GARBAGE)


def m_delete(rng, d):
    target = rng.choice([some_comp(rng, d), surface(d)])
    if target:
        del target[rng.choice(list(target))]


def m_bindings(rng, d):
    surface(d).setdefault("metadata", {}).setdefault("friday", {})["bindings"] = rng.choice(
        [{"/x": {"query": "everything"}}, {"/x": {"query": "run_outputs", "limit": 100000}}, {"/x": "http://x.y"},
         {"/x": {"query": "secrets"}}, None, [1]])
    comps(d).append({"id": "p", "component": "cost_line", "amount": {"@path": rng.choice(["/x", "/y", "/../z", "//"])}})
    comps(d)[0]["children"].append("p")


MUTATIONS = [m_label, m_heading, m_text, m_status_title, m_hostile_component, m_catalog, m_version, m_message_key,
             m_data_model, m_function, m_verb, m_extra_prop, m_slot, m_slot_in_template, m_accessibility, m_blowup,
             m_ids, m_garbage, m_delete, m_bindings]


def make(rng):
    d = copy.deepcopy(base.GOOD)
    for _ in range(rng.choice([0, 1, 1, 2, 2, 3, 4])):
        try:
            rng.choice(MUTATIONS)(rng, d)
        except Exception:  # noqa: BLE001
            pass      # the mutation did not apply to this shape; the document stays what it is
    return d


def escalations(rendered):
    """What an accepted document made that it must never make, judged without the profile's own functions."""
    found = []
    html = rendered.html or ""
    tags = re.findall(r"<[^>]+>", html)           # real tags only: the document's own text arrives escaped as &lt;..&gt;
    if any(BAD_TAGS.match(t) or re.search(r"[\s\"'](?:href|src|action|formaction|on[a-z]+|style)\s*=", t, re.I)
           or "javascript:" in t.lower() for t in tags):
        found.append("a control, link, image, frame or handler")
    for verb in re.findall(r'data-verb="([^"]*)"', html):
        if verb not in ALLOWED_VERBS:
            found.append("a button for a verb outside the allowed set")
    if re.search(r"data-shell-card|data-approval-id", html):
        found.append("an approval marker the shell did not supply")
    for label in LABELS.findall(html):
        folded = oracle_fold(re.sub(r"<[^>]+>", "", label))
        if any(re.search(r"\b" + re.escape(w) + r"\b", folded) for w in WORDS):
            found.append("an approval, payment or secret word on a button or heading")
    return found


def run(count=N, seed=41):
    rng = random.Random(seed)
    outcome = {"ok": 0, "rejected": 0, "escalations": [], "unnamed": 0, "raised": []}
    for i in range(count):
        document = make(rng)
        try:
            rendered = rend.render(document, base.ctx(), pending_approvals=lambda subject, kind: [])
        except Exception as err:  # noqa: BLE001 - nothing may raise, whatever the document is
            outcome["raised"].append((i, type(err).__name__))
            continue
        if rendered.ok:
            outcome["ok"] += 1
            outcome["escalations"] += [(i, e) for e in escalations(rendered)]
        else:
            outcome["rejected"] += 1
            rule = getattr(rendered.receipt, "rule", None) if rendered.receipt is not None else None
            rule = rule or (rendered.receipt.get("rule") if isinstance(rendered.receipt, dict) else None)
            if rule not in RULES:
                outcome["unnamed"] += 1
    return outcome


def test_zero_escalations_in_ten_thousand_hostile_documents():
    result = run()
    assert result["raised"] == [], result["raised"][:5]
    assert result["escalations"] == [], result["escalations"][:5]
    assert result["unnamed"] == 0, "a rejection must name its rule"
    assert result["rejected"] > 4000 and result["ok"] > 1000, (result["ok"], result["rejected"])


def test_the_fuzzer_is_deterministic():
    assert run(300, seed=7)["ok"] == run(300, seed=7)["ok"]
    first = [make(random.Random(3)) for _ in range(5)]
    assert first == [make(random.Random(3)) for _ in range(5)]


def test_the_oracle_can_fail(monkeypatch):
    """With the label rule and the verb rule switched off, the same fuzz must find escalations."""
    monkeypatch.setattr(prof, "reserved_hit", lambda *a, **k: None)
    result = run(3000)
    assert result["escalations"] or result["raised"], "a weakened validator was not caught by the fuzzer"


GARBLED = [
    [{"version": "1.0", "createSurface": {"surfaceId": "s", "catalogId": base.CAT, "sendDataModel": False,
                                          "components": comps_}}]
    for comps_ in (True, 5, 1.5, [[1, 2, 3, 4]], ["ab"], [{"id": {"a": 1}, "component": "column"}],
                   [{"id": ["x"], "component": "column"}], [{"id": "root", "component": "column", "children": [{"a": 1}]}],
                   [{"id": "root", "component": "column", "children": [["x"]]}])
] + [[{"version": "1.0", "createSurface": {"surfaceId": "s", "catalogId": base.CAT, "sendDataModel": False,
                                           "components": [{"id": "root", "component": "column", "children": []}],
                                           "metadata": {"friday": {"bindings": [1]}}}}]]


def test_garbled_documents_are_rejected_whole_with_a_rule_never_raised():
    """Found by this fuzzer: nine shapes of garbage made the validator raise a TypeError, ValueError or
    AttributeError instead of rejecting the document, so a view could crash its caller (NS-41.2-9)."""
    for document in GARBLED:
        rendered = rend.render(document, base.ctx(), pending_approvals=lambda subject, kind: [])
        assert rendered.ok is False and rendered.receipt["rule"] in RULES, document
        assert prof.violations(document, base.ctx()), document


def test_a_document_that_nests_without_end_is_rejected_not_a_crash():
    node = {"id": "root", "component": "column", "children": []}
    deep = {"surfaceId": "s", "catalogId": base.CAT, "sendDataModel": False, "components": [node],
            "metadata": {"friday": {"bindings": {}}}}
    cursor = deep["metadata"]["friday"]["bindings"]
    for _ in range(5000):
        cursor["x"] = {}
        cursor = cursor["x"]
    rendered = rend.render([{"version": "1.0", "createSurface": deep}], base.ctx(), pending_approvals=lambda s, k: [])
    assert rendered.ok is False and rendered.receipt["rule"] in RULES
