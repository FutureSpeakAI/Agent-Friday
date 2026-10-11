"""The Library keeps what it worked out about a document's refusal, and nothing about consent.

A search asks the consent check about every indexed document. The part that costs is the
refusal (the never-send list, the credential places and names, judged on the resolved path), so
that part is kept, per document. The owner's consents (adds, removals, expiry) are read live on
every ask. These tests hold the kept answers to the from-scratch answers on a synthetic tree, and
hold every way a kept answer could go stale: a revoke, an expiry, a new deny mark, a changed
rule, a retargeted link, a rewritten credential file."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.library_fixtures import install_fake_encoder, isolate_library, release_library, write_docs

PEM_HEAD = "-----BEGIN RSA PRIVATE " + "KEY-----"  # pragma: allowlist secret
PEM_TAIL = "-----END RSA PRIVATE " + "KEY-----"  # pragma: allowlist secret
SLIDES = "Speaker notes for the quarterly review deck.\n"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    from agent_friday.services import media_index
    from agent_friday.services.library import grants
    monkeypatch.setattr(media_index, "_roots", lambda: [])
    grants._TRACKED_CACHE.clear()
    grants.forget_kept()
    yield
    grants._TRACKED_CACHE.clear()
    grants.forget_kept()
    release_library(fg, lstore)


def _reference(principal: str, path) -> bool:
    """The consent check as it was before anything was kept: every part worked out on this ask."""
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants, indexer
    path = Path(path)
    if fg.check_grant(path).state == "denied":
        return False
    try:
        rp = path.resolve()
    except OSError:
        return False
    if indexer._refused(rp):
        return False
    return any(grants._covers(a, rp) for a in grants.active_scopes(principal))


def _tree(tmp_path) -> list[Path]:
    """A synthetic Library: plain notes, credential-named files, a file and a folder to be denied,
    a .key file that is only a slide deck, enough bulk to take the batch path, and files nothing covers."""
    docs = {"a.txt": "alpha", "one.txt": "to be denied", "sub/b.txt": "beta", "sub/deeper/c.txt": "gamma",
            "secret/.env": "KEY=value", "keys/id_rsa": "not a real key", "denied_dir/x.txt": "inside a denied folder",
            "deck.key": SLIDES}
    docs |= {f"bulk/f{i:02d}.txt": f"bulk {i}" for i in range(40)}
    out = list(write_docs(tmp_path / "Lib", docs).values())
    out += list(write_docs(tmp_path / "Other", {"z.txt": "nothing covers this"}).values())
    out.append(tmp_path / "Lib" / "missing.txt")                    # not on disk at all
    return out


def _agree(paths, *, label: str, held=None) -> dict:
    """The batch check, the single check and a long-lived batch all give the from-scratch answer."""
    from agent_friday.services.library import grants
    want = {p: _reference("owner", p) for p in paths}
    batch = grants.allower("owner")
    batch.prime(paths)
    got_batch = {p: batch(p) for p in paths}
    got_one = {p: grants.allowed("owner", p) for p in paths}
    assert got_batch == want, label + ": batch differs from the from-scratch check"
    assert got_one == want, label + ": single ask differs from the from-scratch check"
    if held is not None:
        assert {p: held(p) for p in paths} == want, label + ": a long-lived batch differs"
    return want


def test_kept_answers_equal_the_from_scratch_answers_for_allowed_denied_and_revoked(tmp_path, monkeypatch):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    monkeypatch.setattr(grants, "_RECHECK_S", 0.0)                  # the long-lived batch looks at the ledger every ask
    paths = _tree(tmp_path)
    held = grants.allower("owner")

    want = _agree(paths, label="nothing added", held=held)
    assert not any(want.values())

    add = grants.add_scope("owner", str(tmp_path / "Lib"))
    want = _agree(paths, label="added", held=held)
    assert want[tmp_path / "Lib" / "a.txt"] and want[tmp_path / "Lib" / "sub" / "deeper" / "c.txt"]
    assert want[tmp_path / "Lib" / "deck.key"], "a .key that holds no key is a document"
    assert not want[tmp_path / "Lib" / "secret" / ".env"] and not want[tmp_path / "Lib" / "keys" / "id_rsa"]
    assert not want[tmp_path / "Other" / "z.txt"]

    fg.create_deny_mark(str(tmp_path / "Lib" / "one.txt"), "file")
    fg.create_deny_mark(str(tmp_path / "Lib" / "denied_dir"), "folder")
    want = _agree(paths, label="denied", held=held)
    assert not want[tmp_path / "Lib" / "one.txt"] and not want[tmp_path / "Lib" / "denied_dir" / "x.txt"]
    assert want[tmp_path / "Lib" / "a.txt"]

    grants.remove_scope("owner", add["id"])                         # revoked, then asked
    want = _agree(paths, label="revoked", held=held)
    assert not any(want.values())

    grants.add_scope("owner", str(tmp_path / "Lib"), recursive=False)
    want = _agree(paths, label="shallow", held=held)
    assert want[tmp_path / "Lib" / "a.txt"] and not want[tmp_path / "Lib" / "sub" / "b.txt"]


def test_an_expired_consent_stops_covering_even_inside_a_long_lived_batch(tmp_path, monkeypatch):
    """The tracked consent covers a granted folder only until the grant's expiry; the batch notices the
    moment it passes, whatever else it does or does not re-read."""
    import time
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    d = write_docs(tmp_path / "Granted", {"brief.txt": "A granted folder's brief."})
    fg.create_scope_grant(str(tmp_path / "Granted"), "folder", 1)
    grants.add_tracked("owner")
    monkeypatch.setattr(grants, "_RECHECK_S", 3600.0)                # only the expiry can make it look again
    paths = [d["brief.txt"]]
    held = grants.allower("owner")
    assert _agree(paths, label="before expiry", held=held)[paths[0]]

    real, real_mono = time.time, time.monotonic
    monkeypatch.setattr(time, "time", lambda: real() + 2 * 86400)   # two days on: the one-day grant is over
    # and past the 5 s the tracked-roots lists are kept for (that cache is older than the Library
    # cache and applies to the from-scratch check too), with no manual clearing
    monkeypatch.setattr(time, "monotonic", lambda: real_mono() + 10.0)
    want = _agree(paths, label="after expiry", held=held)
    assert want[paths[0]] is False and held(paths[0]) is False


def test_a_removed_or_revoked_consent_is_refused_straight_after(tmp_path):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    d = write_docs(tmp_path / "Lib", {"memo.txt": "memo"})
    ev = grants.add_scope("owner", str(tmp_path / "Lib"))
    held = grants.allower("owner")
    assert held(d["memo.txt"]) and grants.allowed("owner", d["memo.txt"])
    grants.remove_scope("owner", ev["id"])
    assert not grants.allowed("owner", d["memo.txt"])
    assert not grants.allower("owner")(d["memo.txt"])
    ev2 = grants.add_scope("owner", str(tmp_path / "Lib"))
    assert grants.allowed("owner", d["memo.txt"])
    fg.revoke(ev2["id"])                                            # the other way a consent ends
    assert not grants.allowed("owner", d["memo.txt"])


def test_a_new_deny_mark_binds_the_next_ask_and_a_revoked_one_frees_it(tmp_path):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    d = write_docs(tmp_path / "Lib", {"memo.txt": "memo", "other.txt": "other"})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    assert grants.allowed("owner", d["memo.txt"])                   # kept as "not refused"
    mark = fg.create_deny_mark(str(d["memo.txt"]), "file")
    assert not grants.allowed("owner", d["memo.txt"]) and grants.allowed("owner", d["other.txt"])
    fg.revoke(mark["id"])
    assert grants.allowed("owner", d["memo.txt"])


def test_a_changed_rule_drops_what_was_kept(tmp_path, monkeypatch):
    from agent_friday.services import studio_files as sf
    from agent_friday.services.library import grants
    d = write_docs(tmp_path / "Lib", {"sub/b.txt": "beta", "a.txt": "alpha"})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    assert grants.allowed("owner", d["sub/b.txt"])
    real = sf._excluded
    keep = [os.path.normcase(str((tmp_path / "Lib" / "sub").resolve()))]
    monkeypatch.setattr(sf, "_excluded", lambda *a: keep)           # a place the Library never reads
    assert not grants.allowed("owner", d["sub/b.txt"]) and grants.allowed("owner", d["a.txt"])
    assert not _reference("owner", d["sub/b.txt"])
    monkeypatch.setattr(sf, "_excluded", real)
    assert grants.allowed("owner", d["sub/b.txt"])


def test_a_rewritten_credential_file_is_judged_again(tmp_path):
    from agent_friday.services.library import grants
    d = write_docs(tmp_path / "Lib", {"deck.key": SLIDES})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    assert grants.allowed("owner", d["deck.key"])                   # holds no key: kept as "not refused"
    d["deck.key"].write_text(PEM_HEAD + "\nsyntheticbody\n" + PEM_TAIL + "\n", encoding="utf-8")
    assert not _reference("owner", d["deck.key"])
    assert not grants.allowed("owner", d["deck.key"]), "a file rewritten into a key was still served as allowed"


def test_a_key_file_rewritten_with_the_same_size_and_time_is_still_judged(tmp_path):
    """A .key is judged by what it holds, so it is never kept: a rewrite that keeps the size and puts the
    modified time back must not be served from before."""
    from agent_friday.services.library import grants
    body = PEM_HEAD + "\nsyntheticbody\n" + PEM_TAIL + "\n"
    d = write_docs(tmp_path / "Lib", {"deck.key": "s" * len(body)})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    p = d["deck.key"]
    before = os.stat(p)
    batch = grants.allower("owner")
    assert batch(p) and grants.allowed("owner", p)
    p.write_text(body, encoding="utf-8", newline="")
    os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(p)
    assert after.st_size == before.st_size and after.st_mtime_ns == before.st_mtime_ns
    assert not _reference("owner", p)
    assert not batch(p) and not grants.allowed("owner", p)
    assert not any(k[1] == str(p) for k in grants._KEPT), "a content-judged name was kept"


def test_a_mark_landing_while_the_batch_is_built_is_not_missed(tmp_path, monkeypatch):
    """The scopes, the never-send fingerprint and the revision come from one ledger read; a deny that
    lands in the middle makes the build read again instead of keeping the old fingerprint."""
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    d = write_docs(tmp_path / "Lib", {"memo.txt": "memo"})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    assert grants.allowed("owner", d["memo.txt"])                   # kept as "not refused"
    real, fired = grants.active_scopes, []

    def scopes_then_a_mark_lands(*a, **k):
        out = real(*a, **k)
        if not fired:
            fired.append(fg.create_deny_mark(str(d["memo.txt"]), "file"))
        return out
    monkeypatch.setattr(grants, "active_scopes", scopes_then_a_mark_lands)
    batch = grants.allower("owner")
    assert fired and batch.ok
    assert not batch(d["memo.txt"]) and not _reference("owner", d["memo.txt"])


def _link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        if subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode == 0:
            return
    pytest.skip("this machine cannot make a directory link")


def _unlink(link: Path) -> None:
    try:
        os.rmdir(link)
    except OSError:
        os.unlink(link)


def test_a_retargeted_link_is_checked_again(tmp_path, monkeypatch):
    """A document reached through a link is judged on where the link leads NOW. Pointing the link at a
    denied folder must refuse it, with a fresh batch and with a long-lived one."""
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import grants
    monkeypatch.setattr(grants, "_RECHECK_S", 3600.0)
    write_docs(tmp_path / "Lib", {"plain/doc.txt": "plain", "Denied/doc.txt": "denied"})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    fg.create_deny_mark(str(tmp_path / "Lib" / "Denied"), "folder")
    link = tmp_path / "Lib" / "link"
    _link(link, tmp_path / "Lib" / "plain")
    via = link / "doc.txt"

    held = grants.allower("owner")
    assert held(via) and grants.allowed("owner", via)               # kept as "not refused" under this spelling
    _unlink(link)
    _link(link, tmp_path / "Lib" / "Denied")
    assert not _reference("owner", via)
    assert not held(via), "a long-lived batch served the old target"
    assert not grants.allowed("owner", via) and not grants.allower("owner")(via)
    _unlink(link)
    _link(link, tmp_path / "Lib" / "plain")
    assert held(via) and grants.allowed("owner", via)               # and back


def test_the_kept_refusals_are_bounded(tmp_path, monkeypatch):
    from agent_friday.services.library import grants
    monkeypatch.setattr(grants, "_KEPT_MAX", 40)
    d = write_docs(tmp_path / "Lib", {f"n{i:03d}.txt": str(i) for i in range(120)})
    grants.add_scope("owner", str(tmp_path / "Lib"))
    batch = grants.allower("owner")
    batch.prime(list(d.values()))
    assert all(batch(p) for p in d.values())
    assert 0 < len(grants._KEPT) <= 40


def _count(monkeypatch):
    """How many times a document's refusal is worked out from scratch."""
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import indexer
    calls = {"deny": 0, "refused": 0}
    real_check, real_refused = fg.check_grant, indexer._refused

    def check(*a, **k):
        calls["deny"] += 1
        return real_check(*a, **k)

    def refused(*a, **k):
        calls["refused"] += 1
        return real_refused(*a, **k)
    monkeypatch.setattr(fg, "check_grant", check)
    monkeypatch.setattr(indexer, "_refused", refused)
    return calls


def test_a_second_search_does_not_work_the_refusals_out_again(tmp_path, monkeypatch):
    from agent_friday.services.library import grants, indexer, tree
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, {f"d{i:02d}.txt": f"Document number {i} about topic{i}." for i in range(12)})
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    grants.forget_kept()                                            # the sweep above asked about each of them
    calls = _count(monkeypatch)
    first = tree.TreeBuilder(st, "owner").visible_docs()
    assert len(first) == 12 and calls["refused"] >= 12
    calls["deny"] = calls["refused"] = 0
    second = tree.TreeBuilder(st, "owner").visible_docs()
    assert sorted(second) == sorted(first)
    assert calls == {"deny": 0, "refused": 0}, "the second search worked every refusal out again"


def test_revoking_a_scope_leaves_the_document_out_of_the_next_search(tmp_path, monkeypatch):
    from agent_friday.services.library import grants, indexer, search, shelf, tree
    from agent_friday.services.library.store import store_for
    install_fake_encoder(monkeypatch)
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    write_docs(tmp_path / "Leases", {"lease.txt": "The tenant shall pay rent monthly to the landlord.\n\nEnd with ninety days notice."})
    write_docs(tmp_path / "Recipes", {"bread.txt": "Bake the bread at four hundred degrees for thirty minutes."})
    a = grants.add_scope("owner", str(tmp_path / "Leases"))
    grants.add_scope("owner", str(tmp_path / "Recipes"))
    st = store_for("owner")
    for root in (tmp_path / "Leases", tmp_path / "Recipes"):
        indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))

    def titles():
        return sorted(r["title"] for r in tree.TreeBuilder(st, "owner").visible_docs().values())
    assert titles() == ["bread", "lease"]
    first = search.run("how much notice to end the lease")
    assert any(e["doc"] == "lease" for e in first["evidence"])

    grants.remove_scope("owner", a["id"])
    assert titles() == ["bread"]
    after = search.run("how much notice to end the lease")
    assert all(e["doc"] != "lease" for e in after["evidence"])
