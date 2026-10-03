"""The Library reads only what the owner added; a deny beats an add; a tampered
ledger suspends the Library; and an add is never a cloud grant."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.library_fixtures import isolate_library, release_library, write_docs


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    yield
    release_library(fg, lstore)


def _docs(tmp_path):
    return write_docs(tmp_path / "Reporting", {
        "memo.txt": "The tenant shall pay rent monthly.\n\nNotice of ninety days is required.",
        "sub/deep.txt": "A deeper note about the lease.",
    }) | write_docs(tmp_path / "Other", {"private.txt": "Not added to anything."})


# -- test_library_indexes_only_what_was_added ----------------------------------

def test_an_empty_library_reads_nothing(tmp_path):
    from agent_friday.services.library import grants, runtime
    d = _docs(tmp_path)
    assert grants.active_scopes("owner") == []
    assert not grants.allowed("owner", d["memo.txt"])
    assert runtime.resweep("owner") == 0


def test_only_added_folders_are_indexed_and_the_rest_is_purged(tmp_path):
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    d = _docs(tmp_path)
    ev = grants.add_scope("owner", str(tmp_path / "Reporting"), source="you")
    assert ev["event"] == "library_add" and "expires_ts" not in ev
    assert grants.allowed("owner", d["memo.txt"]) and grants.allowed("owner", d["sub/deep.txt"])
    assert not grants.allowed("owner", d["private.txt"])
    st = store_for("owner")
    out = indexer.sweep_scope(st, tmp_path / "Reporting", allowed=lambda p: grants.allowed("owner", p))
    assert out["indexed"] == 2
    # A document no consent covers is not kept.
    indexer.index_file(st, d["private.txt"], tmp_path / "Other")
    from agent_friday.services.library import runtime
    assert runtime.purge_uncovered("owner") == 1
    assert [r["title"] for r in st.list_documents()] == ["deep", "memo"]


def test_a_non_recursive_add_stops_at_the_folder(tmp_path):
    from agent_friday.services.library import grants
    d = _docs(tmp_path)
    grants.add_scope("owner", str(tmp_path / "Reporting"), recursive=False)
    assert grants.allowed("owner", d["memo.txt"])
    assert not grants.allowed("owner", d["sub/deep.txt"])


def test_removing_a_consent_takes_effect_before_any_purge(tmp_path):
    from agent_friday.services.library import grants
    d = _docs(tmp_path)
    ev = grants.add_scope("owner", str(tmp_path / "Reporting"))
    assert grants.allowed("owner", d["memo.txt"])
    grants.remove_scope("owner", ev["id"])
    assert not grants.allowed("owner", d["memo.txt"])


# -- test_library_deny_beats_add -------------------------------------------------

def test_a_deny_mark_beats_an_add(tmp_path):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    d = _docs(tmp_path)
    grants.add_scope("owner", str(tmp_path / "Reporting"))
    fg.create_deny_mark(str(d["memo.txt"]), "file")
    assert not grants.allowed("owner", d["memo.txt"])
    assert grants.allowed("owner", d["sub/deep.txt"])
    with pytest.raises(Exception, match="never-send"):
        grants.add_scope("owner", str(d["memo.txt"]))


# -- test_library_ledger_tamper_suspends_library ---------------------------------

def test_a_tampered_ledger_suspends_the_library_but_not_denies(tmp_path):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    d = _docs(tmp_path)
    grants.add_scope("owner", str(tmp_path / "Reporting"))
    fg.create_deny_mark(str(d["sub/deep.txt"]), "file")
    assert grants.allowed("owner", d["memo.txt"]) and not grants.suspended()
    ledger = fg._ledger_path()
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["event"]["path"] = str(tmp_path / "Other")           # edited after signing
    lines[0] = json.dumps(rec)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fg._invalidate_cache()
    assert grants.suspended()
    assert not grants.allowed("owner", d["memo.txt"])
    assert not grants.allowed("owner", d["private.txt"])
    assert fg.check_grant(d["sub/deep.txt"]).state == "denied"      # the deny still binds


# -- test_library_add_is_not_a_cloud_grant ----------------------------------------

def test_an_added_document_is_not_sendable_to_a_cloud_model(tmp_path):
    from agent_friday.services import egress_gate as eg
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    text = "Patient SSN 123-45-6789 was treated for a chronic condition in March."  # pragma: allowlist secret
    d = write_docs(tmp_path / "Lib", {"chart.txt": text})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    st = store_for("owner")
    indexer.sweep_scope(st, tmp_path / "Lib", allowed=lambda p: grants.allowed("owner", p))
    passage = st.q("SELECT text FROM passages")[0]["text"]
    assert fg.check_grant(d["chart.txt"]).state == "none"
    out = eg._gate_text(passage, "anthropic", "tool_result")
    assert "123-45-6789" not in out            # withheld whole: the gate returns nothing, not the text  # pragma: allowlist secret


def test_the_add_events_are_in_the_signed_ledger_and_name_their_source(tmp_path):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    _docs(tmp_path)
    grants.add_scope("owner", str(tmp_path / "Reporting"), source="card")
    recs = [json.loads(x) for x in fg._ledger_path().read_text(encoding="utf-8").splitlines()]
    assert recs[0]["hmac"] and recs[0]["event"]["source"] == "card"
    assert recs[0]["event"]["principal"] == "owner"


def test_unsafe_and_too_broad_paths_are_refused(tmp_path):
    from agent_friday.services.library import grants
    for bad in ("\\\\server\\share\\x", "https://example.com/a", "relative/path"):
        assert not grants.describe(bad)["ok"]
    assert not grants.describe(str(tmp_path / "missing"))["ok"]
    assert not grants.describe(str(Path.home()))["ok"]


def test_shelf_choices_are_logged_and_read_back(tmp_path):
    from agent_friday.services.library import grants
    d = _docs(tmp_path)
    assert grants.shelf_override("owner", d["memo.txt"]) is None
    grants.set_shelf("owner", str(d["memo.txt"]), "vault")
    assert grants.shelf_override("owner", d["memo.txt"]) == "vault"
    grants.set_shelf("owner", str(d["memo.txt"]), "open")
    assert grants.shelf_override("owner", d["memo.txt"]) == "open"
