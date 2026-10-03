"""Every score change is an event in an append-only, hash-chained log.

Red on main 7c49be86: no log existed, so nothing recorded a score change
with its before and after, and nothing could detect a silent edit. Now a
source observation and a person edit each write an event with `effect`,
editing one byte of an old event fails `verify_chain`, replaying the log
gives the scores the store holds, and an owner correction restores what a
mistaken event moved by naming it rather than rewriting it.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.trust import log as tlog


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    return tmp_path


class TestChain:
    def test_events_chain_and_verify(self, home):
        p = tlog.people_path()
        e1 = tlog.append(p, entity_id="pat", entity_kind="person", kind="owner_statement",
                         origin="owner", detail="first",
                         effect=[{"dimension": "reliability", "before": 0.5, "after": 0.7, "delta": 0.2}])
        e2 = tlog.append(p, entity_id="pat", entity_kind="person", kind="owner_statement",
                         origin="owner", detail="second")
        assert e1["prev_hash"] == tlog.GENESIS and e2["prev_hash"] == e1["hash"]
        assert tlog.verify_chain(p) == {"valid": True, "records": 2, "break_at": None, "reason": None}

    def test_one_changed_byte_breaks_the_chain(self, home):
        p = tlog.people_path()
        tlog.append(p, entity_id="pat", entity_kind="person", kind="owner_statement", origin="owner",
                    detail="kept the Tuesday deadline",
                    effect=[{"dimension": "reliability", "before": 0.5, "after": 0.7, "delta": 0.2}])
        tlog.append(p, entity_id="pat", entity_kind="person", kind="owner_statement", origin="owner",
                    detail="later")
        lines = p.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"after": 0.7', '"after": 0.9')
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        v = tlog.verify_chain(p)
        assert v["valid"] is False and v["break_at"] == 0 and v["reason"] == "hash mismatch"

    def test_kinds_and_origins_are_closed_sets(self, home):
        with pytest.raises(ValueError):
            tlog.append(tlog.people_path(), entity_id="x", kind="guess", origin="owner")
        with pytest.raises(ValueError):
            tlog.append(tlog.people_path(), entity_id="x", kind="observation", origin="web")

    def test_detail_is_short_and_holds_no_copied_content(self, home):
        e = tlog.append(tlog.people_path(), entity_id="x", kind="observation", origin="system",
                        detail="y" * 1000)
        assert len(e["detail"]) == tlog.MAX_DETAIL


class TestSources:
    def test_an_observation_writes_an_event_with_its_effect(self, home):
        from agent_friday.source_trust_graph import SourceTrustGraph
        g = SourceTrustGraph(friday_dir=home)
        g.observe("example.test", "attribution_present", "source_attribution", 0.9, detail="cited")
        events = tlog.read(tlog.sources_path(), entity_id="example.test")
        assert len(events) == 1
        e = events[0]
        assert e["kind"] == "observation" and e["dimension"] == "source_attribution"
        [eff] = e["effect"]
        assert eff["dimension"] == "source_attribution" and eff["after"] > eff["before"]
        assert eff["after"] == g.get("example.test")["scores"]["source_attribution"]

    def test_replaying_the_log_gives_the_stored_scores(self, home):
        from agent_friday.source_trust_graph import SourceTrustGraph
        g = SourceTrustGraph(friday_dir=home)
        for i in range(5):
            g.analyze_fetch([{"source": "a.test", "title": f"Story {i}",
                              "snippet": "per https://x.test/doc the figures", "url": f"https://a.test/{i}"}], [])
        g.observe("a.test", "opinion_labeled", "opinion_separation", 0.9)
        replayed = tlog.replay_scores(tlog.sources_path(), "a.test")
        stored = g.get("a.test")["scores"]
        for dim, val in replayed.items():
            assert stored[dim] == pytest.approx(val, abs=1e-9), dim
        assert "source_attribution" in replayed and "opinion_separation" in replayed

    def test_a_peer_signed_observation_is_an_origin_peer_event(self, home):
        from agent_friday.source_trust_graph import SourceTrustGraph
        g = SourceTrustGraph(friday_dir=home)
        g.observe("example.test", "attribution_present", "source_attribution", 0.9, signed_by="agent-key")
        [e] = tlog.read(tlog.sources_path(), entity_id="example.test")
        assert e["origin"] == "peer"


class TestPeople:
    def test_an_edit_writes_an_event_and_a_correction_restores_it(self, home):
        from agent_friday.people_graph import PeopleGraph
        from agent_friday.trust import people as tp
        pg = PeopleGraph(friday_dir=home)
        pg.add_person("Pat Example")
        person, err = pg.edit("pat_example", scores={"reliability": 0.9},
                              add_evidence={"type": "kept_commitment", "notes": "contract back Tuesday",
                                            "dimension": "reliability"})
        assert err is None
        events = tp.log_for("pat_example")
        kinds = [e["kind"] for e in events]
        assert kinds == ["person_added", "owner_statement"]
        stmt = events[-1]
        [eff] = [e for e in stmt["effect"] if e["dimension"] == "reliability"]
        assert eff["before"] == 0.5 and eff["after"] == 0.9
        out, err = tp.correct_event(stmt["event_id"], friday_dir=home)
        assert err is None and out["restored"] == {"reliability": 0.5}
        assert PeopleGraph(friday_dir=home).load()["people"]["pat_example"]["scores"]["reliability"] == 0.5
        corr = tp.log_for("pat_example")[-1]
        assert corr["kind"] == "owner_correction" and corr["because"] == [stmt["event_id"]]
        assert len(tp.log_for("pat_example")) == 3, "a correction appends; nothing is rewritten"
        assert tlog.verify_chain(tlog.people_path())["valid"]

    def test_a_correction_cannot_be_corrected_again(self, home):
        from agent_friday.people_graph import PeopleGraph
        from agent_friday.trust import people as tp
        pg = PeopleGraph(friday_dir=home)
        pg.add_person("Pat Example")
        pg.edit("pat_example", scores={"competence": 0.2})
        stmt = tp.log_for("pat_example")[-1]
        out, _ = tp.correct_event(stmt["event_id"], friday_dir=home)
        _, err = tp.correct_event(out["event_id"], friday_dir=home)
        assert err and "new statement" in err

    def test_the_log_routes_answer_for_the_owner(self, home):
        # Exercised in tests/api; here the facade shape the routes return.
        from agent_friday.trust import people as tp
        assert tp.log_for("nobody") == []
        assert tp.correct_event("missing")[1].endswith("not found")
