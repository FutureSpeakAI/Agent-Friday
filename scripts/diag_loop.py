"""DIAG ONLY (never merged): run the pre-approved-send files, the laya chat-route file and the
clean-top-bar probe in ONE child pytest process, many times, and print what the probe saw."""
import os, re, subprocess, sys, time

PROBE = "tests/api/test_top_bar_starts_clean.py"
PRE = ["tests/api/test_preapproved_send_retry.py", "tests/api/test_preapproved_sends.py",
       "tests/api/test_preapproved_sends_hardening.py", "tests/api/test_preapproved_sends_lasting_provenance.py"]
COMBOS = {
    "pre4+probe": PRE + [PROBE],
    "prov+probe": [PRE[3], PROBE],
    "laya+pre4+probe": ["tests/api/test_chat_laya_router_route.py"] + PRE + [PROBE],
}
N = int(os.environ.get("DIAG_N", "6"))
env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_XDIST") and k != "PYTEST_CURRENT_TEST"}
bad = 0
deadline = time.time() + 45 * 60
for i in range(N):
    for name, files in COMBOS.items():
        if time.time() > deadline:
            break
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", "pytest", *files, "-n", "0", "-p", "no:randomly",
                            "-p", "no:cacheprovider", "-q", "-rfE"], env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=900)
        out = r.stdout + r.stderr
        diag = [l for l in out.splitlines() if "TOPBAR-LEAK-DIAG" in l or l.startswith(("CARD ", "PUSH-", "changed since", "threads alive"))
                or "left on the top bar" in l]
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        print("[%d %s] rc=%s %.0fs %s" % (i, name, r.returncode, time.time() - t0, tail), flush=True)
        if r.returncode != 0:
            bad += 1
            for l in diag[:120]:
                print("    " + l[:1500], flush=True)
            if not diag:
                print("\n".join("    " + l for l in out.strip().splitlines()[-40:]), flush=True)
print("DIAG LOOP DONE: %d bad runs" % bad, flush=True)
sys.exit(1 if bad else 0)
