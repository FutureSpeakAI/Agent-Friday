"""CodeQL path-injection triage: three sinks a caller-supplied name could steer.

* a Google account id names one `<16 hex>.token.enc` under the tokens folder;
  any other spelling never reaches the filesystem;
* a podcast episode id is matched whole (a `$` anchor accepts a trailing
  newline);
* restoring a trash entry cannot be pointed at a folder outside the trash, and
  a manifest cannot name a file outside its own entry.
"""
from __future__ import annotations

import json

import pytest


# -- Google account ids -------------------------------------------------------

@pytest.fixture
def google(tmp_path, monkeypatch):
    from agent_friday.services import google_accounts as ga
    tokens = tmp_path / "accounts" / "tokens"
    tokens.mkdir(parents=True)
    monkeypatch.setattr(ga, "TOKENS_DIR", tokens)
    return ga, tokens


@pytest.mark.parametrize("bad", ["../x", "..\\x", "C:x", "0123456789abcdef\n", "", "abc",
                                 "0123456789ABCDEF", "0123456789abcdef/../../x", None, 12])
def test_a_google_account_id_that_is_not_sixteen_hex_digits_names_no_file(google, bad):
    ga, _tokens = google
    with pytest.raises(ValueError):
        ga._token_path(bad)


def test_a_traversing_account_id_never_reads_a_file_outside_the_tokens_folder(google, monkeypatch):
    ga, tokens = google
    outside = tokens.parent.parent / "evil.token.enc"
    outside.write_bytes(b"not a token")
    read = []
    monkeypatch.setattr(ga.cs, "read_secret", lambda p: read.append(p) or b"{}")
    assert ga._raw_credentials("../../evil") is None
    assert read == []


def test_a_real_account_id_still_names_its_token_file(google):
    ga, tokens = google
    aid = ga._account_id("someone@example.com")
    assert ga._token_path(aid) == tokens / (aid + ".token.enc")


# -- podcast episode ids ------------------------------------------------------

def test_a_podcast_id_with_a_trailing_newline_is_not_an_episode(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import podcast_engine as pe
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    good = "20260101T000000-abcdef"
    assert pe._dir(good).name == good
    with pytest.raises(pe.PodcastRefused):
        pe._dir(good + "\n")


# -- media trash restore ------------------------------------------------------

@pytest.fixture
def trash(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import media_tidy as mt
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / "home")
    mt.trash_dir().mkdir(parents=True)
    return mt, tmp_path


def _manifest(folder, files):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")


def test_restore_refuses_an_entry_outside_the_trash(trash):
    mt, tmp = trash
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.txt").write_text("keep", encoding="utf-8")
    _manifest(elsewhere, [{"name": "a.txt", "from": str(tmp / "dest" / "a.txt")}])
    assert mt.restore(str(elsewhere))["status"] == "not_found"
    assert mt.restore("..\\..\\elsewhere")["status"] == "not_found"
    assert mt.restore("../../elsewhere")["status"] == "not_found"
    assert (elsewhere / "a.txt").exists() and not (tmp / "dest").exists()


def test_restore_ignores_a_manifest_name_that_leaves_its_entry(trash):
    mt, tmp = trash
    secret = mt.trash_dir() / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    _manifest(mt.trash_dir() / "e1", [{"name": "..\\secret.txt", "from": str(tmp / "dest" / "s.txt")},
                                      {"name": "../secret.txt", "from": str(tmp / "dest" / "t.txt")}])
    out = mt.restore("e1")
    assert out["status"] == "ok" and out["restored"] == []
    assert secret.exists() and not (tmp / "dest").exists()


def test_restore_still_puts_an_entrys_file_back(trash):
    mt, tmp = trash
    entry = mt.trash_dir() / "e2"
    _manifest(entry, [{"name": "a.txt", "from": str(tmp / "orig" / "a.txt")}])
    (entry / "a.txt").write_text("x", encoding="utf-8")
    out = mt.restore("e2")
    assert out["status"] == "ok" and (tmp / "orig" / "a.txt").read_text(encoding="utf-8") == "x"
