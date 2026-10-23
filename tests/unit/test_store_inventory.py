"""The store inventory (north star §37.3-1): every store Friday keeps, with its
version, its size and where it lives in the data folder. It reads without
writing, never opens a credential store, never follows a link, never names a
file it does not know, and goes into the Doctor report, every Friday Bundle
and every readable export."""
from __future__ import annotations

import builtins
import importlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

PASS = "orbit lantern pepper tundra violet 42"   # pragma: allowlist secret
OWNER_FILE = "Taxes 2026 Wilhelmina.pdf"


def _si():
    assert importlib.util.find_spec("agent_friday.services.store_inventory") is not None, \
        "there is no store inventory"
    return importlib.import_module("agent_friday.services.store_inventory")


def _db(p: Path, *, user_version: int = 0, wal: bool = False, meta: str | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t(x)")
    if meta is not None:
        con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO meta VALUES('version', ?)", (meta,))
    if user_version:
        con.execute("PRAGMA user_version=%d" % user_version)
    con.commit()
    con.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / ".friday"
    h.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(h))
    files = {
        "settings.json": json.dumps({"agent_name": "Friday", "version": 3}),
        "ledger/actions.jsonl": json.dumps({"v": 1, "seq": 1}) + "\n" + json.dumps({"v": 1, "seq": 2}) + "\n",
        "instructions/manifest.json": json.dumps({"version": 1, "sections": []}),
        "wiki/a.md": "# A\n",
        "wiki/notes/b.md": "# B page\n",
        "desktop_cards.json": json.dumps({"version": 2, "cards": []}),
        "people_graph.json": json.dumps({"people": {}}),
        "security/keystore.json": json.dumps({"wrap": "none", "v": 1}),
        # credential stores: listed, never opened
        "providers/keys/anthropic.key": "sk-placeholder-not-real",   # pragma: allowlist secret
        "google_accounts/tokens/me.token.enc": "token-placeholder",
        "mcp_servers.json": json.dumps({"servers": {"x": {"env": {"TOKEN": "placeholder"}}}}),
        "google_token.json": json.dumps({"token": "placeholder"}),
        "secret_key": "session-secret-placeholder",
        # not durable
        "runtime/models/big.gguf": "x" * 1000,
        "friday.log": "a log line\n",
        # the owner's own file, dropped in the folder
        OWNER_FILE: "private",
    }
    for rel, text in files.items():
        p = h / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _db(h / "library" / "owner" / "library.sqlite", user_version=1, wal=True)
    _db(h / "conversations" / "index.sqlite", wal=True, meta="1")
    _db(h / "costs.db")
    (h / "conversations" / "c1").mkdir()
    (h / "conversations" / "c1" / "messages.jsonl").write_text('{"role":"user","text":"hi"}\n', encoding="utf-8")
    return h


def _by(inv):
    return {r["store"]: r for r in inv["stores"]}


def _snapshot(h: Path) -> dict:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in h.rglob("*")}


def test_every_store_has_its_version_size_and_location(home):
    inv = _si().inventory(home)
    s = _by(inv)
    assert s["settings"]["version"] == 3 and s["settings"]["location"] == "settings.json"
    assert s["settings"]["bytes"] == (home / "settings.json").stat().st_size
    assert s["action_ledger"]["version"] == 1 and s["action_ledger"]["location"] == "ledger/"
    assert s["instructions"]["version"] == 1
    assert s["desktop_cards"]["version"] == 2
    assert s["library"]["version"] == 1
    assert s["conversation_index"]["version"] == "1"
    assert s["keystore"]["version"] == 1
    assert s["costs"]["version"] == "none"           # it records none, and says so
    assert s["people_graph"]["version"] == "none"
    assert s["wiki"]["files"] == 2 and s["wiki"]["bytes"] == sum(
        p.stat().st_size for p in (home / "wiki").rglob("*") if p.is_file())
    for r in inv["stores"]:
        assert {"store", "location", "format", "version", "bytes", "files", "modified"} <= set(r), r
    assert "soul" in inv["absent"]                    # known, not created yet
    assert s["runtime"]["durable"] is False and s["settings"]["durable"] is True
    assert inv["totals"]["durable_bytes"] > 0 and inv["totals"]["other_bytes"] >= 1000


def test_it_writes_nothing_and_leaves_no_wal_or_shm_behind(home):
    before = _snapshot(home)
    _si().inventory(home)
    assert _snapshot(home) == before
    assert not list(home.rglob("*-wal")) and not list(home.rglob("*-shm"))


def test_a_credential_store_is_listed_and_never_opened(home, monkeypatch):
    opened = []
    real_open, real_io_open = builtins.open, io.open

    def spy(file, *a, **k):
        opened.append(os.path.normcase(os.path.abspath(str(file))))
        return real_open(file, *a, **k)

    def spy_io(file, *a, **k):
        opened.append(os.path.normcase(os.path.abspath(str(file))))
        return real_io_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy_io)
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda db, *a, **k: (opened.append(str(db)), real_connect(db, *a, **k))[1])
    inv = _si().inventory(home)
    s = _by(inv)
    for name, rel in (("provider_keys", "providers/keys/anthropic.key"),
                      ("google_accounts", "google_accounts/tokens/me.token.enc"),
                      ("mcp_servers", "mcp_servers.json"), ("google_token_legacy", "google_token.json"),
                      ("web_session_secret", "secret_key")):
        assert s[name]["version"] == "not_read", name
        assert s[name]["bytes"] and s[name]["bytes"] > 0, name
        target = os.path.normcase(os.path.abspath(str(home / rel)))
        assert not any(target in o for o in opened), (name, opened)
    assert "placeholder" not in json.dumps(inv)


def test_a_link_is_listed_and_not_followed(home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 5000)
    (outside / "settings.json").write_text(json.dumps({"version": 99}), encoding="utf-8")
    try:
        os.symlink(outside, home / "skills", target_is_directory=True)
        os.symlink(outside, home / "wiki" / "elsewhere", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make symbolic links")
    s = _by(_si().inventory(home))
    assert s["skills"].get("link") is True and s["skills"]["bytes"] is None
    assert s["skills"]["version"] == "not_read_link"
    assert s["wiki"]["files"] == 2 and s["wiki"].get("links_not_followed") == 1


def test_a_name_it_does_not_know_is_counted_never_named(home):
    inv = _si().inventory(home)
    assert inv["unlisted"]["entries"] == 1 and inv["unlisted"]["bytes"] == len("private")
    assert "Taxes" not in json.dumps(inv) and "Wilhelmina" not in json.dumps(inv)


def test_a_version_that_is_not_a_plain_word_is_not_reported_as_it_is(home):
    (home / "settings.json").write_text(json.dumps({"version": "wilhelmina@example.test"}), encoding="utf-8")
    (home / "desktop_cards.json").write_text("{not json", encoding="utf-8")
    s = _by(_si().inventory(home))
    assert s["settings"]["version"] == "unrecognised"
    assert s["desktop_cards"]["version"] == "unreadable"


def test_every_folder_the_bundle_knows_is_a_store_in_the_inventory():
    from agent_friday.services import friday_bundle as fb
    si = _si()
    known = {s.location.rstrip("/").split("/")[0].lower() for s in si.STORES}
    names = set(fb._TOP_CATEGORY) | set(fb._FILE_CATEGORY) | set(fb._EXCLUDED_TOPS) | {"security"}
    missing = sorted(n for n in names if n.lower() not in known)
    assert not missing, missing
    assert len({s.name for s in si.STORES}) == len(si.STORES)
    assert len({s.location.lower() for s in si.STORES}) == len(si.STORES)


def test_the_doctor_report_carries_the_inventory_and_no_unknown_name(home):
    from agent_friday.services import doctor
    rep = doctor.build()
    assert "stores" in rep, "the Doctor report has no store inventory"
    s = _by(rep["stores"])
    assert s["settings"]["version"] == 3 and s["settings"]["location"] == "settings.json"
    assert "Wilhelmina" not in json.dumps(rep)
    text = doctor.render_text(rep)
    assert "Stores (inventory" in text and "settings.json" in text


def test_the_weekly_email_carries_no_store_list(home):
    from agent_friday.services import doctor, doctor_weekly
    rep = doctor.build()
    assert "stores" in rep, "the Doctor report has no store inventory"
    text = doctor_weekly._render(rep, False)
    assert "Stores (inventory" not in text and "settings.json" not in text


def test_every_bundle_manifest_carries_the_inventory(home, tmp_path, monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import friday_bundle as fb
    monkeypatch.setitem(sys.modules, "keyring", None)
    m = fb.create(tmp_path / "out" / "b.fridaybundle", PASS, home=home, categories=("memory",),
                  kdf_profile=vc.FAST_PROFILE)
    inv = m["stores"]
    assert isinstance(inv, dict) and "stores" in inv, inv
    s = _by(inv)
    assert s["settings"]["version"] == 3 and s["action_ledger"]["location"] == "ledger/"
    assert s["provider_keys"]["version"] == "not_read"


def test_every_readable_export_manifest_carries_the_inventory(home, tmp_path):
    from agent_friday.services import readable_export
    r = readable_export.export(tmp_path / "out", only=["settings"])
    man = json.loads((Path(r["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    assert "stores" in man, "the export manifest has no store inventory"
    assert _by(man["stores"])["settings"]["version"] == 3
