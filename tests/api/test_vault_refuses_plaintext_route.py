"""The finance template route says it did not save, in one plain line, and leaves no plain file (threat assessment fix #2)."""
import agent_friday.services.agent as agent


def test_the_finance_template_route_says_it_did_not_save_and_leaves_no_plain_file(client, tmp_path, monkeypatch):
    from agent_friday.routes import finance_health as fh
    monkeypatch.setattr(agent, "_VAULT_KEY", None)
    monkeypatch.setattr(agent, "_VAULT_KEY_READY", True)
    monkeypatch.setattr(fh, "FINANCE_DIR", tmp_path)
    r = client.get("/api/finance/portfolio")
    assert r.status_code == 409, r.get_data(as_text=True)[:300]
    assert "plain text" in r.get_data(as_text=True)
    assert not (tmp_path / "portfolio.json").exists()
