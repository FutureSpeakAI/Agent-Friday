"""The closed scope of a peer job and the both-must-allow decision (NS-40.3-2, -4, -5, -7). Pure; test credits only."""
import ast
import copy
import dataclasses
import itertools
import json
from pathlib import Path

import pytest

from agent_friday.services import job_scope as js

CASES = json.loads((Path(__file__).resolve().parents[1] / "federation" / "bundle" / "fixtures"
                    / "wire-v2.json").read_text(encoding="utf-8"))["cases"]
FIXTURE = next(c["envelope"]["body"]["payload"]["terms"]["scope"] for c in CASES
               if c.get("envelope", {}).get("body", {}).get("type") == "job.offer")


def h(n):
    return f"{n:064x}"


REQUESTER, PROVIDER, READER, OUTSIDER = h(1), h(2), h(3), h(4)
DIGEST, TOOL, MODEL = h(10), h(11), h(12)


def scope_doc(**over):
    d = copy.deepcopy(FIXTURE)
    d.update(inputs=[{"digest": DIGEST, "media_type": "text/plain", "max_bytes": 1000, "recipient": PROVIDER,
                      "purpose": "render"}], readers=[READER], approved_tools=[TOOL], allowed_models=[MODEL],
             compute_ms=30000, memory_bytes=1000, storage_bytes=2000, output_bytes=3000, preview="send_to_requester",
             final="send_to_requester", publication="signed_release_required", review_human_keys=[READER])
    d.update(over)
    return d


def grant(**over):
    base = dict(input_digests=frozenset({DIGEST}), recipients=frozenset({PROVIDER}),
                media_types=frozenset({"text/plain"}), max_input_bytes=1000, purposes=frozenset({"render"}),
                tools=frozenset({TOOL}), models=frozenset({MODEL}), readers=frozenset({READER}), compute_ms=30000,
                memory_bytes=1000, storage_bytes=2000, output_bytes=3000, may_send_preview=True, may_send_final=True,
                may_publish=True)
    base.update(over)
    return js.LocalGrant(**base)


SCOPE = js.parse_scope(scope_doc())
GRANT = grant()
ACTIONS = {
    "input": js.InputTransfer(DIGEST, "text/plain", 1000, PROVIDER, "render"),
    "tool": js.ToolRun(TOOL, MODEL, 30000, 1000, 2000, 3000),
    "view": js.View(READER),
    "preview": js.Release("preview", REQUESTER),
    "final": js.Release("final", REQUESTER),
    "publish": js.Publish(True),
}


def decide(action, scope=SCOPE, g=GRANT):
    return js.decide(scope, g, action, requester_key=REQUESTER)


def test_the_bundle_scope_parses_and_anything_looser_is_refused():
    parsed = js.parse_scope(FIXTURE)
    assert parsed.preview == "hold" and parsed.final == "send_to_requester" and parsed.publication == "private"
    assert js.parse_scope(scope_doc()).inputs[0].max_bytes == 1000
    missing = {k: v for k, v in FIXTURE.items() if k != "cloud"}
    bad = [missing, {**FIXTURE, "extra": 1}, {**FIXTURE, "network": "internet"}, {**FIXTURE, "network": True},
           {**FIXTURE, "cloud": True}, {**FIXTURE, "cloud": 0}, {**FIXTURE, "delegate": True},
           {**FIXTURE, "preview": "publish"}, {**FIXTURE, "final": None}, {**FIXTURE, "publication": "public"},
           {**FIXTURE, "compute_ms": 1.5}, {**FIXTURE, "memory_bytes": -1}, {**FIXTURE, "output_bytes": True},
           {**FIXTURE, "storage_bytes": 2 ** 53}, {**FIXTURE, "readers": [READER, READER]},
           {**FIXTURE, "readers": ["zz"]}, {**FIXTURE, "readers": [h(n) for n in range(33)]},
           {**FIXTURE, "allowed_models": [h(n) for n in range(17)]}, {**FIXTURE, "review_human_keys": "x"},
           {**FIXTURE, "inputs": "x"}, {**FIXTURE, "inputs": [{}]}, [], None]
    bad += [scope_doc(inputs=[{**scope_doc()["inputs"][0], key: value}]) for key, value in
            (("digest", "x"), ("recipient", "y"), ("media_type", "a b"), ("purpose", " "), ("max_bytes", -1),
             ("max_bytes", 1.0), ("extra", 1))]
    bad += [scope_doc(inputs=[scope_doc()["inputs"][0]] * 65)]
    for document in bad:
        with pytest.raises(js.ScopeError):
            js.parse_scope(document)


def test_a_fully_matching_action_is_allowed_by_both():
    for name, action in ACTIONS.items():
        assert decide(action).allowed, name


SCOPE_DENIALS = {
    "unknown input": (ACTIONS["input"], dict(digest=h(99)), js.Deny.INPUT),
    "other recipient": (ACTIONS["input"], dict(recipient=OUTSIDER), js.Deny.RECIPIENT),
    "other media type": (ACTIONS["input"], dict(media_type="image/png"), js.Deny.MEDIA_TYPE),
    "too big": (ACTIONS["input"], dict(size=1001), js.Deny.TOO_BIG),
    "negative size": (ACTIONS["input"], dict(size=-1), js.Deny.TOO_BIG),
    "text size": (ACTIONS["input"], dict(size="1"), js.Deny.TOO_BIG),
    "bool size": (ACTIONS["input"], dict(size=True), js.Deny.TOO_BIG),
    "other purpose": (ACTIONS["input"], dict(purpose="train"), js.Deny.PURPOSE),
    "unapproved tool": (ACTIONS["tool"], dict(tool_digest=h(98)), js.Deny.TOOL),
    "unallowed model": (ACTIONS["tool"], dict(model_digest=h(97)), js.Deny.MODEL),
    "compute": (ACTIONS["tool"], dict(compute_ms=30001), js.Deny.LIMIT),
    "memory": (ACTIONS["tool"], dict(memory_bytes=1001), js.Deny.LIMIT),
    "storage": (ACTIONS["tool"], dict(storage_bytes=2001), js.Deny.LIMIT),
    "output": (ACTIONS["tool"], dict(output_bytes=3001), js.Deny.LIMIT),
    "negative limit": (ACTIONS["tool"], dict(compute_ms=-1), js.Deny.LIMIT),
    "unnamed viewer": (ACTIONS["view"], dict(reader_key=OUTSIDER), js.Deny.VIEWER),
}


def test_each_dimension_is_denied_by_the_signed_scope():
    for name, (action, change, code) in SCOPE_DENIALS.items():
        result = decide(dataclasses.replace(action, **change))
        assert (result.allowed, result.code, result.source) == (False, code, js.Source.SIGNED_SCOPE), name
        assert result.renegotiate is True, "a signed-scope refusal may be renegotiated"
    held = js.parse_scope(scope_doc(preview="hold", final="hold", publication="private"))
    for key in ("preview", "final"):
        r = decide(ACTIONS[key], scope=held)
        assert (r.code, r.source) == (js.Deny.RELEASE, js.Source.SIGNED_SCOPE)
    assert decide(dataclasses.replace(ACTIONS["preview"], recipient=OUTSIDER)).code is js.Deny.RELEASE
    assert decide(ACTIONS["publish"], scope=held).code is js.Deny.PUBLICATION
    assert decide(js.Publish(False)).code is js.Deny.PUBLICATION, "a signed release is required"


def test_each_dimension_is_denied_by_the_owners_grant():
    narrower = {
        "digest": (ACTIONS["input"], grant(input_digests=frozenset()), js.Deny.INPUT),
        "recipient": (ACTIONS["input"], grant(recipients=frozenset()), js.Deny.RECIPIENT),
        "media": (ACTIONS["input"], grant(media_types=frozenset()), js.Deny.MEDIA_TYPE),
        "bytes": (ACTIONS["input"], grant(max_input_bytes=999), js.Deny.TOO_BIG),
        "purpose": (ACTIONS["input"], grant(purposes=frozenset()), js.Deny.PURPOSE),
        "tool": (ACTIONS["tool"], grant(tools=frozenset()), js.Deny.TOOL),
        "model": (ACTIONS["tool"], grant(models=frozenset()), js.Deny.MODEL),
        "compute": (ACTIONS["tool"], grant(compute_ms=29999), js.Deny.LIMIT),
        "memory": (ACTIONS["tool"], grant(memory_bytes=999), js.Deny.LIMIT),
        "storage": (ACTIONS["tool"], grant(storage_bytes=1999), js.Deny.LIMIT),
        "output": (ACTIONS["tool"], grant(output_bytes=2999), js.Deny.LIMIT),
        "viewer": (ACTIONS["view"], grant(readers=frozenset()), js.Deny.VIEWER),
        "preview": (ACTIONS["preview"], grant(may_send_preview=False), js.Deny.RELEASE),
        "final": (ACTIONS["final"], grant(may_send_final=False), js.Deny.RELEASE),
        "publish": (ACTIONS["publish"], grant(may_publish=False), js.Deny.PUBLICATION),
    }
    for name, (action, g, code) in narrower.items():
        result = decide(action, g=g)
        assert (result.allowed, result.code, result.source) == (False, code, js.Source.LOCAL_GRANT), name
        assert result.renegotiate is False, "the owner's own limit is not the peer's to renegotiate"


def test_a_grant_never_widens_the_signed_scope():
    wide = grant(input_digests=frozenset({DIGEST, h(99)}), recipients=frozenset({PROVIDER, OUTSIDER}),
                 media_types=frozenset({"text/plain", "image/png"}), max_input_bytes=10 ** 9,
                 purposes=frozenset({"render", "train"}), tools=frozenset({TOOL, h(98)}),
                 models=frozenset({MODEL, h(97)}), readers=frozenset({READER, OUTSIDER}), compute_ms=10 ** 9,
                 memory_bytes=10 ** 9, storage_bytes=10 ** 9, output_bytes=10 ** 9)
    for name, (action, change, code) in SCOPE_DENIALS.items():
        assert decide(dataclasses.replace(action, **change), g=wide).code is code, name
    held = js.parse_scope(scope_doc(preview="hold", final="hold", publication="private"))
    assert not decide(ACTIONS["final"], scope=held, g=wide).allowed and not decide(ACTIONS["publish"], scope=held, g=wide).allowed


def test_both_must_allow_for_every_combination_of_scope_and_grant_per_action():
    held = js.parse_scope(scope_doc(preview="hold", final="hold", publication="private", readers=[], approved_tools=[],
                                    inputs=[]))
    none = grant(input_digests=frozenset(), tools=frozenset(), readers=frozenset(), may_send_preview=False,
                 may_send_final=False, may_publish=False)
    for name, action in ACTIONS.items():
        for scope_ok, grant_ok in itertools.product((True, False), repeat=2):
            result = decide(action, scope=SCOPE if scope_ok else held, g=GRANT if grant_ok else none)
            assert result.allowed is (scope_ok and grant_ok), (name, scope_ok, grant_ok)


def test_worker_execution_has_no_network_cloud_or_delegation():
    everything = grant()
    for action, code in ((js.Network(), js.Deny.NETWORK), (js.CloudFallback(), js.Deny.CLOUD),
                         (js.Delegate(), js.Deny.DELEGATE)):
        for g in (everything, js.LocalGrant()):
            r = decide(action, g=g)
            assert (r.allowed, r.code) == (False, code)


def test_candidate_bytes_and_hashes_reach_only_an_authorized_recipient():
    ok = js.candidate_egress(SCOPE, GRANT, "final", REQUESTER, REQUESTER)
    assert ok == js.Egress(True, True, True)
    held = js.parse_scope(scope_doc(final="hold", preview="hold"))
    assert js.candidate_egress(held, GRANT, "final", REQUESTER, REQUESTER) == js.Egress(False, False, True), \
        "a held candidate stays here; only its status goes"
    assert js.candidate_egress(SCOPE, GRANT, "final", OUTSIDER, REQUESTER) == js.Egress(False, False, False)
    assert js.candidate_egress(SCOPE, grant(may_send_final=False), "final", REQUESTER, REQUESTER) == \
        js.Egress(False, False, True)
    assert js.candidate_egress(SCOPE, GRANT, "draft", REQUESTER, REQUESTER) == js.Egress(False, False, True)
    for scope_doc_, g in itertools.product((scope_doc(), scope_doc(final="hold")), (GRANT, js.LocalGrant())):
        for recipient in (REQUESTER, PROVIDER, OUTSIDER):
            e = js.candidate_egress(js.parse_scope(scope_doc_), g, "final", recipient, REQUESTER)
            assert e.candidate_bytes == e.content_hash, "a guessable hash goes exactly where the bytes may"
            if recipient != REQUESTER:
                assert not e.candidate_bytes and not e.content_hash and not e.status


def test_human_review_needs_a_named_viewer_and_consent():
    assert js.may_open_for_review(SCOPE, READER, True) is True
    for key, consent in ((READER, False), (READER, None), (READER, "yes"), (OUTSIDER, True), ("", True)):
        assert js.may_open_for_review(SCOPE, key, consent) is False


def test_a_refusal_carries_only_a_closed_code():
    result = decide(dataclasses.replace(ACTIONS["input"], digest=h(99), purpose="CANARYWORD"))
    assert isinstance(result, js.Decision) and result.code in set(js.Deny)
    text = repr(result)
    assert h(99) not in text and "CANARYWORD" not in text
    with pytest.raises(TypeError):
        js.decide(SCOPE, GRANT, object())
    with pytest.raises(TypeError):
        js.decide(SCOPE, GRANT, "input")


def test_the_scope_module_is_pure():
    tree = ast.parse(Path(js.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= {"__future__", "re", "dataclasses", "enum"}, imported
