"""Publish to web (docs/design/active/vibe-coding-salon.md §4.10.1, Phase 1b).

An artifact becomes a self-contained static bundle that runs entirely in the
visitor's browser, with no backend, no secrets and no tracking. Publishing is
an outward action: ONE approval card, with the file list, the scan result and
the licence check; nothing leaves before the card is approved; and every
publication is recorded with a receipt. "This PC" writes the bundle into a
read-only published/ folder that a separate static server serves.
"""
from __future__ import annotations

import json
import re

import pytest

from agent_friday.services import approvals
from agent_friday.services import artifacts as art
from agent_friday.services import publish_web as pw

CID = "conv-publish-test"


@pytest.fixture(autouse=True)
def _stores(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    monkeypatch.setattr(pw, "_published_root", lambda: tmp_path / "published")
    monkeypatch.setattr(pw, "_staging_root", lambda: tmp_path / "staging")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    # No network for the licence lookup unless a test provides one.
    monkeypatch.setattr(pw, "_fetch_package_json", lambda url: None)
    # Hosting is another module's job and spawns processes; none here.
    from agent_friday.services import publish_hosting as ph
    monkeypatch.setattr(ph, "ensure_started", lambda: {"serving": False})
    monkeypatch.setattr(ph, "public_base_url", lambda: None)
    pw._reset_for_tests()
    yield


def _md(title="Letter", text="# Hello\n\nA *draft*.\n"):
    return art.put(CID, kind="markdown", title=title, content=text)


# ── the bundle ───────────────────────────────────────────────────────────────

def test_a_markdown_artifact_packs_into_a_self_contained_page():
    rec = _md()
    b = pw.pack(rec)
    assert set(b.files) >= {"index.html", "source.md"}
    html = b.files["index.html"].decode()
    assert "<h1>Hello</h1>" in html and "<em>draft</em>" in html
    assert html.index("Content-Security-Policy") < html.index("<h1>")
    assert "default-src 'none'" in html and "form-action 'none'" in html
    assert 'name="referrer" content="no-referrer"' in html
    from agent_friday import brand
    assert brand.MADE_WITH in html, "the mark credits the product by its name"
    assert b.slug == "letter"
    assert b.size == sum(len(v) for v in b.files.values())


def test_the_mark_is_optional():
    rec = _md()
    from agent_friday import brand
    assert brand.MADE_WITH not in pw.pack(rec, mark=False).files["index.html"].decode()


def test_an_html_app_keeps_its_scripts_and_gets_the_frame_csp():
    rec = art.put(CID, kind="html", title="Counter",
                  content="<!doctype html><html><head></head><body><button id=b>0</button>"
                          "<script>b.onclick=()=>b.textContent++</script></body></html>")
    html = pw.pack(rec).files["index.html"].decode()
    assert "b.onclick" in html
    m = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert m and "script-src 'unsafe-inline' 'wasm-unsafe-eval' https://esm.sh" in m.group(1)
    assert "connect-src https://esm.sh" in m.group(1)
    assert "127.0.0.1" not in m.group(1)


def test_a_table_packs_a_page_and_a_csv():
    rec = art.put(CID, kind="table", title="Rent by month",
                  content={"columns": ["Month", "Rent"], "rows": [["Jan", 1450], ["Feb, late", 1450]]})
    b = pw.pack(rec)
    assert b.files["data.csv"].decode() == 'Month,Rent\nJan,1450\n"Feb, late",1450'
    html = b.files["index.html"].decode()
    assert "<td>Jan</td>" in html and "1450" in html
    assert 'href="data.csv"' in html


def test_a_chart_packs_its_spec_and_the_shared_renderer():
    rec = art.put(CID, kind="chart", title="Calls",
                  content={"type": "bar", "columns": ["W", "n"], "rows": [["W1", 3], ["W2", 5]]})
    b = pw.pack(rec)
    html = b.files["index.html"].decode()
    assert "FridayChart" in html and "renderSVG" in html, "the panel's chart renderer is inlined"
    assert '"rows": [["W1", 3], ["W2", 5]]' in html.replace("\n", "") or '"rows":[["W1",3],["W2",5]]' in html.replace(" ", "")
    assert "data.csv" in b.files


def test_an_image_packs_the_decoded_file():
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    rec = art.put(CID, kind="image", title="Dot", content={"src": png, "alt": "a dot"})
    b = pw.pack(rec)
    assert b.files["image.png"][:8] == b"\x89PNG\r\n\x1a\n"
    assert 'src="image.png"' in b.files["index.html"].decode() and 'alt="a dot"' in b.files["index.html"].decode()


def test_an_svg_with_a_script_is_refused_at_pack_time():
    rec = art.put(CID, kind="svg", title="Bad", content="<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
    with pytest.raises(pw.Refused):
        pw.pack(rec)
    rec2 = art.put(CID, kind="svg", title="Bad2", content="<svg xmlns='http://www.w3.org/2000/svg'><a onclick='x()'></a></svg>")
    with pytest.raises(pw.Refused):
        pw.pack(rec2)


def test_a_diff_packs_as_a_page():
    rec = art.put(CID, kind="diff", title="Changes", content="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n")
    html = pw.pack(rec).files["index.html"].decode()
    assert "+y" in html and "class=\"add\"" in html


# ── the scan ─────────────────────────────────────────────────────────────────

def test_a_clean_bundle_scans_ok():
    s = pw.scan(pw.pack(_md()))
    assert s["ok"] is True and s["refusals"] == []
    assert s["tier"] in ("TIER_1", "TIER_2", "TIER_3")


def test_a_hard_identifier_refuses_the_publish():
    rec = _md("Sources", "Call the clerk. SSN on file: 123-45-6789.\n")  # pragma: allowlist secret
    s = pw.scan(pw.pack(rec))
    assert s["ok"] is False
    assert any("identifier" in r for r in s["refusals"])


def test_a_secret_shaped_string_refuses_the_publish():
    rec = art.put(CID, kind="html", title="App",
                  content="<script>const k='sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789';</script>")  # pragma: allowlist secret
    s = pw.scan(pw.pack(rec))
    assert s["ok"] is False and any("secret" in r for r in s["refusals"])


def test_a_tracking_script_refuses_the_publish():
    rec = art.put(CID, kind="html", title="App",
                  content="<script async src='https://www.googletagmanager.com/gtag/js?id=G-1'></script><h1>hi</h1>")
    s = pw.scan(pw.pack(rec))
    assert s["ok"] is False and any("tracking" in r or "analytics" in r for r in s["refusals"])


def test_the_google_analytics_hosts_are_refused_without_being_named_in_the_tree():
    # The hosts are assembled from fragments, as in test_no_vendored_telemetry:
    # no telemetry endpoint may appear as a literal anywhere in the tree, and the
    # refusal list in publish_web must still catch both spellings.
    for host in ("google" + "-" + "analytics" + "." + "com", "analytics" + "." + "google" + "." + "com"):
        rec = art.put(CID, kind="html", title="App",
                      content="<img src='https://%s/collect?v=1'><h1>hi</h1>" % host)
        s = pw.scan(pw.pack(rec))
        assert s["ok"] is False, host
        assert host in s["trackers"], host


def test_a_script_from_a_host_other_than_the_package_host_refuses():
    rec = art.put(CID, kind="html", title="App", content="<script src='https://cdn.example.com/lib.js'></script>")
    s = pw.scan(pw.pack(rec))
    assert s["ok"] is False and any("cdn.example.com" in r for r in s["refusals"])
    ok = art.put(CID, kind="html", title="App2", content="<script type=module src='https://esm.sh/react@18.3.1'></script>")
    assert pw.scan(pw.pack(ok))["ok"] is True


def test_a_never_send_token_refuses(monkeypatch):
    from agent_friday.services import judgment_gate
    monkeypatch.setattr(judgment_gate, "never_send_hits", lambda text: ["the-secret-project"] if "secret-project" in text else [])
    s = pw.scan(pw.pack(_md("Notes", "About the secret-project budget.\n")))
    assert s["ok"] is False and any("never-send" in r for r in s["refusals"])


# ── the licence check ────────────────────────────────────────────────────────

def test_licences_are_looked_up_for_every_pinned_import(monkeypatch):
    seen = []

    def fake(url):
        seen.append(url)
        if "react@18.3.1" in url:
            return {"license": "MIT"}
        if "left-pad" in url:
            return {"license": "GPL-3.0"}
        return None

    monkeypatch.setattr(pw, "_fetch_package_json", fake)
    rec = art.put(CID, kind="html", title="App",
                  content="<script type=module>import React from 'https://esm.sh/react@18.3.1';"
                          "import lp from 'https://esm.sh/left-pad@1.3.0';import q from 'https://esm.sh/mystery@1.0.0';</script>")
    lic = pw.licences(pw.pack(rec))
    by = {x["package"]: x for x in lic["packages"]}
    assert by["react@18.3.1"]["license"] == "MIT" and by["react@18.3.1"]["flag"] is None
    assert by["left-pad@1.3.0"]["flag"] == "copyleft"
    assert by["mystery@1.0.0"]["license"] == "unknown" and by["mystery@1.0.0"]["flag"] == "unknown"
    assert lic["flagged"] == 2
    assert all(u.startswith("https://esm.sh/") and u.endswith("/package.json") for u in seen)


def test_a_bundle_with_no_imports_needs_no_lookup():
    lic = pw.licences(pw.pack(_md()))
    assert lic == {"packages": [], "flagged": 0}


# ── the card ─────────────────────────────────────────────────────────────────

def test_requesting_a_publish_files_one_pending_card_with_the_evidence():
    rec = _md("Letter to the records office")
    out = pw.request_publish(CID, rec["id"], adapter="this_pc", requested_by="panel")
    card = out["approval"]
    assert card["kind"] == "publish_web" and card["status"] == "pending" and card["gated"] is True
    p = card["payload"]
    assert p["adapter"] == "this_pc" and p["slug"] == "letter-to-the-records-office"
    assert [f["path"] for f in p["files"]] == ["index.html", "source.md"]
    assert all(f["sha256"] and f["bytes"] > 0 for f in p["files"])
    assert p["scan"]["ok"] is True and "licences" in p
    assert p["conversation_id"] == CID and p["artifact_id"] == rec["id"] and p["version"] == 1
    assert p["preview_url"].startswith("/api/publish/preview/")
    assert "This PC" in p["spoken"] and "publish" in p["spoken"].lower()
    assert any("only while this PC is on" in w for w in p["warnings"])
    # The same version asks once.
    again = pw.request_publish(CID, rec["id"], adapter="this_pc")
    assert again["approval"]["approval_id"] == card["approval_id"]


def test_a_refused_scan_files_no_card():
    rec = _md("Sources", "SSN 123-45-6789\n")  # pragma: allowlist secret
    out = pw.request_publish(CID, rec["id"])
    assert out.get("refused") and out.get("approval") is None
    assert approvals.list_approvals(status="pending") == []


def test_large_media_suggests_a_hosted_adapter():
    rec = art.put(CID, kind="html", title="Big", content="<audio src='data:audio/mp3;base64," + "A" * 3_000_000 + "'></audio>")
    out = pw.request_publish(CID, rec["id"])
    assert any("hosted" in w.lower() for w in out["approval"]["payload"]["warnings"])


# ── approval runs the publish once; denial publishes nothing ────────────────

@pytest.fixture
def receipts(monkeypatch):
    calls = []
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "record_external", lambda action, **kw: calls.append((action, kw)))
    return calls


@pytest.fixture
def posted(monkeypatch):
    msgs = []
    from agent_friday.services import conversations
    monkeypatch.setattr(conversations, "append", lambda cid, m: msgs.append((cid, m)) or m)
    return msgs


def test_approving_the_card_publishes_to_this_pc_with_a_receipt(receipts, posted, tmp_path):
    pw.register()
    rec = _md("Letter")
    card = pw.request_publish(CID, rec["id"], adapter="this_pc")["approval"]
    approvals.decide(card["approval_id"], "approve")
    site = tmp_path / "published" / "letter"
    assert (site / "index.html").exists() and (site / "source.md").exists()
    idx = pw.list_published()
    assert len(idx) == 1 and idx[0]["slug"] == "letter" and idx[0]["version"] == 1
    assert idx[0]["adapter"] == "this_pc" and idx[0]["artifact_id"] == rec["id"]
    assert idx[0]["url"].endswith("/letter/")
    assert receipts and receipts[0][0] == "publish_web" and receipts[0][1]["approval_id"] == card["approval_id"]
    assert posted and posted[-1][0] == CID and "letter" in posted[-1][1]["text"].lower()
    done = approvals.get_approval(card["approval_id"])
    assert done["consumed"] is True


def test_denying_publishes_nothing(receipts, posted, tmp_path):
    pw.register()
    rec = _md("Letter")
    card = pw.request_publish(CID, rec["id"])["approval"]
    approvals.decide(card["approval_id"], "deny")
    assert not (tmp_path / "published").exists() or not list((tmp_path / "published").iterdir())
    assert receipts == []
    assert pw.list_published() == []
    assert posted and "not published" in posted[-1][1]["text"].lower()


def test_republish_is_a_new_version_and_unpublish_removes_the_files(receipts, posted, tmp_path):
    pw.register()
    rec = _md("Letter", "one\n")
    c1 = pw.request_publish(CID, rec["id"])["approval"]
    approvals.decide(c1["approval_id"], "approve")
    art.edit(CID, rec["id"], "two\n")
    c2 = pw.request_publish(CID, rec["id"])["approval"]
    assert c2["approval_id"] != c1["approval_id"]
    approvals.decide(c2["approval_id"], "approve")
    idx = pw.list_published()
    assert len(idx) == 1 and idx[0]["version"] == 2
    assert "two" in (tmp_path / "published" / "letter" / "index.html").read_text(encoding="utf-8")
    assert pw.unpublish("letter") is True
    assert not (tmp_path / "published" / "letter").exists()
    assert pw.list_published() == []
    assert pw.unpublish("letter") is False


def test_a_forged_card_without_a_staged_bundle_publishes_nothing(receipts, posted, tmp_path):
    pw.register()
    card = approvals.create_approval(kind="publish_web", subject_type="artifact", subject_id="x/y@v1/this_pc/evil",
                                     title="Publish", payload={"adapter": "this_pc", "slug": "evil", "staging": "nope",
                                                               "conversation_id": CID}, force_gate=True)
    approvals.decide(card["approval_id"], "approve")
    assert not (tmp_path / "published" / "evil").exists()
    assert receipts == []


def test_a_slug_never_escapes_the_published_folder():
    with pytest.raises(ValueError):
        pw.unpublish("../etc")
    assert pw.slug_for("../../Weird Title!!") == "weird-title"
