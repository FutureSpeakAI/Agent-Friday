"""
Agent Friday — the Arbiter: runtime owner of the GPUs.

The policy decides what SHOULD be resident. The Arbiter is what makes it true
and keeps it true: it boots the default plan, grants capability leases, and
executes transitions serially with timeouts and rollback.

Why it owns processes rather than asking a daemon nicely (rule R9). Measured on
the reference instance: loading gemma4:12b (8001 MiB) and then
gemma4:e2b (1763 MiB) against a 9997 MiB budget leaves ONLY the e2b resident.
Ollama evicted the model the policy considers pinned, at every num_ctx tried,
with no model-count limit reached and no report anywhere. A residency layer
cannot delegate placement to a scheduler that makes its own eviction decisions
on different criteria. So:

  * **pinned** seats run as llama-server processes the Arbiter spawns, health-
    checks, and terminates. Nothing else can evict them.
  * **leased** seats may use Ollama, because a lease is precisely the moment
    eviction is wanted.

Concurrency is deliberately absent. Transitions are serial under one lock: a
residency layer that races itself produces exactly the un-debuggable VRAM
exhaustion it exists to prevent.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from agent_friday.core import runtime_dir
from agent_friday.services import hardware_profile as hwp
from agent_friday.services import residency_catalog as rc
from agent_friday.services import residency_policy as rp

STATE_DEFAULT = "DEFAULT"
STATE_TRANSITIONING = "TRANSITIONING"
STATE_LEASED = "LEASED"
STATE_ROLLING_BACK = "ROLLING_BACK"
STATE_DEGRADED = "DEGRADED"

OLLAMA_URL = "http://localhost:11434"
PORT_BASE = 8090          # one loopback port per pinned llama-server seat

# Transition timeouts are derived from the load-time estimator, never fixed: a
# measured 55 s cold load must not share a budget with a 21 s one.
TIMEOUT_FLOOR_S = 45.0
TIMEOUT_MULTIPLE = 3.0


class TransitionError(RuntimeError):
    pass


# ── Chain cancellation — the SAME job-flag pattern `local_image.py` already
# uses (headroom.md §6.5: "Cancellation reuses local_image's job-flag
# pattern"). A chain is not one job with several internal phases (the case
# `local_image`'s own flag was built for) — it is several INDEPENDENT
# grant/release cycles run in sequence by `Arbiter.run_chain`, so this is its
# own flag, of the identical shape, rather than a second use of
# `local_image`'s: a cancelled chain must not be confused with a cancelled
# render that happens to be one of its stages.
_CHAIN_CANCELLED: set = set()
_CHAIN_CANCEL_LOCK = threading.Lock()


def request_chain_cancel(chain_id: str) -> bool:
    """Mark a chain cancelled. Safe to call before it has really begun."""
    if not chain_id:
        return False
    with _CHAIN_CANCEL_LOCK:
        _CHAIN_CANCELLED.add(str(chain_id))
    return True


def is_chain_cancelled(chain_id) -> bool:
    if not chain_id:
        return False
    with _CHAIN_CANCEL_LOCK:
        return str(chain_id) in _CHAIN_CANCELLED


def clear_chain_cancel(chain_id) -> None:
    if not chain_id:
        return
    with _CHAIN_CANCEL_LOCK:
        _CHAIN_CANCELLED.discard(str(chain_id))


# The process-wide Arbiter, set by server._residency_boot. None means the
# residency layer is not governing this process (tests, FRIDAY_NO_ARBITER=1,
# or a failed import) — callers must treat that as "no arbiter", never as an
# error, so nothing depends on residency being present to function.
ARBITER = None


def exclusive_lease() -> dict | None:
    """The lease currently holding the card, or None.

    Acquiring an exclusive lease evicts every seat but the R10-retained ones,
    precisely so an image job gets the whole card. Nothing on the dispatch path
    ever asked whether that lease existed, so the exclusivity was real on the
    way IN and unenforced afterwards: any timer that woke up and called a local
    model loaded ~7 GB straight back onto a card an image job believed it owned.

    The maintainer's report: "An hourly heartbeat launched while I was running
    my last image job and the whole computer slowed to a crawl." The heartbeat
    is harmless while background work goes to the cloud; moving it to a local
    model (correctly — it was costing a million tokens a day) turns it into a
    GPU competitor unless the scheduler knows about leases.
    """
    try:
        arb = get_arbiter()
    except Exception:
        return None
    if arb is None:
        return None
    lease = getattr(arb, "lease", None)
    return dict(lease) if lease else None


def get_arbiter():
    return ARBITER


def ensure_vision(model_id: str) -> bool:
    """For a caller about to send an image to a local seat: load the seat's
    projector if it was left out (vision on demand). True when the seat will
    accept images, or when there is nothing to do."""
    arb = ARBITER
    llama = getattr(arb, "llama", None) if arb is not None else None
    if llama is None:
        return True
    try:
        return llama.ensure_vision(model_id)
    except Exception as e:
        print(f"  [arbiter] ensure_vision({model_id}) failed: {e}")
        return False


def endpoints_path() -> Path:
    return runtime_dir() / "residency" / "endpoints.json"


def _read_published() -> dict:
    """`model_id -> base_url` as the file currently claims. Never raises."""
    try:
        # utf-8-sig: a BOM on this file must not cost a seat its adoption
        # (Friday-Models/docs/DECISIONS.md).
        data = json.loads(endpoints_path().read_text(encoding="utf-8-sig"))
        return {str(m): str(b)
                for m, b in (data.get("endpoints") or {}).items() if b}
    except Exception:
        return {}


def _endpoint_port(base: str) -> int | None:
    try:
        return int(base.rsplit(":", 1)[1].split("/")[0])
    except Exception:
        return None


def _endpoint_alive(base: str) -> bool:
    """Is anything answering /health on that base URL's port right now?"""
    port = _endpoint_port(base)
    if not port:
        return False
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/health" % port, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _tail(path, lines: int = 12) -> str:
    """The last few lines of a seat's log, for an exception message.

    Empty string when there is nothing to say, so callers can concatenate it
    unconditionally and a missing log never turns a real error into a
    formatting one.
    """
    if not path:
        return ""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    kept = [ln for ln in raw.splitlines() if ln.strip()][-lines:]
    if not kept:
        return ""
    sep = chr(10) + "  "
    return sep + sep.join(kept)


def _publish_endpoints(procs: dict, drop=()) -> None:
    """Write the live seat->port map where OTHER processes can read it.

    An in-memory map serves the server and nothing else. Any other process —
    a measurement probe, a worker, a script — has no Arbiter, so it asks the
    Ollama daemon, and with the daemon stopped it raises "Ollama is not
    running" about a model that is loaded and healthy two ports away.
    Measured on the reference machine: the tool-chain probe scored 0/5 with
    that error while the same seat answered a real turn inside the server in
    4.5 s.

    MERGES, never clobbers. `procs` is one process's view, and a fresh process
    starts with an empty one. A fresh process that writes {"endpoints": {}}
    over the file while a seat spawned by the previous process sits healthy
    on its port makes every reader (`seat_endpoint`, `_published_endpoint`,
    tool_budget's /props probe) blind to a live seat in exactly the restart
    window where it matters.
    So entries this process does not own are kept iff they still answer
    /health; our own entries always win, and a foreign entry claiming a port
    we now own is dropped rather than health-checked against OUR server.

    `drop` names seats being evicted right now: eviction publishes before the
    process is terminated, so a health check alone would keep the dying seat.

    Best-effort and never fatal: a seat that cannot publish its port is still
    a working seat for the process that owns it.
    """
    try:
        ours = {m: "http://127.0.0.1:%d/v1" % port
                for m, (_proc, port) in procs.items()}
        our_ports = {_endpoint_port(b) for b in ours.values()}
        merged = dict(ours)
        for model, base in _read_published().items():
            if model in merged or model in drop:
                continue
            if _endpoint_port(base) in our_ports:
                continue
            if _endpoint_alive(base):
                merged[model] = base
        p = endpoints_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"pid": os.getpid(), "updated_at": time.time(),
                "endpoints": merged}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def _published_endpoint(model_id: str) -> str | None:
    """A seat's port from the file, verified live before it is trusted.

    The file can outlive the process that wrote it, so a stale entry must not
    send a caller to a port nobody is listening on. Health-checked first.
    """
    base = _read_published().get(model_id)
    if base and _endpoint_alive(base):
        return base
    return None


def owned_endpoint(model_id: str) -> str | None:
    """The OpenAI-compatible base URL of a seat WE are serving, or None.

    This is the other half of owning the runtime. A brain GGUF served by a
    process the Arbiter runs on a loopback port is invisible to dispatch that
    asks only the daemon on :11434. Measured on the reference machine: the
    brain sat resident and unreachable while an ordinary "reply with one word"
    turn fell through to the cloud and took 2m05s, against 16s when the same
    model was served by the daemon.

    A seat that is resident and unreachable is worse than one that is neither.
    """
    if not model_id:
        return None
    arb = ARBITER
    if arb is not None:
        entry = getattr(arb.llama, "procs", {}).get(model_id)
        if entry:
            _proc, port = entry
            return "http://127.0.0.1:%d/v1" % port
    # No Arbiter in THIS process. The seat may still be running in another one.
    return _published_endpoint(model_id)


def owned_provider(model_id: str) -> dict | None:
    """An ad-hoc provider descriptor for a seat we serve ourselves.

    Built at call time from the live port rather than registered in the
    provider registry: the port is assigned when the process starts and a
    stale registry entry would point dispatch at nothing. `classification` and
    the loopback host are what keep `_call_openai`'s call-time local check
    satisfied, so vault-tier material stays permitted — this is on-device by
    construction, not by configuration.
    """
    base = owned_endpoint(model_id)
    if not base:
        return None
    return {
        "name": "arbiter-local",
        "classification": "local",
        "type": "openai-compatible",
        # MUST be "openai-compatible", not "openai".
        #
        # `adapter_of()` reads `adapter` before `type`, and only the adapters in
        # LOCAL_CAPABLE_ADAPTERS earn local classification. "openai" is not one
        # of them, so this single wrong word made `classification_of()` return
        # "cloud" for a model running on 127.0.0.1 in a process we own, and
        # `local_bypass` came out False. Two consequences, both visible in
        # the activity ledger:
        #
        #   * the egress gate SEALED payloads on their way to Friday's own
        #     local seat — vault-tier spans redacted before reaching a model
        #     that is allowed to see them, which defeats the entire reason the
        #     local tier exists;
        #   * the ledger labelled those turns `gemma4:12b · openai`, so a local
        #     model read as a cloud one.
        "adapter": "openai-compatible",
        "base_url": base,
        "models": [model_id],
        "auth": {"type": "none"},
        "network": {"timeout_s": 600},
    }


def _post(url, body, timeout=600):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ─────────────────────────────────────────────────────────────────────────────
#  Backend drivers
# ─────────────────────────────────────────────────────────────────────────────

class OllamaBackend:
    """Leased seats only. Its scheduler evicts, which is fine during a lease."""

    name = "ollama"

    def __init__(self, base_url=OLLAMA_URL):
        self.base_url = base_url

    def resident(self):
        try:
            return {m["name"]: round((m.get("size_vram") or 0) / 1048576)
                    for m in (_get(self.base_url + "/api/ps") or {})
                    .get("models", [])}
        except Exception:
            return {}

    def has(self, model_id) -> bool:
        """Does the daemon hold this model at all? `/api/tags`, not `/api/ps`:
        the question is whether a load could succeed, not whether one has."""
        try:
            names = {str(m.get("name") or m.get("model") or "")
                     for m in (_get(self.base_url + "/api/tags") or {})
                     .get("models", [])}
        except Exception:
            return False
        mid = str(model_id)
        return mid in names or (mid + ":latest") in names

    def load(self, model_id, num_ctx, keep_alive="15m", think=False):
        body = {"model": model_id, "prompt": "hi", "stream": False,
                "keep_alive": keep_alive,
                "options": {"num_ctx": num_ctx, "num_predict": 8}}
        if think is False:
            body["think"] = False
        _post(self.base_url + "/api/generate", body)

    def evict(self, model_id):
        try:
            _post(self.base_url + "/api/generate",
                  {"model": model_id, "prompt": "", "keep_alive": 0},
                  timeout=120)
        except Exception:
            pass

    def evict_all(self):
        for name in list(self.resident()):
            self.evict(name)


#: Where Friday keeps its own copy of the engine described below, so the
#: capability survives Ollama being uninstalled.
VENDORED_E_SERIES_ENGINE = ("llama.cpp-ollama", "llama-server.exe")


def ollama_engine_path() -> Path:
    """The llama-server build that can read the Gemma-4 e-series.

    Not the daemon. The daemon is what R9 exists because of; this is the engine
    binary that shipped beside it, started and killed by the Arbiter like any
    other seat process. Nothing schedules it but us.

    Ollama is not a dependency of Friday. The daemon serves nothing —
    `_DAEMON_MODELS` is empty because channel_toolcalls.py parses the e-series
    tool-call format directly. The only thing wanted from an Ollama install is
    this binary, because upstream llama.cpp refuses `gemma4:e2b` from a
    provably intact file ("wrong number of tensors; expected 2012, got 601")
    and FridayWeaver is built on it.

    So the engine lives in Friday's own runtime and Ollama is not required.
    Friday's copy comes first; the original path stays as a fallback for an
    install that still has Ollama, because a machine that has not run the
    migration should not lose the seat over it.
    """
    try:
        mine = runtime_dir().joinpath(*VENDORED_E_SERIES_ENGINE)
        if mine.exists():
            return mine
    except Exception:
        pass
    return (Path.home() / "AppData" / "Local" / "Programs" / "Ollama" /
            "lib" / "ollama" / "llama-server.exe")


# Which engine successfully loaded which model, learned at runtime and kept, so
# a model that upstream cannot parse does not pay a failed load on every boot.
_ENGINE_MEMO: dict = {}

# Models we deliberately leave on the Ollama daemon. Currently EMPTY.
#
# It briefly held gemma4:e2b and e4b. Those models emit tool calls in a channel
# format --  <|tool_call>call:get_weather{city:Oslo}<tool_call|>  -- which only
# Ollama's DAEMON parsed, so serving them as processes we owned cost tool
# calling outright: the model called correctly and `tool_calls` came back None.
#
# services/channel_toolcalls.py now parses that format directly, and the seat
# was verified as OUR process completing a five-call dependent chain with the
# right arguments and the right answer. The exception no longer has a reason to
# exist.
#
# The mechanism stays. The next model with a private wire format belongs here,
# with its measurement, rather than in a silent special case somewhere.
DAEMON_SERVED: dict = {}



# ── Seats that outlived the process that spawned them ───────────────────────
#
# `procs` lives in memory, so a restart forgets every llama-server it started
# while the processes themselves keep running and keep their VRAM. Measured
# on the reference machine: EIGHT of them after a day of restarts, holding
# ~4.1 GB between them, none known to the Arbiter that had just booted. That
# is the same defect as a daemon seating a model behind our back -- the rule
# is that nothing occupies that GPU without the Arbiter knowing, and it does
# not care who started it.
#
# So boot LOOKS first: whatever is serving a model the plan wants is adopted,
# and everything else on our ports is reaped.


class AdoptedProc:
    """A llama-server this process did not spawn but now owns.

    Quacks like `subprocess.Popen` for the four calls `evict` makes, so an
    adopted seat is evicted, leased against and republished exactly like one
    we started. Anything less would make adoption a second class of seat.
    """

    def __init__(self, pid: int):
        self.pid = int(pid)
        self._rc = None

    def _alive(self) -> bool:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"if (Get-Process -Id {self.pid} -ErrorAction SilentlyContinue)"
                 f" {{'1'}} else {{'0'}}"],
                capture_output=True, text=True, timeout=10)
            return out.stdout.strip().endswith("1")
        except Exception:
            return False

    def poll(self):
        if self._rc is not None:
            return self._rc
        return None if self._alive() else 0

    def terminate(self):
        try:
            subprocess.run(["taskkill", "/PID", str(self.pid)],
                           capture_output=True, timeout=20)
        except Exception:
            pass

    def kill(self):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(self.pid)],
                           capture_output=True, timeout=20)
            self._rc = 0
        except Exception:
            pass

    def wait(self, timeout=None):
        deadline = time.time() + (timeout or 30)
        while time.time() < deadline:
            if not self._alive():
                self._rc = 0
                return 0
            time.sleep(0.5)
        raise TimeoutError(f"pid {self.pid} did not exit")


def _listening_ports() -> dict:
    """`port -> owning pid` for everything listening, in one call."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetTCPConnection -State Listen | "
             "Select-Object -Property LocalPort,OwningProcess | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30)
        rows = json.loads(out.stdout or "[]")
        if isinstance(rows, dict):
            rows = [rows]
        return {int(r["LocalPort"]): int(r["OwningProcess"]) for r in rows}
    except Exception:
        return {}


def _llama_server_pids() -> set:
    """llama-server processes serving OUR models. Not the daemon's.

    The discriminator is the MODEL PATH, not the binary. Ollama's daemon
    spawns the same `llama-server.exe` for its own models, and the Arbiter
    deliberately uses Ollama's binary for the e-series that upstream cannot
    parse — so "is this Ollama's exe" answers the wrong question in both
    directions.

    Ours load from `~/.friday/runtime/models`; the daemon's load from
    `~/.ollama/models/blobs`. Matching on the binary name alone reaps
    Ollama's live runner along with the real orphans, forcing the daemon to
    reload a model mid-session. Killing another scheduler's process is exactly the
    discourtesy this module exists to stop, and it does not stop being one
    when we are the ones doing it.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
             "Where-Object { $_.CommandLine -like '*.friday*runtime*models*' } | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=30)
        return {int(x) for x in out.stdout.split() if x.strip().isdigit()}
    except Exception:
        return set()


def _model_on_port(port: int) -> str | None:
    """What a llama-server is actually holding. The server, not a record."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/v1/models" % port, timeout=2) as r:
            data = json.loads(r.read().decode()) or {}
        rows = data.get("models") or data.get("data") or []
        for m in rows:
            mid = m.get("id") or m.get("name")
            if mid:
                return str(mid)
    except Exception:
        pass
    return None


#: Flags the Arbiter must own no matter what a model record says. These are
#: not preferences — they are how the Arbiter finds, identifies and reaps its
#: own seats, so a model that overrode them would become invisible to the
#: process responsible for it.
_RESERVED_SERVE_FLAGS = {"-m", "--model", "--alias", "--host", "--port",
                         "-c", "--ctx-size", "--mmproj",
                         "--chat-template-file"}


def _merge_declared_args(cmd: list, declared: list) -> list:
    """Apply a model's declared llama-server flags over the default command.

    A declared flag REPLACES the default's value for that flag rather than
    appending a second copy, because llama.cpp takes the last occurrence and a
    command carrying `-ub 512 ... -ub 2048` reads as a contradiction to anyone
    debugging it later. Flags in `_RESERVED_SERVE_FLAGS` are refused: the
    Arbiter has to be able to identify and reap what it spawned.
    """
    if not declared:
        return cmd
    out = list(cmd)
    i = 0
    while i < len(declared):
        flag = declared[i]
        if not flag.startswith("-"):
            i += 1
            continue
        has_value = (i + 1 < len(declared)
                     and not declared[i + 1].startswith("-"))
        value = declared[i + 1] if has_value else None
        i += 2 if has_value else 1
        if flag in _RESERVED_SERVE_FLAGS:
            print(f"  [arbiter] ignoring declared {flag}: the Arbiter owns it")
            continue
        if flag in out:
            at = out.index(flag)
            if value is not None and at + 1 < len(out) and \
                    not out[at + 1].startswith("-"):
                out[at + 1] = value
            elif value is not None:
                out.insert(at + 1, value)
        else:
            out.append(flag)
            if value is not None:
                out.append(value)
    return out


#: What a llama.cpp build actually says when it will not take the KV cache
#: flags. Anything outside this list is some other failure wearing the same
#: exit code.
_KV_FLAG_REJECTION_MARKERS = (
    "unknown argument",
    "invalid argument",
    "error while handling argument",
    "unrecognized argument",
    "unsupported cache type",
    "invalid cache type",
    "invalid kv cache type",
    "requires flash attention",
)

#: Failures that are emphatically NOT about the flag, listed so that a message
#: containing both (llama.cpp prints usage text on some errors) is read as the
#: allocation failure it is.
_ALLOCATION_FAILURE_MARKERS = (
    "out of memory",
    "cudamalloc",
    "failed to allocate",
    "unable to allocate",
    "insufficient memory",
    "buffer_type_alloc_buffer",
)


def _reap_our_seat_on_port(port: int) -> bool:
    """Kill a llama-server of OURS still holding `port`. True if one died.

    Deliberately narrow. `_llama_server_pids()` is the same discriminator the
    reaper uses — a process is ours only if it loaded from Friday's own model
    directory — so a seat belonging to anything else on that port is left
    exactly where it is. Killing another scheduler's process is the
    discourtesy this module exists to stop.
    """
    try:
        pid = _listening_ports().get(int(port))
        if not pid or pid not in _llama_server_pids():
            return False
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=20)
        print(f"  [arbiter] reaped our leftover seat pid {pid} on :{port}")
        return True
    except Exception:
        return False


def _reads_as_rejected_kv_flag(err) -> bool:
    """True only when the build itself says it will not take --cache-type-*.

    Conservative on purpose: absence of evidence is not evidence that the
    flag is unsupported, and the caller's fallback is expensive enough that
    guessing wrong costs more than not falling back at all.
    """
    text = str(err or "").lower()
    if not text:
        return False
    if any(m in text for m in _ALLOCATION_FAILURE_MARKERS):
        return False
    return any(m in text for m in _KV_FLAG_REJECTION_MARKERS)


def _seat_num_ctx(pid: int) -> int | None:
    """The `-c` a running llama-server was started with, or None."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | "
             f"Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=20)
        parts = (out.stdout or "").split()
        for i, tok in enumerate(parts):
            if tok == "-c" and i + 1 < len(parts) and parts[i + 1].isdigit():
                return int(parts[i + 1])
    except Exception:
        pass
    return None


def survey_live_seats(port_lo: int = PORT_BASE, port_hi: int = PORT_BASE + 40) -> dict:
    """`model_id -> (pid, port)` for llama-servers answering on our ports.

    THE WINDOW IS NOT THE WHOLE STORY. A fixed 8090..8130 scan finds only the
    seats this Arbiter allocated ports for. A seat spawned by hand, by an
    older build, or by a script that picked its own port is invisible to it —
    and invisible here means unadoptable, unpublished, and (before the guard
    in `adopt_or_reap`) killed as an orphan on the next boot.

    For example, a FridayWeaver seat healthy on :8713 answering as
    `gemma4:e2b-fridayweaver-1.0` would make a window-only survey return {},
    the published record would be written empty, and every local turn would
    fall through to the Ollama daemon — which has zero models — and 404 to the
    cloud. The seat is fine; nothing can see it.

    So the window is a floor, not a fence: any port a llama-server we
    recognise is actually LISTENING on is probed too, wherever it sits.
    """
    listening = _listening_ports()
    ports = {p for p in range(port_lo, port_hi + 1) if p in listening}
    # Ports held by a llama-server of OURS, whatever number it landed on.
    try:
        ours = _llama_server_pids()
        ports |= {p for p, pid in listening.items() if pid in ours}
    except Exception:
        pass
    found = {}
    for port in sorted(ports):
        model = _model_on_port(port)
        if model:
            found[model] = (listening[port], port)
    return found


class LlamaServerBackend:
    """Pinned seats. One process per seat, owned end to end (R9).

    Two engines, and the reason is measured rather than defensive. Upstream
    llama.cpp (build 10415) loads `gemma4:12b` from the extracted GGUF fine and
    refuses `gemma4:e2b` from a file that is provably intact:

        done_getting_tensors: wrong number of tensors; expected 2012, got 601

    That error reads like "Ollama shards its models and llama.cpp cannot
    reassemble them". It is not. The manifest has exactly one model
    layer, the extracted file passes the GGUF magic check, and **Ollama's own
    engine binary loads that same file and generates from it** — verified,
    "ready." in 0.90 s. The gemma4 e-series tensor layout simply
    is not what upstream's gemma3n reader expects.

    So we try upstream first and fall back to the engine that ships with
    Ollama. Either way the process is ours: we spawn it, health-check it, and
    kill it. The daemon is not in the path.
    """

    name = "llama-server"

    def __init__(self, binary: Path | None = None, fallback: Path | None = None):
        self.binary = binary or (runtime_dir() / "llama.cpp" /
                                 "llama-server.exe")
        self.fallback = fallback if fallback is not None else \
            ollama_engine_path()
        self.procs: dict = {}          # model_id -> (Popen, port)
        # Serializes the check-then-spawn in `load`. Two roles naming the same
        # model are pinned one after another, but the boot path is threaded
        # and a 27B takes a minute to come up, which is a wide enough window
        # for both to look, both to see nothing, and both to spawn.
        self._load_lock = threading.RLock()
        self._last_load: dict = {}     # model_id -> the kwargs it was last loaded with
        self._vision_wanted: set = set()

    def resident(self):
        return {m: 0 for m in self.procs}

    @staticmethod
    def _declared_engine(model_id):
        """An engine the model store says this model REQUIRES, or None.

        Learning by trial (`_ENGINE_MEMO`) works when every candidate engine
        can at least attempt the file. It does not work when a model needs a
        binary that is not among the candidates at all, and the failure mode
        is expensive rather than merely slow.

        Bonsai 2 27B, for example, needs the PrismML fork of llama.cpp — stock
        llama.cpp rejects its ternary tensor type outright ("invalid ggml type
        143"). If only its weights, projector and chat template are recorded,
        the Arbiter does exactly what it was built to do: sees the seat down,
        kills the working process, respawns with
        `runtime/llama.cpp/llama-server.exe`, gets `exited 1`, marks the seat
        NOT SERVING (R9), and every turn escalates to the cloud. From the
        user's side a local model simply stops answering and a cloud model
        replies instead, for reasons nothing on screen explains.

        A model that needs a particular runtime should be able to say so, in
        the same record that holds its weights. Declared beats learned, and
        both still beat guessing.
        """
        try:
            from agent_friday.services import model_store as _ms
            rec = _ms.get(model_id) or {}
            cand = (rec.get("engine") or "").strip()
            if cand and Path(cand).exists():
                return Path(cand)
        except Exception:
            pass
        return None

    def engines_for(self, model_id):
        """Engines to try, best first, with anything already learned first."""
        known = _ENGINE_MEMO.get(model_id)
        order = [self.binary]
        if self.fallback and Path(self.fallback).exists():
            order.append(Path(self.fallback))
        if known:
            order = [Path(known)] + [b for b in order if str(b) != str(known)]
        # A declared requirement outranks both the default and the memo: the
        # memo can only remember engines that were tried, and the whole point
        # here is an engine that was never a candidate.
        declared = self._declared_engine(model_id)
        if declared:
            order = [declared] + [b for b in order
                                  if str(b) != str(declared)]
        return [b for b in order if Path(b).exists()]

    def load(self, model_id, num_ctx, *, gguf_path, port,
             n_cpu_moe=None, timeout=300, lora_path=None, mmproj_path=None):
        # A SEAT IS A MODEL, NOT A ROLE.
        #
        # `_pin` calls this once per pinned ROLE, and several roles routinely
        # name the same model — on this machine `reasoning`, `heavy_hitter`,
        # `subagent`, `memory_manager` and `local` were all bonsai2:27b.
        # Nothing here checked whether the model was already served, so each
        # role spawned its own 27B process: two were found alive on :8090 and
        # :8091 holding 11,605 MiB of a 12,282 MiB card between them, and
        # because `self.procs` is keyed by model_id the second overwrote the
        # first's entry — so the first was not merely redundant, it was
        # orphaned, unevictable and invisible to the thing responsible for it.
        # A chat turn arriving in that state never returns.
        #
        # Roles share a seat. That was always the intent — `self.procs` being
        # keyed by model is the proof — it simply was not enforced at the one
        # place that creates them.
        self._last_load[model_id] = dict(
            num_ctx=num_ctx, gguf_path=gguf_path, port=port, n_cpu_moe=n_cpu_moe,
            timeout=timeout, lora_path=lora_path, mmproj_path=mmproj_path)
        with self._load_lock:
            existing = self._already_serving(model_id)
            if existing is not None:
                return existing
            return self._load_locked(
                model_id, num_ctx, gguf_path=gguf_path, port=port,
                n_cpu_moe=n_cpu_moe, timeout=timeout, lora_path=lora_path,
                mmproj_path=mmproj_path)

    def _already_serving(self, model_id):
        """0.0 if this model is already seated and answering, else None.

        Checks the process we remember first, then the ports, because a seat
        can outlive the Arbiter that started it (see `adopt_or_reap`) and
        spawning a second copy of a 27B model is not a recoverable mistake on
        a 12 GB card.
        """
        entry = self.procs.get(model_id)
        if entry is not None:
            proc, port = entry
            try:
                if proc.poll() is None and _model_on_port(port) == model_id:
                    return 0.0
            except Exception:
                pass
            # Remembered but not actually serving: forget it rather than
            # refuse to reload, and say so.
            print(f"  [arbiter] {model_id} was recorded on :{port} but is not "
                  f"serving; reloading")
            self.procs.pop(model_id, None)
        try:
            live = survey_live_seats()
        except Exception:
            return None
        if model_id in live:
            pid, port = live[model_id]
            cap = self.seat_cap(model_id)
            over = _seat_num_ctx(pid)
            if over and over > cap:
                return None      # non-conforming; let the caller reload it
            self.procs[model_id] = (AdoptedProc(pid), port)
            _publish_endpoints(self.procs)
            print(f"  [arbiter] {model_id} already serving on :{port} "
                  f"(pid {pid}) — adopted instead of spawning a second copy")
            return 0.0
        return None

    def _load_locked(self, model_id, num_ctx, *, gguf_path, port,
                     n_cpu_moe=None, timeout=300, lora_path=None,
                     mmproj_path=None):
        engines = self.engines_for(model_id)
        if not engines:
            raise TransitionError("no llama-server binary found (looked at %s "
                                  "and %s)" % (self.binary, self.fallback))
        last = None
        for i, binary in enumerate(engines):
            try:
                took = self._spawn(binary, model_id, num_ctx,
                                   gguf_path=gguf_path, port=port + i,
                                   n_cpu_moe=n_cpu_moe, timeout=timeout,
                                   lora_path=lora_path,
                                   mmproj_path=mmproj_path)
                _ENGINE_MEMO[model_id] = str(binary)
                return took
            except TransitionError as e:
                last = e
                # Belt and braces for the overlap described in `_spawn_once`:
                # whatever that attempt may have left listening on its port is
                # ours and is in the way of the next engine. Anything not
                # recognisably one of our seats is left alone.
                _reap_our_seat_on_port(port + i)
        raise TransitionError("no engine could load %s: %s" % (model_id, last))

    # A seat's window is the Arbiter's decision, and it is bounded.
    #
    # MEASURED on this card: the 12b at 131072 took 11,351 MiB of a 12,282 MiB
    # GPU on ONE request. At 262144 -- the model's architectural maximum, which
    # is what `models.json` reports and what a seat gets when nothing clamps it
    # -- boot alone left 448 MiB free, under the 1024 MiB display reserve, in
    # the exact state that drops a second monitor.
    #
    # MEASURED ON THIS CARD, and the answer was not the expected
    # one. The roles contract (docs/reference/roles-and-model-identity.md
    # 6a) sets TOOL_SEAT_NUM_CTX to 65,536 on the reasoning that over-
    # reserving is nearly free because the KV curve is flat -- about 32 MiB --
    # while under-reserving truncates silently. On gemma4:12b on this RTX 4070
    # the curve is not flat:
    #
    #     32,768 -> 1,936 MiB free of 12,282
    #     65,536 ->   551 MiB free      <-- under the 1024 MiB display reserve
    #
    # A 1,385 MiB difference, not 32. The compute buffer scales with context
    # and dwarfs the KV cache at long windows, which is the same effect
    # already recorded on the batch-size flag below. 65,536 costs the user a
    # second monitor on this hardware.
    #
    # The silent-truncation worry also does not apply to these seats:
    # llama-server REJECTS an over-length request with a 400 naming the token
    # count -- observed, "request (47448 tokens) exceeds the available context
    # size" -- rather than dropping the oldest spans. Loud beats quiet, and
    # the fallback is reported to the caller.
    #
    # So: 32,768 here, and the contract's 65,536 wants re-measuring on this
    # card before it is applied to it.
    #
    # Superseded reasoning, kept because the shape of the trade is right:
    # (docs/reference/roles-and-model-identity.md §6a, branch
    # model-suite-determination) showed 32,768 to be the WRONG side of this
    # trade: a real turn does not fit in it, and the overflow is silent --
    # the oldest spans fall out the front and the model answers from a
    # truncated view without anyone being told. Over-reserving is nearly free
    # on this model family because the KV curve is flat; under-reserving
    # corrupts the answer quietly. Between a cost measured in tens of MiB and
    # a failure nobody can see, take the MiB.
    #
    # The ceiling stays, because the thing it was built to stop is real: this
    # seat spawned at its architectural 262,144 left 448 MiB of 12,282 and
    # took a monitor off the desktop. A claim about a flat curve is not the
    # same as a measurement on the card.
    #
    # THE CEILING IS 131,072 BECAUSE IT IS MEASURED WITH THE BATCH CAPS THIS
    # CLASS ALWAYS SPAWNS WITH.
    #
    # At the default batch the compute buffer scales with context and dwarfs
    # the KV cache (a 1,385 MiB delta per doubling), which is why
    # `_spawn_once` pins `-b 512 -ub 512`. Measured end to end on a 12,282 MiB
    # card (RTX 4070), each rung actually serving a completion before the card
    # was read:
    #
    #     no seat at all          1,791 MiB
    #     -c 32768                5,398 MiB   (seat: 3,607)
    #     -c 65536                5,622 MiB   (seat: 3,831, +224 over 32k)
    #     -c 131072               6,082 MiB   (seat: 4,291, +684 over 32k)
    #
    # Doubling twice costs 684 MiB, not 1,385 per doubling, and 131,072 leaves
    # 6,200 MiB free against a 2,560 MiB display reserve. A lower ceiling
    # measures the compute buffer, not the context.
    #
    # Why a smaller window is not "safe": at 32,768 the tool budget trims 75
    # core tools down to 40-49 on every turn to fit 16-18k-token prompts, and
    # that trimming can drop `knowledge_query` -- Friday's own route into its
    # knowledge graph. A
    # window that forces the agent to discard its capabilities mid-turn is not
    # a safe default, it is a quiet one.
    # EVERY NUMBER ABOVE WAS MEASURED ON gemma4:12b, AND IT DOES NOT TRANSFER.
    #
    # Gemma 4 interleaves five sliding-window layers with one full-attention
    # layer, eight times over — only the eight full layers scale with `-c`,
    # which is exactly why the curve looked flat enough to justify 131,072.
    # Bonsai 2 27B is a dense Qwen3.5: every layer's KV scales, and the model
    # is more than twice the size. Measured on the same card, the
    # 27B at 65,536 held 11,518 MiB of 12,282 and left 495 MiB free, under
    # even the old 1,024 MiB display reserve, let alone the 2,560 in force.
    # At 131,072 — what this cap allows — the seat thrashes and a turn takes
    # tens of seconds.
    #
    # The cap is not wrong; it is a ceiling derived from one model family and
    # applied to all of them. A model whose serving window has been measured
    # should be able to say so in the same record that holds its weights and
    # its engine, and the arbiter should believe it. Declared beats the class
    # default, and the class default still beats guessing — the ceiling stays
    # a ceiling, so a declaration can only ever lower it.
    MAX_SEAT_NUM_CTX = 131072

    @staticmethod
    def _declared_num_ctx(model_id):
        """A serving window the model store says this model was measured at.

        Returns None when nothing is declared, so the class ceiling applies
        unchanged. Never raises: a store that cannot be read must not stop a
        seat from loading.
        """
        try:
            from agent_friday.services import model_store as _ms
            rec = _ms.get(model_id) or {}
            v = int(rec.get("serve_num_ctx") or 0)
            return v if v > 0 else None
        except Exception:
            return None

    @classmethod
    def seat_cap(cls, model_id):
        """The largest window this model may be served at on this machine."""
        declared = cls._declared_num_ctx(model_id)
        if declared:
            return min(int(declared), int(cls.MAX_SEAT_NUM_CTX))
        return int(cls.MAX_SEAT_NUM_CTX)

    @staticmethod
    def _declared_serve_args(model_id):
        """Extra llama-server flags this model was measured with, or []."""
        try:
            from agent_friday.services import model_store as _ms
            rec = _ms.get(model_id) or {}
            args = rec.get("serve_args")
            if isinstance(args, (list, tuple)):
                return [str(a) for a in args if str(a).strip()]
        except Exception:
            pass
        return []

    # KV CACHE QUANTIZATION. Off by default in llama.cpp, which stores K and V
    # at f16; q8_0 halves that (8 bits plus a 2-byte scale per 32 elements =
    # 1.0625 bytes/element against 2).
    #
    # Computed from gemma4-12b.gguf's own metadata, at the
    # -c 32768 this seat actually runs:
    #
    #     f16   832 MiB      q8_0   442 MiB      saved 390 MiB (47%)
    #
    # Modest on purpose, and worth saying so rather than implying a bigger
    # win: Gemma 4 interleaves five sliding-window layers (1024-token window,
    # 8 KV heads, 256+256 dims) with one full-attention layer (1 KV head,
    # 512+512), eight times over. Only the eight full layers scale with -c, so
    # the f16 cache was never the multi-gigabyte object it is on a dense
    # model. 390 MiB is real headroom on a card with ~700 MiB free; it is not
    # room for another seat.
    #
    # q8_0 rather than q4_0: the quality cost of a quantized KV rises sharply
    # below 8 bits, and this seat holds long tool-loop transcripts where an
    # early token being wrong compounds. Halving is the safe half of the
    # available win.
    #
    # Requires flash attention for the V cache, which this command already
    # passes unconditionally.
    KV_CACHE_TYPE = "q8_0"

    def _kv_cache_type(self) -> str:
        """The KV cache type to spawn seats with. Settings override, then the
        class default, then f16 once a spawn has proved the flag unusable."""
        if getattr(self, "_kv_quant_unsupported", False):
            return "f16"
        try:
            from agent_friday.core import _load_settings
            v = ((_load_settings() or {}).get("model_routing") or {}).get(
                "kv_cache_type")
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception:
            pass
        return self.KV_CACHE_TYPE

    def _spawn(self, binary, model_id, num_ctx, *, gguf_path, port,
               n_cpu_moe=None, timeout=300, lora_path=None, mmproj_path=None):
        """Spawn a seat, retrying once unquantized if the KV flag is rejected.

        The retry exists because this flag is the only argument in the command
        that a given llama.cpp build may not accept, and the failure mode
        without it is the worst one available: the seat never comes up, every
        local call 404s, and — per commit 8a30831 — every turn silently
        escalates to the cloud. A boot that is 30 seconds slower and correct
        beats a boot that is fast and seatless.
        """
        quantized = self._kv_cache_type() not in ("", "f16")
        try:
            return self._spawn_once(binary, model_id, num_ctx,
                                    gguf_path=gguf_path, port=port,
                                    n_cpu_moe=n_cpu_moe, timeout=timeout,
                                    lora_path=lora_path,
                                    mmproj_path=mmproj_path)
        except TransitionError as e:
            if not quantized or getattr(self, "_kv_quant_unsupported", False):
                raise
            if not _reads_as_rejected_kv_flag(e):
                # An unexplained failure is usually transient here: the seat
                # being replaced is often still releasing its VRAM when the
                # new one starts: a spawn can die silently during model load
                # and the same arguments come up fine seconds later. So retry
                # once as-is, and keep the
                # quantized cache we came for.
                if not getattr(self, "_kv_retry_in_flight", False):
                    self._kv_retry_in_flight = True
                    try:
                        print("  [arbiter] %s: spawn failed (%s) — retrying "
                              "once with the same settings." % (model_id, e))
                        time.sleep(5)
                        return self._spawn_once(
                            binary, model_id, num_ctx, gguf_path=gguf_path,
                            port=port, n_cpu_moe=n_cpu_moe, timeout=timeout,
                            lora_path=lora_path, mmproj_path=mmproj_path)
                    finally:
                        self._kv_retry_in_flight = False
                # The f16 fallback must NOT fire on any spawn failure. A seat
                # that fails to load because the card is full would read as
                # "this build rejects --cache-type-k", latch f16 for the whole
                # process, and spawn every subsequent seat with a KV cache
                # twice the size — on the card that just ran out of room. A
                # fallback that makes its own trigger more likely is not a
                # fallback.
                #
                # So the downgrade needs the build to actually say so.
                # Anything else is reported as what it is and re-raised: an
                # out-of-memory spawn should look like an out-of-memory spawn,
                # not like a capability this machine turns out not to have.
                raise
            self._kv_quant_unsupported = True
            print("  [arbiter] %s: spawn failed with a quantized KV cache "
                  "(%s) — retrying at f16. This build may not support "
                  "--cache-type-k/v; seats will use f16 for the rest of this "
                  "process." % (model_id, e))
            # `_log` is function-local elsewhere in this module, not a
            # module global — import here rather than assume one exists.
            __import__("logging").getLogger("friday.residency").warning(
                "KV quantization rejected by %s spawning %s (%s); "
                "falling back to f16", Path(binary).name, model_id, e)
            return self._spawn_once(binary, model_id, num_ctx,
                                    gguf_path=gguf_path, port=port,
                                    n_cpu_moe=n_cpu_moe, timeout=timeout,
                                    lora_path=lora_path,
                                    mmproj_path=mmproj_path)

    def _spawn_once(self, binary, model_id, num_ctx, *, gguf_path, port,
                    n_cpu_moe=None, timeout=300, lora_path=None,
                    mmproj_path=None):
        try:
            asked = int(num_ctx or 0)
        except Exception:
            asked = 0
        cap = self.seat_cap(model_id)
        if asked > cap:
            why = ("declared for this model" if cap < self.MAX_SEAT_NUM_CTX
                   else "to keep the display reserve")
            print(f"  [arbiter] {model_id}: capping context {asked:,} -> "
                  f"{cap:,} ({why})")
            num_ctx = cap
        cmd = [str(binary), "-m", str(gguf_path), "--alias", model_id,
               "--host", "127.0.0.1", "--port", str(port),
               "-ngl", "99", "--flash-attn", "on", "-c", str(num_ctx),
               "--jinja", "--no-webui",
               # Batch size caps the COMPUTE buffer, which scales with context
               # and dwarfs the KV cache at long windows. Measured: the 12b at
               # 131072 took 11351 MiB of GPU at the default batch against 7813
               # under Ollama, which runs -b 512 -ub 512. The extra ~3.5 GB is
               # the compute buffer, not the model, and it is the difference
               # between the pinned pair fitting and not.
               "-b", "512", "-ub", "512"]
        # `-b 512 -ub 512` is measured on gemma4:12b and is not universal
        # either. On Bonsai 2 27B, -ub 2048 lifted prompt processing from 370
        # to 467 tok/s on a 16k prompt, and -np 1 meant every background probe
        # queued behind a chat turn on the one slot — the seat log showed
        # four-token requests taking seven to twenty seconds because of it.
        # A model that has been measured can declare the flags it was measured
        # with, by the same argument as `engine` and `serve_num_ctx` above: a
        # per-model fact belongs in the per-model record, not in a class
        # constant that some other model's measurement set.
        cmd = _merge_declared_args(cmd, self._declared_serve_args(model_id))
        # See KV_CACHE_TYPE above. Settings can pin this back to "f16" without
        # a code change; `_spawn_with_fallback` retries unquantized if the
        # binary rejects the flag, so a build that does not support it costs a
        # slower boot rather than the local seat.
        kv_type = self._kv_cache_type()
        # A model that declared its own KV type (serve_args) keeps it: llama.cpp
        # takes the LAST occurrence of a flag, so appending the global one
        # after the declared one would silently undo the measurement.
        if kv_type and kv_type != "f16" and "--cache-type-k" not in cmd:
            cmd += ["--cache-type-k", kv_type, "--cache-type-v", kv_type]
        # A seat with no chat template silently falls back to ChatML, which
        # leaks `<|im_end|>` into replies and — the part that matters — hands
        # the seat a template with NO TOOL DEFINITIONS. gemma4:e2b and e4b ship
        # without an embedded template; gguf_extract borrows the 12b's (same
        # architecture, same tokenizer) and writes it beside the weights.
        # Without this the sidekick would look like a model that cannot call
        # tools, which is the misdiagnosis this codebase has already made twice.
        try:
            from agent_friday.services import gguf_extract as _gx
            tmpl = _gx.chat_template_path(model_id)
            if tmpl.exists():
                cmd += ["--chat-template-file", str(tmpl)]
        except Exception:
            pass
        # The vision tower, by exactly the same argument as the template above,
        # and this is the THIRD time this shape of bug has been found here.
        # `gguf_extract.extract` has always copied the projector out beside the
        # weights and reported it; `_spawn` never passed it on. So a seat with
        # `gemma4-12b.mmproj.gguf` sitting 160 MB away in the same directory
        # answered llama.cpp's own
        #   "image input is not supported - hint: ... you may need to provide
        #    the mmproj"
        # to every image (measured on the reference machine, two images, two colours).
        #
        # That is the same misdiagnosis in a new costume: a model that CAN see
        # looking like a model that cannot, because a file next to it was never
        # handed over. A text-only model simply has no projector here, so a
        # missing file is a normal outcome and not a failure.
        #
        # The store's own projector comes first (`models.json` `mmproj`,
        # resolved by `model_store.seat_files` to a local copy when one
        # exists); the extractor's side-file is the fallback for models that
        # were imported from Ollama before the store recorded projectors.
        if self._vision_on_demand(model_id) and model_id not in self._vision_wanted:
            # VISION ON DEMAND (models.json `vision: "on_demand"`). The
            # projector costs ~500 MiB of VRAM and, on a card already near
            # full, it measured as the difference between 482 and 109 tok/s of
            # prefill. The seat loads without it; the first request that
            # carries an image calls `ensure_vision`, which reloads this seat
            # with the projector.
            pass
        elif mmproj_path:
            cmd += ["--mmproj", str(mmproj_path)]
        else:
            try:
                from agent_friday.services import gguf_extract as _gx
                proj = _gx.projector_path(model_id)
                if proj.exists():
                    cmd += ["--mmproj", str(proj)]
            except Exception:
                pass
        # The adapter. FridayWeaver-1.0 is a Q8_0 base plus a LoRA applied at
        # serve time; without `--lora` the process that comes up under that
        # name is the stock base model, which is the silent wrong-model
        # failure Friday-Models/docs/DECISIONS.md refuses.
        # A record that names an adapter and a spawn that cannot pass it is a
        # TransitionError, never a base seat under the fine-tune's name.
        if lora_path:
            if not Path(str(lora_path)).exists():
                raise TransitionError(
                    "%s names adapter %s and it is not reachable; refusing "
                    "to serve the base under the fine-tune's name"
                    % (model_id, lora_path))
            cmd += ["--lora", str(lora_path)]
        if n_cpu_moe is not None:
            cmd += ["--n-cpu-moe", str(n_cpu_moe)]
        # THE SEAT'S OWN BANNER, KEPT. This was stdout=DEVNULL/stderr=DEVNULL,
        # which threw away the only place llama.cpp ever states what it
        # actually did: the context it settled on, the KV cache type, whether
        # the mmproj loaded, and the reason it refused to start. That is how a
        # seat serving 32,768 of a 262,144 native window went unnoticed -- the
        # number was printed at load and discarded in the same breath. Same
        # disease as the Arbiter's own boot messages disappearing into the
        # tray's DEVNULL; this is the other half of it.
        #
        # A FILE, never a pipe: nothing here drains the seat's output, and a
        # pipe that fills blocks the model mid-generation. The child inherits
        # its own handle, so closing ours after the spawn leaks nothing.
        log_path = None
        try:
            import re as _re
            log_path = (runtime_dir() / "logs" /
                        ("llama-%s-%d.log"
                         % (_re.sub(r"[^A-Za-z0-9._-]", "_", str(model_id)), port)))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "ab", buffering=0) as _fh:
                _fh.write(("\n=== %s  %s  port %d ===\n"
                           % (time.strftime("%Y-%m-%dT%H:%M:%S"), model_id,
                              port)).encode())
                proc = subprocess.Popen(cmd, stdout=_fh,
                                        stderr=subprocess.STDOUT,
                                        cwd=str(Path(binary).parent))
        except Exception:
            # Never let logging cost a seat. If the file cannot be opened we
            # are back to the old behaviour, which worked -- silently.
            log_path = None
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    cwd=str(Path(binary).parent))
        t0 = time.time()
        while time.time() - t0 < timeout:
            if proc.poll() is not None:
                # The reason is in the banner we now keep. Putting the tail in
                # the exception is the difference between "exited 1" and a
                # diagnosis -- the same argument as logging the 400 body.
                raise TransitionError(
                    "%s exited %s loading %s%s"
                    % (Path(binary).parent.name, proc.returncode, model_id,
                       _tail(log_path)))
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/health" % port, timeout=3) as r:
                    ready = r.status == 200
            except Exception:
                ready = False
            if ready:
                if lora_path:
                    why = self._adapter_missing(port, lora_path)
                    if why:
                        proc.terminate()
                        raise TransitionError(
                            "%s came up without its adapter (%s); stopped "
                            "rather than serve the base under the "
                            "fine-tune's name" % (model_id, why))
                self.procs[model_id] = (proc, port)
                _publish_endpoints(self.procs)
                return round(time.time() - t0, 2)
            time.sleep(1.5)
        # TERMINATE IS A REQUEST; THE VRAM IS NOT FREE UNTIL THE PROCESS IS.
        #
        # `proc.terminate()` followed straight by the raise would let `load()`
        # move on to the next engine on the next port immediately. A 27B seat
        # takes seconds to die and release ten gigabytes, so the two overlap:
        # two bonsai2:27b servers alive on :8090 and :8091 at once can hold
        # 11,605 MiB of a 12,282 MiB card. The second spawn then competes with
        # the corpse of the first for the memory it needs, which is a good way
        # to turn one slow boot into two failures.
        #
        # So wait for the exit, escalate to a kill if the request is ignored,
        # and only then report the failure. A caller that is about to try
        # another engine is entitled to a card in the state this one found it.
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
        raise TransitionError("%s never became ready in %ss for %s"
                              % (Path(binary).name, timeout, model_id))

    @staticmethod
    def _adapter_missing(port: int, lora_path) -> str | None:
        """Ask the seat what adapters it loaded. `None` when the adapter is
        there at a non-zero scale; otherwise the reason.

        A definitive negative (the endpoint answers and the adapter is not in
        the list, or is at scale 0) is a refusal. A build without the
        endpoint, or a transient error, is not: the spawn passed `--lora` and
        llama-server exits non-zero when it cannot load one, so the process
        being up is itself the evidence in that case.
        """
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/lora-adapters" % port,
                    timeout=5) as r:
                if r.status != 200:
                    return None
                rows = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            return None
        if not isinstance(rows, list):
            return None
        want = Path(str(lora_path)).name.lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = str(row.get("path") or "").lower()
            if path.endswith(want) or Path(path).name == want:
                try:
                    scale = float(row.get("scale", 0))
                except Exception:
                    scale = 0.0
                if scale > 0:
                    return None
                return "adapter listed at scale %s" % scale
        return "adapter not in /lora-adapters (%d listed)" % len(rows)

    @staticmethod
    def _vision_on_demand(model_id):
        """models.json declares `vision: "on_demand"` for this model."""
        try:
            from agent_friday.services import model_store as _ms
            return str((_ms.get(model_id) or {}).get("vision") or "") == "on_demand"
        except Exception:
            return False

    def ensure_vision(self, model_id, params=None):
        """Make sure `model_id` is served WITH its projector. A no-op unless
        the model declares vision on demand. Reloads the seat (same context,
        same flags) when it is running without one; returns True when the
        seat can take images afterwards."""
        if not self._vision_on_demand(model_id):
            return True
        with self._load_lock:
            if model_id in self._vision_wanted:
                return True
            params = dict(params or self._last_load.get(model_id) or {})
            if not params.get("gguf_path") or not params.get("port"):
                return False
            self._vision_wanted.add(model_id)
            print(f"  [arbiter] {model_id}: an image arrived; reloading with the "
                  f"vision projector")
            self.evict(model_id)
            num_ctx = params.pop("num_ctx", None)
            try:
                self._load_locked(model_id, num_ctx, **params)
            except Exception as e:
                self._vision_wanted.discard(model_id)
                print(f"  [arbiter] {model_id}: could not load the projector: {e}")
                return False
            return True

    def evict(self, model_id):
        entry = self.procs.pop(model_id, None)
        # `drop` because the seat is still answering /health at this point —
        # termination comes below — and the merge would otherwise keep it.
        _publish_endpoints(self.procs, drop=(model_id,))
        if not entry:
            return
        proc, _ = entry
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except Exception:
                proc.kill()

    def evict_all(self):
        for m in list(self.procs):
            self.evict(m)

    def adopt_or_reap(self, wanted: set) -> dict:
        """Take ownership of what the plan wants; kill what it does not.

        Called at boot, before anything is loaded. `wanted` is the set of
        model ids the computed plan intends to keep pinned.

        Adoption is not politeness — it is the only way the invariant holds
        across a restart. An orphan we cannot see is an orphan we cannot
        evict when a lease needs the card, and it still costs its VRAM.
        Reaping the rest is what stops one leak per restart.
        """
        report = {"adopted": [], "reaped": []}
        try:
            live = survey_live_seats()
        except Exception as e:
            print(f"  [arbiter] could not survey live seats: {e}")
            return report

        # Seats WE already hold, whether or not they are listening yet. A
        # llama-server loading a 262k-context model takes tens of seconds and
        # answers nothing while it does; without this it is invisible to the
        # survey, unclaimed, and reaped by its own owner mid-load.
        claimed = set()
        for _m, _entry in list(self.procs.items()):
            try:
                _pid = getattr(_entry[0], "pid", None)
                if _pid:
                    claimed.add(int(_pid))
            except Exception:
                pass

        for model_id, (pid, port) in live.items():
            # Adoption inherits whatever the previous process decided, and a
            # seat started at 262144 keeps that window forever if we simply
            # take it. Boot then reports a healthy adoption while the card sits
            # at 448 MiB free. A seat that does not conform to policy is not
            # adopted; it is reaped, and the plan reloads it inside the cap.
            over = _seat_num_ctx(pid)
            _cap = self.seat_cap(model_id)
            if over and over > _cap:
                print(f"  [arbiter] not adopting {model_id} on :{port}: it was "
                      f"started at {over:,} context, over the "
                      f"{_cap:,} cap - reloading it instead")
                continue

            if model_id in wanted and model_id not in self.procs:
                self.procs[model_id] = (AdoptedProc(pid), port)
                claimed.add(pid)
                report["adopted"].append(f"{model_id} on :{port} (pid {pid})")
            elif model_id in self.procs:
                claimed.add(pid)

        # REAP ONLY WHAT THE SURVEY COULD IDENTIFY.
        #
        # `_llama_server_pids() - claimed` reaps by ABSENCE: anything the
        # survey did not return is assumed dead weight and killed. That
        # inverts the burden of proof on the one question where a wrong
        # answer is unrecoverable — a process we cannot identify is not the
        # same as a process nobody wants, and the difference is invisible
        # from here.
        #
        # A healthy seat outside the scanned window (say FridayWeaver on
        # :8713) makes the survey return {}, nothing is claimed, and this loop
        # would `taskkill /F` the only working local model on the machine —
        # while fixing nothing, because the routing failure is the
        # invisibility, not the process.
        #
        # So: a pid is reaped only if the survey positively SAW it serving a
        # model and nothing claimed it. An unidentified llama-server is left
        # alone. The cost is that a genuine orphan on a port we cannot see
        # may leak its VRAM until someone kills it by hand; that is strictly
        # the cheaper mistake, and the survey widening above shrinks the set
        # it can happen to.
        # A SEAT THE USER'S CONFIGURATION ROUTES TO IS NOT AN ORPHAN.
        #
        # `wanted` comes from the residency plan alone. A plan can contain no
        # pinned text seat at all — only stt/tts on-demand and a leased image
        # seat — when the brain has never been measured on the hardware. Then
        # `wanted` is empty, and a healthy gemma4:e2b-fridayweaver-1.0 seat,
        # surveyed and identified, would be reaped as unwanted the moment it
        # became visible. Making it visible without this check turns
        # invisibility-as-luck into a reliable kill.
        #
        # An enabled local openai-compatible descriptor naming that port is
        # an explicit statement that dispatch routes there. The plan not
        # asking for a seat is not the same as the user not wanting one.
        _protected = set()
        try:
            from agent_friday.services.provider_registry import (
                get_provider_registry)
            _cfg_ports = set()
            for prov in get_provider_registry().get_enabled_providers():
                if (prov.get("classification") == "local"
                        and prov.get("type") == "openai-compatible"):
                    _p = _endpoint_port(str(prov.get("base_url") or ""))
                    if _p:
                        _cfg_ports.add(_p)
            _protected = {pid for _m, (pid, port) in live.items()
                          if port in _cfg_ports}
        except Exception:
            _protected = set()

        # AN EMPTY PLAN IS NOT AUTHORITY TO KILL.
        #
        # `wanted` is empty whenever the plan pins no llama.cpp seat — which
        # is a NORMAL state wherever the brain has not been measured on the
        # hardware and the plan therefore lists only stt/tts on-demand and a
        # leased image seat. Reaping on an empty
        # `wanted` reads "the plan asked for nothing, so everything running
        # is garbage". The truthful reading is "the plan has no opinion",
        # and no opinion is not a mandate.
        #
        # Otherwise a healthy seat is surveyed, found absent from an empty
        # `wanted`, and killed on the very restart meant to make it reachable.
        if not wanted:
            print("  [arbiter] plan pins no llama.cpp seat; not reaping "
                  f"{len(_llama_server_pids())} live llama-server process(es) "
                  "— an empty plan is no opinion, not a mandate")
            _publish_endpoints(self.procs)
            return report

        _surveyed = {pid for _m, (pid, _p) in live.items()}
        for pid in ((_llama_server_pids() & _surveyed) - claimed - _protected):
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=20)
                report["reaped"].append(pid)
            except Exception:
                pass

        # UNCONDITIONALLY, and the condition it replaces is the bug. This used
        # to publish only `if report["adopted"] or report["reaped"]`, so the
        # one case that most needs the file rewritten -- nothing running at all
        # -- was the one case that left it untouched. When a pinned seat dies
        # with a restart, the survey comes back empty, nothing is adopted or
        # reaped, and endpoints.json goes on naming its port for hours after
        # the last process listening there has exited. `_serves` catches it at
        # every call, so
        # nothing was misrouted -- but a record that is wrong and merely
        # disbelieved is still wrong, it is the first artefact anyone debugging
        # this reads, and every reader paid a failing probe for it. Publishing
        # here prunes it: foreign entries survive only while they answer
        # /health, so this stays a merge and never becomes a clobber.
        reaped = set(report["reaped"])
        _publish_endpoints(self.procs, drop={
            m for m, (pid, _port) in live.items() if pid in reaped})
        for line in report["adopted"]:
            print(f"  [arbiter] adopted {line}")
        if report["reaped"]:
            print(f"  [arbiter] reaped {len(report['reaped'])} orphaned "
                  f"llama-server process(es): {sorted(report['reaped'])}")
        return report


class ComfyUIBackend:
    """Image generation. Started for a lease, stopped on release."""

    name = "comfyui"

    def __init__(self, root: Path | None = None, port=8188):
        self.root = root or (runtime_dir() / "ComfyUI")
        self.venv = runtime_dir() / "venv-comfy" / "Scripts" / "python.exe"
        self.port = port
        self.proc = None

    def running(self):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/system_stats" % self.port, timeout=2)
            return True
        except Exception:
            return False

    def start(self, timeout=300):
        if self.running():
            return 0.0
        self.proc = subprocess.Popen(
            [str(self.venv), "main.py", "--port", str(self.port)],
            cwd=str(self.root), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise TransitionError("ComfyUI exited %s" % self.proc.returncode)
            if self.running():
                return round(time.time() - t0, 2)
            time.sleep(2)
        self.stop()
        raise TransitionError("ComfyUI never became ready in %ss" % timeout)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except Exception:
                self.proc.kill()
        self.proc = None


# ─────────────────────────────────────────────────────────────────────────────
#  The Arbiter
# ─────────────────────────────────────────────────────────────────────────────

def _configured_image_model() -> str | None:
    """Which image model the creative seat names, if it is really installed.

    Read here rather than inside rp.plan so the policy stays pure. A configured
    model whose weights are absent is ignored, so the plan never advertises an
    exclusive lease for something that cannot render.
    """
    try:
        from agent_friday.core import _load_settings
        from agent_friday.services import local_image
        st = _load_settings() or {}
        # Same resolution order as creative_engine._configured_image_model,
        # including the flat `creative_model` fallback. If the two disagreed,
        # the plan would name one model while another actually rendered — the
        # quiet kind of wrong that reads as correct in every log.
        cr = (st.get("capability_routing") or {}).get("creative_image") or {}
        mid = cr.get("model") or st.get("creative_model")
        return mid if mid and local_image.is_installed(mid) else None
    except Exception:
        return None


def _normalise_seat_files(raw) -> dict:
    out = {}
    for model_id, v in dict(raw or {}).items():
        if isinstance(v, dict):
            if v.get("gguf"):
                out[model_id] = {"gguf": str(v["gguf"]),
                                 "lora": str(v["lora"]) if v.get("lora")
                                 else None,
                                 "mmproj": str(v["mmproj"]) if v.get("mmproj")
                                 else None}
        elif v:
            out[model_id] = {"gguf": str(v), "lora": None, "mmproj": None}
    return out


class Arbiter:
    def __init__(self, profile=None, entries=None, *, ollama=None,
                 llama=None, comfy=None, gguf_paths=None):
        self.profile = profile or hwp.get()
        self.entries = entries if entries is not None else \
            rc.installed_entries(self.profile)
        self.ollama = ollama or OllamaBackend()
        self.llama = llama or LlamaServerBackend()
        self.comfy = comfy or ComfyUIBackend()
        # Default to the extracted-GGUF registry rather than an empty dict.
        # Left empty, every pinned seat silently took the degraded-pin path and
        # the residency layer's central rule (R9) was unenforced everywhere
        # while looking configured — the caller has to remember to pass the one
        # thing without which nothing works.
        # A seat is up to three files. `gguf_paths` accepts the old shape
        # (`model_id -> weights path`) and the current one
        # (`model_id -> {"gguf", "lora", "mmproj"}`); both are normalised to
        # the second so `_load_pinned` can pass an adapter and a projector.
        self.gguf_paths = _normalise_seat_files(
            gguf_paths if gguf_paths is not None else rc.seat_files())
        self.state = STATE_DEFAULT
        self.plan = None
        self.planned_at = None          # when self.plan was computed
        self.lease = None
        self.transitions = []           # audit trail, with timings
        self._lock = threading.Lock()
        self._replan_lock = threading.Lock()
        # model_id -> last time §7's thrash row actually recorded a mark
        # (respond_to_monitor's own debounce; see _THRASH_MARK_INTERVAL_S).
        self._last_thrash_mark: dict = {}

    # ── planning ────────────────────────────────────────────────────────────

    #: How stale a plan may be before a reader gets a fresh one. Matched to
    #: hardware_profile's display-reserve cache (20 s) because that probe is the
    #: expensive part of replanning; a shorter TTL would spawn PowerShell more
    #: often than the sampler can answer, and a longer one re-opens the gap this
    #: exists to close.
    PLAN_MAX_AGE_S = 20.0

    def plan_fresh(self, max_age_s: float | None = None):
        """The plan, recomputed if it has gone stale. Never raises.

        `self.plan` is computed once at boot and then only on an explicit
        replan or a seat change. Settings -> Models rendered that snapshot
        under the heading "WHAT WILL NOT FIT RIGHT NOW", which was false in the
        way that matters: measured on the reference machine, the panel showed
        refusals computed at boot against a card with ~10 GB free, while the card had
        1 GB free and the settings the refusals referred to had since been
        rewritten by seat_binding.apply(). One payload, two moments -- the roles
        section read current settings, the refusal section read a plan from
        boot -- and they disagreed on four of six entries.

        Readers get a plan or they get nothing; a stale plan presented as
        current is worse than an empty list, because it is confidently wrong
        about the machine in front of the user.
        """
        ttl = self.PLAN_MAX_AGE_S if max_age_s is None else max_age_s
        fresh = (self.plan is not None and self.planned_at is not None
                 and (time.time() - self.planned_at) < ttl)
        if fresh:
            return self.plan
        # Non-blocking: if another thread is already replanning, use what we
        # have rather than queueing a second sample behind it. A poll every few
        # seconds must never stack.
        if not self._replan_lock.acquire(blocking=False):
            return self.plan
        try:
            from agent_friday.core import _load_settings
            from agent_friday.services import residency_catalog as _rc
            from agent_friday.services import seat_binding as _sb
            self.entries = _rc.installed_entries(self.profile)
            return self.compute_plan(
                _sb.overrides_from_settings(_load_settings() or {}))
        except Exception as e:
            _log = __import__("logging").getLogger("friday.residency")
            _log.warning("replan on read failed, serving the plan from %s: %s",
                         self.planned_at, e)
            return self.plan
        finally:
            self._replan_lock.release()

    def compute_plan(self, overrides=None):
        """Plan against the LIVE prompt overhead and the LIVE display draw.

        The system prompt is assembled from the vault, self-knowledge and
        persona, and the tool registry grows when someone adds a tool. Both are
        subtracted from every seat's usable window, so a plan built on a stale
        overhead figure hands out windows that are the wrong size in a way
        nothing would report.

        The desktop's VRAM draw is the same kind of moving number and was being
        treated as a constant. It is sampled here for the same reason: a plan
        is only as good as the machine it was planned against, and the gap
        between a 542 MiB cached floor and a 2,778 MiB compositor is enough
        to take a monitor off the desktop. Sampling stays out here in
        the arbiter so `rp.plan` remains a pure function of the profile and its
        golden fixtures keep meaning something.
        """
        # THE USER'S SEAT BINDINGS SURVIVE A BARE RECOMPUTE.
        #
        # Every reader passes `seat_binding.overrides_from_settings(...)` --
        # `plan_fresh`, the status route, replan -- so the plan they render
        # pins the seat the user chose. `boot()` must too: a bare
        # `compute_plan()` gives a plan with no binding, which prints "plan
        # pins no llama.cpp seat" and skips `_load_pinned` entirely. The status
        # page then shows `gemma4:e2b-fridayweaver-1.0` pinned at 131,072 while
        # no llama-server exists, because the only path that spawns one has
        # planned without the settings that name it. `server.py` primes this
        # with the settings overrides immediately before `boot()`; a call
        # with `overrides=None` reuses them rather than planning blind.
        if overrides is None:
            overrides = getattr(self, "_last_overrides", None)
        else:
            self._last_overrides = overrides
        from agent_friday.services import context_budget
        hwp.refresh_display_reserve(
            self.profile, ours_resident_mib=self._ours_resident_mib())
        _cloud = ()
        try:
            from agent_friday.core import _load_settings as _ls
            from agent_friday.services import seat_binding as _sb
            _cloud = _sb.cloud_seats_from_settings(_ls() or {})
        except Exception:
            pass
        self.plan = rp.plan(self.profile, self.entries, overrides,
                            overhead_tokens=context_budget.overhead_tokens(),
                            image_model=_configured_image_model(),
                            cloud_roles=_cloud)
        self.planned_at = time.time()
        return self.plan

    def preview(self, assignments):
        """What a proposed {role: model} selection would cost, before it lands.

        Goes through the Arbiter rather than beside it so the advice is
        computed against the SAME profile and catalog the plan uses -- including
        the live display reserve. An advisory built from a stale snapshot would
        tell the user a lineup fits on a card that no longer has the room.

        Never refuses. Returns `fits`, the overflow, and what would have to give.
        """
        with self._lock:
            hwp.refresh_display_reserve(
            self.profile, ours_resident_mib=self._ours_resident_mib())
            from agent_friday.services import context_budget
            return rp.preview_assignment(
                assignments or {}, self.entries, self.profile,
                overhead_tokens=context_budget.overhead_tokens())

    def timeout_for(self, entry):
        est = (entry or {}).get("est_load_s")
        if not est:
            return TIMEOUT_FLOOR_S * TIMEOUT_MULTIPLE
        return max(TIMEOUT_FLOOR_S, est * TIMEOUT_MULTIPLE)

    # ── headroom, before anything is loaded ─────────────────────────────────

    def admit(self, entry, *, current_host_mib=0):
        """Both refusals, checked before a load is attempted (R2 + R8).

        The disk charge for a LOAD is the host-RAM portion, not the artifact
        size. The artifact is already on disk — loading it does not consume
        the disk again. What it consumes is pagefile: Phase A A7 measured free
        disk falling 27.7 -> 7.0 GB while a 29 GB model was resident, and
        recovering when it unloaded. So the pagefile grows with the RESIDENT
        SET, and charging the artifact instead both refuses loads that are fine
        and misses the growth that is not.

        `check_disk_headroom` with the artifact size remains the right check
        for a DOWNLOAD, which is a different question.
        """
        total = 0
        gpu_portion = 0
        for m in (entry.get("measured") or []):
            if (m.get("total_mib") or 0) > total:
                total = m["total_mib"]
                gpu_portion = m.get("vram_mib") or 0
        ram = rp.check_ram_headroom(self.profile, total, current_host_mib)
        if not ram["ok"]:
            return ram
        host_mib = max(0, total - gpu_portion)
        disk = rp.check_disk_headroom(self.profile, host_mib)
        if not disk["ok"]:
            return disk
        return {"ok": True, "rule_id": None, "explanation": ""}

    # ── boot ────────────────────────────────────────────────────────────────

    def authorized_daemon_models(self) -> set:
        """Models the Arbiter has actually asked the daemon to hold."""
        out = set()
        try:
            for seat in (self.plan.get("seats") or {}).values():
                if not isinstance(seat, dict):
                    continue
                if str(seat.get("backend") or seat.get("device") or "").find("ollama") >= 0 \
                        or seat.get("served_by") == "ollama":
                    if seat.get("model_id"):
                        out.add(seat["model_id"])
        except Exception:
            pass
        try:
            for m in (getattr(self, "_daemon_leases", None) or set()):
                out.add(m)
        except Exception:
            pass
        return out

    def reconcile_daemon(self, evict: bool = True) -> dict:
        """Nothing occupies that GPU without the Arbiter knowing.

        The rule does not care who started it. Ollama's daemon has its own
        scheduler, and it can seat a model at 262k context behind the
        Arbiter's back, take the card, and drop a second monitor -- with the
        Arbiter none the wiser unless it looks.

        HONEST LIMIT, and it matters: Ollama exposes observation (`/api/ps`)
        and eviction (`keep_alive: 0`), but NO admission hook. Friday cannot
        stop `ollama run` in a terminal, or the tray app, from loading
        something -- it can only notice and reclaim. `OLLAMA_MAX_LOADED_MODELS`
        and `OLLAMA_KEEP_ALIVE` are read by the daemon at START, so enforcing
        them means owning daemon startup, which we do not. This is
        detect-and-reclaim, not prevention, and it should not be described as
        more than that.
        """
        report = {"authorized": [], "unexpected": [], "evicted": []}
        try:
            resident = self.ollama.resident() or {}
        except Exception as e:
            print(f"  [arbiter] could not read daemon residency: {e}")
            return report
        allowed = self.authorized_daemon_models()
        for model, mib in resident.items():
            if model in allowed:
                report["authorized"].append(model)
                continue
            report["unexpected"].append((model, mib))
            print(f"  [arbiter] daemon is holding {model!r} ({mib} MiB) that "
                  f"nothing here asked for")
            if evict:
                try:
                    self.ollama.evict(model)
                    report["evicted"].append(model)
                except Exception as e:
                    print(f"  [arbiter] could not evict {model!r}: {e}")
        if report["evicted"]:
            print(f"  [arbiter] reclaimed the card from "
                  f"{len(report['evicted'])} unauthorised daemon model(s)")
        return report

    def boot(self, *, measure_baseline=True):
        """Bring the machine to the default plan.

        Reconciles rather than reloading: a seat that is already resident and
        matches the plan is adopted, which on this host avoids a needless 20.5 s
        load every restart.
        """
        with self._lock:
            # BEFORE anything else. `evict_all` walks `self.procs`, which is
            # empty in a fresh process, so seats left running by the previous
            # one survived every restart -- including the baseline wipe, which
            # then measured an "idle" floor with several GB of forgotten
            # models still resident. Look at the machine first.
            self.compute_plan()
            try:
                _wanted = {s.get("model_id") for s in
                           (self.plan.get("seats") or {}).values()
                           if isinstance(s, dict) and s.get("status") == "pinned"}
                self.llama.adopt_or_reap({m for m in _wanted if m})
            except Exception as e:
                print(f"  [arbiter] adopt/reap failed (continuing): {e}")
            try:
                self.reconcile_daemon(evict=True)
            except Exception as e:
                print(f"  [arbiter] daemon reconciliation failed: {e}")

            if measure_baseline:
                # The one moment we can honestly measure the idle GPU floor.
                self.ollama.evict_all()
                self.llama.evict_all()
                time.sleep(2)
                hwp.refresh_baseline(self.profile, assert_idle=True)
            self.compute_plan()
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                already = self.ollama.resident()
                for role in ("interactive_brain", "sidekick"):
                    seat = (self.plan["seats"] or {}).get(role)
                    if not seat or seat.get("status") != "pinned":
                        continue
                    if seat["model_id"] in already:
                        self._record("adopt", role, seat["model_id"], 0.0)
                        continue
                    self._load_pinned(seat, role)
                emb = (self.plan["seats"] or {}).get("embedder")
                if emb and str(emb.get("device", "")).startswith("gpu"):
                    self._load_leased(emb, "embedder")
                self.state = STATE_DEFAULT
            except Exception as e:
                self.state = STATE_ROLLING_BACK
                self._rollback()
                self.state = STATE_DEGRADED
                raise TransitionError("boot failed: %s" % e)
            self._record("boot", "plan", None, round(time.time() - t0, 2))
            return self.plan

    # ── leases ──────────────────────────────────────────────────────────────

    def grant(self, kind, *, role=None, ttl_s=300):
        """Grant a capability lease, executing its transition serially."""
        with self._lock:
            if self.lease is not None:
                return {"ok": False, "error": "lease %s already held"
                        % self.lease["kind"]}
            if self.state not in (STATE_DEFAULT,):
                return {"ok": False, "error": "arbiter is %s" % self.state}
            # DISPLAY HEADROOM, checked against the LIVE card before committing.
            #
            # The plan does its arithmetic against a floor measured once at
            # Arbiter boot (542 MiB on the reference machine, against a
            # documented ~1 GB Windows compositor cost); without this check
            # nothing ever asks the GPU what is actually free. A card at 322
            # MiB free after a heartbeat loads a 9.6 GB model is where an
            # indirect display driver crashes and Windows loses a monitor.
            #
            # A refusal here is a sentence the user can read. A driver reset
            # is them waving a mouse at a dead screen wondering what happened.
            try:
                from agent_friday.services.hardware_profile import vram_headroom
                from agent_friday.services.headroom_contract import (
                    resolve_display_reserve)
                _reserve = resolve_display_reserve(self.profile)
                _hd = vram_headroom(reserve_mib=_reserve["mib"])
                if _hd.get("total_mib") and not _hd.get("ok"):
                    self.state = STATE_DEFAULT
                    return {"ok": False, "error": (
                        "not enough VRAM left for the desktop: %d MiB free "
                        "against a %d MiB display reserve (short by %d). "
                        "Loading now risks the display driver, which is how "
                        "a second monitor gets dropped. Free the "
                        "card or close a display-heavy app first."
                        % (_hd.get("free_mib", 0),
                           _hd.get("display_reserve_mib", 0),
                           _hd.get("shortfall_mib", 0))),
                        "refused": {"rule_id": "R-DISPLAY-RESERVE"}}
            except Exception:
                pass
            # DISK SYSTEM-VOLUME FLOOR, headroom.md §7's disk-floor row:
            # "refuse every load and every fetch". The EXISTING
            # `check_disk_headroom` (R8) watches the volume the MODEL STORE
            # lives on; this is the separate HR5 gap `machine_monitor`
            # closes -- the volume Windows itself is installed on, where
            # the pagefile and `~/.friday` live no matter what
            # `OLLAMA_MODELS` points at, and the one a stray temp-directory
            # leak can fill to 0 bytes. Uses
            # the SAME `DISK_FLOOR_MIB` (10 GiB, R8) rather than a new
            # number -- not D1-gated.
            try:
                from agent_friday.services import machine_monitor as mm
                _mon_s = mm.last_sample() or mm.sample(
                    ours_resident_mib=self._ours_resident_mib())
                # `disk_system_verdict`, not `verdict()` -- the latter also
                # advances the shared thrash-history window and would cost
                # this admission check a "tick" of thrash state on every
                # single grant(), which is not this check's to spend.
                _disk_v = mm.disk_system_verdict(_mon_s) or {}
                if _disk_v.get("status") == "breached":
                    self.state = STATE_DEFAULT
                    return {"ok": False, "error": (
                        "not enough free space on the system volume to "
                        "start a new load: %s"
                        % _disk_v.get("explanation", "")),
                        "refused": {"rule_id": "R-DISK-SYSTEM"}}
            except Exception:
                pass
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                if kind == "heavy_turn":
                    seat = self.plan["seats"].get("heavy_hitter")
                    if seat is None:
                        refusal = [r for r in self.plan["refusals"]
                                   if r["role"] == "heavy_hitter"]
                        self.state = STATE_DEFAULT
                        return {"ok": False,
                                "error": refusal[0]["explanation"]
                                if refusal else "no heavy seat"}
                    entry = self._entry(seat["model_id"])
                    adm = self.admit(entry)
                    if not adm["ok"]:
                        self.state = STATE_DEFAULT
                        return {"ok": False, "refused": adm,
                                "error": adm["explanation"]}
                    displaced = self._evict_pinned()
                    self._load_leased(seat, "heavy_hitter")
                    self.lease = {"kind": kind, "role": "heavy_hitter",
                                  "model_id": seat["model_id"],
                                  "displaced": displaced,
                                  "expires_at": time.time() + ttl_s}
                elif kind == "image_job":
                    displaced = self._evict_pinned()
                    self._evict_all_but_retained()
                    took = self.comfy.start()
                    self._record("start", "image", "comfyui", took)
                    self.lease = {"kind": kind, "role": "image",
                                  "model_id": "z-image-turbo-fp8",
                                  "displaced": displaced,
                                  "expires_at": time.time() + ttl_s}
                else:
                    self.state = STATE_DEFAULT
                    return {"ok": False, "error": "unknown lease %r" % kind}
                self.state = STATE_LEASED
                el = round(time.time() - t0, 2)
                self._record("grant", kind, self.lease.get("model_id"), el)
                return {"ok": True, "lease": dict(self.lease),
                        "transition_s": el}
            except Exception as e:
                self.state = STATE_ROLLING_BACK
                self._rollback()
                return {"ok": False, "error": str(e), "rolled_back": True}

    def release(self):
        """Give the GPU back and restore the default plan."""
        with self._lock:
            if self.lease is None:
                return {"ok": True, "note": "no lease held"}
            kind = self.lease["kind"]
            self.state = STATE_TRANSITIONING
            t0 = time.time()
            try:
                if kind == "heavy_turn":
                    self.ollama.evict(self.lease["model_id"])
                    self.llama.evict(self.lease["model_id"])
                elif kind == "image_job":
                    self.comfy.stop()
                self._restore_pinned(self.lease.get("displaced"))
                self.lease = None
                self.state = STATE_DEFAULT
                el = round(time.time() - t0, 2)
                self._record("release", kind, None, el)
                return {"ok": True, "transition_s": el}
            except Exception as e:
                self.state = STATE_DEGRADED
                return {"ok": False, "error": str(e)}

    def expire_if_due(self):
        """A crashed lease holder must not strand the GPU."""
        if self.lease and time.time() > self.lease.get("expires_at", 0):
            return self.release()
        return {"ok": True, "note": "not due"}

    # ── chains ──────────────────────────────────────────────────────────────

    def run_chain(self, plan: dict, on_stage, *,
                  chain_id: str | None = None) -> dict:
        """Execute a `ChainPlan` (`residency_policy.plan_chain`), stage by
        stage (headroom.md §6.5).

        `on_stage(stage) -> {"seconds": float, "vram_mib": int} | None` is
        the caller's own work — this method does not know how to transcribe
        audio or run ComfyUI, only how to sequence leases around whatever
        `on_stage` actually does. It is called once per stage, `resident`,
        `leased`, `cpu` and `cloud` alike, so a chain's cloud stages still
        run (they just never touch a lease).

        For each stage: re-check the machine via a fresh
        `machine_monitor.sample()`/`verdict()` at the boundary. If the
        DISPLAY RESERVE is breached, stop BEFORE the stage, release
        whatever lease is held, and raise the three-way through the
        existing `workflow_plan.build()`/`decide()` path, naming the stage
        — this is the one breach response this phase builds (§7's
        display-reserve row); a lesser VRAM-slack or RAM-floor breach is
        D1-gated and not answered here (`machine_monitor.verdict()` itself
        already reports those as `basis: "unknown"`, never `breached`, so
        there is nothing for this method to act on for them yet).
        Otherwise: `grant()` if the stage is `leased`, run `on_stage`,
        `release()`. A stage's real footprint/wall-clock, when `on_stage`
        reports one, is recorded through `record_measurement` (HR10) — a
        chain is the cheapest measurement job there is, per that section's
        own line.

        Cancellation reuses `local_image`'s job-flag pattern, as its own
        flag (`request_chain_cancel`/`is_chain_cancelled`/
        `clear_chain_cancel`, module-level above): checked before every
        stage, cleared when the chain ends for any reason.
        """
        from agent_friday.services import machine_monitor as mm
        from agent_friday.services import workflow_plan as wfp

        chain_id = chain_id or uuid.uuid4().hex[:12]
        stages = list(plan.get("stages") or [])
        results = []
        try:
            for i, stage in enumerate(stages):
                role = stage.get("role")
                if is_chain_cancelled(chain_id):
                    if self.lease is not None:
                        self.release()
                    results.append({"stage": i, "role": role,
                                    "status": "cancelled"})
                    break

                # §6.5 / §7 — the display-reserve row, checked live at the
                # boundary, before committing to this stage.
                try:
                    sample = mm.sample(
                        ours_resident_mib=self._ours_resident_mib())
                    verdict = mm.verdict(sample, profile=self.profile)
                except Exception as e:
                    verdict = None
                    print(f"  [arbiter] chain boundary sample failed "
                          f"(continuing): {e}")
                if verdict and verdict.get("display", {}).get("status") == \
                        "breached":
                    if self.lease is not None:
                        self.release()
                    why = verdict["display"]["explanation"]
                    proposal = wfp.build(
                        "The graphics card is needed elsewhere",
                        [{"title": "Continue the chain (%s)" % role,
                          "detail": "%s Stopped before stage %d (%s)."
                                   % (why, i, role),
                          "cls": "interactive"}],
                        summary="The display reserve was breached before "
                                "stage %d (%s): %s" % (i, role, why))
                    results.append({
                        "stage": i, "role": role, "status": "breached",
                        "verdict": verdict["display"],
                        "proposal_id": proposal["id"]})
                    break

                where = stage.get("where")
                if where == "leased":
                    kind = "image_job" if role in ("image", "video") \
                        else "heavy_turn"
                    granted = self.grant(kind)
                    if not granted.get("ok"):
                        results.append({
                            "stage": i, "role": role, "status": "refused",
                            "error": granted.get("error")})
                        break
                    t0 = time.time()
                    try:
                        out = on_stage(stage) or {}
                    finally:
                        self.release()
                    seconds = out.get("seconds", round(time.time() - t0, 2))
                    self._record_chain_measurement(stage, out, seconds)
                    results.append({"stage": i, "role": role,
                                    "status": "ran", "seconds": seconds})
                else:
                    t0 = time.time()
                    out = on_stage(stage) or {}
                    seconds = out.get("seconds", round(time.time() - t0, 2))
                    results.append({"stage": i, "role": role,
                                    "status": "ran", "seconds": seconds,
                                    "where": where})
        finally:
            clear_chain_cancel(chain_id)
        return {"chain_id": chain_id, "results": results}

    def _record_chain_measurement(self, stage: dict, out: dict,
                                  seconds: float) -> None:
        """HR10 — a chain records what it measured. Only for a leased stage
        that actually ran (a refusal or a cloud stage never reaches here) and
        only when `on_stage` reported real numbers; a stage that reports
        nothing records nothing rather than a placeholder (HR6)."""
        model_id = stage.get("model_id")
        vram_mib = out.get("vram_mib")
        if not model_id or vram_mib is None:
            return
        role = stage.get("role")
        try:
            fp = rc.make_footprint(
                modality="image" if role in ("image", "video") else "text",
                device="gpu", basis="measured",
                measured_at=time.strftime("%Y-%m-%d"),
                vram_mib=vram_mib, load_s=out.get("load_s"),
                unit="image" if role in ("image", "video") else "token",
                work_s_per_unit=seconds if role in ("image", "video")
                else None,
                artifact_bytes=out.get("artifact_bytes"))
            rc.record_footprint(model_id, rc.profile_fingerprint(self.profile),
                                fp)
        except Exception as e:
            print(f"  [arbiter] could not record chain measurement for "
                  f"{model_id!r} (continuing): {e}")

    # ── intrusion response (headroom.md §7, §12 Phase 5) ───────────────────
    #
    # `machine_monitor` is a sampler, not a decider (its own module
    # docstring: "it only ever reports"). Its `tick()` calls
    # `respond_to_monitor` below on every sample -- 5s cadence while a lease
    # is held, 60s at rest -- as a DISPATCH, not a decision: the reading is
    # handed to the ONE thing in this process allowed to act on it. Every
    # action from here on touches only Friday's own leases, seats and
    # registered jobs (HR7) -- never a PID this process did not start or
    # register.
    #
    # Only the rows §7's table lists as NOT D1-gated are answered:
    #   * display reserve breached  -> cancel + release, regardless of what
    #     kind of lease is held ("anything")
    #   * thrash signature breached, while a lease is held -> log + mark the
    #     leased model's footprint degraded (the phase's own resolution:
    #     NOT wired as equivalent to a VRAM-slack breach, since that state
    #     does not exist under D1)
    # The disk-system floor's response ("refuse every load and fetch") is
    # answered at the point of refusal instead -- `grant()`'s own
    # R-DISK-SYSTEM check above, and `routes/skills.ollama_pull` for
    # fetches -- because refusing admission IS "release leases at the
    # boundary": nothing new is granted, and whatever is already running
    # releases through its own existing completion path rather than being
    # torn down mid-job (that forced tear-down is the display-reserve row's
    # job, not this one's).
    #
    # `vram_slack`/`ram_available` breaches are not read here at all --
    # `machine_monitor.verdict()` reports them `basis: "unknown"` on every
    # machine today (D1 not decided), and there is nothing for this method
    # to act on until that changes.

    #: Debounce for the thrash log/record, matching `machine_monitor.
    #: _LOG_INTERVAL_S`'s own reasoning: a sustained breach fires on every
    #: 5s tick for as long as it lasts, and re-recording (and re-printing)
    #: the same mark every 5s would drown the log the way an uncapped
    #: rejection line does.
    _THRASH_MARK_INTERVAL_S = 300.0

    def respond_to_monitor(self, sample_: dict, verdict: dict) -> dict | None:
        """§7's ladder, the rows this phase answers. Called by
        `machine_monitor.tick()`; safe to call with no lease held (a no-op)
        and safe to call repeatedly (idempotent — `release()` with nothing
        held is already a no-op, and the thrash mark is debounced above).
        """
        if self.lease is None:
            return None
        display = (verdict or {}).get("display") or {}
        if display.get("status") == "breached":
            return self._on_display_breach(display)
        thrash = (verdict or {}).get("thrash") or {}
        if thrash.get("status") == "breached":
            return self._on_thrash_breach(sample_, thrash)
        return None

    def _cancel_inflight_render(self) -> list:
        """Cancel any OF OUR OWN in-flight `local_image` renders (HR7).

        Found through `core.PROCESSES` — the SAME registry
        `POST /api/processes/<pid>/cancel` already reads (`routes/
        tasks.py`) — filtered to `image-*` ids that have not already
        finished. Never reaches for a PID outside that registry, and never
        touches ComfyUI or a GPU process this Arbiter did not itself start
        (`self.comfy`, stopped through `release()` below, not here).
        """
        cancelled = []
        try:
            from agent_friday.core import PROCESSES, PROCESSES_LOCK
            from agent_friday.services import local_image as li
            with PROCESSES_LOCK:
                live = [pid for pid, p in PROCESSES.items()
                       if str(pid).startswith("image-")
                       and (p or {}).get("status") not in
                       ("completed", "error", "cancelled")]
            for pid in live:
                try:
                    li.request_cancel(pid)
                    li.interrupt_comfy()
                except Exception:
                    continue
                with PROCESSES_LOCK:
                    p = PROCESSES.get(pid)
                    if p is not None:
                        p["status"] = "cancelled"
                        p["ended"] = time.time()
                        p["label"] = ("Cancelled: the display reserve was "
                                      "breached (headroom.md §7)")
                cancelled.append(pid)
        except Exception as e:
            print(f"  [arbiter] could not cancel an in-flight render "
                  f"(continuing): {e}")
        return cancelled

    def _on_display_breach(self, display_verdict: dict) -> dict:
        """§7 — "display reserve breached | anything": cancel any in-flight
        render, release every lease, evict leased seats. Keep the retained
        sidekick (R10) only if it still fits the reserve once the lease's
        own draw is gone; otherwise it goes too. This is the monitor-loss
        case, answered for real.
        """
        why = display_verdict.get("explanation", "")
        cancelled = self._cancel_inflight_render()
        rel = self.release()
        evicted_retained = []
        try:
            from agent_friday.services import machine_monitor as mm
            s2 = mm.sample(ours_resident_mib=self._ours_resident_mib())
            v2 = mm.verdict(s2, profile=self.profile, _track_history=False)
            if (v2.get("display") or {}).get("status") == "breached":
                # Releasing OUR OWN lease did not clear it -- something
                # else on the card is the cause, and §7's line is explicit
                # that the retained sidekick is not exempt from that:
                # "Keep the retained sidekick only if it still fits inside
                # the reserve; otherwise it goes too."
                with self._lock:
                    for model_id in list(self._retained_models()):
                        if not model_id:
                            continue
                        self.llama.evict(model_id)
                        self.ollama.evict(model_id)
                        evicted_retained.append(model_id)
        except Exception as e:
            print(f"  [arbiter] post-release display-reserve recheck "
                  f"failed (continuing): {e}")
        self._record("intrusion-display-breach", "monitor", None, 0.0)
        print(f"  [arbiter] display reserve breached ({why}) -- cancelled "
              f"{len(cancelled)} render(s), released every lease"
              + (f", evicted the retained seat(s) {evicted_retained} "
                 "(still short even after release)" if evicted_retained
                 else "") + " (headroom.md §7, HR7)")
        return {"action": "display_breach", "cancelled_render": cancelled,
               "release": rel, "evicted_retained": evicted_retained,
               "explanation": why}

    def _on_thrash_breach(self, sample_: dict, thrash_verdict: dict) -> dict | None:
        """§7's thrash row, as this phase resolves it: log, and mark the
        currently-leased model's footprint degraded with the sample
        attached, so `runs_well` reflects it next time (§5.2) — NOT wired
        as equivalent to a VRAM-slack breach, since that state does not
        exist under D1 (§13 D1).
        """
        model_id = (self.lease or {}).get("model_id")
        if not model_id:
            return None
        now = time.time()
        last = self._last_thrash_mark.get(model_id, 0.0)
        if (now - last) < self._THRASH_MARK_INTERVAL_S:
            return None
        self._last_thrash_mark[model_id] = now
        why = thrash_verdict.get("explanation", "sustained thrash signature")
        try:
            rc.record_thrash_degraded(
                model_id, rc.profile_fingerprint(self.profile), sample_, why)
        except Exception as e:
            print(f"  [arbiter] could not record thrash degradation for "
                  f"{model_id!r} (continuing): {e}")
        self._record("intrusion-thrash-breach", "monitor", model_id, 0.0)
        print(f"  [arbiter] thrash signature breached while {model_id!r} is "
              f"leased -- {why} (marked degraded, headroom.md §7)")
        return {"action": "thrash_breach", "model_id": model_id,
               "explanation": why}

    # ── internals ───────────────────────────────────────────────────────────

    def _entry(self, model_id):
        for e in self.entries:
            if e["model_id"] == model_id:
                return e
        return {"model_id": model_id}

    def _record(self, action, role, model_id, seconds):
        self.transitions.append({"action": action, "role": role,
                                 "model_id": model_id, "seconds": seconds,
                                 "state": self.state})

    def _measure_resident(self, seat, cold_load_s=None):
        """Record what a seat ACTUALLY cost at the context it was given.

        Placement at an unmeasured context is an estimate — extrapolated along
        the model's KV slope, or in the worst case a lower bound from a smaller
        context. Loading the seat is the moment that estimate can be replaced
        with a fact, and the next plan then works from evidence. Without this
        the ladder would keep re-deciding from the same two seed rows forever.
        """
        model_id, num_ctx = seat.get("model_id"), seat.get("num_ctx")
        if not model_id or not num_ctx:
            return
        try:
            for m in (_get(self.ollama.base_url + "/api/ps") or {}) \
                    .get("models", []):
                if m.get("name") != model_id:
                    continue
                vram = round((m.get("size_vram") or 0) / 1048576)
                total = round((m.get("size") or 0) / 1048576)
                if not total:
                    return
                row = {"num_ctx": num_ctx, "vram_mib": vram,
                       "total_mib": total,
                       "pct_gpu": round(100.0 * vram / total) if total else 0,
                       "backend": rc.BACKEND_OLLAMA}
                if cold_load_s:
                    row["cold_load_s"] = round(cold_load_s, 2)
                rc.record_measurement(
                    model_id, rc.profile_fingerprint(self.profile), row)
                return
        except Exception:
            pass

    def _seat_files(self, model_id) -> dict:
        """The files a seat is spawned from, or an empty map."""
        return dict(self.gguf_paths.get(model_id) or {})

    def _llama_kwargs(self, files: dict) -> dict:
        """Only pass the adapter and projector when the seat has them, so a
        backend with the older `load()` signature keeps working."""
        kw = {}
        if files.get("lora"):
            kw["lora_path"] = files["lora"]
        if files.get("mmproj"):
            kw["mmproj_path"] = files["mmproj"]
        return kw

    def _daemon_has(self, model_id) -> bool:
        """Whether the daemon could serve this model. A backend without a
        `has()` (the test fakes) is assumed able, which is the old behaviour."""
        has = getattr(self.ollama, "has", None)
        if has is None:
            return True
        try:
            return bool(has(model_id))
        except Exception:
            return False

    def _load_pinned(self, seat, role):
        """R9: a pinned seat is a process we own, not a request to a daemon."""
        entry = self._entry(seat["model_id"])
        files = self._seat_files(seat["model_id"])
        gguf = files.get("gguf")
        t0 = time.time()
        why = DAEMON_SERVED.get(seat["model_id"])
        if why:
            # A deliberate exception, not a failure. It still reports as an
            # unenforced pin below, because that is exactly what it is — and a
            # seat we chose not to own must look the same in the status output
            # as one we failed to own, or the honest report becomes a
            # comfortable one.
            gguf = None
        if gguf:
            port = PORT_BASE + len(self.llama.procs)
            try:
                took = self.llama.load(seat["model_id"], seat["num_ctx"],
                                       gguf_path=gguf, port=port,
                                       timeout=self.timeout_for(entry),
                                       **self._llama_kwargs(files))
                self._record("load-pinned", role, seat["model_id"], took)
                return took
            except TransitionError as e:
                # A backend that cannot hold this model is a DEGRADED PIN, not
                # a failed boot. Measured case: Ollama stores gemma4:e2b across
                # several blobs, so a single blob path gives llama-server
                # "wrong number of tensors; expected 2012, got 601". Taking the
                # whole boot down for that would leave the machine with no
                # seats at all, which is strictly worse than one unenforced pin.
                why = str(e)
        else:
            why = "no GGUF mapped"

        # A MISSING MAPPING NEVER FALLS TO A DAEMON THAT DOES NOT HAVE THE
        # MODEL. When the GGUF registry names files in a directory that no
        # longer exists and Ollama holds zero models, every pinned load would
        # go to the daemon, 404, and leave the boot DEGRADED with nothing in
        # the log that said why. An honest empty seat, with
        # the reason on it, is the state the status endpoint should show.
        if not self._daemon_has(seat["model_id"]):
            reason = ("%s for %s, and the Ollama daemon does not have it "
                      "either; this seat is NOT SERVING (R9)"
                      % (why, seat["model_id"]))
            seat["pin_unenforced"] = reason
            seat["absent"] = True
            print("  [arbiter] %s" % reason)
            try:
                __import__("logging").getLogger("friday.residency").error(
                    "[arbiter] %s", reason)
            except Exception:
                pass
            self._record("load-pinned-absent", role, seat["model_id"],
                         round(time.time() - t0, 2))
            return 0.0

        self.ollama.load(seat["model_id"], seat["num_ctx"])
        took = round(time.time() - t0, 2)
        self._measure_resident(seat, took)
        seat["pin_unenforced"] = (
            "%s for %s, so this seat runs on Ollama and MAY BE EVICTED by the "
            "daemon's own scheduler (R9)" % (why, seat["model_id"]))
        self._record("load-pinned-degraded", role, seat["model_id"], took)
        return took

    def _load_leased(self, seat, role):
        """Leased seats may use either backend; a lease is when eviction is
        wanted, so Ollama's own scheduler is harmless here.

        The 26b runs on llama-server by measurement: within the R3 VRAM ceiling
        the two backends tie (27.95 Ollama vs 27.80 llama-server at
        --n-cpu-moe 20), and re-pulling Ollama's 17 GB copy would itself breach
        R8. llama-server also takes explicit --n-cpu-moe and -c, which is the
        control the offload placement needs.
        """
        entry = self._entry(seat["model_id"])
        files = self._seat_files(seat["model_id"])
        gguf = files.get("gguf")
        t0 = time.time()
        if gguf:
            port = PORT_BASE + 20 + len(self.llama.procs)
            took = self.llama.load(
                seat["model_id"], seat["num_ctx"] or 2048, gguf_path=gguf,
                port=port,
                n_cpu_moe=(seat.get("offload") or {}).get("n_cpu_moe", 20)
                if seat.get("is_moe") else None,
                timeout=self.timeout_for(entry),
                **self._llama_kwargs(files))
        elif not self._daemon_has(seat["model_id"]):
            raise TransitionError(
                "no GGUF mapped for %s and the Ollama daemon does not have "
                "it; the lease cannot be served" % seat["model_id"])
        else:
            self.ollama.load(seat["model_id"], seat["num_ctx"] or 2048)
            took = round(time.time() - t0, 2)
            self._measure_resident(seat, took)
        self._record("load-leased", role, seat["model_id"], took)
        return took

    def _evict_pinned(self):
        """Stand down the seats a lease may take. R10 says which it may not.

        The sidekick stays. A lease that evicts the whole pinned set makes
        Friday mute for the duration — from the outside the machine looks
        hung rather than busy. The maintainer's ruling: "keep e2b awake so
        Friday is always alive."
        """
        displaced = []
        for role in ("interactive_brain", "sidekick", "embedder"):
            if role in rp.RETAINED_THROUGH_LEASE:
                continue
            seat = (self.plan["seats"] or {}).get(role)
            if not seat or not str(seat.get("device", "")).startswith("gpu"):
                continue
            t0 = time.time()
            self.llama.evict(seat["model_id"])
            self.ollama.evict(seat["model_id"])
            displaced.append(role)
            self._record("evict", role, seat["model_id"],
                         round(time.time() - t0, 2))
        return displaced

    def _restore_pinned(self, roles=None):
        """Reload what a lease actually displaced.

        `roles` comes from the lease's own record, so a retained seat is not
        needlessly reloaded — reloading the sidekick would evict it first and
        briefly produce exactly the silence R10 exists to prevent.
        """
        for role in (roles if roles is not None
                     else ("interactive_brain", "sidekick")):
            if role in rp.RETAINED_THROUGH_LEASE:
                continue
            seat = (self.plan["seats"] or {}).get(role)
            if seat and seat.get("status") == "pinned":
                self._load_pinned(seat, role)

    def _retained_models(self):
        seats = self.plan["seats"] or {}
        return {(seats.get(r) or {}).get("model_id")
                for r in rp.RETAINED_THROUGH_LEASE if seats.get(r)}

    def _evict_all_but_retained(self):
        """Clear the card for an exclusive lease, except what R10 keeps.

        `evict_all()` on either backend would take the sidekick with everything
        else. Evicting and then reloading it would cost a cold start and open
        exactly the window of silence R10 exists to close, so the retained
        seats are named and skipped instead.
        """
        keep = self._retained_models()
        for name in list(self.ollama.resident()):
            if name not in keep:
                self.ollama.evict(name)
        for name in list(self.llama.procs):
            if name not in keep:
                self.llama.evict(name)

    def _rollback(self):
        try:
            self.comfy.stop()
            self._evict_all_but_retained()
            self._restore_pinned()
            self.lease = None
        except Exception:
            pass

    # ── introspection ───────────────────────────────────────────────────────

    def _ours_resident_mib(self) -> int:
        """VRAM this process knows it is holding, for the display-reserve probe.

        `refresh_display_reserve` falls back to the card's own `memory.used`
        when the WDDM counter is unusable, and `memory.used` counts our seats
        too. Handing it this number keeps it from double-counting them and
        refusing every placement -- the exact failure the `vram_baseline_mib`
        comment in hardware_profile.detect_gpus warns about.
        """
        total = 0
        try:
            for mib in (self.ollama.resident() or {}).values():
                if isinstance(mib, (int, float)) and mib > 0:
                    total += int(mib)
        except Exception:
            pass
        # `llama.procs` values are `(Popen, port)` tuples, not dicts, so the
        # old read of `proc.get("vram_mib")` counted nothing and the
        # display-reserve sampler then treated every llama-server seat as
        # compositor draw. The seat's footprint lives on the plan.
        try:
            seats = ((self.plan or {}).get("seats") or {})
            by_model = {}
            for seat in seats.values():
                if isinstance(seat, dict) and seat.get("model_id"):
                    by_model.setdefault(seat["model_id"], seat)
            for model_id in list(getattr(self.llama, "procs", {}) or {}):
                mib = (by_model.get(model_id) or {}).get("vram_mib")
                if isinstance(mib, (int, float)) and mib > 0:
                    total += int(mib)
        except Exception:
            pass
        return total

    def status(self):
        return {
            "state": self.state,
            "lease": dict(self.lease) if self.lease else None,
            "plan_seats": {r: (s or {}).get("model_id")
                           for r, s in (self.plan or {}).get("seats", {}).items()},
            "resident_ollama": self.ollama.resident(),
            "resident_llama_server": list(self.llama.procs),
            "transitions": list(self.transitions),
        }

    def shutdown(self):
        with self._lock:
            self.comfy.stop()
            self.llama.evict_all()
            self.state = STATE_DEFAULT
