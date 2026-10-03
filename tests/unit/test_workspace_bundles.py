"""Bundle workspaces and "Improve this workspace" (salon spec §4.9, §4.9.1
items 3 and 5, §9.1 "Workspace evolution"): an improved bundle runs only in
the frame; the swap needs one approval; rollback restores the previous bundle
hash; a change touching a reserved status colour fails the brand check.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approvals
from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import workspace_bundles as wb
from agent_friday.services import workspace_registry as reg


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    # The approvals store is this test's own: a card another test left pending
    # must not be counted here, nor this test's left behind.
    from agent_friday.services import approvals as _ap
    monkeypatch.setattr(_ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(_ap, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(_ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(wb, "_root", lambda: tmp_path / "workspaces")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "smoke", lambda cid: {"ran": False, "ok": None, "errors": [], "note": "smoke not run (test)", "sha": "", "ms": 0})
    yield


# ── the brand check ──────────────────────────────────────────────────────────

def test_brand_check_flags_a_reserved_status_colour_and_names_it():
    bad = wb.brand_check("<h1 style='color:#f59e0b'>x</h1>", "")
    assert bad and "#f59e0b" in bad[0] and "amber" in bad[0].lower()
    bad = wb.brand_check("", "a{background:rgb(255,0,128)}")
    assert bad and "deny" in bad[0].lower()
    # Near misses count: a colour you would read as the approve green.
    assert wb.brand_check("", "b{color:#05f582}")
    # Case and short forms too.
    assert wb.brand_check("", "c{color:#F59E0B}")


def test_brand_check_passes_the_brand_palette_and_neutrals():
    css = ":root{--accent:#00d4ff;--violet:#7b61ff;--fg:#e8f4ff;--bg:#0b0e14;--line:rgba(255,255,255,.1)} h1{color:#fff} p{color:#94a3b8}"
    assert wb.brand_check("<p style='color:#00d4ff'>ok</p>", css) == []


def test_brand_check_is_delta_e_not_string_matching():
    # #00ff80 shifted well away in hue is fine; the same hue at a slightly
    # different lightness is not.
    assert wb.brand_check("", "a{color:#0080ff}") == []
    assert wb.brand_check("", "a{color:#00f57a}")


# ── the manifest ─────────────────────────────────────────────────────────────

def test_manifest_check_requires_the_bundle_contract():
    good = {"id": "rent-board", "name": "Rent board", "version": "0.1.0", "friday_api": 1,
            "capabilities": {"read": [], "write": [], "network": ["none"]}}
    assert wb.check_manifest(json.dumps(good)) == []
    assert wb.check_manifest("not json")
    assert any("friday_api" in p for p in wb.check_manifest(json.dumps({**good, "friday_api": 2})))
    assert any("network" in p for p in wb.check_manifest(json.dumps({**good, "capabilities": {"network": ["https://x"]}})))
    assert any("id" in p for p in wb.check_manifest(json.dumps({**good, "id": "Not A Slug!"})))


# ── install, versions, rollback ──────────────────────────────────────────────

def test_install_creates_a_bundle_workspace_from_a_codebase_and_versions_it():
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    assert ws["id"] == "rent-board" and ws["boundary"] == {"kind": "bundle"}
    assert ws["group"] == "mine" and ws["label"] == "Rent board" and ws["codebase_id"] == rec["id"]
    assert len(ws["versions"]) == 1 and ws["current"] == ws["versions"][0]["sha256"]
    first = ws["current"]
    assert "<html" in wb.html(ws["id"]).lower()
    assert ws["id"] in [w["id"] for w in wb.list_installed()]
    # A second install after a step is a new version and the old one is kept.
    html = cb.read(rec["id"], "index.html").replace("<h1>", "<h1 data-v='2'>")
    cb.step(rec["id"], {"index.html": html}, "Marked the heading")
    ws2 = wb.install(rec["id"])
    assert ws2["id"] == ws["id"] and len(ws2["versions"]) == 2 and ws2["current"] != first
    assert "data-v='2'" in wb.html(ws["id"])
    # Rollback is one click: current goes back, nothing is deleted, it is recorded.
    ws3 = wb.rollback(ws["id"], first)
    assert ws3["current"] == first and "data-v='2'" not in wb.html(ws["id"])
    assert len(ws3["versions"]) == 2 and ws3["history"][-1]["kind"] == "rollback"
    with pytest.raises(KeyError):
        wb.rollback(ws["id"], "0" * 64)


def test_install_refuses_a_bundle_that_repaints_a_status_colour_or_breaks_the_manifest():
    rec = cb.create("Loud", template="bundle")
    css = cb.read(rec["id"], "index.html").replace("</style>", "h1{color:#ff0080}</style>")
    cb.step(rec["id"], {"index.html": css}, "Painted the heading deny-pink")
    with pytest.raises(wb.BrandRefused) as ei:
        wb.install(rec["id"])
    assert "#ff0080" in str(ei.value)
    rec2 = cb.create("Broken", template="bundle")
    cb.step(rec2["id"], {"manifest.json": "{"}, "Broke the manifest")
    with pytest.raises(wb.ManifestRefused):
        wb.install(rec2["id"])


def test_install_needs_a_bundle_codebase():
    rec = cb.create("Plain site", template="static")
    with pytest.raises(wb.ManifestRefused):
        wb.install(rec["id"])


# ── improve ──────────────────────────────────────────────────────────────────

def test_improve_on_a_native_workspace_is_refused_with_the_reason():
    with pytest.raises(wb.NativeWorkspace) as ei:
        wb.improve("news")
    assert "own source" in str(ei.value).lower() or "not built" in str(ei.value).lower()
    with pytest.raises(KeyError):
        wb.improve("no-such-workspace")


def test_improve_on_a_bundle_opens_one_codebase_chat_seeded_from_the_installed_version():
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    # The original codebase may be gone; improve still works from the installed files.
    out = wb.improve(ws["id"])
    assert out["conversation_id"] and out["codebase_id"]
    conv = convs.load(out["conversation_id"])
    assert conv["codebase"] == out["codebase_id"]
    crec = cb.load(out["codebase_id"])
    assert crec["workspace_id"] == ws["id"] and crec["template"] == "bundle"
    assert cb.read(out["codebase_id"], "index.html") == wb.html(ws["id"])
    # Asking again returns the same chat, not a second codebase.
    again = wb.improve(ws["id"])
    assert again["conversation_id"] == out["conversation_id"]
    # The model is told which workspace this codebase improves.
    block = cb.context_block_for(out["codebase_id"])
    assert "improves the workspace" in block.lower() and ws["label"] in block


# ── the swap card ────────────────────────────────────────────────────────────

def test_swap_is_one_card_and_installs_only_on_approval():
    wb.register()
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    out = wb.improve(ws["id"])
    cid = out["codebase_id"]
    html = cb.read(cid, "index.html").replace("<h1>", "<h1 data-v='2'>")
    cb.step(cid, {"index.html": html}, "Marked the heading")
    card = wb.request_swap(cid, requested_by="friday")
    assert card["status"] == "pending" and card["kind"] == "workspace_swap"
    p = card["payload"]
    assert p["workspace_id"] == ws["id"] and p["codebase_id"] == cid and p["sha"] and p["brand"] == "ok"
    assert p["smoke"]["ran"] is False and "not run" in p["smoke"]["note"].lower()
    assert p["spoken"] and ws["label"] in p["spoken"]
    # One card per codebase head: asking twice does not stack cards.
    assert wb.request_swap(cid, requested_by="friday")["approval_id"] == card["approval_id"]
    assert len(approvals.list_approvals(status="pending")) == 1
    # Not yet installed.
    assert "data-v='2'" not in wb.html(ws["id"])
    approvals.decide(card["approval_id"], "approve")
    assert "data-v='2'" in wb.html(ws["id"])
    assert len(wb.get(ws["id"])["versions"]) == 2
    done = approvals.get_approval(card["approval_id"])
    assert done["status"] in ("approved", "used", "executed", "done")


def test_a_declined_swap_installs_nothing():
    wb.register()
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    cid = wb.improve(ws["id"])["codebase_id"]
    cb.step(cid, {"index.html": cb.read(cid, "index.html").replace("<h1>", "<h1 data-v='2'>")}, "Marked")
    card = wb.request_swap(cid, requested_by="friday")
    approvals.decide(card["approval_id"], "deny")
    assert "data-v='2'" not in wb.html(ws["id"]) and len(wb.get(ws["id"])["versions"]) == 1


def test_a_swap_that_fails_the_brand_check_raises_no_card():
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    cid = wb.improve(ws["id"])["codebase_id"]
    cb.step(cid, {"index.html": cb.read(cid, "index.html").replace("</style>", "h1{color:#f59e0b}</style>")}, "Amber")
    with pytest.raises(wb.BrandRefused):
        wb.request_swap(cid, requested_by="friday")
    assert approvals.list_approvals(status="pending") == []


def test_a_swap_that_fails_the_smoke_is_a_run_failed_blocker_not_a_card(monkeypatch):
    monkeypatch.setattr(cb, "smoke", lambda cid: {"ran": True, "ok": False, "errors": ["boom"], "note": "", "sha": "x", "ms": 5})
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    cid = wb.improve(ws["id"])["codebase_id"]
    cb.step(cid, {"index.html": cb.read(cid, "index.html") + "<!-- v2 -->"}, "Touched")
    with pytest.raises(wb.SmokeFailed) as ei:
        wb.request_swap(cid, requested_by="friday")
    assert ei.value.blocker == "run_failed" and "boom" in str(ei.value)
    assert approvals.list_approvals(status="pending") == []


def test_the_first_swap_of_a_fresh_bundle_codebase_creates_the_workspace():
    wb.register()
    rec = cb.create("Chore wheel", template="bundle")
    card = wb.request_swap(rec["id"], requested_by="you")
    assert card["payload"]["workspace_id"] is None and "new workspace" in card["payload"]["spoken"].lower()
    approvals.decide(card["approval_id"], "approve")
    ids = [w["id"] for w in wb.list_installed()]
    assert "chore-wheel" in ids


# ── boundaries ───────────────────────────────────────────────────────────────

def test_every_registry_workspace_declares_a_native_boundary_and_bundles_declare_theirs():
    for w in reg.workspaces():
        b = reg.boundary(w["id"])
        assert b["kind"] == "native" and b["components"], w["id"]
    rec = cb.create("Rent board", template="bundle")
    ws = wb.install(rec["id"])
    assert reg.boundary(ws["id"]) == {"kind": "bundle"}
    with pytest.raises(KeyError):
        reg.boundary("nothing-here")
