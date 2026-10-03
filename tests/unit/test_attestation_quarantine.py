"""A peer attestation moves no local source score while the agent kind is held.

Red on main 7c49be86: `/api/federation/attestations/import` sat on the news
blueprint (outside the Federation switch), an attestation verified against
the key it declared itself, and `_apply_to_graph` folded it into local
scores at full weight. Any key holder who could reach the import could move
the owner's source trust. Imported observations now go to a quarantine
lane at weight zero until the agent kind is enabled and the peer is an
attested agent entity.
"""
from __future__ import annotations

import pytest

nacl = pytest.importorskip("nacl.signing")

from agent_friday import source_trust_federation as stf  # noqa: E402
from agent_friday.source_trust_graph import SourceTrustGraph  # noqa: E402
from agent_friday.trust import agents as tagents  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    import agent_friday.source_trust_graph as stg
    monkeypatch.setattr(stg, "_instance", None)
    return tmp_path


def _self_signed(home, domain="peerclaims.test"):
    """An attestation signed by this machine's own key: a 'self-declared' peer."""
    att = stf.sign_attestation(domain, {"type": "claim_verified", "claim": "the figure was right",
                                        "evidence": "a document", "counter_sources": []},
                               friday_dir=home)
    assert att and stf.verify_attestation(att, friday_dir=home)
    return att


def test_a_valid_self_declared_attestation_changes_no_score(home):
    g = SourceTrustGraph(friday_dir=home)
    g.observe("peerclaims.test", "attribution_present", "source_attribution", 0.9)
    before = dict(g.get("peerclaims.test")["scores"])
    out = stf.import_attestation(_self_signed(home), friday_dir=home)
    assert out["accepted"] is True, out   # stored, as before
    after = dict(SourceTrustGraph(friday_dir=home).get("peerclaims.test")["scores"])
    assert after == before, "a peer attestation moved a local score while Federation is held"
    assert tagents.quarantined_count() == 1
    obs = [o for o in SourceTrustGraph(friday_dir=home).get("peerclaims.test")["observations"]
           if o.get("signed_by") not in ("local", "user")]
    assert obs == [], "a peer-signed observation reached the record"


def test_the_quarantine_row_carries_weight_zero_and_no_claim_text_beyond_the_observation(home):
    stf.import_attestation(_self_signed(home), friday_dir=home)
    rows = tagents.quarantine_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1 and '"weight": 0.0' in rows[0]


def test_the_quarantine_holds_until_both_switches_are_on_and_the_peer_is_attested(home, monkeypatch):
    """Red by the named mutation: delete the `_agents.enabled()` check in
    `_apply_to_graph` and this test's first assertion fails."""
    from agent_friday.services import held_features as hf
    monkeypatch.setattr(hf, "enabled", lambda feature, settings=None: feature == hf.FEDERATION)
    g = SourceTrustGraph(friday_dir=home)
    g.observe("peerclaims.test", "attribution_present", "source_attribution", 0.9)
    before = dict(g.get("peerclaims.test")["scores"])
    stf.import_attestation(_self_signed(home), friday_dir=home)
    assert dict(SourceTrustGraph(friday_dir=home).get("peerclaims.test")["scores"]) == before
    # Both switches on, but no attested agent entity for this key: still held.
    monkeypatch.setattr(hf, "enabled", lambda feature, settings=None: True)
    att = _self_signed(home, "second.test")
    g.observe("second.test", "attribution_present", "source_attribution", 0.9)
    before2 = dict(SourceTrustGraph(friday_dir=home).get("second.test")["scores"])
    stf.import_attestation(att, friday_dir=home)
    assert dict(SourceTrustGraph(friday_dir=home).get("second.test")["scores"]) == before2
    assert tagents.quarantined_count() == 2
