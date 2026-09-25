"""Picking a model for a seat takes effect, and says so.

A pick in Settings > Intelligence must persist AND be visible: POST
/api/settings returns 200, settings.json holds the new model, the flat
`orchestrator_model` mirror updates, other seats are untouched, it survives a
reload, and `/api/intelligence` -- the panel's own data source -- reports the
new seat.

The CONFIRMATION is the fragile part. The panel renders from /api/intelligence,
which has profiled at **32.7s cold and 12.6s warm** against the **30s
AbortController** that `useIntelligence` sets on itself. A refresh that aborts,
or has not landed when the user looks, leaves the seat on screen unchanged, so
a working save reads as "nothing changed" and the panel as "times out".

These tests pin the behaviour end to end, so a regression cannot hide behind a
slow panel.
"""

import pytest

PICK = "claude-opus-5-5"
SEATS = ("reasoning", "subagent")


def _seat_in_settings(client, seat):
    d = client.get("/api/settings").get_json() or {}
    return (((d.get("settings") or {}).get("capability_routing") or {})
            .get(seat) or {}).get("model")


def _seat_in_panel(client, seat):
    """What Settings > Intelligence would render: a row in `roles`, keyed by
    `key`. The panel reads this route, not /api/settings."""
    d = client.get("/api/intelligence").get_json() or {}
    for row in (d.get("roles") or []):
        if isinstance(row, dict) and row.get("key") == seat:
            return row.get("model")
    return None


def _pick(client, seat, model=PICK):
    return client.post("/api/settings", json={
        "settings": {"capability_routing": {seat: {"model": model}}}})


@pytest.mark.parametrize("seat", SEATS)
def test_picking_opus_5_5_is_accepted_and_persisted(client, seat):
    r = _pick(client, seat)
    assert r.status_code == 200, r.get_json()
    assert _seat_in_settings(client, seat) == PICK


@pytest.mark.parametrize("seat", SEATS)
def test_the_panels_own_data_source_reports_the_pick(client, seat):
    """The exact link that appeared broken. If this passes and the screen still
    does not change, the fault is the panel's refresh, not the save."""
    _pick(client, seat)
    assert _seat_in_panel(client, seat) == PICK


def test_both_seats_can_hold_it_at_once(client):
    """"either seat" -- setting one must not clear the other."""
    for seat in SEATS:
        assert _pick(client, seat).status_code == 200
    for seat in SEATS:
        assert _seat_in_settings(client, seat) == PICK


def test_the_pick_survives_a_reload(client):
    """Persistence, not just an in-memory write. The settings cache is dropped
    so the next read comes off disk, which is what a restart does."""
    import agent_friday.core as core

    for seat in SEATS:
        _pick(client, seat)
    core._SETTINGS_CACHE["ts"] = 0
    core._SETTINGS_CACHE["value"] = None
    for seat in SEATS:
        assert _seat_in_settings(client, seat) == PICK


def test_the_flat_mirror_follows_the_seat(client):
    """`orchestrator_model` is the legacy flat key several call sites still read.
    `_sync_capability_routing` mirrors the seat into it; if that stopped, half
    the app would keep using the old model while the panel showed the new one."""
    _pick(client, "reasoning")
    d = client.get("/api/settings").get_json() or {}
    assert (d.get("settings") or {}).get("orchestrator_model") == PICK


def test_picking_one_seat_leaves_the_others_alone(client):
    """capability_routing is deep-merged. A shallow write would reset every
    untouched seat to its factory default."""
    before = client.get("/api/settings").get_json() or {}
    other = ((before.get("settings") or {}).get("capability_routing") or {})
    keep = {k: (v or {}).get("model") for k, v in other.items()
            if k not in SEATS}
    _pick(client, "reasoning")
    after = client.get("/api/settings").get_json() or {}
    now = ((after.get("settings") or {}).get("capability_routing") or {})
    for k, was in keep.items():
        assert (now.get(k) or {}).get("model") == was, \
            "seat %r changed from %r to %r" % (k, was, (now.get(k) or {}).get("model"))


def test_the_next_turn_would_route_to_the_pick(client):
    """The router's own resolver, not a settings read. `cloud_frontier` resolves
    from the `reasoning` capability, which is what a cloud turn consults."""
    from agent_friday.services import tiers

    _pick(client, "reasoning")
    import agent_friday.core as core
    core._SETTINGS_CACHE["ts"] = 0
    res = tiers.resolve("cloud_frontier")
    assert res.model == PICK, "tier resolved to %r (%s)" % (res.model, res.reason)


@pytest.fixture
def one_installed_model(monkeypatch):
    """A READABLE installed list holding exactly one model.

    The gate deliberately allows a save when it cannot read the list at all
    (Ollama down AND an empty store), because refusing on an unknown would lock
    someone out of a control that may be perfectly valid. This machine is in
    exactly that state — so a test that merely picks a missing model asserts
    something about the MACHINE rather than about the gate, which is the trap the
    nemo availability test already documents. Give the gate something to read.
    """
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "installed",
                        lambda force=False: [("gemma4:e2b", 1)])
    return "gemma4:e2b"


def test_a_pick_that_cannot_be_honoured_is_refused_with_a_reason(
        client, one_installed_model):
    """A silent refusal reads as "nothing changed", so it must carry a sentence."""
    r = _pick(client, "reasoning", "gemma4:definitely-not-installed-9z")
    assert r.status_code == 400, \
        "accepted a seat for a model that is not installed"
    body = r.get_json() or {}
    reason = body.get("detail") or body.get("error") or body.get("message")
    assert reason, "refused without saying why: %s" % body
    assert len(str(reason)) > 15, "refusal reason is not a sentence: %r" % reason
    assert one_installed_model in str(reason), \
        "a refusal should say what IS installed, not only what is not"
    # ...and the refusal must not have changed anything.
    assert _seat_in_settings(client, "reasoning") != \
        "gemma4:definitely-not-installed-9z"


def test_a_seat_write_with_no_provider_is_still_checked(client,
                                                        one_installed_model):
    """The regression this change closes.

    `_check_seat_installed` only fired when the caller DECLARED a local
    provider, so a write that omitted `provider` skipped the check entirely and
    a local model with no weights was accepted with 200. index.html does omit
    it: catalog entries carry `provider` (singular) while the UI reads
    `m.providers` (plural), so the key arrives undefined and JSON.stringify
    drops it. The body below sends no provider, exactly as the UI does.
    """
    r = client.post("/api/settings", json={
        "settings": {"capability_routing": {
            "reasoning": {"model": "gemma4:definitely-not-installed-9z"}}}})
    assert r.status_code == 400, \
        "a provider-less seat write bypassed the installed check"


def test_a_cloud_gateway_id_is_not_asked_to_prove_it_is_installed(
        client, one_installed_model):
    """The other half of inferring locality: a gateway id always contains '/',
    is never local, and must not be refused for not being on disk."""
    r = client.post("/api/settings", json={
        "settings": {"capability_routing": {
            "reasoning": {"model": "anthropic/claude-opus-5.5"}}}})
    assert r.status_code == 200, r.get_json()


def test_the_panel_labels_how_fresh_its_residency_read_is(client):
    """The residency block is snapshotted now, so the payload has to say whether
    what it is showing is live, stale, or not yet read -- otherwise a cached
    reading is indistinguishable from a current one."""
    d = client.get("/api/intelligence").get_json() or {}
    assert "residency_reading" in d
    assert d["residency_reading"] in ("fresh", "stale", "unknown")
    if d["residency_reading"] != "fresh":
        assert d.get("residency_age"), "a non-live read must state its age"
