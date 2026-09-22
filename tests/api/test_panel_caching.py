"""Slow panel routes answer from the stale-while-revalidate cache.

Each of these routes did seconds-to-minutes of work on every panel open:
a git sweep over every repo (17-31 s for 75 repos), two PowerShell launches
(~3 s), a full RSS crawl (70+ s). A second open inside the fresh window must
not redo that work, the response must say how old its data is, and a git
action taken from the Code workspace must show on the next scan.
"""
import subprocess

import agent_friday.routes.code as code
import agent_friday.routes.core_routes as core_routes
import agent_friday.routes.news as news
import agent_friday.services.code_engine as code_engine


def test_repo_scan_is_computed_once_and_dated(client, monkeypatch, tmp_path):
    for name in ("alpha", "beta"):
        (tmp_path / name / ".git").mkdir(parents=True)
    monkeypatch.setattr(code, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(code, "_repo_path", lambda n: str(tmp_path / n))
    sweeps = []
    monkeypatch.setattr(code, "_git_repo_summary",
                        lambda rp: sweeps.append(rp) or {"name": rp.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]})

    first = client.get("/api/repos/scan").get_json()
    second = client.get("/api/repos/scan").get_json()

    assert [r["name"] for r in first["repos"]] == ["alpha", "beta"]
    assert second["repos"] == first["repos"]
    assert len(sweeps) == 2                    # one sweep, two repos
    assert first["as_of"] == second["as_of"]


def test_git_action_drops_the_cached_scan(client, monkeypatch, tmp_path):
    (tmp_path / "alpha" / ".git").mkdir(parents=True)
    monkeypatch.setattr(code, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(code, "_repo_path", lambda n: str(tmp_path / n))
    sweeps = []
    monkeypatch.setattr(code, "_git_repo_summary", lambda rp: sweeps.append(rp) or {"name": "alpha"})
    monkeypatch.setattr(code_engine, "_code_log", lambda *a, **k: None)

    client.get("/api/repos/scan")
    code_engine._git_result(str(tmp_path / "alpha"),
                            subprocess.CompletedProcess([], 0, "ok", ""), "commit")
    client.get("/api/repos/scan")

    assert len(sweeps) == 2


def test_fresh_param_forces_a_rescan(client, monkeypatch, tmp_path):
    (tmp_path / "alpha" / ".git").mkdir(parents=True)
    monkeypatch.setattr(code, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(code, "_repo_path", lambda n: str(tmp_path / n))
    sweeps = []
    monkeypatch.setattr(code, "_git_repo_summary", lambda rp: sweeps.append(rp) or {"name": "alpha"})

    client.get("/api/repos/scan")
    client.get("/api/repos/scan?fresh=1")

    assert len(sweeps) == 2


def test_system_reads_processes_once_and_dates_them(client, monkeypatch):
    reads = []
    monkeypatch.setattr(core_routes, "_system_top_processes",
                        lambda: reads.append(1) or [{"Name": "python", "CPU_s": 1.0, "MemMB": 10.0}])
    monkeypatch.setattr(core_routes, "_system_disks",
                        lambda: [{"Name": "C", "UsedGB": 1, "FreeGB": 1, "TotalGB": 2}])

    a = client.get("/api/system").get_json()
    b = client.get("/api/system").get_json()

    assert a["status"] == "ok" and b["status"] == "ok"
    assert a["processes"][0]["Name"] == "python"
    assert len(reads) == 1
    assert a["processes_as_of"] == b["processes_as_of"]


def test_disks_come_from_psutil_without_a_subprocess(monkeypatch):
    launched = []
    monkeypatch.setattr(core_routes.subprocess, "run", lambda *a, **k: launched.append(a))
    disks = core_routes._system_disks()
    assert launched == []
    assert disks and {"Name", "UsedGB", "FreeGB", "TotalGB"} <= set(disks[0])


def test_news_clusters_crawl_once_and_report_build_time(client, monkeypatch):
    crawls = []
    monkeypatch.setattr(news, "_compute_news_clusters", lambda: crawls.append(1) or [])

    a = client.get("/api/news/clusters").get_json()
    b = client.get("/api/news/clusters").get_json()

    assert len(crawls) == 1
    assert a["generated_at"] == b["generated_at"]
