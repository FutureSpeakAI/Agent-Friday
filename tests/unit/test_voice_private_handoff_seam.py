"""No raw private detail reaches the cloud voice model. The seam, tested.

Voice runs on a cloud model. When a spoken request needs the user's own data —
mail, vault, wiki, files, contacts, finances, health — a local model reads the
raw data on this machine and only its scrubbed summary is handed to the cloud
session. That is the whole promise, and it lives on one seam:

    request() -> prepare() -> the card -> _on_decision() -> _send() -> the call

So the test seeds real-shaped identifiers into what the local model "says" and
then asserts, at the point the text is actually handed to the live call, that
none of them survived. Asserting on the scrub's own return value would only
prove the scrub scrubs; asserting here proves that what LEAVES is clean, which
is the claim being made to the user.

Everything is injected: `answer_fn` stands in for the local model and
`_deliver` is captured, so none of this needs a GPU, a cloud key, or a call.
"""
import pytest

lc = pytest.importorskip("agent_friday.services.local_context")

#: Shaped like the real thing so the scrubber's own patterns fire, and drawn
#: entirely from ranges reserved for fiction so nothing here belongs to
#: anyone: an SSN in the never-issued 900 block, the published Visa test
#: number, a 555-01xx phone, an example.org address, an invented street.
SEEDED = {
    "ssn": "900-00-0000",  # pragma: allowlist secret - never-issued SSN block
    "card": "4111 1111 1111 1111",  # pragma: allowlist secret - Visa test number
    "phone": "(512) 555-0147",
    "email": "not.a.real.person@example.org",
    "street": "1847 Cedar Hollow Lane",
}

RAW_ANSWER = (
    "Her details: reach her on {phone} or {email}. "
    "She lives at {street}. Her social is {ssn} and the card on file "
    "ends with {card}. She likes hiking on weekends."
).format(**SEEDED)


@pytest.fixture
def captured(monkeypatch):
    """Whatever was handed to the live call."""
    sent = []
    monkeypatch.setattr(lc, "_deliver",
                        lambda cid, text, kind: sent.append(
                            {"cid": cid, "text": text, "kind": kind}) or True)
    monkeypatch.setattr(lc, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_note_in_conversation", lambda *a, **k: None)
    # _on_decision guards on the real store's exactly-once claim, which has no
    # record of a synthetic id. Stand it in so the test exercises the seam
    # rather than the store.
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "claim_for_execution", lambda aid: True)
    monkeypatch.setattr(approvals, "mark_used", lambda *a, **k: None)
    return sent


def _no_seed_in(text, where):
    """Assert not one seeded identifier survived into `text`."""
    leaked = [name for name, value in SEEDED.items() if value in (text or "")]
    assert not leaked, (
        "%s carried raw %s to the cloud voice model. The point of the local "
        "handoff is that this cannot happen: %r" % (where, leaked, text))
    # Digits are the tell even if the formatting changed on the way through.
    for name, value in SEEDED.items():
        bare = "".join(ch for ch in value if ch.isdigit())
        if len(bare) >= 7:
            assert bare not in "".join(ch for ch in (text or "") if ch.isdigit()), (
                "%s carried the digits of %s through reformatting" % (where, name))


# ── The payload on the card is what the user is asked to approve ────────────

def test_the_card_the_user_sees_carries_no_raw_identifier(captured):
    out = lc.request("What does his sister enjoy?", conversation_id="conv-seam",
                     cloud_model="gemini-live", answer_fn=lambda q: (RAW_ANSWER, "qwen3:4b"))
    # Refusing the whole answer is a valid outcome and still safe, but it must
    # not be how this test quietly stops asserting: either nothing was shared,
    # or what was shared is clean.
    assert out["status"] in ("pending", "sent", "withheld")
    if out["status"] == "withheld":
        assert not (out.get("text") or ""), "a withheld answer shares nothing"
        return
    text = out.get("text") or ""
    if out["status"] == "pending":
        from agent_friday.services import approvals
        rec = approvals.get_approval(out["approval_id"]) or {}
        text = ((rec.get("payload") or {}).get("text")
                or rec.get("description") or "")
    assert text, "there must be something to approve"
    _no_seed_in(text, "the approval card")


def test_nothing_raw_reaches_the_live_call_when_it_is_approved(captured):
    """The moment that matters: the approved text going into the session."""
    draft = lc.prepare(RAW_ANSWER)
    if not draft["text"]:
        # The floor took it whole. Safe, and the assertion becomes: with
        # nothing to approve, nothing is handed over.
        lc._on_decision({"approval_id": "a-seam-1", "status": "approved",
                         "payload": {"conversation_id": "conv-seam",
                                     "text": "", "version": 1}})
        assert not [c for c in captured if (c["text"] or "").strip()
                    and c["kind"] == "context"]
        return
    lc._on_decision({"approval_id": "a-seam-1", "status": "approved",
                     "payload": {"conversation_id": "conv-seam",
                                 "text": draft["text"], "version": 1}})
    handed = [c for c in captured if c["kind"] == "context"]
    assert handed, "an approved share must actually reach the call"
    _no_seed_in(handed[0]["text"], "the live call")


def test_a_decline_hands_over_no_text_at_all(captured):
    lc._on_decision({"approval_id": "a-seam-2", "status": "declined",
                     "payload": {"conversation_id": "conv-seam",
                                 "text": RAW_ANSWER, "version": 1}})
    for c in captured:
        assert c["kind"] != "context", "a decline must send no context"
        _no_seed_in(c["text"], "the decline notice")


# ── The scrub is the existing gate, not a second one ────────────────────────

def test_the_scrub_replaces_identifiers_with_placeholders_not_silence():
    """A blanked answer teaches the user nothing; a placeholder does."""
    scrubbed, placeholders = lc.scrub(RAW_ANSWER)
    _no_seed_in(scrubbed, "the scrub")
    assert placeholders, "the card must be able to say what was replaced"
    for p in placeholders:
        assert "placeholder" in p and "category" in p
        for value in SEEDED.values():
            assert value not in str(p), (
                "a placeholder must name the CATEGORY, never the value: %r" % p)


def test_it_uses_the_existing_pii_gate_rather_than_a_private_copy():
    """Stephen's instruction: reuse the Privacy Shield, do not add a scrubber."""
    import inspect
    src = inspect.getsource(lc.scrub)
    assert "_scrub_pii" in src, (
        "the handoff must call core's PII gate, not re-implement one")


def test_a_scrub_that_cannot_run_withholds_instead_of_sending(monkeypatch):
    """Fail closed. An unavailable privacy check is not a reason to send raw."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "_scrub_pii",
                        lambda t: (_ for _ in ()).throw(RuntimeError("gate down")))
    text, placeholders = lc.scrub(RAW_ANSWER)
    assert text == "", "with no working scrub, nothing may be shared"
    assert placeholders and "unavailable" in str(placeholders).lower()


def test_the_floor_refuses_rather_than_trimming(monkeypatch):
    """Never-send material takes the whole answer down, not just the phrase."""
    monkeypatch.setattr(lc, "floor_hits", lambda t: ["a never-send phrase"])
    draft = lc.prepare("Something ordinary about the weekend.")
    assert draft["text"] == ""
    assert "never-send" in draft.get("refused", "")


# ── No local model means no share, ever ────────────────────────────────────

def test_no_local_model_means_nothing_is_shared(captured):
    """The failure that must never become 'send it to the cloud instead'."""
    out = lc.request("What does his sister enjoy?", conversation_id="conv-seam",
                     cloud_model="gemini-live", answer_fn=lambda q: (RAW_ANSWER, None))
    assert out["status"] == "unavailable"
    assert not [c for c in captured if c["kind"] == "context"]
    for c in captured:
        _no_seed_in(c["text"], "the unavailable notice")


# ── Which local model reads it, and saying so out loud ─────────────────────

def _seats(monkeypatch, *, installed, serving):
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "serving", lambda: {m: "http://x" for m in serving})
    monkeypatch.setattr(local_seats, "resolve",
                        lambda role, configured=None: installed.get(role))


def test_a_seat_that_is_already_up_answers_without_a_word(monkeypatch):
    _seats(monkeypatch, installed={"brain": "qwen3:14b", "sidekick": "qwen3:4b"},
           serving=["qwen3:14b"])
    seat, note = lc.pick_local_seat()
    assert seat == "qwen3:14b"
    assert note == "", "nothing to explain when the answer is instant"


def test_the_sidekick_answers_when_the_main_seat_is_down(monkeypatch):
    """Stephen's instruction: summon one or use the sidekick."""
    _seats(monkeypatch, installed={"brain": "qwen3:14b", "sidekick": "qwen3:4b"},
           serving=["qwen3:4b"])
    seat, note = lc.pick_local_seat()
    assert seat == "qwen3:4b"
    assert "sidekick" in note.lower(), "and say so out loud"


def test_a_cold_seat_is_summoned_and_the_wait_is_announced(monkeypatch):
    _seats(monkeypatch, installed={"brain": "qwen3:14b", "sidekick": None},
           serving=[])
    seat, note = lc.pick_local_seat()
    assert seat == "qwen3:14b", "a cold seat is still a local seat"
    assert "moment" in note.lower(), (
        "an unexplained minute of silence reads as Friday ignoring him")


def test_no_local_model_at_all_says_so_and_refuses(monkeypatch):
    _seats(monkeypatch, installed={"brain": None, "sidekick": None}, serving=[])
    seat, note = lc.pick_local_seat()
    assert seat is None
    assert "will not" in note.lower(), (
        "the refusal must be explicit, not a silent failure")
    assert "cloud" in note.lower()


def test_unknown_residency_is_treated_as_cold_not_as_up(monkeypatch):
    """Guessing 'it is probably up' turns a one-minute wait into a timeout."""
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "serving",
                        lambda: (_ for _ in ()).throw(RuntimeError("no arbiter")))
    monkeypatch.setattr(local_seats, "resolve",
                        lambda role, configured=None: "qwen3:14b" if role == "brain" else None)
    seat, note = lc.pick_local_seat()
    assert seat == "qwen3:14b"
    assert note, "if we cannot tell, warn about the wait"


def test_the_spoken_note_reaches_the_call_before_the_local_model_runs(monkeypatch,
                                                                     captured):
    """The announcement is worthless after the wait it explains."""
    order = []
    _seats(monkeypatch, installed={"brain": "qwen3:14b", "sidekick": None}, serving=[])
    monkeypatch.setattr(lc, "_deliver",
                        lambda cid, text, kind: order.append("said:" + kind) or True)
    monkeypatch.setattr(lc, "local_answer",
                        lambda q, seat=None: order.append("ran_local") or ("Nothing private here.", seat))
    lc.request("What is on his calendar?", conversation_id="conv-seam",
               cloud_model="gemini-live")
    assert order[:2] == ["said:notice", "ran_local"], order


# ── Unrestricted cloud mode does not turn this path off ────────────────────

def test_unrestricted_cloud_mode_does_not_disable_the_handoff_scrub(monkeypatch):
    """seal_outbound skips its PII scrub entirely under recorded unrestricted
    consent. This path must not.

    The difference is what the two things promise. seal_outbound is a
    safeguard applied on the way out, and the owner may record a decision to
    lift it. The handoff is not a safeguard being applied to him — it is the
    mechanism of the feature: the cloud model asked for a summary, so a
    summary is what it gets. Honouring "unrestricted" here would silently turn
    "ask my local model" into "send my mail to Google", which is not what
    either setting says.
    """
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: True)
    try:
        from agent_friday.privacy import cloud_consent
        monkeypatch.setattr(cloud_consent, "is_unrestricted_cloud", lambda: True)
    except Exception:
        pass
    scrubbed, placeholders = lc.scrub(RAW_ANSWER)
    _no_seed_in(scrubbed, "the scrub under unrestricted cloud mode")
    assert placeholders


def test_the_handoff_scrubs_directly_rather_than_through_the_outbound_gate():
    """Why the test above holds, pinned where it can be checked."""
    import inspect
    src = inspect.getsource(lc.scrub)
    assert "seal_outbound" not in src, (
        "routing through seal_outbound would inherit its unrestricted-mode "
        "bypass and send raw private data")
    assert "_scrub_pii" in src
