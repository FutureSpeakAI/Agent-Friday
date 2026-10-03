"""The tray's routes: mute and clear-all, with approvals untouchable."""
from agent_friday import notifications_engine as ne
from agent_friday.services import notification_policy as pol


def test_mute_route_refuses_approvals_and_clear_all_keeps_them(client, monkeypatch):
    monkeypatch.setattr(pol, "_LAST_TURN", [0.0])
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    r = client.post("/api/notifications/mute", json={"kind": "approval_pending",
                                                     "source": "approvals"})
    assert r.get_json()["status"] == "refused"
    r = client.post("/api/notifications/mute", json={"kind": "info", "source": "actions"})
    assert r.get_json()["status"] == "ok" and "info|actions" in r.get_json()["mutes"]
    ne.push(title="Front Page failed", source="scheduler", kind="scheduled_failure",
            priority="high")
    ne.push(title="Approval needed: send email", source="approvals",
            kind="approval_pending", dedupe_key="appr:1")
    assert client.post("/api/notifications/clear-all").get_json()["cleared"] == 1
    items = client.get("/api/notifications").get_json()["items"]
    assert [i["kind"] for i in items if i.get("kind") == "approval_pending"] == ["approval_pending"]
    client.post("/api/notifications/mute", json={"kind": "info", "source": "actions",
                                                 "unmute": True})
    assert not pol.is_muted("info", "actions")


def test_clear_group_route_keeps_approvals_and_the_list_carries_groups(client, monkeypatch):
    monkeypatch.setattr(pol, "_LAST_TURN", [0.0])
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    ne.push(title="Front Page failed", source="scheduler", kind="scheduled_failure",
            priority="high", dedupe_key="sched-fail:sch_news_morning")
    ne.push(title="Approval needed: run it on a cloud model", source="approvals",
            kind="approval_pending", dedupe_key="appr:route",
            meta={"group": "job:sched:sch_news_morning"})
    groups = client.get("/api/notifications").get_json()["groups"]
    assert [len(g["items"]) for g in groups if g["key"] == "job:sched:sch_news_morning"] == [2]
    r = client.post("/api/notifications/clear-group", json={"group": "job:sched:sch_news_morning"})
    assert r.get_json()["cleared"] == 1
    items = client.get("/api/notifications").get_json()["items"]
    assert [i["kind"] for i in items if i.get("source") in ("scheduler", "approvals")] == [
        "approval_pending"]
    assert client.post("/api/notifications/clear-group", json={}).status_code == 400
