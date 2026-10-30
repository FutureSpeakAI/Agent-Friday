"""Friday re-checks her own safeguards at start and every few hours (north
star §26.20-1).

The sweep runs Friday's own deterministic checks (services/doctor's check
functions and services/health_check's credential round trip), no model, no
network:

* receipt_chain: the action ledger's chain and signatures hold;
* rules: the signed rules (cLaws) match their pinned signature;
* egress_gate: the egress gate's start-up self-test passed in this process;
* action_gate: an unknown tool is classified outward (the gate is registered
  and fails closed);
* credential_store: a secret can be written and read back;
* vault: the vault's settings file reads;
* authority: the owner's authority switch (the kill switch for outward
  actions) can be read;
* update_signatures: the minisign verifier passes a known-answer test (a key
  made for the check, never a release key; both signature kinds), every
  configured trusted key reads, and once a release key exists an unsigned
  release is refused. update_check's own manifest rules (name, version,
  expiry, downgrade) are not re-checked here; their tests cover them;
* sandbox: the network-blocking names are still in the Python sandbox's
  start-up code (checked as text: running it here would block Friday's own
  network), the host backend is still classed outward and an unknown backend
  forbidden, and the Python sandbox's environment holds only allowlisted
  variable names and dead proxies. The shell sandbox's environment is not
  checked here.

What it does not cover yet, said so the gap stays visible: connector domains
and principal invariants.

A problem goes out through services/integrity_report (the log, /api/health
under data_integrity, and one Needs-you card per safeguard, raised again if it
comes back after being fixed). Every sweep is a ``self_check`` entry in the
action ledger: when it ran, how many checks, which failed, by fixed id only.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

INTERVAL_S = 6 * 3600
RETRY_S = 600                            # sooner while something failed, so a fix is seen
FIRST_DELAY_S = 60.0
NOT_COVERED = ("connector_domains", "principal_invariants")

#: Doctor states that mean the safeguard is not doing its job.
BAD = {"broken", "misconfigured"}


def _doctor(fn_name: str) -> Callable[[], tuple]:
    def run() -> tuple:
        from agent_friday.services import doctor
        return getattr(doctor, fn_name)()
    return run


def _credential_store() -> tuple:
    from agent_friday.services import health_check
    ok, detail = health_check.check_credential_store()
    if ok:
        return "healthy", "round_trip_ok"
    if str(detail).startswith("KeystoreLocked"):
        return "unverified", "keystore_locked"           # waiting for the passphrase: not a fault
    return "broken", "round_trip_failed"


def _minisign_pair():
    """(public key text, sign(data, comment) -> signature text) for a key made
    now and thrown away: the check never touches a real signing key."""
    import base64
    import hashlib
    import os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = os.urandom(8)
    pub = "untrusted comment: self-check key\n" + base64.b64encode(b"Ed" + kid + pk).decode()

    def sign(data: bytes, comment: str, alg: bytes = b"ED") -> str:
        signed = hashlib.blake2b(data, digest_size=64).digest() if alg == b"ED" else data
        sig = sk.sign(signed)
        gsig = sk.sign(sig + comment.encode("utf-8"))
        return "\n".join(["untrusted comment: self-check", base64.b64encode(alg + kid + sig).decode(),
                          "trusted comment: " + comment, base64.b64encode(gsig).decode()])
    return pub, sign


def _update_signatures() -> tuple:
    from agent_friday.services import minisign, trust_keys
    for k in tuple(trust_keys.RELEASE_KEYS) + tuple(trust_keys.PUBLIC_LIST_KEYS):
        try:
            minisign.parse_public_key(k)
        except minisign.SignatureError:
            return "broken", "trusted_key_unreadable"
    if trust_keys.RELEASE_KEYS:
        from agent_friday.services import update_check
        if not {"unsigned", "unreachable"} <= set(update_check.refused_states()):
            return "broken", "unsigned_release_not_refused"
    pub, sign = _minisign_pair()
    _other, sign_other = _minisign_pair()
    body = b"friday self-check"
    for alg in (b"Ed", b"ED"):                                  # plain and prehashed (minisign's default)
        sig = sign(body, "name=self-check", alg)
        try:
            minisign.verify(body, sig, [pub])
        except minisign.SignatureError:
            return "broken", "good_signature_refused"
        try:
            minisign.verify(body + b"!", sig, [pub])
        except minisign.SignatureError:
            continue
        return "broken", "bad_signature_accepted"
    good = sign(body, "name=self-check")
    lines = good.split("\n")
    forged_comment = "\n".join(lines[:2] + ["trusted comment: name=agent-friday-release"] + lines[3:])
    for data, sig, keys in ((body + b"!", good, [pub]),                  # a changed file
                            (body, forged_comment, [pub]),               # a changed trusted comment
                            (body, sign_other(body, "name=self-check"), [pub]),   # someone else's key
                            (body, good, [])):                           # nobody's key
        try:
            minisign.verify(data, sig, keys)
        except minisign.SignatureError:
            continue
        return "broken", "bad_signature_accepted"
    return "healthy", "verifier_ok"


#: The names the code sandbox's start-up code must replace to block the network.
SANDBOX_BLOCKS = ("socket", "create_connection", "getaddrinfo", "create_server", "socketpair")


#: Every variable the Python sandbox's environment may hold: code_sandbox.
#: _clean_env builds it from nothing, so anything else came from Friday's own
#: environment (where secrets live). A variable added there must be added here.
SANDBOX_ENV = frozenset({
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
    "NO_PROXY", "no_proxy", "PYTHONIOENCODING", "FRIDAY_SANDBOX", "SystemRoot", "windir", "PATH", "LANG"})


def _sandbox() -> tuple:
    import os
    from pathlib import Path

    from agent_friday.services import code_sandbox as cs
    boot = cs._BOOT
    compile(boot, "<sandbox boot>", "exec")
    if any('"%s"' % n not in boot for n in SANDBOX_BLOCKS) \
            or not all(t in boot for t in ("_no_network", "setattr(_m, _n, _no_network)", "_ss")):
        return "broken", "network_block_missing"
    if cs.classify({"backend": "host"})[0] != cs.OUTWARD:
        return "broken", "host_backend_not_outward"
    if cs.classify({"backend": "self-check-unknown"})[0] != cs.FORBIDDEN:
        return "broken", "unknown_backend_allowed"
    env = cs._clean_env(Path(os.sep) / "friday-self-check")
    if set(env) - SANDBOX_ENV:
        return "broken", "environment_inherited"
    if any(env.get(k) != cs.DEAD_PROXY for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                                  "http_proxy", "https_proxy", "all_proxy")) \
            or env.get("NO_PROXY", "") or env.get("no_proxy", ""):
        return "broken", "proxy_not_dead"
    return "healthy", "sandbox_rules_ok"


def _key_present() -> bool:
    """Whether a governance key exists, without creating one."""
    try:
        from agent_friday.services import doctor
        return bool(doctor._governance_key_present()[0])
    except Exception:  # noqa: BLE001
        return False


CHECKS = (
    ("receipt_chain", _doctor("_c_ledger"), "The action ledger's receipts no longer check (a break to review in Activity)."),
    ("rules", _doctor("_c_claws"), "Friday's signed rules do not match their signature."),
    ("egress_gate", _doctor("_c_egress"), "The egress gate's own start-up test failed."),
    ("action_gate", _doctor("_c_action_gate"), "The action gate does not treat an unknown tool as outward."),
    ("credential_store", _credential_store, "Friday cannot write and read back a secret in its credential store."),
    ("vault", _doctor("_c_vault"), "The vault's settings file cannot be read."),
    ("authority", _doctor("_c_authority"), "The owner's authority switch cannot be read."),
    ("update_signatures", _update_signatures,
     "The signature verifier that update checks use no longer tells a good signature from a bad one."),
    ("sandbox", _sandbox, "The Python sandbox's own rules (its network block, its clean environment) are not intact."),
)


def _problem(cid: str, state: str, code: str) -> bool:
    if state in BAD:
        return True
    if cid == "rules" and code == "rules_signature_mismatch":
        return True
    if state == "unverified" and (code.startswith("check_failed") or code.endswith("_unreadable")):
        return True
    return False


_RUN = threading.Lock()
#: Checks that reach the action ledger (and so its key): skipped while there is
#: no governance key yet, so a sweep never creates one.
NEEDS_KEY = {"authority"}


def sweep(record: bool = True) -> dict:
    """Run every check now, one sweep at a time (a sweep already running is
    not doubled: its last result is returned). Returns {at, checked, failed:
    [{id, code}]}; never raises. A check that raises counts as failed."""
    if not _RUN.acquire(blocking=False):
        return dict(_LAST)
    try:
        return _sweep(record)
    finally:
        _RUN.release()


def _sweep(record: bool) -> dict:
    failed = []
    have_key = _key_present()
    for cid, fn, _said in CHECKS:
        if cid in NEEDS_KEY and not have_key:
            continue
        try:
            state, code = fn()
        except Exception as e:  # noqa: BLE001 - a check that cannot run is a problem
            state, code = "unverified", "check_failed:" + type(e).__name__
        if _problem(cid, str(state), str(code)):
            failed.append({"id": cid, "code": str(code)[:60]})
    out = {"at": time.time(), "checked": len(CHECKS), "failed": failed, "not_covered": list(NOT_COVERED)}
    if record and have_key:                  # the ledger needs the key; a sweep never mints one
        try:
            from agent_friday.services import action_ledger
            action_ledger.try_append("self_check", {"checked": out["checked"],
                                                    "failed": [f["id"] for f in failed],
                                                    "codes": [f["code"] for f in failed]})
        except Exception:  # noqa: BLE001
            pass
    _LAST.update(out)
    return out


_LAST: dict = {}
_SAID = {cid: said for cid, _fn, said in CHECKS}


def probe() -> list:
    """For services/integrity_report: the last sweep's problems. It never
    sweeps itself (/api/health reads it on every hit); the periodic thread
    does."""
    last = dict(_LAST)
    return [{"store": "safeguard:" + f["id"], "state": "check_failed" if f["code"].startswith("check_failed")
             else "safeguard_failed", "records": None, "message": _SAID.get(f["id"], f["id"])}
            for f in last.get("failed") or []]


def start_periodic(interval_s: float = INTERVAL_S, first_delay_s: float = FIRST_DELAY_S,
                   sleep: Callable[[float], None] = time.sleep) -> threading.Thread:
    """Sweep a little after start and then every `interval_s`, saying any
    problem through integrity_report. Returns the daemon thread."""
    from agent_friday.services import integrity_report
    integrity_report.register("safeguards", probe)

    def _run():
        sleep(first_delay_s)
        while True:
            failed = False
            try:
                failed = bool(sweep().get("failed"))
                integrity_report.announce()
            except Exception:  # noqa: BLE001 - the next sweep tries again
                pass
            sleep(min(interval_s, RETRY_S) if failed else interval_s)
    t = threading.Thread(target=_run, name="self-verify", daemon=True)
    t.start()
    return t
