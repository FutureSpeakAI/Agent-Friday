"""Two things the itinerary investigation ranked and left for later.

P5 — A CALENDAR ENTRY IS NOT SPENDING. Five identical `create_calendar_event`
cards came out labelled `internal, spend, spend, internal, spend`, because the
label is a substring scan over the card's text and three of those events happened
to mention a ticket price. The gating decision is not affected -- it comes from
dissent_gate -- but the owner reads the label, and a calendar entry that says
"spend" is asking him to approve the wrong thing. The governance gate has already
classified the tool by then; the card should use that answer instead of guessing
from words.

P7 — "bonsai2:27b IS NOT LOADED (SERVING: bonsai2:27b)". Delegating a small job
to the local seat was refused with a message that read as a contradiction. The
real cause is neither name matching nor a model outage: `large_local` resolves
`heavy_hitter or reasoning`, `heavy_hitter` is bound to the CLOUD model
claude-opus-5-5, so it wins and the local `reasoning` binding -- bonsai2:27b,
which is serving -- is never consulted. The tier then asks whether that cloud
model is loaded locally, and says no while naming the local seat that is up.

A LOCAL tier must only consider LOCAL candidates. That uses the owner's own local
binding rather than substituting something arbitrary, and it never crosses the
local/cloud line, which this module already promises not to do.
"""

import pytest


# ── P5: the card's label describes the ACTION, not its arguments ────────────

TICKET_NOTES = 'tickets $35 each, order at tickets.example.org'


def _store(tmp_path, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    return ap


def test_a_calendar_event_with_a_ticket_link_is_not_labelled_spend(tmp_path,
                                                                  monkeypatch):
    """The reported symptom, through the real path: no class is injected here,
    it is derived exactly as the taint gate derives it."""
    from agent_friday.services import agent as ag
    ap = _store(tmp_path, monkeypatch)
    inp = {"title": "Scott Silven: The Lost Things", "notes": TICKET_NOTES}
    cls = ag._gate_policy_class("create_calendar_event", inp)
    rec = ap.create_approval(
        kind="tainted_action", subject_type="tool_action",
        subject_id="taint:p5:1", title="Create calendar event",
        action_description='create_calendar_event %r' % (inp,),
        force_gate=True, payload={"tool": "create_calendar_event", "input": inp},
        action_class=cls, requested_by="taint_gate")
    assert rec["policy_class"] != "spend", (
        "a calendar entry is still labelled spend because its notes mention "
        "buying a ticket")


def test_the_old_scan_is_what_got_it_wrong(tmp_path, monkeypatch):
    """Pins the mechanism so a future edit cannot quietly reintroduce it: the
    scan over the whole card text DOES say spend, and the action-based class
    does not."""
    from agent_friday.services import agent as ag
    from agent_friday.services import approvals as ap
    text = ('create_calendar_event {"title": "x", "notes": "%s"}' % TICKET_NOTES)
    assert ap._label_hard_class(text) == "spend"          # the old answer
    assert ag._gate_policy_class("create_calendar_event", {}) != "spend"


def test_one_instruction_gets_one_label(tmp_path, monkeypatch):
    """Five events from a single "create them" must not be labelled differently
    because some venues mention a price and others do not."""
    from agent_friday.services import agent as ag
    notes = ["quiet room, no stairs", TICKET_NOTES, "seated show",
             "buy tickets at theatre.example.com", "book ahead"]
    seen = {ag._gate_policy_class("create_calendar_event", {"notes": n})
            for n in notes}
    assert len(seen) == 1, "one instruction produced labels %r" % (seen,)
    assert "spend" not in seen, seen


def test_the_tool_still_decides_its_own_character(tmp_path, monkeypatch):
    """Specificity is kept, because the TOOL is what the label is about."""
    from agent_friday.services import agent as ag
    assert ag._gate_policy_class("send_email", {}) == "external_message"
    assert ag._gate_policy_class("delete_file", {"path": "x"}) == "irreversible"
    assert ag._gate_policy_class("search_web", {"q": "x"}) == "internal"


def test_an_unknown_class_is_ignored_rather_than_trusted(tmp_path, monkeypatch):
    """A caller passing nonsense must not invent a policy or skip a gate."""
    ap = _store(tmp_path, monkeypatch)
    rec = ap.create_approval(
        kind="tainted_action", subject_type="tool_action",
        subject_id="taint:p5:bogus", title="x",
        action_description="send_email {}", force_gate=True,
        payload={"tool": "send_email", "input": {}},
        action_class="not_a_class", requested_by="taint_gate")
    assert rec["policy_class"] in ap.POLICY_TABLE_DEFAULTS, rec["policy_class"]
    assert rec["gated"] is True


def test_without_an_explicit_class_the_scan_still_runs(tmp_path, monkeypatch):
    """Callers that cannot say what the action is keep today's behaviour."""
    ap = _store(tmp_path, monkeypatch)
    rec = ap.create_approval(
        kind="tainted_action", subject_type="tool_action",
        subject_id="taint:p5:noclass", title="x",
        action_description='send_email {"to": "a@b.com"}',
        force_gate=True, payload={"tool": "send_email", "input": {}},
        requested_by="taint_gate")
    assert rec["policy_class"] in ap.POLICY_TABLE_DEFAULTS


def test_the_taint_gate_passes_the_class_it_derived():
    import inspect
    from agent_friday.services import agent as ag
    src = inspect.getsource(ag._taint_card)
    assert "action_class=_gate_policy_class(" in src, (
        "_taint_card still lets the label be guessed from the card text")


# ── P7: a local tier only considers local models ───────────────────────────

@pytest.fixture
def local_seat(monkeypatch):
    """bonsai2:27b serving; heavy_hitter bound to a CLOUD model, as reported."""
    from agent_friday.services import tiers
    binding = {"sidekick_fast": "anthropic/claude-sonnet-5",
               "heavy_hitter": "claude-opus-5-5",
               "reasoning": "bonsai2:27b",
               "subagent": "claude-opus-5-5"}
    monkeypatch.setattr(tiers, "_capability", lambda n: binding.get(n, ""))
    monkeypatch.setattr(tiers, "_serving_locally", lambda: {"bonsai2:27b"})
    return tiers


def test_a_local_tier_uses_the_local_binding_not_the_cloud_one(local_seat):
    """The reported refusal. large_local must land on bonsai2:27b."""
    r = local_seat.resolve("large_local")
    assert r.model == "bonsai2:27b", (
        "large_local resolved to %r (%s)" % (r.model, r.reason))


def test_the_refusal_never_contradicts_itself(local_seat, monkeypatch):
    """Nothing local is bound at all: the message must not name a model as
    unloaded while naming the serving seat in the same breath."""
    from agent_friday.services import tiers
    monkeypatch.setattr(tiers, "_capability",
                        lambda n: {"heavy_hitter": "claude-opus-5-5"}.get(n, ""))
    r = tiers.resolve("large_local")
    why = (r.reason or "")
    assert r.model is None, r.model
    assert not ("is not loaded" in why and "bonsai2:27b" in why), (
        "the refusal still reads as a contradiction: %r" % why)
    assert "cloud" in why.lower() or "local" in why.lower(), why


def test_a_local_binding_that_is_not_serving_still_says_so(monkeypatch):
    """The genuinely informative case is untouched: bound locally, not up."""
    from agent_friday.services import tiers
    monkeypatch.setattr(tiers, "_capability",
                        lambda n: {"heavy_hitter": "gemma3:4b"}.get(n, ""))
    monkeypatch.setattr(tiers, "_serving_locally", lambda: {"bonsai2:27b"})
    r = tiers.resolve("large_local")
    assert r.model is None
    assert "not loaded" in (r.reason or ""), r.reason


def test_a_cloud_tier_is_unaffected(local_seat):
    r = local_seat.resolve("cloud_frontier")
    assert r.model in ("claude-opus-5-5", None), r.model


def test_a_failed_residency_probe_still_does_not_refuse(monkeypatch):
    """Could-not-look must stay 'unverified', never a refusal."""
    from agent_friday.services import tiers
    monkeypatch.setattr(tiers, "_capability",
                        lambda n: {"reasoning": "bonsai2:27b"}.get(n, ""))
    monkeypatch.setattr(tiers, "_serving_locally", lambda: set())
    r = tiers.resolve("large_local")
    assert r.model == "bonsai2:27b", r.reason
