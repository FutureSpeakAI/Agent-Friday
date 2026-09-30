#!/usr/bin/env python3
"""Live smoke check for podcasts, run against a deployed Friday on this machine.

    python scripts/smoke_podcasts.py            # phase 1: a Briefing run gets an episode
    python scripts/smoke_podcasts.py --phase 2  # also: the page carries the player

Phase 1 generates today's Briefing through the News route (a real, local
model call), then waits for that run's episode and checks it end to end:
queued by the routine's own hook, written, spoken, a transcript with timed
lines and citations, captions, audio served with range requests, the
listening check passed, and signed provenance. It prints one line per check
and exits 0 only when every check passed.

The render waits for 60 s of inactivity before it starts (the scheduler's
idle gate), so leave the machine alone while it runs. It talks only to the
local server over loopback; nothing here reaches the internet except what
the Briefing itself fetches.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def base_url() -> str:
    port = 3000
    try:
        port = int((Path.home() / ".friday" / "friday_server.port").read_text().strip())
    except Exception:
        pass
    return "http://127.0.0.1:%d" % port


def fetch(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None,
          timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(
        {"Content-Type": "application/json"} if data else {}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def run(base: str, phase: int, wait_s: float, fetcher=fetch, say=print) -> bool:
    ok = True

    def check(cond, what, detail=""):
        nonlocal ok
        ok &= bool(cond)
        say(("PASS " if cond else "FAIL ") + what + (" (" + detail + ")" if detail and not cond else ""))
        return cond

    st, body, _ = fetcher(base + "/api/podcasts")
    if not check(st == 200, "podcast API answers", "HTTP %s" % st):
        return False
    run_id = datetime.now().strftime("%Y-%m-%d")
    say("…generating today's Briefing on the local model (this can take a few minutes)")
    st, body, _ = fetcher(base + "/api/briefing/generate", "POST", {}, timeout=900)
    check(st == 200, "Briefing generated", "HTTP %s %s" % (st, body[:200]))
    st, body, _ = fetcher(base + "/api/podcasts/for-run?routine=briefing&run_id=" + run_id)
    ep = (json.loads(body or b"{}").get("episode") or {}) if st == 200 else {}
    if not check(ep.get("id"), "the Briefing's hook queued an episode", "none for %s" % run_id):
        return False
    check(ep.get("privacy") == "private", "a Briefing episode is private (it carries mail)")
    eid, t0 = ep["id"], time.time()
    say("…waiting for episode %s (render needs 60 s of inactivity)" % eid)
    while time.time() - t0 < wait_s:
        st, body, _ = fetcher(base + "/api/podcasts/" + eid)
        ep = json.loads(body).get("episode") or {}
        if ep.get("status") in ("ready", "failed", "cancelled"):
            break
        time.sleep(10)
    if not check(ep.get("status") == "ready", "episode finished",
                 "%s: %s" % (ep.get("status"), (ep.get("error") or {}).get("message") or ep.get("stage_detail"))):
        return False
    lines = ep.get("lines") or []
    said = [ln for ln in lines if not ln.get("signature")]
    check(len(said) >= 4, "a transcript with lines", "%d lines" % len(said))
    check(all(ln.get("start") is not None for ln in lines), "every line is timed")
    check(any(ln.get("cites") for ln in said), "lines cite their sources")
    check(str(ep.get("writer_model") or "") and "claude" not in str(ep.get("writer_model")).lower(),
          "written by a local model", str(ep.get("writer_model")))
    check((ep.get("check") or {}).get("ok") is True, "the listening check passed",
          json.dumps(ep.get("check")))
    check((ep.get("provenance") or {}).get("signed"), "provenance is signed")
    st, body, _ = fetcher(base + "/api/podcasts/%s/captions.vtt" % eid)
    check(st == 200 and body.startswith(b"WEBVTT"), "captions served")
    st, body, hdr = fetcher(base + "/api/podcasts/%s/audio" % eid, headers={"Range": "bytes=0-1023"})
    check(st == 206 and len(body) == 1024, "audio served with range requests", "HTTP %s" % st)
    if phase >= 2:
        st, body, _ = fetcher(base + "/")
        page = body.decode("utf-8", "replace")
        check("function PodcastPlayer(" in page, "the page carries the podcast player")
        check("'podcasts'" in page and "Podcasts" in page, "Studio has a Podcasts view")
        check("--fr-" in page[page.find("PODCASTS — player"):][:6000], "the player reads the brand tokens")
        check(any(ln.get("signature") for ln in lines), "the episode has its signature lines")
    say("ALL PASSED" if ok else "SOME CHECKS FAILED")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--phase", type=int, default=1)
    ap.add_argument("--base", default="")
    ap.add_argument("--wait", type=float, default=1800.0, help="seconds to wait for the render")
    a = ap.parse_args()
    return 0 if run(a.base or base_url(), a.phase, a.wait) else 1


if __name__ == "__main__":
    sys.exit(main())
